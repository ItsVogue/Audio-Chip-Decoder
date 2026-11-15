# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""Look at a dump before decoding: padding, markers, repeats, embedded files."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

PAD_MIN = 256          # a constant run at the end counts as padding from this length
MARKER_MAX = 64        # shorter constant runs at the start are reported as a marker


@dataclass
class Embedded:
    kind: str        # "WAV", "MP3", "VOC", "Ogg", "FLAC"
    offset: int
    length: int      # 0 when the length is not known
    detail: str = ""


@dataclass
class Structure:
    size: int
    lead_len: int = 0          # constant bytes at the very start
    lead_value: int = 0
    trail_len: int = 0         # constant bytes at the very end
    trail_value: int = 0
    blank: bool = False        # every byte identical: erased chip or bad contact
    repeat_of: int = 0         # dump is this many bytes repeated (wrong chip size chosen)
    entropy: float = 0.0       # bits per byte, 0-8
    embedded: list[Embedded] = field(default_factory=list)
    strings: list[tuple[int, str]] = field(default_factory=list)
    string_count: int = 0

    @property
    def content_start(self) -> int:
        return self.lead_len if self.lead_len >= PAD_MIN else 0

    @property
    def content_end(self) -> int:
        return self.size - (self.trail_len if self.trail_len >= PAD_MIN else 0)

    @property
    def has_start_marker(self) -> bool:
        return 4 <= self.lead_len <= MARKER_MAX and self.lead_value == 0

    @property
    def has_end_padding(self) -> bool:
        return self.trail_len >= PAD_MIN

    def describe(self) -> list[str]:
        out = []
        if self.blank:
            out.append(f"Every byte is 0x{self.lead_value:02X}. The chip is blank, or the reader had no contact.")
            return out
        if self.repeat_of:
            out.append(f"The dump is the same {self.repeat_of:,} bytes repeated {self.size // self.repeat_of} times. "
                       "The chip is probably smaller than the size chosen when it was read.")
        if self.has_start_marker:
            out.append(f"Starts with {self.lead_len} zero bytes, which looks like a start-of-track marker.")
        elif self.lead_len >= PAD_MIN:
            out.append(f"Starts with {self.lead_len:,} bytes of 0x{self.lead_value:02X} padding.")
        if self.has_end_padding:
            out.append(f"Ends with {self.trail_len:,} bytes of 0x{self.trail_value:02X} padding "
                       f"({100 * self.trail_len / self.size:.0f}% of the dump).")
        for e in self.embedded:
            where = f"at offset 0x{e.offset:X}"
            out.append(f"Contains a {e.kind} file {where}" + (f", {e.length:,} bytes" if e.length else "")
                       + (f" ({e.detail})" if e.detail else "") + ".")
        return out


_SCAN = 1 << 20          # work through large images a megabyte at a time


def _run_start(b: np.ndarray) -> int:
    first = b[0]
    for off in range(0, len(b), _SCAN):
        diff = np.flatnonzero(b[off:off + _SCAN] != first)
        if len(diff):
            return off + int(diff[0])
    return len(b)


def _run_end(b: np.ndarray) -> int:
    last = b[-1]
    for end in range(len(b), 0, -_SCAN):
        start = max(end - _SCAN, 0)
        diff = np.flatnonzero(b[start:end] != last)
        if len(diff):
            return len(b) - 1 - (start + int(diff[-1]))
    return len(b)


