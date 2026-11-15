# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""Split decoded audio into the separate sounds a chip holds.

Voice chips usually store many short clips. Two ways to find them:

* An index in the chip (NEC uPD7759, OKI MSM6295, LPC speech phrases). The
  format says exactly where each sound starts and ends.
* Silence. For plain sample streams, a stretch with no activity (blank
  filler or steady silence) between two stretches of audio marks a boundary.
"""
from __future__ import annotations

import numpy as np

from .chipformats import Sound
from .codecs import is_structured, render

MIN_GAP_S = 0.15         # silence at least this long separates two sounds
MIN_SOUND_S = 0.04       # anything shorter is a click, not a sound
_WINDOW_S = 0.008
MAX_SOUNDS = 2000


def find_by_silence(x: np.ndarray, rate: int) -> list[Sound]:
    """Sounds in a mono int16 signal, separated by silent gaps."""
    win = max(int(rate * _WINDOW_S), 8)
    n = len(x) // win
    if n < 4:
        return [Sound(0, len(x), "Sound 1")] if len(x) else []
    blocks = x[: n * win].reshape(n, win).astype(np.int32)
    activity = blocks.max(axis=1) - blocks.min(axis=1)       # steady values, including filler, give 0
    loud = float(np.percentile(activity, 99))
    if loud <= 0:
        return []
    active = activity > max(loud * 0.04, 1.0)
    gap = max(int(MIN_GAP_S * rate / win), 1)
    sounds: list[Sound] = []
    start = None
    quiet = 0
    for i, a in enumerate(active.tolist() + [False] * (gap + 1)):
        if a:
            if start is None:
                start = i
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet > gap:
                end = i - quiet + 1
                if (end - start) * win >= MIN_SOUND_S * rate:
                    sounds.append(Sound(start * win, min(end * win, len(x)), ""))
                start = None
                quiet = 0
    if len(sounds) > MAX_SOUNDS:
        return []
    for i, s in enumerate(sounds, 1):
        s.label = f"Sound {i}"
    return sounds


def sounds_of(data: bytes, hyp, rate: int, audio: np.ndarray | None = None) -> tuple[list[Sound], str]:
    """(sounds, how they were found) for one stretch of bytes under a reading."""
    if hyp is None:
        return [], ""
    if is_structured(hyp):
        r = render(data, hyp)
        return list(r.sounds), "the chip's own index"
    if audio is None:
        from .codecs import decode
        audio = decode(data, hyp)
    mono = audio if audio.ndim == 1 else audio.mean(axis=1).astype(np.int16)
    return find_by_silence(mono, max(rate, 1000)), "silent gaps of 0.15 seconds or more"
