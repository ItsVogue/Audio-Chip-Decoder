# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""GeneralPlus A1800 audio, added alongside the existing decoders without changing them.

A1800 is the speech and music codec of many GeneralPlus-based toys: 320 samples (20 ms at
16,000 Hz) per frame, 40 bytes per frame at 16 kbit/s. Decoding is done by the open-source
a1800_codec project (https://github.com/John-K/a1800_codec, Apache License 2.0), which the app
carries as a small helper program (audiochip/a18helper) and runs separately.

Two layouts are recognised, both by structure, never by guess:

* Toy flash containers, as found in a Fisher-Price Little People flash dump:
      FF  40|42  00  E0 2E  [payload length, 4 bytes LE]  40 01 00 00  [payload]
  The payload length must be a positive multiple of 40 and fit in the dump, and the last
  field must be 320 (samples per frame). A dump whose second half repeats the first is
  scanned once.
* A plain .a18 file: [payload length, 4 bytes LE] [bit rate, 2 bytes LE] [frames].

Each container becomes one entry in the Sounds list. The speed is 16,000 Hz, stated by the
codec; it is never estimated.
"""
from __future__ import annotations

import os
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .chipformats import Rendered, Sound

HERE = Path(__file__).with_name("a18helper")
RATE = 16000
FRAME_BYTES = 40
FRAME_SAMPLES = 320
HEADER = 13
MAX_CLIPS = 4000
GAP_SECONDS = 0.25
_MARKS = (b"\xff\x40\x00\xe0\x2e", b"\xff\x42\x00\xe0\x2e")

# id: (name, decode, encode, how)
CAPABILITIES = {
    "a1800": ("GeneralPlus A1800: toy flash containers and .a18 files", True, False, "a1800_codec (helper program)"),
}


class HelperMissing(RuntimeError):
    pass


class HelperError(RuntimeError):
    pass


_MISSING = ("The A1800 helper (a18helper/a1800.exe) is missing from this installation, so this audio cannot be "
            "decoded. Installing the app again restores it.")


def helper() -> list[str] | None:
    override = os.environ.get("AUDIOCHIP_A1800")
    if override is not None:
        return shlex.split(override) or None
    exe = HERE / ("a1800.exe" if sys.platform == "win32" else "a1800")
    if exe.exists():
        return [str(exe)]
    if sys.platform != "win32":
        found = shutil.which("a1800_codec")
        if found:
            return [found]
    return None


def available() -> bool:
    return helper() is not None


def _check_cancel() -> None:
    from .codecs import check_cancel
    check_cancel()


# ------------------------------------------------------------------ finding
@dataclass(frozen=True)
class Clip:
    offset: int          # where the container (or file) starts in the data
    start: int           # where the A1800 frames start
    length: int          # bytes of frames
    kind: int            # the container's second byte, 0x40 or 0x42; 0 for a plain .a18 file
    bitrate: int = 16000

    @property
    def frames(self) -> int:
        return self.length // (self.bitrate // 400)

    @property
    def seconds(self) -> float:
        return self.frames * FRAME_SAMPLES / RATE


def mirrored_half(data: bytes) -> bool:
    """True if the second half of the data repeats the first, as a chip smaller than the dump reads."""
    n = len(data)
    return n >= 2048 and n % 2 == 0 and data[:n // 2] == data[n // 2:]


def a18_file(data: bytes) -> Clip | None:
    """The data as one plain .a18 file, if its header agrees with its size."""
    if len(data) < 6 + FRAME_BYTES:
        return None
    length, bitrate = struct.unpack("<IH", data[:6])
    if not 4800 <= bitrate <= 32000 or bitrate % 800:
        return None
    frame = bitrate // 400
    if length < frame or length % frame or not length <= len(data) - 6 < length + 64:
        return None
    return Clip(0, 6, length, 0, bitrate)


def find_containers(data: bytes, limit: int = MAX_CLIPS) -> list[Clip]:
    """Toy flash containers in the data, in order. Every rule of the header must hold."""
    d = bytes(data)
    end = len(d) // 2 if mirrored_half(d) else len(d)
    starts = []
    for mark in _MARKS:
        pos = d.find(mark, 0, end)
        while pos >= 0:
            starts.append(pos)
            pos = d.find(mark, pos + 1, end)
    out: list[Clip] = []
    for pos in sorted(starts):
        if pos + HEADER > end:
            continue
        length, per_frame = struct.unpack("<II", d[pos + 5:pos + 13])
        if per_frame != FRAME_SAMPLES or length <= 0 or length % FRAME_BYTES:
            continue
        if pos + HEADER + length > end:
            continue
        out.append(Clip(pos, pos + HEADER, length, d[pos + 1]))
        if len(out) >= limit:
            break
    return out


def find_clips(data: bytes) -> list[Clip]:
    one = a18_file(data)
    return [one] if one else find_containers(data)


# ----------------------------------------------------------------- decoding
def _read_wav(path: str) -> np.ndarray:
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise HelperError("the helper wrote no audio")
    pos = 12
    while pos + 8 <= len(raw):
        tag, size = raw[pos:pos + 4], struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        if tag == b"fmt ":
            fmt, channels, _, _, _, bits = struct.unpack("<HHIIHH", raw[pos + 8:pos + 24])
            if fmt != 1 or channels != 1 or bits != 16:
                raise HelperError("the helper wrote audio in an unexpected form")
        elif tag == b"data":
            body = raw[pos + 8:pos + 8 + size]
            return np.frombuffer(body[:len(body) // 2 * 2], dtype="<i2").copy()
        pos += 8 + size + (size & 1)
    raise HelperError("the helper wrote no audio")


def decode_frames(frames: bytes, bitrate: int = 16000, workdir: str | None = None) -> np.ndarray:
    """Decode one run of A1800 frames to 16-bit samples at 16,000 Hz."""
    tool = helper()
    if tool is None:
        raise HelperMissing(_MISSING)
    own = workdir is None
    folder = tempfile.mkdtemp(prefix="audiochip_a18_") if own else workdir
    src, dst = os.path.join(folder, "clip.a18"), os.path.join(folder, "clip.wav")
    try:
        with open(src, "wb") as f:
            f.write(struct.pack("<IH", len(frames), bitrate) + frames)
        if os.path.exists(dst):
            os.remove(dst)
        try:
            done = subprocess.run(tool + ["decode", src, dst], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, timeout=60 + len(frames) / 20000,
                                  creationflags=0x08000000 if sys.platform == "win32" else 0)
        except OSError as e:
            raise HelperMissing(f"The A1800 helper could not be started: {e}") from None
        except subprocess.TimeoutExpired:
            raise HelperError("the helper did not finish in time and was stopped") from None
        if done.returncode != 0 or not os.path.exists(dst):
            raise HelperError((done.stderr.decode("utf-8", "replace").strip().splitlines() or ["decoding failed"])[-1][:200])
        return _read_wav(dst)
    finally:
        if own:
            shutil.rmtree(folder, ignore_errors=True)


def decode_tolerant(frames: bytes, bitrate: int = 16000, workdir: str | None = None) -> tuple[np.ndarray, int]:
    """Decode a clip even if some frames are invalid. Returns (samples, frames left silent).

    The decoder gives up on a whole clip when one frame breaks its rules. When that happens the clip is
    split in two and each part tried again, down to single frames; a frame that fails alone is left silent.
    """
    step = bitrate // 400
    try:
        return decode_frames(frames, bitrate, workdir), 0
    except HelperError:
        count = len(frames) // step
        if count <= 1:
            return np.zeros(FRAME_SAMPLES * max(count, 0), dtype=np.int16), count
        half = count // 2 * step
        a, bad_a = decode_tolerant(frames[:half], bitrate, workdir)
        b, bad_b = decode_tolerant(frames[half:count * step], bitrate, workdir)
        return np.concatenate([a, b]), bad_a + bad_b


def render(b: np.ndarray) -> Rendered:
    data = b.tobytes()
    empty = np.zeros(0, dtype=np.int16)
    clips = find_clips(data)
    if not clips:
        return Rendered(empty, None, [], "No GeneralPlus A1800 container (FF 40/42 00 E0 2E, length, 320) and no "
                                         ".a18 file header was found in these bytes.")
    if not available():
        return Rendered(empty, None, [], _MISSING)
    folder = tempfile.mkdtemp(prefix="audiochip_a18_")
    parts, sounds, problems, short, silent = [], [], [], 0, 0
    gap = np.zeros(int(RATE * GAP_SECONDS), dtype=np.int16)
    pos = 0
    try:
        for i, c in enumerate(clips, 1):
            _check_cancel()
            try:
                x, bad = decode_tolerant(data[c.start:c.start + c.length], c.bitrate, folder)
            except HelperMissing as e:
                return Rendered(empty, None, [], str(e))
            if bad:
                silent += bad
                if bad >= c.frames:
                    problems.append(f"clip at 0x{c.offset:X}: no frame could be decoded")
                    continue
            if len(x) != c.frames * FRAME_SAMPLES:
                short += 1
            if parts:
                parts.append(gap)
                pos += len(gap)
            label = f"Clip {i}" + (f" (type {c.kind:02X})" if c.kind else "")
            sounds.append(Sound(pos, pos + len(x), label, c.offset))
            parts.append(x)
            pos += len(x)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if not parts:
        return Rendered(empty, None, [], "The A1800 data could not be decoded. " + " | ".join(problems[:3]))
    total = sum(c.seconds for c in clips)
    if clips[0].kind == 0:
        note = (f"GeneralPlus A1800 file: {clips[0].frames:,} frames at {clips[0].bitrate:,} bit/s, "
                f"{total:.2f} seconds at 16,000 Hz.")
    else:
        note = (f"GeneralPlus A1800: {len(clips)} audio containers found by their headers, {total:.1f} seconds in "
                f"all, decoded at 16,000 Hz (320 samples per 40-byte frame).")
        if mirrored_half(data):
            note += " The second half of the dump repeats the first, so it was scanned once."
    if problems:
        note += f" {len(problems)} could not be decoded: " + " | ".join(problems[:2])
    if silent:
        note += f" {silent} invalid frame(s) were left silent."
    if short:
        note += f" {short} decoded to a different length than their frame count gives."
    return Rendered(np.concatenate(parts), RATE, sounds if len(sounds) > 1 else [], note)


# ---------------------------------------------------------------- detection
def extra_candidates(data: bytes, Candidate, Hypothesis, not_this: float) -> list:
    hyp = Hypothesis("a1800")

    def miss(why: str):
        return [Candidate(hyp, 1.0, not_this, 0.0, 0.0, why)]

    try:
        clips = find_clips(data)
    except Exception:
        clips = []
    if not clips:
        return miss("No GeneralPlus A1800 container header and no .a18 file header in the data.")
    if not available():
        return miss(f"The data holds {len(clips)} GeneralPlus A1800 clip(s), but the A1800 helper is missing from "
                    "this installation.")
    first = clips[0]
    try:
        probe = decode_frames(data[first.start:first.start + min(first.length, 10 * FRAME_BYTES)], first.bitrate)
    except (HelperMissing, HelperError) as e:
        return miss(f"The data has a GeneralPlus A1800 header, but the first clip could not be decoded: {e}")
    if not len(probe):
        return miss("The data has a GeneralPlus A1800 header, but the first clip decoded to nothing.")
    total = sum(c.seconds for c in clips)
    if first.kind == 0:
        ev = (f"The file is a GeneralPlus .a18 file: its header gives {first.length:,} bytes of frames at "
              f"{first.bitrate:,} bit/s, which matches the file size.")
        cap = "high"
    else:
        types = sorted({c.kind for c in clips})
        ev = (f"{len(clips)} GeneralPlus A1800 containers found: each starts FF {'/'.join(f'{t:02X}' for t in types)} "
              f"00 E0 2E, gives a length that is a multiple of 40 and fits in the dump, and states 320 samples "
              f"per frame. {total:.1f} seconds of audio in all at 16,000 Hz.")
        cap = "high" if len(clips) >= 3 else "medium"
    return [Candidate(hyp, 0.001, 0.0, 0.0, 0.0, ev, worst_channel=0.001, evidence=ev, cap=cap)]


# -------------------------------------------------------------- registration
def register(CODECS: dict, RENDERERS: dict, Codec, COST: dict) -> None:
    if "a1800" in CODECS:
        return
    RENDERERS.setdefault("a1800", render)
    COST.setdefault("a1800", 6.0)                # seconds per megabyte: one helper run per clip
    CODECS["a1800"] = Codec(
        "a1800", "GeneralPlus A1800 (toy speech and music)", "chip", FRAME_BYTES / FRAME_SAMPLES,
        "The codec of many GeneralPlus-based talking toys. Clips are found by their container headers; each "
        "becomes a sound in the list.",
        "Container: FF 40|42 00 E0 2E, payload length (4 bytes), 320 (4 bytes), then 40-byte frames.\n"
        "Each frame decodes to 320 samples, 20 ms at 16,000 Hz; 16 kbit/s.\n"
        "Decoded by the open-source a1800_codec, carried as a helper program.",
        lambda b: render(b).samples, structured=True, layouts=("normal",))