def _repeat_unit(data: bytes) -> int:
    n = len(data)
    unit = 0
    while n >= 512 and n % 2 == 0 and data[: n // 2] == data[n // 2: n]:
        n //= 2
        unit = n
    return unit


def _entropy(b: np.ndarray) -> float:
    counts = np.bincount(b, minlength=256).astype(np.float64)
    p = counts[counts > 0] / len(b)
    return float(-(p * np.log2(p)).sum())


# MPEG audio frame header tables (layer III only; enough to find MP3 streams)
_BITRATES_V1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_BITRATES_V2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _mp3_frame_len(h: bytes) -> tuple[int, int]:
    """Return (frame length, sample rate) for a 4-byte header, or (0, 0)."""
    if h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
        return 0, 0
    version = (h[1] >> 3) & 3
    layer = (h[1] >> 1) & 3
    if version == 1 or layer != 1:
        return 0, 0
    br_i = h[2] >> 4
    sr_i = (h[2] >> 2) & 3
    if br_i in (0, 15) or sr_i == 3:
        return 0, 0
    pad = (h[2] >> 1) & 1
    rate = _RATES[version][sr_i]
    kbps = (_BITRATES_V1 if version == 3 else _BITRATES_V2)[br_i]
    length = (144 if version == 3 else 72) * kbps * 1000 // rate + pad
    return length, rate


def _find_mp3(data: bytes, min_frames: int = 8) -> list[Embedded]:
    found = []
    pos = 0
    n = len(data)
    tries = 0
    while tries < 2_000_000:                 # bound the work on very large images
        tries += 1
        pos = data.find(b"\xFF", pos)
        if pos < 0 or pos + 4 > n:
            break
        length, rate = _mp3_frame_len(data[pos:pos + 4])
        if not length:
            pos += 1
            continue
        end = pos
        frames = 0
        while end + 4 <= n:
            flen, frate = _mp3_frame_len(data[end:end + 4])
            if not flen or frate != rate:
                break
            end += flen
            frames += 1
        if frames >= min_frames:
            end = min(end, n)
            found.append(Embedded("MP3", pos, end - pos, f"{frames} frames, {rate} Hz"))
            pos = end
        else:
            pos += 1
    return found


def _find_wav(data: bytes) -> list[Embedded]:
    found = []
    pos = 0
    while True:
        pos = data.find(b"RIFF", pos)
        if pos < 0:
            break
        if data[pos + 8: pos + 12] == b"WAVE" and pos + 12 <= len(data):
            size = struct.unpack_from("<I", data, pos + 4)[0] + 8
            size = min(size, len(data) - pos)
            found.append(Embedded("WAV", pos, size))
            pos += max(size, 12)
        else:
            pos += 4
    return found


def _find_simple(data: bytes, magic: bytes, kind: str) -> list[Embedded]:
    found = []
    pos = data.find(magic)
    if pos >= 0:
        found.append(Embedded(kind, pos, 0))
    return found


def _strings(data: bytes, min_len: int = 8, keep: int = 12) -> tuple[list[tuple[int, str]], int]:
    b = np.frombuffer(data, dtype=np.uint8)[: 8 << 20]          # the first 8 MB is plenty
    printable = ((b >= 0x20) & (b < 0x7F)).astype(np.int8)
    edges = np.diff(np.concatenate(([0], printable, [0])))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    long = (ends - starts) >= min_len
    starts, ends = starts[long], ends[long]
    sample = [(int(s), data[s:e][:60].decode("ascii")) for s, e in zip(starts[:keep], ends[:keep])]
    return sample, int(len(starts))


def analyse_structure(data: bytes) -> Structure:
    b = np.frombuffer(data, dtype=np.uint8)
    st = Structure(size=len(b))
    if len(b) == 0:
        return st
    st.lead_len, st.lead_value = _run_start(b), int(b[0])
    st.trail_len, st.trail_value = _run_end(b), int(b[-1])
    st.entropy = _entropy(b)
    if st.lead_len == len(b):
        st.blank = True
        return st
    st.repeat_of = _repeat_unit(data)
    # Search only between the padding: long runs of FF are slow to scan and hold nothing.
    lo, hi = st.content_start, st.content_end
    body = data[lo:hi]
    st.embedded = (_find_wav(body) + _find_mp3(body)
                   + _find_simple(body, b"Creative Voice File\x1a", "VOC")
                   + _find_simple(body, b"OggS\x00", "Ogg")
                   + _find_simple(body, b"fLaC\x00", "FLAC"))
    for e in st.embedded:
        e.offset += lo
    st.embedded.sort(key=lambda e: e.offset)
    st.strings, st.string_count = _strings(data)
    return st
