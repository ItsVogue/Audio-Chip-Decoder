"""Work out how audio is stored in a dump.

Method: decode the bytes under every plausible reading (a "hypothesis"),
then measure how much energy lands in the top sixteenth of the frequency
band. Real recordings were filtered before they were stored, so a correct
decode keeps that region nearly empty. A wrong decode distorts the waveform
and sprays energy all the way up. The reading with the least leakage wins.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .codecs import CODECS, LAYOUTS, Hypothesis, decode, oki_stats
from .structure import Structure

MAX_ANALYSIS_BYTES = 1 << 17      # analyse at most this much of each dump
MIN_ANALYSIS_BYTES = 4096
WHITE_NOISE_LEAK = 1 / 16         # leakage of data with no structure at all
AUDIO_LIKE_LEAK = 0.03            # below this the decode looks like filtered audio

# Less common readings must win clearly before they are preferred.
_PENALTY_LAYOUT = {"normal": 1.0, "bitrev": 1.25, "nibswap": 1.25}
_PENALTY_CHANNELS = {1: 1.0, 2: 1.25}
_PENALTY_FAMILY = {"linear8": 1.0, "companded8": 1.0, "linear16": 1.25, "adpcm": 1.0}

# Reading 8-bit samples as 16-bit pairs keeps every other sample as the high byte.
_HALVED_AS_16 = {"u8": ("u16le", "u16be"), "s8": ("s16le", "s16be")}


@dataclass
class Candidate:
    hyp: Hypothesis
    leak: float            # share of energy in the top 1/16 of the band (lower is better)
    score: float           # leak with rarity penalties applied; candidates are ranked by this
    kurtosis: float        # loudness-distribution shape; natural audio is usually above 3
    dc: float              # average offset as a share of full scale
    note: str = ""
    worst_channel: float = 0.0   # leak of the roughest channel (same as leak for mono)


@dataclass
class Detection:
    candidates: list[Candidate] = field(default_factory=list)   # best first
    confidence: str = "none"      # "high" | "medium" | "low" | "none"
    reason: str = ""              # plain-language explanation
    analysed_bytes: int = 0

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    def find(self, hyp: Hypothesis) -> Candidate | None:
        for c in self.candidates:
            if c.hyp == hyp:
                return c
        return None


def auto_hypotheses() -> list[Hypothesis]:
    hyps = []
    for codec in CODECS.values():
        if codec.family == "adpcm":
            continue
        layouts = LAYOUTS if codec.family != "linear16" else ("normal",)
        for layout in layouts:
            for ch in (1, 2):
                hyps.append(Hypothesis(codec.id, layout, ch))
    return hyps


def analysis_window(data: bytes, st: Structure) -> np.ndarray:
    """Pick the bytes to analyse: the content between any padding, capped in size."""
    b = np.frombuffer(data, dtype=np.uint8)
    start, end = st.content_start, st.content_end
    start += (-start) % 4                    # stay aligned for 16-bit and stereo readings
    b = b[start:end]
    if len(b) > MAX_ANALYSIS_BYTES:
        # four evenly spaced blocks, each aligned to 4 bytes
        block = MAX_ANALYSIS_BYTES // 4
        offs = [(i * (len(b) - block) // 3) // 4 * 4 for i in range(4)]
        b = np.concatenate([b[o:o + block] for o in offs])
    return np.ascontiguousarray(b[: len(b) // 4 * 4])


def spectrum(x: np.ndarray, nseg: int = 2048) -> np.ndarray:
    """Average power spectrum of a mono float signal (Welch, Hann window)."""
    if len(x) < nseg:
        nseg = max(64, 1 << int(math.log2(max(len(x), 64))))
        if len(x) < nseg:
            x = np.pad(x, (0, nseg - len(x)))
    hop = nseg // 2
    count = 1 + (len(x) - nseg) // hop
    idx = np.arange(nseg)[None, :] + hop * np.arange(count)[:, None]
    seg = x[idx]
    seg = seg - seg.mean(axis=1, keepdims=True)
    spec = np.abs(np.fft.rfft(seg * np.hanning(nseg), axis=1)) ** 2
    return spec.mean(axis=0)


def leak_of(x: np.ndarray) -> float:
    p = spectrum(x.astype(np.float64))
    total = p[1:].sum()
    if not np.isfinite(total) or total <= 0:
        return 1.0
    top = len(p) // 16
    return float(p[-top:].sum() / total)


def _kurtosis(x: np.ndarray) -> float:
    x = x.astype(np.float64)
    x = x - x.mean()
    x2 = x * x
    v = x2.mean()
    return float((x2 * x2).mean() / (v * v)) if v > 0 else 0.0


def evaluate(window: np.ndarray, hyp: Hypothesis) -> Candidate:
    x = decode(window, hyp)                       # (frames, channels)
    leaks = [leak_of(x[:, c]) for c in range(x.shape[1])]
    leak = float(np.mean(leaks))
    family = CODECS[hyp.codec].family
    score = leak * _PENALTY_LAYOUT[hyp.layout] * _PENALTY_CHANNELS[hyp.channels] * _PENALTY_FAMILY[family]
    mono = x.mean(axis=1)
    return Candidate(hyp, leak, score, _kurtosis(mono), float(mono.mean() / 32768.0),
                     worst_channel=float(max(leaks)))


def _evaluate_adpcm(window: np.ndarray) -> list[Candidate]:
    """Test the two OKI ADPCM nibble orders.

    Decoding anything that is not ADPCM drives the decoder into its limits within a few
    hundred samples (measured: over 25% of samples on the rail for every other format and
    for random data, under 1% for real ADPCM). That makes a cheap, sharp test, so a short
    stretch is tried first and the slow full decode only runs if it passes.
    """
    out = []
    for cid, high_first in (("oki_hi", True), ("oki_lo", False)):
        x, rail, top = oki_stats(window[: 8 * 1024], high_first)
        if rail < 0.1:
            x, rail, top = oki_stats(window[: 48 * 1024], high_first)
        leak = leak_of(x)
        ok = rail < 0.02 and top < 0.02
        note = (f"{100 * rail:.1f}% of samples hit the decoder's limit and its step size sat at maximum "
                f"{100 * top:.1f}% of the time" + ("." if ok else "; real ADPCM audio rarely does either."))
        # A decode that pins the limits is not ADPCM: push it to the bottom of the ranking.
        score = leak if ok else max(leak, WHITE_NOISE_LEAK) * 4
        out.append(Candidate(Hypothesis(cid), leak, score, _kurtosis(x), float(x.mean() / 32768.0), note,
                             worst_channel=leak))
    return out


def _judge(cands: list[Candidate]) -> tuple[str, str]:
    best = cands[0]
    # The rival is the best reading that is a genuinely different interpretation. Readings that
    # merely skip every other sample of the best one (more channels, or 8-bit data taken as
    # 16-bit pairs) look just as smooth on band-limited sound; they are settled by ear because
    # they play at half speed, so they do not count against confidence here.
    def alias(c: Candidate) -> bool:
        b, h = best.hyp, c.hyp
        if h.is_twin_of(b) or (h.codec, h.layout) == (b.codec, b.layout):
            return True
        if h.layout == b.layout and CODECS[h.codec].twin == b.codec:
            return True
        return h.layout == b.layout and h.codec in _HALVED_AS_16.get(b.codec, ())
    rival = next((c for c in cands[1:] if not alias(c)), None)
    ratio = (rival.score / best.score) if rival and best.score > 0 else float("inf")
    twin = next((c for c in cands[1:4] if c.hyp.is_twin_of(best.hyp)), None)
    twin_note = (f" {CODECS[twin.hyp.codec].name} scores almost the same and sounds nearly identical."
                 if twin else "")
    if best.leak >= AUDIO_LIKE_LEAK:
        if best.leak >= WHITE_NOISE_LEAK * 0.7:
            return "none", ("No reading of these bytes looks like filtered audio. The chip may hold "
                            "compressed sound in a format this version does not know, program code, or data.")
        return "low", (f"The best reading still leaves {100 * best.leak:.1f}% of its energy at the top of the "
                       "band, more than a clean recording would. Compare the alternatives by ear.")
    if ratio >= 2.0:
        return "high", (f"This reading leaves {100 * best.leak:.2f}% of its energy at the top of the band; "
                        f"the next different reading leaves {ratio:.1f} times more." + twin_note)
    if ratio >= 1.25:
        return "medium", (f"This reading is ahead of the next different one by {ratio:.2f} times. "
                          "Worth a quick listen to the runner-up." + twin_note)
    return "low", ("Several readings score about the same. Compare the top few by ear." + twin_note)


def detect(data: bytes, st: Structure) -> Detection:
    """Rank every reading of one dump."""
    det = Detection()
    if st.blank:
        det.reason = "The dump is blank, so there is nothing to decode."
        return det
    window = analysis_window(data, st)
    det.analysed_bytes = len(window)
    if len(window) < MIN_ANALYSIS_BYTES:
        det.reason = (f"Only {len(window):,} bytes of content were found, which is too little to identify "
                      "an encoding with any confidence.")
        if len(window) < 256:
            return det
    cands = [evaluate(window, h) for h in auto_hypotheses()]
    # Two separate 8-bit channels can pass for one 16-bit stream, because the "low byte" is
    # then smooth sound rather than the noise a real low byte carries. If an 8-bit two-channel
    # reading has both channels clean, the 16-bit readings are that same data misread.
    two_clean = any(c.hyp.channels == 2 and CODECS[c.hyp.codec].family != "linear16"
                    and c.worst_channel < AUDIO_LIKE_LEAK for c in cands)
    if two_clean:
        for c in cands:
            if CODECS[c.hyp.codec].family == "linear16":
                c.score *= 100
                c.note = "Its low bytes form a clean signal of their own, so this is 8-bit data, not 16-bit."
    cands += _evaluate_adpcm(window)
    cands.sort(key=lambda c: c.score)
    det.candidates = cands
    conf, reason = _judge(cands)
    if len(window) < MIN_ANALYSIS_BYTES:
        conf = "low" if conf != "none" else conf
        reason = det.reason + " " + reason
    det.confidence, det.reason = conf, reason
    return det


def combine(detections: list[Detection], weights: list[float] | None = None) -> Detection:
    """Pool the evidence of several dumps that came from the same equipment.

    Scores are combined as a weighted geometric mean, so one quiet or
    half-empty chip cannot outvote the rest.
    """
    usable = [(d, w) for d, w in zip(detections, weights or [1.0] * len(detections)) if d.candidates and w > 0]
    if not usable:
        return Detection(reason="None of the dumps held enough content to analyse.")
    keys = set.intersection(*[{c.hyp.key for c in d.candidates} for d, _ in usable])
    total_w = sum(w for _, w in usable)
    pooled = []
    for key in keys:
        hyp = Hypothesis.from_key(key)
        log_score = log_leak = kurt = 0.0
        for d, w in usable:
            c = d.find(hyp)
            log_score += w * math.log(max(c.score, 1e-9))
            log_leak += w * math.log(max(c.leak, 1e-9))
            kurt += w * c.kurtosis
        pooled.append(Candidate(hyp, math.exp(log_leak / total_w), math.exp(log_score / total_w),
                                kurt / total_w, 0.0))
    pooled.sort(key=lambda c: c.score)
    conf, reason = _judge(pooled)
    agree = sum(1 for d, _ in usable if d.best.hyp == pooled[0].hyp or d.best.hyp.is_twin_of(pooled[0].hyp))
    reason = f"Pooled over {len(usable)} dumps; {agree} of them pick this reading on their own. " + reason
    return Detection(pooled, conf, reason, sum(d.analysed_bytes for d, _ in usable))
