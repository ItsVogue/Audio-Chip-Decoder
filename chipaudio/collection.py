"""Group dumps into tracks, put chips in order, and hold the decode settings.

A collection is everything the user opened. Each folder is one track; the
dumps in it are the chips of that track, in playing order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from .codecs import CODECS, Hypothesis, decode
from .detect import Detection, combine, detect
from .ingest import Dump, DumpError, is_dump_candidate, load_dump
from .rate import RateEstimate, RateEvidence, estimate, measure
from .structure import Structure, analyse_structure

Progress = Callable[[int, int, str], None]
RATE_BYTES = 4 << 20          # speed evidence is gathered from at most this much of each dump


@dataclass
class Chip:
    dump: Dump
    structure: Structure
    detection: Detection
    role: str = "chip"            # "chip" | "duplicate" | "prejoined" | "blank"
    role_note: str = ""
    rate_evidence: RateEvidence | None = None

    @property
    def name(self) -> str:
        return self.dump.name

    @property
    def in_track(self) -> bool:
        return self.role == "chip"


@dataclass
class Track:
    name: str
    folder: Path
    chips: list[Chip] = field(default_factory=list)        # playing order; non-members at the end
    detection: Detection = field(default_factory=Detection)
    warnings: list[str] = field(default_factory=list)
    order_basis: str = ""
    hyp_override: Hypothesis | None = None
    rate_override: int | None = None

    @property
    def members(self) -> list[Chip]:
        return [c for c in self.chips if c.in_track]

    def joined_bytes(self) -> bytes:
        return b"".join(c.dump.data for c in self.members)


@dataclass
class Collection:
    tracks: list[Track] = field(default_factory=list)
    detection: Detection = field(default_factory=Detection)
    rate_estimate: RateEstimate = field(default_factory=RateEstimate)
    hyp: Hypothesis | None = None          # collection-wide reading
    rate: int = 0                          # collection-wide playback speed
    rate_confirmed: bool = False           # True once the user (or a profile) has set the speed
    hyp_source: str = "auto"               # "auto" | "user" | "profile"
    errors: list[tuple[Path, str]] = field(default_factory=list)

    # ---- settings in force for a track
    def hyp_for(self, track: Track) -> Hypothesis | None:
        return track.hyp_override or self.hyp

    def rate_for(self, track: Track) -> int:
        return track.rate_override or self.rate

    @property
    def chips(self) -> list[Chip]:
        return [c for t in self.tracks for c in t.chips]


# ------------------------------------------------------------------ gathering
def gather(paths: Iterable[str | Path]) -> list[Path]:
    """Expand files and folders into a list of dump files, in a stable order."""
    out: list[Path] = []
    seen: set[Path] = set()
    for p in paths:
        p = Path(p)
        found = [p] if p.is_file() else sorted(q for q in p.rglob("*") if is_dump_candidate(q)) if p.is_dir() else []
        for q in found:
            r = q.resolve()
            if r not in seen:
                seen.add(r)
                out.append(q)
    return out


# ------------------------------------------------------------------- ordering
def _numbers(stem: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", stem)]


def _order(chips: list[Chip]) -> tuple[list[Chip], str]:
    """Order chips by the number in their file names.

    Tries the last number in each name, then the first, and uses whichever
    gives every chip a different value. Falls back to alphabetical order.
    """
    if len(chips) < 2:
        return chips, "single dump"
    nums = [_numbers(Path(c.name).stem) for c in chips]
    best = None
    for label, pick in (("the last number in each file name", -1), ("the first number in each file name", 0)):
        if all(nums):
            vals = [n[pick] for n in nums]
            if len(set(vals)) == len(vals):
                span = max(vals) - min(vals)
                if best is None or span < best[0]:
                    best = (span, label, vals)
    if best:
        _, label, vals = best
        return [c for _, c in sorted(zip(vals, chips), key=lambda t: t[0])], label
    return sorted(chips, key=lambda c: c.name.lower()), "alphabetical order (no usable numbers in the file names)"


def _assign_roles(chips: list[Chip]) -> None:
    for c in chips:
        if c.structure.blank:
            c.role, c.role_note = "blank", "Every byte is the same: blank chip or no contact when it was read."
    # identical content: keep the dump with the shortest name
    by_hash: dict[str, list[Chip]] = {}
    for c in chips:
        if c.role == "chip":
            by_hash.setdefault(c.dump.sha256, []).append(c)
    for group in by_hash.values():
        group.sort(key=lambda c: (len(c.name), c.name.lower()))
        for dup in group[1:]:
            dup.role, dup.role_note = "duplicate", f"Identical to {group[0].name}."


def _find_prejoined(chips: list[Chip]) -> None:
    """A dump that is exactly the other chips joined in order is a copy, not a chip."""
    members = [c for c in chips if c.role == "chip"]
    if len(members) < 3:
        return
    for cand in members:
        others, _ = _order([c for c in members if c is not cand])
        if sum(c.dump.size for c in others) == cand.dump.size and \
                b"".join(c.dump.data for c in others) == cand.dump.data:
            cand.role = "prejoined"
            cand.role_note = f"Exactly the other {len(others)} chips joined in order; kept out of the track."
            return


def _check_order(track: Track) -> None:
    m = track.members
    if len(m) < 2:
        return
    for i, c in enumerate(m):
        if c.structure.has_end_padding and i != len(m) - 1:
            track.warnings.append(f"{c.name} ends in {c.structure.trail_len:,} bytes of padding but is not the "
                                  "last chip. Check the chip order.")
        if c.structure.has_start_marker and i != 0:
            track.warnings.append(f"{c.name} starts with a zero marker like a first chip, but is number {i + 1}. "
                                  "Check the chip order.")
    sizes = {c.dump.size for c in m}
    if len(sizes) > 1:
        track.warnings.append("The chips are not all the same size: " + ", ".join(f"{s:,}" for s in sorted(sizes))
                              + " bytes.")
    for c in m:
        if c.structure.repeat_of:
            track.warnings.append(f"{c.name} repeats itself every {c.structure.repeat_of:,} bytes. "
                                  "It was probably read as a larger chip than it is.")


# ------------------------------------------------------------------- building
def build(paths: Iterable[str | Path], progress: Progress | None = None) -> Collection:
    """Load, analyse and organise dumps. This is the whole automatic pipeline."""
    files = gather(paths)
    coll = Collection()
    by_folder: dict[Path, list[Chip]] = {}
    total = len(files)
    for i, f in enumerate(files):
        if progress:
            progress(i, total, f"Analysing {f.name}")
        try:
            dump = load_dump(f)
        except DumpError as e:
            coll.errors.append((f, str(e)))
            continue
        st = analyse_structure(dump.data)
        det = detect(dump.data, st)
        by_folder.setdefault(f.parent, []).append(Chip(dump, st, det))

    for folder in sorted(by_folder, key=lambda p: str(p).lower()):
        chips = by_folder[folder]
        _assign_roles(chips)
        _find_prejoined(chips)
        members, basis = _order([c for c in chips if c.role == "chip"])
        rest = sorted((c for c in chips if c.role != "chip"), key=lambda c: c.name.lower())
        track = Track(folder.name or str(folder), folder, members + rest, order_basis=basis)
        track.detection = combine([c.detection for c in members],
                                  [c.structure.content_end - c.structure.content_start for c in members])
        _check_order(track)
        coll.tracks.append(track)

    if progress:
        progress(total, total, "Pooling the evidence")
    _decide(coll)
    return coll


def _decide(coll: Collection) -> None:
    """Choose the collection-wide reading and speed from the pooled evidence."""
    members = [c for t in coll.tracks for c in t.members]
    coll.detection = combine([c.detection for c in members],
                             [c.structure.content_end - c.structure.content_start for c in members])
    best = coll.detection.best
    coll.hyp = best.hyp if best and coll.detection.confidence != "none" else None
    coll.hyp_source = "auto"
    # A track that clearly disagrees with the rest came from different equipment.
    for t in coll.tracks:
        tb = t.detection.best
        if (coll.hyp and tb and t.detection.confidence == "high" and tb.hyp != coll.hyp
                and not tb.hyp.is_twin_of(coll.hyp)):
            t.hyp_override = tb.hyp
            t.warnings.append(f"This track decodes best as {tb.hyp.label()}, unlike the rest of the collection. "
                              "It has been given its own setting.")
    measure_rates(coll)


def measure_rates(coll: Collection) -> None:
    """(Re)gather speed evidence under the readings now in force."""
    evidence = []
    for t in coll.tracks:
        hyp = coll.hyp_for(t)
        for c in t.members:
            if hyp is None or CODECS[hyp.codec].family == "adpcm" and c.dump.size > 1 << 18:
                c.rate_evidence = None
                continue
            end = min(c.structure.content_end, c.structure.content_start + RATE_BYTES)
            x = decode(c.dump.data[c.structure.content_start:end], hyp)
            c.rate_evidence = measure(x.mean(axis=1)) if len(x) else None
            if c.rate_evidence:
                evidence.append(c.rate_evidence)
    coll.rate_estimate = estimate(evidence)
    if not coll.rate_confirmed:
        coll.rate = coll.rate_estimate.best


# ------------------------------------------------------------------- decoding
def chip_audio(coll: Collection, track: Track, chip: Chip, trim: bool = False,
               hyp: Hypothesis | None = None) -> np.ndarray:
    hyp = hyp or coll.hyp_for(track)
    data = chip.dump.data
    if trim and chip.structure.has_end_padding:
        data = data[: chip.structure.content_end]
    return decode(data, hyp)


def track_audio(coll: Collection, track: Track, trim: bool = False,
                hyp: Hypothesis | None = None) -> np.ndarray:
    """Join the member chips byte for byte, then decode, so nothing is lost at the joins."""
    hyp = hyp or coll.hyp_for(track)
    members = track.members
    if not members:
        return np.zeros((0, hyp.channels), dtype=np.int16)
    data = track.joined_bytes()
    last = members[-1]
    if trim and last.structure.has_end_padding:
        data = data[: len(data) - last.structure.trail_len]
    return decode(data, hyp)


def move_chip(track: Track, chip: Chip, delta: int) -> bool:
    """Move a chip earlier (-1) or later (+1) in the playing order."""
    members = track.members
    if chip not in members:
        return False
    i = members.index(chip)
    j = i + delta
    if not 0 <= j < len(members):
        return False
    members[i], members[j] = members[j], members[i]
    track.chips = members + [c for c in track.chips if not c.in_track]
    track.order_basis = "set by hand"
    track.warnings = [w for w in track.warnings if "its own setting" in w]
    _check_order(track)
    return True
