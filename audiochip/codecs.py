"""Decoders: turn stored bytes into 16-bit audio samples.

Every decoder is registered in CODECS with a plain-language description and
the formula it applies, so the app can show exactly what was done.
Adding a format means adding one function and one Codec entry here.
"""
from __future__ import annotations

import hashlib
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
    structured: bool = False  # the dump has its own layout (index, commands); see chipformats
    layouts: tuple = ("normal", "bitrev", "nibswap")   # byte layouts worth offering


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


# ------------------------------------------------------- other 4-bit ADPCM kinds
_IMA_STEPS = (7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66,
              73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408,
              449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
              2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630,
              9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
              32767)
_YAMAHA_ADJ = (230, 230, 230, 230, 307, 409, 512, 614)


def ima_decode_nibbles(nibbles) -> tuple[np.ndarray, float, float]:
    """IMA/DVI ADPCM: same idea as OKI with a 16-bit range and an 89-entry step table."""
    steps, adj = _IMA_STEPS, _OKI_ADJ
    signal = idx = rail = top = i = 0
    out = np.empty(len(nibbles), dtype=np.int16)
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
        if signal > 32767:
            signal = 32767
            rail += 1
        elif signal < -32768:
            signal = -32768
            rail += 1
        idx += adj[n & 7]
        if idx < 0:
            idx = 0
        elif idx >= 88:
            idx = 88
            top += 1
        out[i] = signal
        i += 1
    total = max(len(nibbles), 1)
    return out, rail / total, top / total


def ima_encode(samples: np.ndarray) -> list[int]:
    steps, adj = _IMA_STEPS, _OKI_ADJ
    signal = idx = 0
    codes = []
    for s in samples.astype(np.int32).tolist():
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
        diff = (step >> 3) + (step if code & 4 else 0) + (step >> 1 if code & 2 else 0) + (step >> 2 if code & 1 else 0)
        signal = max(-32768, min(32767, signal - diff if code & 8 else signal + diff))
        idx = max(0, min(88, idx + adj[code & 7]))
        codes.append(code)
    return codes


def yamaha_decode_nibbles(nibbles) -> tuple[np.ndarray, float, float]:
    """Yamaha ADPCM (Y8950, YM2608/2610 ADPCM-B, YMZ280B, AICA)."""
    adj = _YAMAHA_ADJ
    signal = rail = top = i = 0
    step = 127
    out = np.empty(len(nibbles), dtype=np.int16)
    for n in nibbles:
        diff = ((2 * (n & 7) + 1) * step) >> 3
        if n & 8:
            signal -= diff
        else:
            signal += diff
        if signal > 32767:
            signal = 32767
            rail += 1
        elif signal < -32768:
            signal = -32768
            rail += 1
        step = (step * adj[n & 7]) >> 8
        if step < 127:
            step = 127
        elif step >= 24576:
            step = 24576
            top += 1
        out[i] = signal
        i += 1
    total = max(len(nibbles), 1)
    return out, rail / total, top / total


