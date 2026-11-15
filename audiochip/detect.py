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

from .codecs import CODECS, LAYOUTS, Hypothesis, adpcm_stats, decode
from .structure import Structure

MAX_ANALYSIS_BYTES = 1 << 17      # analyse at most this much of each dump
MIN_ANALYSIS_BYTES = 4096
WHITE_NOISE_LEAK = 1 / 16         # leakage of data with no structure at all
AUDIO_LIKE_LEAK = 0.03            # below this the decode looks like filtered audio

# Less common readings must win clearly before they are preferred.
_PENALTY_LAYOUT = {"normal": 1.0, "bitrev": 1.25, "nibswap": 1.25}
_PENALTY_CHANNELS = {1: 1.0, 2: 1.25}
_PENALTY_FAMILY = {"linear8": 1.0, "companded8": 1.0, "linear16": 1.25, "adpcm": 1.0, "delta": 1.0, "chip": 1.0}
_SPECIAL = ("adpcm", "delta", "chip", "manual")     # tested by their own rules, not by the general sweep
_NOT_THIS = 4 * (1 / 16)                  # score given to a special reading that does not fit
LPC_SCAN_MAX = 1 << 18                    # dumps larger than this are not searched for LPC speech

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
    evidence: str = ""           # set when the reading was recognised by a signature or pattern
    cap: str = ""                # highest confidence this kind of evidence can justify


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
        if codec.family in _SPECIAL:
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


def _wander(x: np.ndarray) -> float:
    """How much the short-term average drifts, relative to the signal itself."""
    x = x.astype(np.float64)
    n = len(x) // 512 * 512
    if n < 4096:
        return 0.0
    sd = float(x.std())
    return float(x[:n].reshape(-1, 512).mean(axis=1).std() / sd) if sd > 0 else 0.0


def _evaluate_adpcm(window: np.ndarray) -> list[Candidate]:
    """Test each kind of 4-bit ADPCM in both nibble orders.

    Decoding anything that is not ADPCM drives the decoder into its limits within a few
    hundred samples (measured: over 25% of samples on the rail for every other format and
    for random data, under 1% for real ADPCM). That makes a cheap, sharp test, so a short
    stretch is tried first and the slow full decode only runs if it passes.
    """
    out = []
    for kind in ("oki", "ima", "yamaha"):
        for order, high_first in (("hi", True), ("lo", False)):
            x, rail, top = adpcm_stats(window[: 8 * 1024], kind, high_first)
            if rail < 0.1:
                x, rail, top = adpcm_stats(window[: 48 * 1024], kind, high_first)
            leak = leak_of(x)
            ok = rail < 0.02 and top < 0.02
            note = (f"{100 * rail:.1f}% of samples hit the decoder's limit and its step size sat at maximum "
                    f"{100 * top:.1f}% of the time" + ("." if ok else "; real ADPCM audio rarely does either."))
            # A decode that pins the limits is not ADPCM: push it to the bottom of the ranking.
            score = leak if ok else max(leak, WHITE_NOISE_LEAK) * 4
            if ok:
                # The kinds of ADPCM differ only in their step tables, so each decodes the others'
                # data into something smooth. But the errors pile up in the running total and the
                # result drifts up and down (measured: block averages wander by 20% to 100% of the
                # signal for the wrong kind, under 3% for the right one).
                w = _wander(x)
                if w > 0.1:
                    score *= (w / 0.05) ** 2
                    note += (f" The output drifts by {100 * w:.0f}% of its own level, which happens when "
                             "the data was made for a different kind of ADPCM.")
            out.append(Candidate(Hypothesis(f"{kind}_{order}"), leak, score, _kurtosis(x),
                                 float(x.mean() / 32768.0), note, worst_channel=leak))
    return out


