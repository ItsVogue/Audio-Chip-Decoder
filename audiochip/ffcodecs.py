"""Audio formats decoded by FFmpeg, added alongside the existing decoders without changing them.

The app ships a small FFmpeg (audiochip/ffhelper, built by tools/build-ffmpeg.sh with only
audio decoders switched on; LGPL) and runs it as a separate program, the same way it runs
minipro for XGecu programmers. Nothing here is a decoder written for this app: every format
below is decoded by FFmpeg's own, established implementation.

What it adds:

* AAC / HE-AAC (ADTS streams, M4A / MP4 / 3GP), WMA (ASF), AMR-NB and AMR-WB, Speex (in Ogg),
  QCELP and EVRC (QCP, 3G2), CRI ADX, EA ADPCM (SCHl files), Bink Audio, Creative ADPCM (VOC),
  Nintendo DSP ADPCM inside BRSTM / BFSTM / GENH files, XWMA, and WAV or CAF files whose
  contents the built-in reader does not handle.
* The same files when they sit inside a larger dump: they are found by their signatures.
* Headerless AMR-NB frames (recognised by the chain of frame headers) and headerless
  G.726 at 16 kbit/s (manual selection only).

Files are recognised by signature only, and a file is offered only after FFmpeg has opened it
and reported an audio stream. Nothing here is ever chosen on a guess.
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .chipformats import Rendered, Sound

HERE = Path(__file__).with_name("ffhelper")
MAX_OUTPUT = 512 << 20          # decoded audio accepted from one file, in bytes
MAX_FILES = 400                 # files decoded from inside one dump
MAX_CHANNELS = 8
GAP_SECONDS = 0.25              # silence laid between files found inside one dump

# id: (name, decode, encode, how)
CAPABILITIES = {
    "aac": ("AAC / HE-AAC: ADTS streams, M4A, MP4, 3GP", True, False, "FFmpeg"),
    "wma": ("WMA 1, 2, Pro, Lossless and Voice (ASF, XWMA)", True, False, "FFmpeg"),
    "amr": ("AMR-NB and AMR-WB: .amr files, 3GP, headerless AMR-NB frames", True, False, "FFmpeg"),
    "speex": ("Speex in Ogg", True, False, "FFmpeg"),
    "qcelp": ("QCELP and EVRC (QCP, 3G2)", True, False, "FFmpeg"),
    "adx": ("CRI ADX", True, False, "FFmpeg"),
    "ea": ("EA ADPCM (SCHl files)", True, False, "FFmpeg"),
    "bink": ("Bink Audio (BIK, BK2 audio tracks, BINKA)", True, False, "FFmpeg"),
    "creative": ("Creative ADPCM 4-bit, 2.6-bit and 2-bit (VOC, WAV)", True, False, "FFmpeg"),
    "dsp_stream": ("Nintendo DSP ADPCM in BRSTM / BFSTM / GENH", True, False, "FFmpeg"),
    "g726_16": ("G.726 16 kbit/s, headerless", True, False, "FFmpeg"),
}


class HelperMissing(RuntimeError):
    """The FFmpeg helper program is not part of this installation."""


class HelperError(RuntimeError):
    """FFmpeg could not decode the data."""


# ------------------------------------------------------------------ the helper
def helper() -> list[str] | None:
    """Command that starts the FFmpeg helper, or None if this installation has none."""
    override = os.environ.get("AUDIOCHIP_FFMPEG")
    if override is not None:
        return shlex.split(override) or None
    exe = HERE / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if exe.exists():
        return [str(exe)]
    if sys.platform != "win32":
        found = shutil.which("ffmpeg")
        if found:
            return [found]
    return None


def available() -> bool:
    return helper() is not None


_MISSING = ("The FFmpeg helper (ffhelper/ffmpeg.exe) is missing from this installation, so this format cannot "
            "be decoded. Installing the app again restores it.")


def _flags() -> int:
    return 0x08000000 if sys.platform == "win32" else 0        # CREATE_NO_WINDOW


def _check_cancel() -> None:
    from .codecs import check_cancel
    check_cancel()


def _run(args: list[str], limit: int, timeout: float) -> tuple[bytes, str, bool]:
    """Run the helper. Returns (stdout, stderr text, cut short because the output limit was reached)."""
    tool = helper()
    if tool is None:
        raise HelperMissing(_MISSING)
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(tool + args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err,
                                    creationflags=_flags())
        except OSError as e:
            raise HelperMissing(f"The FFmpeg helper could not be started: {e}") from None
        chunks: list[bytes] = []
        size = [0]

        def pump():
            try:
                while True:
                    block = proc.stdout.read(1 << 18)
                    if not block:
                        break
                    chunks.append(block)
                    size[0] += len(block)
                    if size[0] > limit:
                        break
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        cut = False
        try:
            while reader.is_alive():
                reader.join(0.05)
                if size[0] > limit:
                    cut = True
                    break
                if time.monotonic() > deadline:
                    raise HelperError(f"FFmpeg did not finish within {timeout:.0f} seconds and was stopped.")
                _check_cancel()
        finally:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                pass
            reader.join(5)
            try:
                proc.stdout.close()
            except OSError:
                pass
        err.seek(0)
        text = err.read(1 << 16).decode("utf-8", "replace")
    return b"".join(chunks), text, cut


class _TempFile:
    """The bytes to decode, written where FFmpeg can read and seek them."""

    def __init__(self, data: bytes, prefix: bytes = b""):
        fd, self.path = tempfile.mkstemp(prefix="audiochip_ff_", suffix=".bin")
        with os.fdopen(fd, "wb") as f:
            f.write(prefix)
            f.write(data)

    def __enter__(self) -> str:
        return self.path

    def __exit__(self, *exc) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass


def _clean(text: str) -> str:
    lines = [ln.strip() for ln in text.replace("\r", "\n").splitlines() if ln.strip()]
    lines = [re.sub(r"^\[[^\]]*\]\s*", "", ln) for ln in lines]
    lines = [re.sub(r"\S*audiochip_ff_\w+\.bin:?\s*", "", ln) for ln in lines]
    return "; ".join(dict.fromkeys(ln for ln in lines if ln))[:300]


_NAMES = {
    "aac": "AAC", "aac_latm": "AAC (LATM)", "wmav1": "WMA 1", "wmav2": "WMA 2", "wmapro": "WMA Pro",
    "wmalossless": "WMA Lossless", "wmavoice": "WMA Voice", "amr_nb": "AMR-NB", "amrnb": "AMR-NB",
    "amr_wb": "AMR-WB", "amrwb": "AMR-WB", "speex": "Speex", "qcelp": "QCELP", "evrc": "EVRC",
    "adpcm_adx": "CRI ADX ADPCM", "adpcm_thp": "Nintendo DSP ADPCM", "adpcm_thp_le": "Nintendo DSP ADPCM",
    "binkaudio_rdft": "Bink Audio", "binkaudio_dct": "Bink Audio", "adpcm_ct": "Creative ADPCM 4-bit",
    "adpcm_sbpro_4": "Creative ADPCM 4-bit", "adpcm_sbpro_3": "Creative ADPCM 2.6-bit",
    "adpcm_sbpro_2": "Creative ADPCM 2-bit", "alac": "Apple Lossless", "mp3": "MP3", "mp2": "MPEG layer II",
    "adpcm_yamaha": "Yamaha ADPCM", "adpcm_ima_oki": "OKI / Dialogic ADPCM", "truespeech": "TrueSpeech",
    "g723_1": "G.723.1", "g729": "G.729", "gsm": "GSM 06.10", "gsm_ms": "GSM 06.10", "adpcm_g726": "G.726",
    "adpcm_g726le": "G.726", "adpcm_ms": "Microsoft ADPCM", "adpcm_swf": "Flash ADPCM",
}


def _codec_name(raw: str) -> str:
    word = raw.split()[0] if raw.split() else raw
    if word in _NAMES:
        return _NAMES[word]
    if word.startswith("adpcm_ea") or word.startswith("adpcm_ima_ea"):
        return "EA ADPCM"
    if word.startswith("adpcm_ima"):
        return "IMA ADPCM"
    if word.startswith("pcm_"):
        return "PCM"
    return word


_STREAM = re.compile(r"Stream #0[^\n]*?Audio: ([^\n,]+)(?:,\s*(\d+) Hz)?(?:,\s*([^,\n]+))?")


@dataclass(frozen=True)
class Info:
    codec: str          # FFmpeg's description of the audio, e.g. "aac (LC)"
    rate: int
    layout: str         # "mono", "stereo", "5.1" ...

    def text(self) -> str:
        return f"{self.codec}, {self.layout or 'unknown layout'}, {self.rate:,} Hz"


def _input_args(fmt: str, path: str) -> list[str]:
    extra = {"g726_16": ["-f", "g726", "-code_size", "2", "-ar", "8000"],
             "g726le_16": ["-f", "g726le", "-code_size", "2", "-ar", "8000"]}.get(fmt, ["-f", fmt])
    return ["-nostdin", "-hide_banner", "-v", "info"] + extra + ["-i", path]


def probe(data: bytes, fmt: str, prefix: bytes = b"") -> Info | None:
    """Ask FFmpeg what audio the file holds. None if it finds no audio stream in it."""
    with _TempFile(data, prefix) as path:
        _, text, _ = _run(_input_args(fmt, path), 1 << 16, 30)
    m = _STREAM.search(text)
    if not m or not m.group(2):
        return None
    return Info(_codec_name(m.group(1).strip()), int(m.group(2)), (m.group(3) or "").strip())


def _parse_wav(raw: bytes) -> tuple[np.ndarray, int]:
    """PCM out of the WAV stream FFmpeg wrote to its output pipe."""
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise HelperError("FFmpeg produced no audio.")
    pos, rate, channels = 12, 0, 0
    while pos + 8 <= len(raw):
        tag, size = raw[pos:pos + 4], struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = pos + 8
        if tag == b"fmt ":
            fmt, channels, rate = struct.unpack("<HHI", raw[body:body + 8])
            if fmt not in (1, 0xFFFE) or not 1 <= channels <= 64 or rate <= 0:
                raise HelperError("FFmpeg produced audio in an unexpected form.")
        elif tag == b"data":
            if not channels:
                break
            pcm = raw[body:]                                  # a piped WAV cannot state its length
            n = len(pcm) // (2 * channels)
            x = np.frombuffer(pcm, dtype="<i2", count=n * channels).reshape(n, channels)
            return x, rate
        pos = body + size + (size & 1)
    raise HelperError("FFmpeg produced no audio.")


def decode_file(data: bytes, fmt: str, prefix: bytes = b"") -> tuple[np.ndarray, int, bool]:
    """Decode one file. Returns (int16 frames x channels, sample rate, cut short at the size limit)."""
    timeout = min(900.0, 60.0 + 30.0 * (len(data) / (1 << 20)))
    with _TempFile(data, prefix) as path:
        args = _input_args(fmt, path)
        args[args.index("info")] = "error"
        raw, text, cut = _run(args + ["-map", "0:a:0", "-vn", "-sn", "-dn", "-c:a", "pcm_s16le", "-fflags",
                                      "+bitexact", "-f", "wav", "pipe:1"], MAX_OUTPUT, timeout)
    try:
        x, rate = _parse_wav(raw)
    except HelperError:
        raise HelperError(_clean(text) or "FFmpeg found no audio it could decode in this data.") from None
    if x.shape[1] > MAX_CHANNELS:
        x = x[:, :MAX_CHANNELS]
    return np.ascontiguousarray(x), rate, cut


# ------------------------------------------------------------------ signatures
@dataclass(frozen=True)
class Found:
    offset: int
    length: int
    kind: str           # name shown to the user
    fmt: str            # FFmpeg reader used for it
    prefix: bytes = b""  # bytes put in front of the data before FFmpeg reads it


_ASF = bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")
_ASF_FILE_PROPS = bytes.fromhex("a1dcab8c47a9cf118ee400c00c205365")
_AMR_NB = (13, 14, 16, 18, 20, 21, 27, 32, 6, 0, 0, 0, 0, 0, 0, 1)        # frame bytes by type, header included
_AMR_WB = (18, 24, 33, 37, 41, 47, 51, 59, 61, 6, 0, 0, 0, 0, 1, 1)
_ADTS_RATES = 13


def _amr_chain(d: bytes, pos: int, sizes: tuple, limit: int = 1 << 30) -> tuple[int, int, int]:
    """Follow AMR frames from pos. Returns (frames, position after the last good frame, frames that carry data)."""
    n = live = 0
    end = len(d)
    while pos < end and n < limit:
        toc = d[pos]
        if toc & 0x83:
            break
        step = sizes[(toc >> 3) & 15]
        if not step or pos + step > end:
            break
        if step > 1 and d.count(0, pos + 1, pos + step) < step - 1:
            live += 1
        pos += step
        n += 1
    return n, pos, live


def _adts_chain(d: bytes, pos: int, limit: int = 1 << 30) -> tuple[int, int]:
    n = 0
    end = len(d)
    first = None
    while pos + 7 <= end and n < limit:
        if d[pos] != 0xFF or (d[pos + 1] & 0xF6) != 0xF0:
            break
        key = d[pos + 2] & 0xFC                                # profile and sample rate: constant in a stream
        if ((d[pos + 2] >> 2) & 15) >= _ADTS_RATES or (first is not None and key != first):
            break
        size = ((d[pos + 3] & 3) << 11) | (d[pos + 4] << 3) | (d[pos + 5] >> 5)
        if size < 8 or pos + size > end:
            break
        first = key
        pos += size
        n += 1
    return n, pos


def _mp4_length(d: bytes, off: int) -> int:
    pos, seen = off, set()
    while pos + 8 <= len(d):
        size = struct.unpack(">I", d[pos:pos + 4])[0]
        tag = d[pos + 4:pos + 8]
        if size == 1 and pos + 16 <= len(d):
            size = struct.unpack(">Q", d[pos + 8:pos + 16])[0]
        if size < 8 or not all(32 <= c < 127 for c in tag) or pos + size > len(d):
            break
        seen.add(tag)
        pos += size
    return pos - off if b"moov" in seen else 0


def _ogg_length(d: bytes, off: int) -> int:
    pos = off
    while pos + 27 <= len(d) and d[pos:pos + 4] == b"OggS":
        nseg = d[pos + 26]
        if pos + 27 + nseg > len(d):
            break
        size = 27 + nseg + sum(d[pos + 27:pos + 27 + nseg])
        if pos + size > len(d):
            break
        pos += size
    return pos - off


def _voc_length(d: bytes, off: int) -> int:
    if off + 26 > len(d):
        return 0
    pos = off + struct.unpack("<H", d[off + 20:off + 22])[0]
    while pos < len(d):
        kind = d[pos]
        if kind == 0:
            return pos + 1 - off
        if pos + 4 > len(d) or kind > 9:
            break
        pos += 4 + int.from_bytes(d[pos + 1:pos + 4], "little")
    return min(pos, len(d)) - off


def _ea_length(d: bytes, off: int) -> int:
    pos = off
    while pos + 8 <= len(d):
        tag = d[pos:pos + 4]
        if tag[:2] != b"SC" or tag[3:4] != b"l":
            break
        size = struct.unpack("<I", d[pos + 4:pos + 8])[0]
        if size > 1 << 28:
            size = struct.unpack(">I", d[pos + 4:pos + 8])[0]
        if size < 8 or pos + size > len(d):
            break
        pos += size
        if tag == b"SCEl":
            break
    return pos - off


def _wav_needs_helper(d: bytes, off: int) -> bool:
    """True for a WAV or CAF file the built-in reader (libsndfile) cannot open."""
    try:
        import io

        import soundfile
        with soundfile.SoundFile(io.BytesIO(bytes(d[off:]))):
            return False
    except Exception:
        return True


def _at(d: bytes, off: int) -> Found | None:
    """The file that starts at this offset, if its signature and header are those of a supported format."""
    n = len(d) - off
    if n < 16:
        return None
    h = d[off:off + 64]
    if h[:16] == _ASF:
        p = d.find(_ASF_FILE_PROPS, off, off + (1 << 16))
        size = struct.unpack("<Q", d[p + 40:p + 48])[0] if p > 0 and p + 48 <= len(d) else 0
        return Found(off, size if 64 < size <= n else n, "WMA (ASF) file", "asf")
    if h[:6] == b"#!AMR\n":
        frames, end, _ = _amr_chain(d, off + 6, _AMR_NB)
        return Found(off, end - off, "AMR-NB file", "amr") if frames >= 3 else None
    if h[:9] == b"#!AMR-WB\n":
        frames, end, _ = _amr_chain(d, off + 9, _AMR_WB)
        return Found(off, end - off, "AMR-WB file", "amr") if frames >= 3 else None
    if h[4:8] == b"ftyp":
        size = _mp4_length(d, off)
        return Found(off, size, "MP4 / M4A / 3GP file", "mov") if size else None
    if h[0] == 0x80 and h[1] == 0x00:
        data_at = struct.unpack(">H", h[2:4])[0] + 4
        if (16 <= data_at <= n and d[off + data_at - 6:off + data_at] == b"(c)CRI" and h[4] in (2, 3, 4)
                and 1 <= h[7] <= MAX_CHANNELS):
            rate, samples = struct.unpack(">II", h[8:16])
            block = h[5] or 18
            size = data_at + -(-samples // ((block - 2) * 2)) * block * h[7]
            if d[off + size:off + size + 2] == b"\x80\x01" and size + 4 <= n:     # end-of-data block, if not counted
                size += 4 + struct.unpack(">H", d[off + size + 2:off + size + 4])[0]
            return Found(off, min(size, n), "CRI ADX file", "adx") if 100 <= rate <= 192000 else None
        return None
    if h[:3] in (b"BIK", b"KB2") and h[3:4].isalpha():
        size, frames = struct.unpack("<II", h[4:12])
        tracks = struct.unpack("<I", h[40:44])[0]
        if 44 < size + 8 <= n and 0 < frames < 1 << 22 and 0 < tracks <= 256:
            return Found(off, size + 8, "Bink file", "bink")
        return None
    if h[:4] == b"1FCB" and 1 <= h[5] <= MAX_CHANNELS:
        return Found(off, n, "Bink Audio file", "binka")
    if h[:4] == b"SCHl":
        size = _ea_length(d, off)
        return Found(off, size, "EA audio file", "ea") if size > 16 else None
    if h[:20] == b"Creative Voice File\x1a":
        return Found(off, _voc_length(d, off) or n, "Creative VOC file", "voc") if _wav_needs_helper(d, off) else None
    if h[:4] == b"RIFF" and h[8:12] in (b"QLCM", b"XWMA", b"WAVE"):
        size = struct.unpack("<I", h[4:8])[0] + 8
        size = size if 12 < size <= n else n
        if h[8:12] == b"QLCM":
            return Found(off, size, "QCP file (QCELP / EVRC)", "qcp")
        if h[8:12] == b"XWMA":
            return Found(off, size, "XWMA file", "xwma")
        return Found(off, size, "WAV file", "wav") if _wav_needs_helper(d, off) else None
    if h[:4] == b"caff" and h[4:6] == b"\x00\x01":
        return Found(off, n, "CAF file", "caf") if _wav_needs_helper(d, off) else None
    if h[:4] == b"OggS" and h[28:36] == b"Speex   ":
        size = _ogg_length(d, off)
        return Found(off, size, "Ogg Speex file", "ogg") if size else None
    if h[:4] == b"RSTM" and h[4:6] in (b"\xfe\xff", b"\xff\xfe"):
        size = struct.unpack(">I" if h[4:6] == b"\xfe\xff" else "<I", h[8:12])[0]
        return Found(off, size if 64 < size <= n else n, "Nintendo BRSTM file", "brstm")
    if h[:4] == b"FSTM" and h[4:6] in (b"\xfe\xff", b"\xff\xfe"):
        size = struct.unpack(">I" if h[4:6] == b"\xfe\xff" else "<I", h[12:16])[0]
        return Found(off, size if 64 < size <= n else n, "Nintendo BFSTM file", "bfstm")
    if h[:4] == b"GENH" and 1 <= struct.unpack("<I", h[4:8])[0] <= MAX_CHANNELS:
        return Found(off, n, "GENH file", "genh")
    if h[0] == 0xFF and (h[1] & 0xF6) == 0xF0:
        frames, end = _adts_chain(d, off)
        return Found(off, end - off, "AAC (ADTS) stream", "aac") if frames >= 8 or (frames >= 2 and end == len(d)) else None
    return None


def headerless_amr(d: bytes) -> Found | None:
    """AMR-NB frames stored without the '#!AMR' line: recognised by a long, unbroken chain of frame headers."""
    if len(d) < 13 * 50 or d[:2] == b"#!":
        return None
    frames, end, live = _amr_chain(d, 0, _AMR_NB)
    if frames < 50 or live < 0.8 * frames:                 # blank areas also chain; real frames carry data
        return None
    rest = d[end:]
    if len(rest) > max(64, len(d) // 20) and rest.strip(b"\x00\xff"):
        return None                                        # the chain must cover the data, bar padding
    return Found(0, end, "AMR-NB frames without a file header", "amr", b"#!AMR\n")


_MAGICS = (_ASF, b"#!AMR", b"ftyp", b"(c)CRI", b"BIK", b"KB2", b"1FCB", b"SCHl", b"Creative Voice File\x1a",
           b"QLCM", b"XWMA", b"Speex   ", b"RSTM", b"FSTM", b"GENH")
_BACK = {b"ftyp": 4, b"QLCM": 8, b"XWMA": 8, b"Speex   ": 28}
_ADTS_SCAN = re.compile(rb"\xff[\xf0\xf1\xf8\xf9]")


def find_files(data: bytes, limit: int = MAX_FILES) -> list[Found]:
    """Supported audio files anywhere in the data, in order, without overlaps."""
    d = bytes(data)
    first = _at(d, 0) or headerless_amr(d)
    if first and first.length >= len(d) - 64:
        return [first]
    starts: set[int] = set()
    for magic in _MAGICS:
        pos = d.find(magic)
        while pos >= 0 and len(starts) < 8 * limit:
            if magic == b"(c)CRI":                          # the ADX header ends with this; find where it begins
                for back in range(pos - 2, max(pos - 4096, -1), -1):
                    if d[back] == 0x80 and d[back + 1] == 0 and struct.unpack(">H", d[back + 2:back + 4])[0] + 4 == pos + 6 - back:
                        starts.add(back)
                        break
            else:
                starts.add(pos - _BACK.get(magic, 0))
            pos = d.find(magic, pos + 1)
    for m in _ADTS_SCAN.finditer(d):
        if len(starts) >= 16 * limit:
            break
        starts.add(m.start())
    out: list[Found] = []
    end = 0
    for off in sorted(s for s in starts if s >= 0):
        if off < end:
            continue
        f = _at(d, off)
        if f and f.length > 0:
            out.append(f)
            end = off + f.length
            if len(out) >= limit:
                break
    return out


# -------------------------------------------------------------------- decoding
_info_cache: dict[tuple, Info | None] = {}


def _digest(data: bytes) -> tuple:
    return len(data), hashlib.blake2b(data, digest_size=12).digest()


def identify(data: bytes, f: Found) -> Info | None:
    """What FFmpeg says the file is, remembered so detection and decoding ask only once."""
    piece = bytes(data[f.offset:f.offset + f.length])
    key = _digest(piece) + (f.fmt,)
    if key not in _info_cache:
        if len(_info_cache) > 256:
            _info_cache.clear()
        try:
            _info_cache[key] = probe(piece, f.fmt, f.prefix)
        except (HelperMissing, HelperError):
            return None
    return _info_cache[key]


def _fit(x: np.ndarray, rate: int, to_rate: int, channels: int) -> np.ndarray:
    """Bring one decoded file to the sample rate and channel count of the first, so they can be joined."""
    if x.shape[1] != channels:
        mono = x.astype(np.int32).mean(axis=1).astype(np.int16)[:, None]
        x = np.repeat(mono, channels, axis=1) if channels > 1 else mono
    if rate != to_rate and len(x) > 1:
        n = max(1, int(round(len(x) * to_rate / rate)))
        at = np.linspace(0, len(x) - 1, n)
        x = np.stack([np.interp(at, np.arange(len(x)), x[:, c]) for c in range(x.shape[1])], axis=1).astype(np.int16)
    return x


def render_files(b: np.ndarray) -> Rendered:
    data = b.tobytes()
    empty = np.zeros(0, dtype=np.int16)
    if not available():
        return Rendered(empty, None, [], _MISSING)
    files = find_files(data)
    if not files:
        return Rendered(empty, None, [], "No AAC, WMA, AMR, Speex, QCELP, ADX, EA, Bink, VOC or similar file was "
                                         "found in these bytes.")
    pieces, problems = [], []
    rate = channels = 0
    cut_any = False
    desc = ""
    for f in files:
        _check_cancel()
        piece = data[f.offset:f.offset + f.length]
        try:
            x, r, cut = decode_file(piece, f.fmt, f.prefix)
        except HelperMissing as e:
            return Rendered(empty, None, [], str(e))
        except HelperError as e:
            problems.append(f"{f.kind} at 0x{f.offset:X}: {e}")
            continue
        if not len(x):
            problems.append(f"{f.kind} at 0x{f.offset:X}: no audio in it")
            continue
        if not rate:
            rate, channels = r, x.shape[1]
            info = identify(data, f)
            desc = f"{f.kind}: {info.text()}" if info else f"{f.kind}, {r:,} Hz"
        cut_any |= cut
        pieces.append((_fit(x, r, rate, channels), f, r))
    if not pieces:
        return Rendered(empty, None, [], "FFmpeg could not decode it. " + " | ".join(problems[:3]))
    if len(pieces) == 1:
        x, f, _ = pieces[0]
        note = f"Decoded by FFmpeg: {desc}."
        if f.offset or f.length < len(data) - 64:
            note += f" The file occupies 0x{f.offset:X} to 0x{f.offset + f.length:X} of the dump."
        sounds = []
    else:
        gap = np.zeros((int(rate * GAP_SECONDS), channels), dtype=np.int16)
        parts, sounds, pos = [], [], 0
        for i, (x, f, r) in enumerate(pieces, 1):
            if parts:
                parts.append(gap)
                pos += len(gap)
            sounds.append(Sound(pos, pos + len(x), f"File {i} ({f.kind})", f.offset))
            parts.append(x)
            pos += len(x)
        x = np.concatenate(parts)
        mixed = len({r for _, _, r in pieces}) > 1
        note = (f"{len(pieces)} audio files were found inside the dump and decoded by FFmpeg. The first is {desc}."
                + (" They do not all share one sample rate; the others were converted to the first one's."
                   if mixed else ""))
    if problems:
        note += f" {len(problems)} could not be decoded: " + " | ".join(problems[:2])
    if cut_any:
        note += " The audio was longer than the app keeps in memory and was cut short."
    return Rendered(x[:, 0] if x.shape[1] == 1 else x, rate, sounds, note)


def _g726_16(fmt: str):
    def run(b: np.ndarray) -> np.ndarray:
        if len(b) < 8 or not available():
            return np.zeros(0, dtype=np.int16)
        try:
            x, _, _ = decode_file(b.tobytes(), fmt)
        except (HelperError, HelperMissing):
            return np.zeros(0, dtype=np.int16)
        return x[:, 0]
    return run


# ------------------------------------------------------------------- detection
def extra_candidates(data: bytes, Candidate, Hypothesis, not_this: float) -> list:
    """A candidate for audio files FFmpeg decodes, offered only when one is found and FFmpeg opens it."""
    hyp = Hypothesis("ffaudio")

    def miss(why: str):
        return [Candidate(hyp, 1.0, not_this, 0.0, 0.0, why)]

    try:
        files = find_files(data, 64)
    except Exception:
        files = []
    if not files:
        return miss("No AAC, WMA, AMR, Speex, QCELP, ADX, EA, Bink or similar file signature in the data.")
    if not available():
        return miss(f"The data holds a {files[0].kind}, but the FFmpeg helper is missing from this installation.")
    opened = [(f, i) for f, i in ((f, identify(data, f)) for f in files[:3]) if i]
    if not opened:
        return miss(f"The data has the signature of a {files[0].kind}, but FFmpeg could not open it as one.")
    f, info = opened[0]
    covered = sum(x.length for x in files)
    used = len(data) - data.count(b"\xff") - data.count(b"\x00") if len(files) > 1 or f.offset else len(data)
    whole = len(files) == 1 and f.offset == 0 and f.length >= len(data) - 64
    if whole:
        ev = f"The data is a {f.kind}, and FFmpeg opens it as {info.text()}."
        return [Candidate(hyp, 0.001, 0.0, 0.0, 0.0, ev, worst_channel=0.001, evidence=ev,
                          cap="medium" if f.prefix else "high")]
    share = covered / max(used, 1)
    what = (f"{len(files)}{'+' if len(files) >= 64 else ''} audio files" if len(files) > 1 else f"A {f.kind}")
    ev = (f"{what} found inside the dump by signature, the first at 0x{f.offset:X} ({f.kind}: {info.text()}). "
          f"Together they take up about {min(100, round(100 * share))}% of the bytes that are not blank.")
    if share >= 0.5:
        return [Candidate(hyp, 0.001, 0.0, 0.0, 0.0, ev, worst_channel=0.001, evidence=ev, cap="medium")]
    return miss(ev + " That is under half of the dump, so this reading is not chosen automatically; pick it "
                     "from the list to hear those files.")


# ---------------------------------------------------------------- registration
def register(CODECS: dict, RENDERERS: dict, Codec, COST: dict) -> None:
    def put(codec):
        if codec.id not in CODECS:                          # never replace a decoder that is already there
            CODECS[codec.id] = codec

    RENDERERS.setdefault("ffaudio", render_files)
    COST.setdefault("ffaudio", 1.0)                         # seconds per megabyte, for the progress window
    put(Codec("ffaudio", "Audio file via FFmpeg (AAC, WMA, AMR, Speex, QCELP, ADX, EA, Bink, VOC ADPCM)", "chip", 1,
              "A compressed audio file, on its own or stored inside the dump. It is decoded by FFmpeg, which the "
              "app carries as a small helper program.",
              "Found by file signature, then decoded by FFmpeg's own decoder for that format.\n"
              "The file states its sample rate and channel count; nothing is estimated.",
              lambda b: render_files(b).samples, structured=True, layouts=("normal",), own_channels=True))
    put(Codec("g726_16", "G.726 ADPCM, 16 kbit/s", "adpcm", 0.25,
              "2 bits per sample. The 16 kbit/s member of the ITU telephone ADPCM family. Decoded by FFmpeg.",
              "ITU-T G.726 2-bit ADPCM at 8,000 Hz, codes packed most significant bits first, decoded by FFmpeg.",
              _g726_16("g726_16"), auto=False, layouts=("normal",)))
    put(Codec("g726le_16", "G.726 ADPCM, 16 kbit/s, low bits first", "adpcm", 0.25,
              "The same 2-bit G.726 with the codes packed from the low end of each byte. Decoded by FFmpeg.",
              "ITU-T G.726 2-bit ADPCM at 8,000 Hz, codes packed least significant bits first, decoded by FFmpeg.",
              _g726_16("g726le_16"), auto=False, layouts=("normal",)))
    COST.setdefault("g726_16", 0.2)
    COST.setdefault("g726le_16", 0.2)
