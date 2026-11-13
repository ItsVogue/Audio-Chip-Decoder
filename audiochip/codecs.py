"""Decoders: turn stored bytes into 16-bit audio samples.

Every decoder is registered in CODECS with a plain-language description and
the formula it applies, so the app can show exactly what was done.
Adding a format means adding one function and one Codec entry here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


@dataclass(frozen=True)
class Codec:
    id: str
    name: str                 # short label shown in lists
    family: str               # "linear8" | "companded8" | "linear16" | "adpcm"
    bytes_per_sample: float   # per channel
    plain: str                # one-sentence explanation for non-specialists
    formula: str              # exact maths, shown in the Details drawer
    decode: Callable[[np.ndarray], np.ndarray]   # uint8 array -> int16 array
    twin: str | None = None   # id of a decoder that sounds almost identical
    auto: bool = True         # included in automatic detection


# ---------------------------------------------------------------- 8-bit linear
def _u8(b: np.ndarray) -> np.ndarray:
    return ((b.astype(np.int16) - 128) << 8).astype(np.int16)


def _s8(b: np.ndarray) -> np.ndarray:
    return (b.view(np.int8).astype(np.int16) << 8).astype(np.int16)


def _signmag8(b: np.ndarray) -> np.ndarray:
    mag = (b & 0x7F).astype(np.int16) << 8
    return np.where(b & 0x80, -mag, mag).astype(np.int16)


# ------------------------------------------------------------- 8-bit companded
def _mulaw_core(b: np.ndarray) -> np.ndarray:
    """Sign in bit 7 (set = negative), exponent in bits 6-4, mantissa in 3-0."""
    a = b.astype(np.int32)
    mag = (((a & 15) * 8 + 132) << ((a >> 4) & 7)) - 132
    return np.where(a & 128, -mag, mag).astype(np.int16)


def _mulaw_inv(b: np.ndarray) -> np.ndarray:
    return _mulaw_core(b)


def _mulaw_std(b: np.ndarray) -> np.ndarray:
    return _mulaw_core(b ^ 0xFF)


def _alaw_core(b: np.ndarray) -> np.ndarray:
    a = b.astype(np.int32)
    seg = (a >> 4) & 7
    man = a & 15
    mag = np.where(seg == 0, (man << 4) + 8, ((man << 4) + 0x108) << np.maximum(seg - 1, 0))
    return np.where(a & 128, -mag, mag).astype(np.int16)


def _alaw_std(b: np.ndarray) -> np.ndarray:
    # G.711 A-law: stored byte is XORed with 0x55, and bit 7 set means positive.
    return (-_alaw_core(b ^ 0x55).astype(np.int32)).astype(np.int16)


def _alaw_plain(b: np.ndarray) -> np.ndarray:
    return _alaw_core(b)


# --------------------------------------------------------------- 16-bit linear
def _pairs(b: np.ndarray) -> np.ndarray:
    return b[: len(b) // 2 * 2]


def _s16le(b: np.ndarray) -> np.ndarray:
    return _pairs(b).view("<i2").astype(np.int16)


def _s16be(b: np.ndarray) -> np.ndarray:
    return _pairs(b).view(">i2").astype(np.int16)


def _u16le(b: np.ndarray) -> np.ndarray:
    return (_pairs(b).view("<u2").astype(np.int32) - 32768).astype(np.int16)


def _u16be(b: np.ndarray) -> np.ndarray:
    return (_pairs(b).view(">u2").astype(np.int32) - 32768).astype(np.int16)


# ------------------------------------------------------------ 4-bit OKI ADPCM
_OKI_STEPS = (16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
              107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449,
              494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552)
_OKI_ADJ = (-1, -1, -1, -1, 2, 4, 6, 8)


def oki_decode_nibbles(nibbles) -> tuple[np.ndarray, float, float]:
    """Decode 4-bit OKI/Dialogic ADPCM codes.

    Returns (samples, fraction of samples on the rail, fraction of time the
    step size sat at its maximum). The two fractions are used by detection:
    real ADPCM audio rarely pins either, random data pins both.
    """
    steps, adj = _OKI_STEPS, _OKI_ADJ
    signal = 0
    idx = 0
    out = np.empty(len(nibbles), dtype=np.int16)
    rail = 0
    top = 0
    i = 0
    for n in nibbles:
        step = steps[idx]
        diff = step >> 3
        if n & 4:
            diff += step
        if n & 2:
            diff += step >> 1
        if n & 1:
            diff += step >> 2
        if n & 8:
            signal -= diff
        else:
            signal += diff
        if signal > 2047:
            signal = 2047
            rail += 1
        elif signal < -2048:
            signal = -2048
            rail += 1
        idx += adj[n & 7]
        if idx < 0:
            idx = 0
        elif idx >= 48:
            idx = 48
            top += 1
        out[i] = signal
        i += 1
    total = max(len(nibbles), 1)
    return (out.astype(np.int32) * 16).astype(np.int16), rail / total, top / total


def oki_encode(samples: np.ndarray) -> list[int]:
    """Encode int16 samples to OKI ADPCM nibbles (used to build test dumps)."""
    steps, adj = _OKI_STEPS, _OKI_ADJ
    signal = 0
    idx = 0
    codes = []
    for s in (samples.astype(np.int32) >> 4).tolist():
        step = steps[idx]
        delta = s - signal
        code = 0
        if delta < 0:
            code = 8
            delta = -delta
        if delta >= step:
            code |= 4
            delta -= step
        if delta >= step >> 1:
            code |= 2
            delta -= step >> 1
        if delta >= step >> 2:
            code |= 1
        diff = step >> 3
        if code & 4:
            diff += step
        if code & 2:
            diff += step >> 1
        if code & 1:
            diff += step >> 2
        signal = signal - diff if code & 8 else signal + diff
        signal = max(-2048, min(2047, signal))
        idx = max(0, min(48, idx + adj[code & 7]))
        codes.append(code)
    return codes


def _nibbles(b: np.ndarray, high_first: bool) -> list[int]:
    hi = (b >> 4).astype(np.uint8)
    lo = (b & 15).astype(np.uint8)
    pair = np.stack([hi, lo] if high_first else [lo, hi], axis=1)
    return pair.reshape(-1).tolist()


def _oki_hi(b: np.ndarray) -> np.ndarray:
    return oki_decode_nibbles(_nibbles(b, True))[0]


def _oki_lo(b: np.ndarray) -> np.ndarray:
    return oki_decode_nibbles(_nibbles(b, False))[0]


def oki_stats(b: np.ndarray, high_first: bool) -> tuple[np.ndarray, float, float]:
    return oki_decode_nibbles(_nibbles(b, high_first))


_MULAW_FORMULA = (
    "magnitude = (((b & 15) * 8 + 132) << ((b >> 4) & 7)) - 132\n"
    "sample    = -magnitude if (b & 128) else magnitude"
)
_ALAW_FORMULA = (
    "seg = (b >> 4) & 7 ; man = b & 15\n"
    "magnitude = (man << 4) + 8            if seg == 0\n"
    "          = ((man << 4) + 264) << (seg - 1)   otherwise\n"
    "sample    = -magnitude if (b & 128) else magnitude"
)
_OKI_FORMULA = (
    "Each 4-bit code moves the output up or down by a step.\n"
    "diff = step/8 + (code&4 ? step : 0) + (code&2 ? step/2 : 0) + (code&1 ? step/4 : 0)\n"
    "signal += -diff if (code & 8) else diff ; clamp to 12 bits\n"
    "step index += [-1,-1,-1,-1,2,4,6,8][code & 7] ; 49-entry step table"
)

CODECS: dict[str, Codec] = {c.id: c for c in [
    Codec("u8", "8-bit unsigned", "linear8", 1,
          "One byte per sample, 0 to 255, with silence at 128.",
          "sample = (b - 128) * 256", _u8),
    Codec("s8", "8-bit signed", "linear8", 1,
          "One byte per sample, -128 to 127, two's complement.",
          "sample = int8(b) * 256", _s8),
    Codec("signmag8", "8-bit sign and magnitude", "linear8", 1,
          "One byte per sample: the top bit is the sign, the other seven are loudness.",
          "sample = (-(b & 127) if (b & 128) else (b & 127)) * 256", _signmag8),
    Codec("mulaw_inv", "8-bit mu-law, inverted storage", "companded8", 1,
          "One byte per sample, compressed so quiet sounds keep detail. "
          "Standard telephone mu-law with every stored byte inverted.",
          _MULAW_FORMULA, _mulaw_inv, twin="alaw_plain"),
    Codec("mulaw_std", "8-bit mu-law (G.711)", "companded8", 1,
          "One byte per sample, standard telephone mu-law.",
          "b = b XOR 255, then:\n" + _MULAW_FORMULA, _mulaw_std, twin="alaw_std"),
    Codec("alaw_plain", "8-bit A-law, unmasked storage", "companded8", 1,
          "One byte per sample, A-law compression without the usual bit mask.",
          _ALAW_FORMULA, _alaw_plain, twin="mulaw_inv"),
    Codec("alaw_std", "8-bit A-law (G.711)", "companded8", 1,
          "One byte per sample, standard telephone A-law.",
          "b = b XOR 85, then (polarity flipped):\n" + _ALAW_FORMULA, _alaw_std, twin="mulaw_std"),
    Codec("s16le", "16-bit signed, low byte first", "linear16", 2,
          "Two bytes per sample, low byte stored first.",
          "sample = int16(b0 + 256 * b1)", _s16le),
    Codec("s16be", "16-bit signed, high byte first", "linear16", 2,
          "Two bytes per sample, high byte stored first.",
          "sample = int16(256 * b0 + b1)", _s16be),
    Codec("u16le", "16-bit unsigned, low byte first", "linear16", 2,
          "Two bytes per sample, 0 to 65535, low byte stored first.",
          "sample = (b0 + 256 * b1) - 32768", _u16le),
    Codec("u16be", "16-bit unsigned, high byte first", "linear16", 2,
          "Two bytes per sample, 0 to 65535, high byte stored first.",
          "sample = (256 * b0 + b1) - 32768", _u16be),
    Codec("oki_hi", "4-bit OKI ADPCM, high nibble first", "adpcm", 0.5,
          "Half a byte per sample. Each code says how far to move from the previous sample. "
          "Common on OKI MSM voice chips and Dialogic .VOX files.",
          _OKI_FORMULA, _oki_hi, twin=None),
    Codec("oki_lo", "4-bit OKI ADPCM, low nibble first", "adpcm", 0.5,
          "Half a byte per sample, as above with the two halves of each byte swapped.",
          _OKI_FORMULA, _oki_lo, twin=None),
]}

# ------------------------------------------------------------------ byte layout
_BITREV = np.array([int(f"{i:08b}"[::-1], 2) for i in range(256)], dtype=np.uint8)

LAYOUTS: dict[str, tuple[str, Callable[[np.ndarray], np.ndarray]]] = {
    "normal": ("as stored", lambda b: b),
    "bitrev": ("bit order reversed in each byte", lambda b: _BITREV[b]),
    "nibswap": ("the two halves of each byte swapped", lambda b: ((b << 4) | (b >> 4)).astype(np.uint8)),
}


@dataclass(frozen=True)
class Hypothesis:
    """One complete reading of the bytes: decoder, byte layout, channel count."""
    codec: str
    layout: str = "normal"
    channels: int = 1

    @property
    def key(self) -> str:
        return f"{self.codec}/{self.layout}/{self.channels}"

    @classmethod
    def from_key(cls, key: str) -> "Hypothesis":
        codec, layout, ch = key.split("/")
        return cls(codec, layout, int(ch))

    def label(self) -> str:
        c = CODECS[self.codec]
        parts = [c.name]
        if self.layout != "normal":
            parts.append(LAYOUTS[self.layout][0])
        parts.append("mono" if self.channels == 1 else f"{self.channels} channels")
        return ", ".join(parts)

    def is_twin_of(self, other: "Hypothesis") -> bool:
        return (self.layout == other.layout and self.channels == other.channels
                and CODECS[self.codec].twin == other.codec)


def decode(data: bytes | np.ndarray, hyp: Hypothesis) -> np.ndarray:
    """Decode bytes under a hypothesis. Returns int16 array shaped (frames, channels)."""
    b = np.frombuffer(data, dtype=np.uint8) if isinstance(data, (bytes, bytearray, memoryview)) else data
    b = LAYOUTS[hyp.layout][1](b)
    x = CODECS[hyp.codec].decode(b)
    ch = hyp.channels
    n = len(x) // ch * ch
    return x[:n].reshape(-1, ch)


def frames_for_bytes(nbytes: int, hyp: Hypothesis) -> int:
    return int(nbytes / CODECS[hyp.codec].bytes_per_sample) // hyp.channels
