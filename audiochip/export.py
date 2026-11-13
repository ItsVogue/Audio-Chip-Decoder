"""Write WAV files and a report that records exactly how they were made."""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import APP_NAME, __version__
from .codecs import CODECS, LAYOUTS
from .collection import Collection, Track, chip_audio, track_audio

Progress = Callable[[int, int, str], None]


def wav_bytes(x: np.ndarray, rate: int) -> bytes:
    """16-bit PCM WAV. x is int16 shaped (frames, channels) or (frames,)."""
    if x.ndim == 1:
        x = x.reshape(-1, 1)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams((x.shape[1], 2, int(rate), 0, "NONE", "not compressed"))
        w.writeframes(np.ascontiguousarray(x, dtype="<i2").tobytes())
    return buf.getvalue()


def _safe(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "untitled"


@dataclass
class ExportOptions:
    per_chip: bool = True
    joined: bool = True
    trim_padding: bool = True
    embedded: bool = True


def _write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def export(coll: Collection, outdir: str | Path, opts: ExportOptions | None = None,
           progress: Progress | None = None) -> dict:
    """Export every track. Returns the manifest that is also written to disk."""
    opts = opts or ExportOptions()
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = sum((len(t.members) if opts.per_chip else 0) + (1 if opts.joined and t.members else 0)
               for t in coll.tracks)
    done = 0
    files = []
    used: set[str] = set()

    def unique(rel: Path) -> Path:
        cand, n = rel, 2
        while str(cand).lower() in used:
            cand = rel.with_name(f"{rel.stem} ({n}){rel.suffix}")
            n += 1
        used.add(str(cand).lower())
        return cand

    for t in coll.tracks:
        hyp = coll.hyp_for(t)
        rate = coll.rate_for(t)
        if hyp is None or not rate:
            continue
        tname = _safe(t.name)
        if opts.joined and t.members:
            if progress:
                progress(done, jobs, f"Writing {t.name}")
            x = track_audio(coll, t, trim=opts.trim_padding)
            rel = unique(Path("Joined tracks") / f"{tname}.wav")
            digest = _write(outdir / rel, wav_bytes(x, rate))
            last = t.members[-1]
            files.append({"wav": rel.as_posix(), "kind": "joined track", "track": t.name,
                          "source": [c.name for c in t.members], "frames": int(len(x)),
                          "seconds": round(len(x) / rate, 3), "rate": rate, "reading": hyp.key,
                          "trimmed_bytes": last.structure.trail_len
                          if opts.trim_padding and last.structure.has_end_padding else 0,
                          "source_sha256": hashlib.sha256(t.joined_bytes()).hexdigest(), "wav_sha256": digest})
            done += 1
        if opts.per_chip:
            for c in t.members:
                if progress:
                    progress(done, jobs, f"Writing {t.name} / {c.name}")
                x = chip_audio(coll, t, c, trim=opts.trim_padding)
                rel = unique(Path("Individual chips") / tname / f"{_safe(Path(c.name).stem)}.wav")
                digest = _write(outdir / rel, wav_bytes(x, rate))
                files.append({"wav": rel.as_posix(), "kind": "single chip", "track": t.name, "source": [c.name],
                              "frames": int(len(x)), "seconds": round(len(x) / rate, 3), "rate": rate,
                              "reading": hyp.key,
                              "trimmed_bytes": c.structure.trail_len
                              if opts.trim_padding and c.structure.has_end_padding else 0,
                              "source_sha256": c.dump.sha256, "wav_sha256": digest})
                done += 1
        if opts.embedded:
            for c in t.chips:
                for n, e in enumerate(c.structure.embedded, 1):
                    if e.kind not in ("WAV", "MP3") or not e.length:
                        continue
                    rel = unique(Path("Embedded files") / tname
                                 / f"{_safe(Path(c.name).stem)} file {n}.{e.kind.lower()}")
                    blob = c.dump.data[e.offset:e.offset + e.length]
                    digest = _write(outdir / rel, blob)
                    files.append({"wav": rel.as_posix(), "kind": f"embedded {e.kind} file, copied out unchanged",
                                  "track": t.name, "source": [c.name], "offset": e.offset, "bytes": e.length,
                                  "source_sha256": c.dump.sha256, "wav_sha256": digest})
    if progress:
        progress(jobs, jobs, "Writing the report")
    manifest = {
        "app": f"{APP_NAME} {__version__}",
        "exported": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "options": {"per_chip": opts.per_chip, "joined": opts.joined, "trim_padding": opts.trim_padding},
        "collection": {
            "reading": coll.hyp.key if coll.hyp else None,
            "reading_source": coll.hyp_source,
            "confidence": coll.detection.confidence,
            "rate": coll.rate,
            "rate_confirmed_by_ear": coll.rate_confirmed,
        },
        "tracks": [{
            "name": t.name, "folder": str(t.folder), "reading": coll.hyp_for(t).key if coll.hyp_for(t) else None,
            "rate": coll.rate_for(t), "order_basis": t.order_basis, "warnings": t.warnings,
            "chips": [{"file": c.name, "role": c.role, "note": c.role_note, "bytes": c.dump.size,
                       "format": c.dump.source_format, "sha256": c.dump.sha256,
                       "end_padding": c.structure.trail_len if c.structure.has_end_padding else 0,
                       "own_best_reading": c.detection.best.hyp.key if c.detection.best else None,
                       "own_confidence": c.detection.confidence} for c in t.chips],
        } for t in coll.tracks],
        "files": files,
        "skipped": [{"file": str(p), "reason": why} for p, why in coll.errors],
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (outdir / "Report.txt").write_text(report_text(coll, manifest), encoding="utf-8")
    return manifest


def describe_reading(coll: Collection, track: Track | None = None) -> str:
    hyp = coll.hyp_for(track) if track else coll.hyp
    rate = coll.rate_for(track) if track else coll.rate
    if hyp is None:
        return "No audio encoding recognised"
    return f"{hyp.label()}, {rate:,} Hz"


def report_text(coll: Collection, manifest: dict) -> str:
    L: list[str] = []
    add = L.append
    add(f"{APP_NAME} {__version__} - conversion report")
    add(f"Exported: {manifest['exported']}")
    add("")
    add("HOW THE AUDIO WAS READ")
    if coll.hyp:
        c = CODECS[coll.hyp.codec]
        add(f"  Encoding : {c.name}")
        add(f"  Layout   : bytes {LAYOUTS[coll.hyp.layout][0]}, "
            + ("mono" if coll.hyp.channels == 1 else f"{coll.hyp.channels} channels interleaved"))
        add(f"  Chosen by: {'automatic detection' if coll.hyp_source == 'auto' else coll.hyp_source}")
        best = coll.detection.best
        add(f"  Automatic detection: {best.hyp.label() if best else 'nothing recognised'}"
            f" (confidence: {coll.detection.confidence})")
        add(f"  Evidence : {coll.detection.reason}")
        add("  Formula  :")
        for line in c.formula.splitlines():
            add(f"      {line}")
    else:
        add("  No encoding was recognised. " + coll.detection.reason)
    add(f"  Speed    : {coll.rate:,} Hz - "
        + ("confirmed by ear" if coll.rate_confirmed else "ESTIMATED, NOT CONFIRMED BY EAR"))
    add(f"  Speed evidence: {coll.rate_estimate.reason}")
    add("  Output   : 16-bit PCM WAV. No filtering, volume change or pitch change."
        + (" Padding at the end of each track removed." if manifest["options"]["trim_padding"]
           else " All padding kept."))
    add("")
    add("TRACKS")
    for t in coll.tracks:
        hyp = coll.hyp_for(t)
        rate = coll.rate_for(t)
        m = t.members
        secs = 0.0
        if hyp and rate and m:
            total = sum(c.dump.size for c in m)
            secs = total / CODECS[hyp.codec].bytes_per_sample / hyp.channels / rate
        add(f"  {t.name}: {len(m)} chip{'s' if len(m) != 1 else ''}, about {secs:.1f} s"
            + (f" [own setting: {hyp.label()}, {rate:,} Hz]" if t.hyp_override or t.rate_override else ""))
        add(f"    Order ({t.order_basis}): " + ", ".join(c.name for c in m))
        for c in t.chips:
            if not c.in_track:
                role = {"prejoined": "pre-joined copy"}.get(c.role, c.role)
                add(f"    Left out - {c.name}: {role}. {c.role_note}")
        for w in t.warnings:
            add(f"    CHECK: {w}")
    if coll.errors:
        add("")
        add("FILES THAT COULD NOT BE READ")
        for p, why in coll.errors:
            add(f"  {p.name}: {why}")
    add("")
    add("FILES WRITTEN (SHA-256 of each source and output is in manifest.json)")
    for f in manifest["files"]:
        if "seconds" in f:
            add(f"  {f['wav']}  {f['seconds']:.3f} s")
        else:
            add(f"  {f['wav']}  {f['bytes']:,} bytes ({f['kind']})")
    add("")
    return "\n".join(L)
