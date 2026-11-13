"""Write audio files and a report that records exactly how they were made."""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import APP_NAME, __version__
from .cleanup import Cleanup, clean
from .codecs import CODECS, LAYOUTS, is_structured
from .collection import Collection, Track, chip_audio, track_audio
from .sounds import sounds_of

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


MP3_RATES = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)
FORMATS = {"wav": "WAV, 16-bit (exact)", "flac": "FLAC (smaller, still exact)", "mp3": "MP3 (smallest, lossy)",
           "ogg": "Ogg Vorbis (small, lossy)", "opus": "Opus (small, lossy)"}
OPUS_RATES = (8000, 12000, 16000, 24000, 48000)


class FormatUnavailable(RuntimeError):
    pass


def audio_bytes(x: np.ndarray, rate: int, fmt: str = "wav") -> tuple[bytes, int]:
    """Encode int16 audio. Returns (file bytes, sample rate actually written)."""
    if fmt == "wav":
        return wav_bytes(x, rate), rate
    try:
        import soundfile as sf
    except Exception as e:                                   # missing package or missing codec library
        raise FormatUnavailable(f"{fmt.upper()} output needs the 'soundfile' package ({e}).") from e
    if x.ndim == 1:
        x = x.reshape(-1, 1)
    buf = io.BytesIO()
    if fmt == "flac":
        sf.write(buf, x, int(rate), format="FLAC", subtype="PCM_16")
        return buf.getvalue(), rate
    if fmt == "ogg":
        sf.write(buf, x, int(rate), format="OGG", subtype="VORBIS")
        return buf.getvalue(), rate
    if fmt in ("mp3", "opus"):
        allowed = MP3_RATES if fmt == "mp3" else OPUS_RATES
        out_rate = next((r for r in allowed if r >= rate), allowed[-1])
        if out_rate != rate:                                 # MP3 only allows a fixed set of speeds
            n = max(int(round(len(x) * out_rate / rate)), 1)
            pos = np.arange(n) * (rate / out_rate)
            x = np.stack([np.interp(pos, np.arange(len(x)), x[:, c]) for c in range(x.shape[1])], axis=1)
            x = np.clip(np.round(x), -32768, 32767).astype(np.int16)
        if fmt == "opus":
            sf.write(buf, x, out_rate, format="OGG", subtype="OPUS")
            return buf.getvalue(), out_rate
        if "MP3" not in sf.available_formats():
            raise FormatUnavailable("This copy of the sound library cannot write MP3.")
        sf.write(buf, x, out_rate, format="MP3")
        return buf.getvalue(), out_rate
    raise ValueError(fmt)


