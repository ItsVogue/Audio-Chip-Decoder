"""Spot compressed audio stored in fixed-size frames, even when the codec itself is unknown.

Many speech chips and toy sound modules store sound as frames of equal length, each starting
with the same marker byte or two. The app may not have a decoder for the codec, but the
framing is still evidence worth reporting: it says "this is framed, compressed data" and
gives the frame size, which narrows down what the codec can be.
"""
from __future__ import annotations

import numpy as np

MIN_PERIOD, MAX_PERIOD = 8, 512
SCAN_BYTES = 1 << 16


def frame_period(data: bytes) -> tuple[int, list[tuple[int, int]], float] | None:
    """(frame size, [(position in frame, constant byte)], share of frames that agree), or None.

    Looks for a spacing at which bytes equal the byte one spacing later far more often than
    chance (1 in 256), then lists which positions in the frame always hold the same value.
    """
    b = np.frombuffer(data, dtype=np.uint8)[:SCAN_BYTES]
    n = len(b)
    if n < MAX_PERIOD * 8:
        return None
    best, best_rate = 0, 0.0
    for lag in range(MIN_PERIOD, MAX_PERIOD + 1):
        rate = float(np.count_nonzero(b[:-lag] == b[lag:])) / (n - lag)
        if rate > best_rate * 1.02:                          # prefer the shortest spacing among multiples
            best, best_rate = lag, rate
    if best_rate < 0.012:                                    # chance alone gives about 0.004
        return None
    frames = b[: n // best * best].reshape(-1, best)
    fixed = []
    agree = []
    for pos in range(best):
        vals, counts = np.unique(frames[:, pos], return_counts=True)
        share = counts.max() / len(frames)
        if share >= 0.9:
            fixed.append((pos, int(vals[counts.argmax()])))
            agree.append(share)
    if not fixed or len(fixed) > best // 2:                  # mostly constant: filler, not frames
        return None
    return best, fixed, float(np.mean(agree))


def describe(data: bytes) -> str:
    """One paragraph for the user, or "" when no framing was found."""
    found = frame_period(data)
    if not found:
        return ""
    size, fixed, share = found
    marks = ", ".join(f"byte {p} is 0x{v:02X}" for p, v in fixed[:4])
    rates = ", ".join(f"{size * 8 * fps / 1000:.3g} kbit/s at {1000 // fps} ms per frame" for fps in (50, 33))
    return (f" The bytes are laid out in frames of {size} bytes: in {100 * share:.0f}% of them {marks}. "
            f"That is the pattern of compressed speech stored frame by frame ({rates}), in a codec this "
            "version has no decoder for. Nothing has been guessed: no decoder was applied.")