def _evaluate_cvsd(window: np.ndarray) -> list[Candidate]:
    """Test for 1-bit CVSD speech.

    The decoder smooths anything, so leakage cannot be used to recognise CVSD. The bits
    themselves can: a CVSD encoder tracking quiet or slow sound alternates 0101..., so
    neighbouring bits differ far more often than the 50% of random data (measured: 69% to
    96% for CVSD, 18% to 54% for every other format), and equally at every bit position.
    """
    out = []
    for layout in ("normal", "bitrev"):
        hyp = Hypothesis("cvsd", layout)
        bits = np.unpackbits(LAYOUTS[layout][1](window[: 1 << 16]))
        change = bits[1:] != bits[:-1]
        rate = float(change.mean()) if len(change) else 0.0
        per = change[: len(change) // 8 * 8].reshape(-1, 8).mean(axis=0) if len(change) >= 8 else np.zeros(8)
        # With the bits of each byte taken in the wrong order, the step from one byte to the next
        # compares bits that were never neighbours, so that one position stands out.
        spread = float(per.max() - per.min())
        if not (0.58 <= rate <= 0.97 and spread <= 0.08):
            why = (f"Neighbouring bits differ {100 * rate:.0f}% of the time; CVSD gives 60% or more."
                   if rate < 0.58 else
                   f"Bit changes are uneven across the byte (spread {100 * spread:.0f}%), unlike a 1-bit stream "
                   "in this bit order.")
            out.append(Candidate(hyp, 1.0, _NOT_THIS, 0.0, 0.0, why))
            continue
        x = decode(window[: 1 << 14], hyp)[:, 0]
        leak = leak_of(x)
        ev = (f"Neighbouring bits differ {100 * rate:.0f}% of the time, evenly across all eight bit positions. "
              "Random data gives 50%; a 1-bit delta encoder idling on quiet sound gives far more. "
              "That pattern identifies CVSD delta modulation.")
        out.append(Candidate(hyp, leak, min(leak, AUDIO_LIKE_LEAK / 4) * (1 + 20 * spread), _kurtosis(x),
                             0.0, ev, worst_channel=leak, evidence=ev, cap="medium"))
    return out


def _evaluate_chips(data: bytes, st: Structure, sweep_found_audio: bool) -> list[Candidate]:
    """Recognise voice-chip formats that have a layout of their own."""
    from . import chipformats as cf
    b = np.frombuffer(data, dtype=np.uint8)
    out = []

    def add(codec, layout, found, ev, miss, cap="high"):
        hyp = Hypothesis(codec, layout)
        if found:
            out.append(Candidate(hyp, 0.001, 1e-9 * _PENALTY_LAYOUT[layout], 0.0, 0.0, ev,
                                 worst_channel=0.001, evidence=ev, cap=cap))
        else:
            out.append(Candidate(hyp, 1.0, _NOT_THIS, 0.0, 0.0, miss))

    upd = cf.upd7759_present(b)
    add("upd7759", "normal", upd,
        "The dump starts with the NEC uPD7759 signature (5A A5 69 55) and its table of sound addresses.",
        "No uPD7759 signature at the start of the dump.")

    entries = [] if upd else cf.oki_table_entries(b)
    oki = False
    rail = 0.0
    if entries:
        # the phrases must also decode as clean ADPCM
        _, start, stop = entries[0]
        _, rail, top = adpcm_stats(b[start: min(stop + 1, start + 8192)], "oki", True)
        oki = rail < 0.02 and top < 0.05
    add("oki_table", "normal", oki,
        f"The first 1,024 bytes are an OKI MSM6295 phrase table with {len(entries)} valid entries, and the "
        "phrases decode as clean OKI ADPCM.",
        "The start of the dump is not an MSM6295 phrase table.")

    # LPC speech: search for phrases. Costly, so only when nothing simpler fits.
    scan = not sweep_found_audio and not upd and not oki and 256 <= len(b) <= LPC_SCAN_MAX
    results = {}
    for layout in ("normal", "bitrev"):
        src = LAYOUTS[layout][1](b) if scan else b
        for bits in (6, 5):
            results[layout, bits] = cf.lpc_evidence(src, bits) if scan else (0, 0.0, 1.0)
    best_cover = max(r[1] for r in results.values())
    for codec, bits in (("lpc_5220", 6), ("lpc_5110", 5), ("lpc_0281", 5)):
        for layout in ("normal", "bitrev"):
            count, cover, rough = results[layout, bits]
            found = count >= 3 and cover >= 0.2 and cover >= 0.8 * best_cover
            ev = (f"{count} speech phrases were found, covering {100 * cover:.0f}% of the bytes. Their frames "
                  f"change smoothly from one to the next (roughness {rough:.2f}; speech gives about 0.3, "
                  "random data about 1.0) and each ends with the chip's stop code.")
            miss = ("Not searched for LPC speech." if not scan else
                    f"Only {count} possible LPC phrases, covering {100 * cover:.0f}% of the bytes.")
            add(codec, layout, found, ev, miss, cap="medium")
            if found and codec == "lpc_0281":
                out[-1].score *= 1.05           # same data as the TMS5110 reading; list that one first
    return out


_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


def _judge(cands: list[Candidate]) -> tuple[str, str]:
    best = cands[0]
    if best.evidence:
        note = ""
        twin = next((c for c in cands[1:4] if c.evidence and c.hyp.is_twin_of(best.hyp)), None)
        if twin:
            note = (f" {CODECS[twin.hyp.codec].name} fits the same data; the two chips differ only in their "
                    "internal tables, so try both by ear.")
        return best.cap or "high", best.evidence + note
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
    cands += _evaluate_cvsd(window)
    found_audio = min(c.score for c in cands) < AUDIO_LIKE_LEAK       # includes a CVSD match
    cands += _evaluate_chips(data, st, found_audio)
    from .morecodecs import extra_candidates
    cands += extra_candidates(data, Candidate, Hypothesis, _NOT_THIS)
    from . import consolecodecs, ffcodecs
    cands += ffcodecs.extra_candidates(data, Candidate, Hypothesis, _NOT_THIS)
    cands += consolecodecs.extra_candidates(data, Candidate, Hypothesis, _NOT_THIS)
    from . import a1800
    cands += a1800.extra_candidates(data, Candidate, Hypothesis, _NOT_THIS)
    cands.sort(key=lambda c: c.score)
    det.candidates = cands
    conf, reason = _judge(cands)
    if len(window) < MIN_ANALYSIS_BYTES and not cands[0].evidence:
        conf = "low" if conf != "none" else conf
        reason = det.reason + " " + reason
    if conf == "none":                                   # unknown format: report framing if there is any
        from .framing import describe
        reason += describe(data)
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
        seen = []
        for d, w in usable:
            c = d.find(hyp)
            seen.append(c)
            log_score += w * math.log(max(c.score, 1e-9))
            log_leak += w * math.log(max(c.leak, 1e-9))
            kurt += w * c.kurtosis
        # a signature or pattern counts for the pool only if every dump shows it
        shown = [c for c in seen if c.evidence]
        ev = shown[0].evidence if len(shown) == len(seen) else ""
        pooled.append(Candidate(hyp, math.exp(log_leak / total_w), math.exp(log_score / total_w),
                                kurt / total_w, 0.0, evidence=ev, cap=shown[0].cap if ev else ""))
    pooled.sort(key=lambda c: c.score)
    conf, reason = _judge(pooled)
    agree = sum(1 for d, _ in usable if d.best.hyp == pooled[0].hyp or d.best.hyp.is_twin_of(pooled[0].hyp))
    reason = f"Pooled over {len(usable)} dumps; {agree} of them pick this reading on their own. " + reason
    return Detection(pooled, conf, reason, sum(d.analysed_bytes for d, _ in usable))