@dataclass
class ExportOptions:
    per_chip: bool = True
    joined: bool = True
    trim_padding: bool = True
    embedded: bool = True
    per_sound: bool = False              # also write each separate sound as its own file
    fmt: str = "wav"                     # "wav" | "flac" | "mp3"
    cleanup: Cleanup = field(default_factory=Cleanup)


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
               + (1 if opts.per_sound and t.members else 0) for t in coll.tracks)
    done = 0
    files = []
    used: set[str] = set()
    ext = "." + opts.fmt

    def unique(rel: Path) -> Path:
        cand, n = rel, 2
        while str(cand).lower() in used:
            cand = rel.with_name(f"{rel.stem} ({n}){rel.suffix}")
            n += 1
        used.add(str(cand).lower())
        return cand

    def write_audio(rel: Path, x: np.ndarray, rate: int, entry: dict) -> None:
        x, info = clean(x, rate, opts.cleanup)
        blob, out_rate = audio_bytes(x, rate, opts.fmt)
        digest = _write(outdir / rel, blob)
        entry.update({"wav": rel.as_posix(), "frames": int(len(x)), "seconds": round(len(x) / rate, 3),
                      "rate": rate, "wav_sha256": digest})
        if out_rate != rate:
            entry["file_rate"] = out_rate
        if info:
            entry["cleanup"] = info
        files.append(entry)

    for t in coll.tracks:
        hyp = coll.hyp_for(t)
        rate = coll.rate_for(t)
        if hyp is None or not rate:
            continue
        tname = _safe(t.name)
        whole = None
        if (opts.joined or opts.per_sound) and t.members:
            whole = track_audio(coll, t, trim=opts.trim_padding)
        if opts.joined and t.members:
            if progress:
                progress(done, jobs, f"Writing {t.name}")
            last = t.members[-1]
            write_audio(unique(Path("Joined tracks") / f"{tname}{ext}"), whole, rate,
                        {"kind": "joined track", "track": t.name, "source": [c.name for c in t.members],
                         "reading": hyp.key,
                         "trimmed_bytes": last.structure.trail_len
                         if opts.trim_padding and last.structure.has_end_padding else 0,
                         "source_sha256": hashlib.sha256(t.joined_bytes()).hexdigest()})
            done += 1
        if opts.per_chip:
            for c in t.members:
                if progress:
                    progress(done, jobs, f"Writing {t.name} / {c.name}")
                x = chip_audio(coll, t, c, trim=opts.trim_padding)
                write_audio(unique(Path("Individual chips") / tname / f"{_safe(Path(c.name).stem)}{ext}"), x, rate,
                            {"kind": "single chip", "track": t.name, "source": [c.name], "reading": hyp.key,
                             "trimmed_bytes": c.structure.trail_len
                             if opts.trim_padding and c.structure.has_end_padding else 0,
                             "source_sha256": c.dump.sha256})
                done += 1
        if opts.per_sound and t.members:
            if progress:
                progress(done, jobs, f"Splitting {t.name} into sounds")
            from .collection import track_sounds
            found, basis = track_sounds(coll, t, whole)
            width = max(len(str(len(found))), 2)
            for n, snd in enumerate(found, 1):
                rel = unique(Path("Separate sounds") / tname / f"{tname} - {n:0{width}d}{ext}")
                entry = {"kind": "separate sound", "track": t.name, "source": [c.name for c in t.members],
                         "reading": hyp.key, "found_by": basis, "starts_at_s": round(snd.start / rate, 3)}
                if snd.offset >= 0:
                    entry["offset"] = snd.offset
                write_audio(rel, whole[snd.start:snd.end], rate, entry)
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
        "options": {"per_chip": opts.per_chip, "joined": opts.joined, "trim_padding": opts.trim_padding,
                    "per_sound": opts.per_sound, "format": opts.fmt, "cleanup": opts.cleanup.describe()},
        "collection": {
            "reading": coll.hyp.key if coll.hyp else None,
            "reading_source": coll.hyp_source,
            "confidence": coll.detection.confidence,
            "rate": coll.rate,
            "rate_confirmed_by_ear": coll.rate_confirmed,
            "rate_stated_by_format": coll.rate_estimate.fixed,
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
        + ("stated by the chip format" if coll.rate_estimate.fixed else
           "confirmed by ear" if coll.rate_confirmed else "ESTIMATED, NOT CONFIRMED BY EAR"))
    add(f"  Speed evidence: {coll.rate_estimate.reason}")
    o = manifest["options"]
    kind = {"wav": "16-bit PCM WAV", "flac": "FLAC, 16-bit, lossless", "mp3": "MP3 (lossy)",
            "ogg": "Ogg Vorbis (lossy)", "opus": "Opus (lossy)"}[o.get("format", "wav")]
    done = o.get("cleanup") or []
    add(f"  Output   : {kind}. "
        + ("Clean-up applied: " + "; ".join(done) + "." if done
           else "No filtering, volume change or pitch change.")
        + (" Padding at the end of each track removed." if o["trim_padding"] else " All padding kept."))
    if o.get("format") in ("mp3", "opus"):
        add("             MP3 and Opus allow only certain speeds; files at other speeds were resampled to the "
            "next one up.")
    add("")
    add("TRACKS")
    for t in coll.tracks:
        hyp = coll.hyp_for(t)
        rate = coll.rate_for(t)
        m = t.members
        secs = 0.0
        if hyp and rate and m:
            if is_structured(hyp):
                secs = len(track_audio(coll, t)) / rate
            else:
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
