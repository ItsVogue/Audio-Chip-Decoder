"""Shortlist the playback speed (sample rate).

The speed is not stored in a dump, so it cannot be read back. What can be
done is to rule speeds in or out from what the audio contains:

* Voice pitch. If the recording has a speaking voice, its pitch must land in
  the human range once the right speed is applied.
* Voice bandwidth. Speech carries nearly all of its energy below about
  3.4 kHz, so the point where the energy stops tells how wide the band is.

Both are estimates. The result is a ranked shortlist for a listening check,
never a final answer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .detect import spectrum

STANDARD_RATES = (4000, 5512, 6000, 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)
COMMON_RATES = {8000, 11025, 16000, 22050, 32000, 44100}
FALLBACK_NARROW = (8000, 11025, 16000)      # no voice, sound limited to part of the band
FALLBACK_WIDE = (22050, 32000, 44100)       # no voice, sound fills the band (music, effects)

_FRAME = 2048
_LAG_MIN, _LAG_MAX = 16, 800
_PITCH_CENTRE_HZ = 170.0      # middle of the speaking range; one octave either side is allowed
_PITCH_SPREAD_OCT = 1.0
_VOICE_TOP_HZ = 3200.0        # frequency below which speech carries about 95% of its energy
_VOICE_TOP_SPREAD_OCT = 0.5
_SPEECH_EDGE_MAX = 0.35       # speech recorded on music-grade hardware uses only the bottom of the band


@dataclass
class RateEvidence:
    frames: int = 0                 # frames analysed
    voiced: int = 0                 # frames with a clear pitch
    period: float = 0.0             # median pitch period, in samples
    edge: float = 1.0               # share of the band below which 95% of the energy sits
    speech_like: bool = False

    def describe(self) -> list[str]:
        out = []
        if self.speech_like:
            out.append(f"Voice found: {self.voiced} of {self.frames} frames have a clear pitch, "
                       f"repeating every {self.period:.0f} samples.")
            out.append(f"95% of the energy sits in the lowest {100 * self.edge:.0f}% of the band.")
        elif self.voiced:
            out.append(f"Pitched sound in {self.voiced} of {self.frames} frames, but it is not band-limited "
                       "like speech, so it is treated as music or effects.")
        else:
            out.append("No speaking voice found, so pitch cannot narrow the speed.")
        if self.edge > 0.6:
            out.append("The sound fills the band up to its top, which says nothing about speed on its own.")
        return out


@dataclass
class RateGuess:
    rate: int
    score: float
    pitch_hz: float = 0.0
    voice_top_hz: float = 0.0

    def why(self) -> str:
        if not self.pitch_hz:
            return "common chip playback speed"
        return f"voice pitch {self.pitch_hz:.0f} Hz, speech energy up to {self.voice_top_hz / 1000:.1f} kHz"


@dataclass
class RateEstimate:
    shortlist: list[RateGuess] = field(default_factory=list)   # best first, every standard rate
    informed: bool = False                                      # False: nothing in the audio narrowed it
    reason: str = ""
    fixed: bool = False                                         # True: the chip format itself states the speed

    @property
    def best(self) -> int:
        return self.shortlist[0].rate if self.shortlist else FALLBACK_WIDE[0]

    def top(self, n: int = 3) -> list[int]:
        """The n best speeds that are far enough apart to tell apart by ear, slowest first."""
        picked: list[int] = []
        for g in self.shortlist:
            if all(abs(math.log2(g.rate / r)) > 0.2 for r in picked):
                picked.append(g.rate)
            if len(picked) == n:
                break
        return sorted(picked)


def measure(x: np.ndarray) -> RateEvidence:
    """Gather speed evidence from a mono signal. Works in samples, not seconds."""
    x = x.astype(np.float64)
    ev = RateEvidence()
    if len(x) < _FRAME * 2:
        return ev
    p = spectrum(x)
    cum = np.cumsum(p[1:])
    if cum[-1] > 0:
        ev.edge = float((np.searchsorted(cum, 0.95 * cum[-1]) + 1) / len(p))
    rms_all = math.sqrt(float(np.mean((x - x.mean()) ** 2))) or 1.0
    periods = []
    step = max(_FRAME, (len(x) - _FRAME) // 400)      # at most ~400 frames per dump
    for start in range(0, len(x) - _FRAME, step):
        fr = x[start:start + _FRAME]
        fr = fr - fr.mean()
        energy = float(np.dot(fr, fr))
        if math.sqrt(energy / _FRAME) < 0.3 * rms_all:
            continue
        ev.frames += 1
        spec = np.fft.rfft(fr, 2 * _FRAME)
        ac = np.fft.irfft(spec * np.conj(spec))[:_LAG_MAX + 1]
        ac = ac / ac[0]
        seg = ac[_LAG_MIN:_LAG_MAX + 1]
        peak = float(seg.max())
        if peak < 0.5:
            continue
        periods.append(_LAG_MIN + int(seg.argmax()))
    ev.voiced = len(periods)
    if periods:
        ev.period = float(np.median(periods))
    ev.speech_like = (ev.voiced >= 8 and ev.voiced >= 0.1 * max(ev.frames, 1)
                      and ev.edge < _SPEECH_EDGE_MAX)
    return ev


def _bell(value: float, centre: float, spread_oct: float) -> float:
    return math.exp(-0.5 * (math.log2(value / centre) / spread_oct) ** 2)


def estimate(evidence: list[RateEvidence]) -> RateEstimate:
    """Combine the evidence of one or more dumps from the same equipment."""
    speech = [e for e in evidence if e.speech_like]
    est = RateEstimate()
    if not speech:
        edges = [e.edge for e in evidence if e.frames]
        wide = bool(edges) and float(np.median(edges)) > 0.5
        first = FALLBACK_WIDE if wide else FALLBACK_NARROW
        order = list(first) + [r for r in STANDARD_RATES if r not in first]
        est.shortlist = [RateGuess(r, 1.0 if r in first else 0.5) for r in order]
        est.reason = ("No speaking voice was found, so nothing in this audio pins down the speed. "
                      + ("The sound fills the whole band, which is typical of music-grade hardware, "
                         "so the faster common speeds are listed first. " if wide else
                         "The slower common speeds are listed first. ")
                      + "Use the listening check, or the speed from the equipment's documentation.")
        return est
    guesses = []
    for r in STANDARD_RATES:
        log_score = 0.0
        pitches, tops = [], []
        for e in speech:
            pitch = r / e.period
            top = e.edge * r / 2
            pitches.append(pitch)
            tops.append(top)
            log_score += math.log(max(_bell(pitch, _PITCH_CENTRE_HZ, _PITCH_SPREAD_OCT), 1e-12))
            log_score += math.log(max(_bell(top, _VOICE_TOP_HZ, _VOICE_TOP_SPREAD_OCT), 1e-12))
        score = math.exp(log_score / len(speech)) * (1.0 if r in COMMON_RATES else 0.6)
        guesses.append(RateGuess(r, score, float(np.median(pitches)), float(np.median(tops))))
    guesses.sort(key=lambda g: -g.score)
    est.shortlist = guesses
    est.informed = True
    a, b = guesses[0], guesses[1]
    est.reason = (f"Based on {len(speech)} dump{'s' if len(speech) != 1 else ''} with a speaking voice. "
                  f"At {a.rate:,} Hz the voice pitch is {a.pitch_hz:.0f} Hz and speech energy reaches "
                  f"{a.voice_top_hz / 1000:.1f} kHz. {b.rate:,} Hz is the next most likely. "
                  "This is an estimate: confirm it with the listening check.")
    return est