def yamaha_encode(samples: np.ndarray) -> list[int]:
    adj = _YAMAHA_ADJ
    signal = 0
    step = 127
    codes = []
    for s in samples.astype(np.int32).tolist():
        delta = s - signal
        code = 8 if delta < 0 else 0
        code |= min(7, (abs(delta) * 4) // step)
        diff = ((2 * (code & 7) + 1) * step) >> 3
        signal = max(-32768, min(32767, signal - diff if code & 8 else signal + diff))
        step = max(127, min(24576, (step * adj[code & 7]) >> 8))
        codes.append(code)
    return codes


ADPCM_KINDS = {"oki": oki_decode_nibbles, "ima": ima_decode_nibbles, "yamaha": yamaha_decode_nibbles}


def adpcm_stats(b: np.ndarray, kind: str, high_first: bool) -> tuple[np.ndarray, float, float]:
    return ADPCM_KINDS[kind](_nibbles(b, high_first))


def _adpcm(kind: str, high_first: bool):
    return lambda b: ADPCM_KINDS[kind](_nibbles(b, high_first))[0]


# ------------------------------------------------------------ 1-bit CVSD delta
_CVSD_MIN, _CVSD_MAX = 0.0416, 1.0954
_CVSD_CHARGE = float(np.exp(-1.0 / (0.004 * 16000.0)))     # syllabic filter, 4 ms
_CVSD_LEAK = float(np.exp(-1.0 / (0.001 * 16000.0)))       # integrator, 1 ms


def cvsd_decode_bits(bits) -> np.ndarray:
    """Continuously variable slope delta modulation (HC55516, MC3417, MC3418).

    Each bit moves the output up or down by the current step. Three equal bits in a row
    make the step grow; anything else lets it shrink.
    """
    lo, hi, charge, leak = _CVSD_MIN, _CVSD_MAX, _CVSD_CHARGE, _CVSD_LEAK
    step = lo
    level = 0.0
    sr = 0
    out = np.empty(len(bits), dtype=np.float64)
    i = 0
    for bit in bits:
        sr = ((sr << 1) | bit) & 7
        level = (level + step if bit else level - step) * leak
        if sr == 0 or sr == 7:
            step = hi - (hi - step) * charge
        else:
            step *= charge
            if step < lo:
                step = lo
        out[i] = level
        i += 1
    return np.clip(out * 10000.0, -32768, 32767).astype(np.int16)


def cvsd_encode(samples: np.ndarray) -> list[int]:
    lo, hi, charge, leak = _CVSD_MIN, _CVSD_MAX, _CVSD_CHARGE, _CVSD_LEAK
    step = lo
    level = 0.0
    sr = 0
    bits = []
    for s in (samples.astype(np.float64) / 10000.0).tolist():
        bit = 1 if s > level else 0
        sr = ((sr << 1) | bit) & 7
        level = (level + step if bit else level - step) * leak
        if sr == 0 or sr == 7:
            step = hi - (hi - step) * charge
        else:
            step = max(lo, step * charge)
        bits.append(bit)
    return bits


def _cvsd(b: np.ndarray) -> np.ndarray:
    return cvsd_decode_bits(np.unpackbits(b).tolist())      # bit 7 of each byte first


# ------------------------------------------------------- chips with a structure
def _structured(render):
    return lambda b: render(b).samples


def _lazy(name: str, *args):
    def render(b: np.ndarray):
        from . import chipformats
        return getattr(chipformats, name)(b, *args)
    return render


RENDERERS = {
    "upd7759": _lazy("upd7759_render"),
    "oki_table": _lazy("oki_table_render"),
    "lpc_5220": _lazy("lpc_render", "tms5220"),
    "lpc_5110": _lazy("lpc_render", "tms5110"),
    "lpc_0281": _lazy("lpc_render", "tmc0281"),
}

_IMA_FORMULA = (
    "diff = step/8 + (code&4 ? step : 0) + (code&2 ? step/2 : 0) + (code&1 ? step/4 : 0)\n"
    "signal += -diff if (code & 8) else diff ; clamp to 16 bits\n"
    "step index += [-1,-1,-1,-1,2,4,6,8][code & 7] ; 89-entry step table (7 ... 32767)"
)
_YAMAHA_FORMULA = (
    "diff = ((2 * (code & 7) + 1) * step) / 8\n"
    "signal += -diff if (code & 8) else diff ; clamp to 16 bits\n"
    "step = step * [230,230,230,230,307,409,512,614][code & 7] / 256 ; kept within 127 ... 24576"
)
_CVSD_FORMULA = (
    "One bit per sample, bit 7 of each byte first.\n"
    "level = (level + step if bit else level - step) * leak\n"
    "step grows toward its maximum after three equal bits in a row, otherwise decays"
)
_LPC_FORMULA = (
    "Frames of 25 ms: energy (4 bits), repeat (1), pitch (5 or 6), then filter\n"
    "coefficients K1-K10 (5,5,4,4,4,4,4,3,3,3 bits). Energy 0 is silence, 15 ends the phrase.\n"
    "A buzz at the pitch (or hiss when pitch is 0) is shaped by a 10-stage lattice filter."
)
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
    Codec("ima_hi", "4-bit IMA ADPCM, high nibble first", "adpcm", 0.5,
          "Half a byte per sample. The IMA/DVI kind of ADPCM, used by many later sound chips, "
          "toys and voice recorders.", _IMA_FORMULA, _adpcm("ima", True)),
    Codec("ima_lo", "4-bit IMA ADPCM, low nibble first", "adpcm", 0.5,
          "Half a byte per sample, IMA ADPCM with the two halves of each byte swapped.",
          _IMA_FORMULA, _adpcm("ima", False)),
    Codec("yamaha_hi", "4-bit Yamaha ADPCM, high nibble first", "adpcm", 0.5,
          "Half a byte per sample. The ADPCM used by Yamaha sound chips (Y8950, YM2608, YM2610, YMZ280B).",
          _YAMAHA_FORMULA, _adpcm("yamaha", True)),
    Codec("yamaha_lo", "4-bit Yamaha ADPCM, low nibble first", "adpcm", 0.5,
          "Half a byte per sample, Yamaha ADPCM with the two halves of each byte swapped.",
          _YAMAHA_FORMULA, _adpcm("yamaha", False)),
    Codec("cvsd", "1-bit CVSD delta modulation", "delta", 0.125,
          "One bit per sample: each bit only says up or down. Used for speech on 1980s pinball and "
          "arcade boards (HC55516, MC3417).", _CVSD_FORMULA, _cvsd, layouts=("normal", "bitrev")),
    Codec("upd7759", "NEC uPD7759 voice ROM", "chip", 0.5,
          "A voice ROM for the NEC uPD7759: a header lists the sounds, each stored as blocks of "
          "4-bit ADPCM that carry their own playback speed.",
          "Header: last sound number, signature 5A A5 69 55, then one 2-byte address per sound.\n"
          "Blocks: 00-3F silence, 40-7F 256 codes, 80-BF n codes, C0-FF repeat. "
          "Speed = 640 kHz / (4 * (n + 1)).\n"
          "Each 4-bit code adds a value from a 16 x 16 table to the output.",
          _structured(RENDERERS["upd7759"]), structured=True, layouts=("normal",)),
    Codec("oki_table", "OKI MSM6295 voice ROM (phrase table)", "chip", 0.5,
          "A voice ROM for the OKI MSM6295: the first 1,024 bytes list up to 127 phrases by start "
          "and end address, each stored as 4-bit OKI ADPCM.",
          "Table entry n at byte 8n: 3-byte start address, 3-byte end address, 2 zero bytes.\n" + _OKI_FORMULA,
          _structured(RENDERERS["oki_table"]), structured=True, layouts=("normal",)),
    Codec("lpc_5220", "TI LPC speech (TMS5220 / TMS5200)", "chip", 0.02,
          "Synthesised speech: the chip stores pitch, loudness and mouth-shape settings 40 times a "
          "second and a speech chip rebuilds the voice. Used with the TMS5220 on arcade boards and computers.",
          _LPC_FORMULA, _structured(RENDERERS["lpc_5220"]), structured=True, layouts=("normal", "bitrev")),
    Codec("lpc_5110", "TI LPC speech (TMS5110)", "chip", 0.02,
          "Synthesised speech for the TMS5110 family, as used in later talking toys and some arcade boards.",
          _LPC_FORMULA, _structured(RENDERERS["lpc_5110"]), structured=True, layouts=("normal", "bitrev"),
          twin="lpc_0281"),
    Codec("lpc_0281", "TI LPC speech (TMC0281 / TMS5100)", "chip", 0.02,
          "Synthesised speech for the first TI speech chip, as used in early talking toys.",
          _LPC_FORMULA, _structured(RENDERERS["lpc_0281"]), structured=True, layouts=("normal", "bitrev"),
          twin="lpc_5110"),
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


_SLOW = ("adpcm", "delta", "chip")        # decoded sample by sample in Python: worth remembering
_cache: dict[tuple, object] = {}


def _remember(key: tuple, make):
    if key in _cache:
        _cache[key] = _cache.pop(key)          # move to the end: most recently used
        return _cache[key]
    value = make()
    _cache[key] = value
    while len(_cache) > 12:
        _cache.pop(next(iter(_cache)))
    return value


def _as_array(data) -> np.ndarray:
    return np.frombuffer(data, dtype=np.uint8) if isinstance(data, (bytes, bytearray, memoryview)) else data


def _digest(b: np.ndarray) -> bytes:
    return hashlib.blake2b(np.ascontiguousarray(b).data, digest_size=12).digest()


def decode(data: bytes | np.ndarray, hyp: Hypothesis) -> np.ndarray:
    """Decode bytes under a hypothesis. Returns int16 array shaped (frames, channels)."""
    b = _as_array(data)
    codec = CODECS[hyp.codec]
    if codec.structured:
        return render(b, hyp).samples.reshape(-1, 1)

    def run():
        x = codec.decode(LAYOUTS[hyp.layout][1](b))
        ch = hyp.channels
        n = len(x) // ch * ch
        return x[:n].reshape(-1, ch)
    if codec.family in _SLOW and len(b) > 4096:
        return _remember((hyp.key, len(b), _digest(b)), run)
    return run()


def render(data: bytes | np.ndarray, hyp: Hypothesis):
    """Decode a structured chip format: audio, playback speed if stated, and the separate sounds."""
    b = _as_array(data)
    make = RENDERERS[hyp.codec]
    return _remember(("render", hyp.key, len(b), _digest(b)), lambda: make(LAYOUTS[hyp.layout][1](b)))


def is_structured(hyp: Hypothesis | None) -> bool:
    return bool(hyp) and CODECS[hyp.codec].structured


def frames_for_bytes(nbytes: int, hyp: Hypothesis) -> int:
    """Samples produced by this many bytes. Only meaningful for plain sample streams."""
    return int(nbytes / CODECS[hyp.codec].bytes_per_sample) // hyp.channels
