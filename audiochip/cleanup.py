"""Optional clean-up applied on export. Off by default: the plain export is the chip's exact output.

* Offset and rumble: old 8-bit chips often sit off-centre; a gentle high-pass removes that.
* Hum: narrow notches at the mains frequency and its harmonics.
* Clicks: isolated samples that jump far from both neighbours are replaced by the value between them.
* Level: scale so the loudest peak sits just below full volume.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_BLOCK = 1 << 16


@dataclass
class Cleanup:
    remove_offset: bool = False
    hum_hz: int = 0              # 0 = off, otherwise 50 or 60
    declick: bool = False
    level: bool = False

    @property
    def any(self) -> bool:
        return self.remove_offset or bool(self.hum_hz) or self.declick or self.level

    def describe(self) -> list[str]:
        out = []
        if self.remove_offset:
            out.append("offset and rumble below 20 Hz removed")
        if self.hum_hz:
            out.append(f"{self.hum_hz} Hz hum and its harmonics notched out")
        if self.declick:
            out.append("isolated clicks repaired")
        if self.level:
            out.append("level raised so the loudest peak is 1 dB below full volume")
        return out


def _gain(nfft: int, rate: int, opts: Cleanup) -> np.ndarray:
    f = np.fft.rfftfreq(nfft, 1.0 / rate)
    g = np.ones(len(f))
    if opts.remove_offset:
        g *= f ** 2 / (f ** 2 + 20.0 ** 2)                  # second-order high-pass at 20 Hz
    if opts.hum_hz:
        for k in range(1, 9):
            centre = opts.hum_hz * k
            if centre >= rate / 2:
                break
            g *= 1.0 - np.exp(-0.5 * ((f - centre) / 1.5) ** 2)
    return g


def _filter(x: np.ndarray, rate: int, opts: Cleanup) -> np.ndarray:
    """Apply the frequency-domain gain in overlapping blocks, so any length can be processed."""
    n = len(x)
    if n <= _BLOCK:
        nfft = max(n, 16)
        return np.fft.irfft(np.fft.rfft(x, nfft) * _gain(nfft, rate, opts), nfft)[:n]
    hop = _BLOCK // 2
    win = np.hanning(_BLOCK + 1)[:-1]                       # overlapping halves add up to 1
    g = _gain(_BLOCK, rate, opts)
    pad = np.concatenate([np.zeros(hop), x, np.zeros(_BLOCK)])
    out = np.zeros(len(pad))
    for start in range(0, len(pad) - _BLOCK + 1, hop):
        out[start:start + _BLOCK] += np.fft.irfft(np.fft.rfft(pad[start:start + _BLOCK] * win) * g, _BLOCK)
    return out[hop:hop + n]


def _declick(x: np.ndarray) -> tuple[np.ndarray, int]:
    if len(x) < 5:
        return x, 0
    d = x[1:-1] - 0.5 * (x[:-2] + x[2:])                     # distance from the line through the neighbours
    scale = float(np.median(np.abs(d))) * 1.4826 + 1e-9
    step = np.abs(x[2:] - x[:-2])
    bad = (np.abs(d) > 12 * scale) & (np.abs(d) > 4 * step + 600)
    idx = np.flatnonzero(bad) + 1
    x = x.copy()
    x[idx] = 0.5 * (x[idx - 1] + x[idx + 1])
    return x, int(len(idx))


def clean(x: np.ndarray, rate: int, opts: Cleanup) -> tuple[np.ndarray, dict]:
    """Clean int16 audio shaped (frames, channels). Returns the result and what was done."""
    if not opts.any or not len(x):
        return x, {}
    y = x.astype(np.float64)
    if y.ndim == 1:
        y = y.reshape(-1, 1)
    info: dict = {}
    clicks = 0
    for c in range(y.shape[1]):
        col = y[:, c]
        if opts.declick:
            col, k = _declick(col)
            clicks += k
        if opts.remove_offset or opts.hum_hz:
            col = _filter(col, rate, opts)
        y[:, c] = col
    if opts.declick:
        info["clicks_repaired"] = clicks
    if opts.level:
        peak = float(np.abs(y).max())
        if peak > 0:
            gain = 32767 * 10 ** (-1 / 20) / peak
            y *= gain
            info["gain_db"] = round(float(20 * np.log10(gain)), 2)
    return np.clip(np.round(y), -32768, 32767).astype(np.int16), info
