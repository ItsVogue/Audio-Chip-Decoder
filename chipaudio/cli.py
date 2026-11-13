"""Command-line front end: the same engine as the desktop app, for batch work."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_NAME, __version__
from .codecs import CODECS, LAYOUTS, Hypothesis
from .collection import Collection, build, measure_rates
from .export import ExportOptions, describe_reading, export
from . import profiles


def _progress(i: int, n: int, text: str) -> None:
    print(f"\r[{i:>4}/{n}] {text[:60]:<60}", end="", file=sys.stderr, flush=True)
    if i >= n:
        print(file=sys.stderr)


def _apply_overrides(coll: Collection, args) -> None:
    if args.profile:
        p = profiles.find(args.profile)
        if not p:
            sys.exit(f"No profile matches '{args.profile}'. Available: "
                     + "; ".join(q.name for q in profiles.load_all()))
        coll.hyp, coll.hyp_source = p.hyp, f"profile '{p.name}'"
        coll.rate, coll.rate_confirmed = p.rate, True
        for t in coll.tracks:
            t.hyp_override = None
        measure_rates(coll)
    if args.codec:
        coll.hyp = Hypothesis(args.codec, args.layout, args.channels)
        coll.hyp_source = "user"
        for t in coll.tracks:
            t.hyp_override = None
        measure_rates(coll)
    if args.rate:
        coll.rate, coll.rate_confirmed = args.rate, True


def _summary(coll: Collection) -> None:
    print(f"\nReading : {describe_reading(coll)}")
    print(f"Confidence in the encoding: {coll.detection.confidence}")
    print(f"  {coll.detection.reason}")
    print(f"Speed   : {coll.rate:,} Hz ({'set' if coll.rate_confirmed else 'estimated, not confirmed by ear'})")
    print(f"  {coll.rate_estimate.reason}")
    if not coll.rate_confirmed:
        print(f"  Speeds worth comparing by ear: {', '.join(f'{r:,}' for r in coll.rate_estimate.top(3))} Hz")
    if coll.detection.candidates:
        print("\nTop readings (share of energy at the top of the band; lower is better):")
        for c in coll.detection.candidates[:5]:
            print(f"  {100 * c.leak:7.3f}%  {c.hyp.label()}")
    print("\nTracks:")
    for t in coll.tracks:
        m = t.members
        print(f"  {t.name}: {len(m)} chip{'s' if len(m) != 1 else ''} "
              f"({', '.join(c.name for c in m)}) - order: {t.order_basis}")
        for c in t.chips:
            if not c.in_track:
                print(f"      left out: {c.name} ({c.role}) {c.role_note}")
            for line in c.structure.describe() if c.structure.embedded or c.structure.repeat_of else []:
                print(f"      {c.name}: {line}")
        for w in t.warnings:
            print(f"      CHECK: {w}")
    for p, why in coll.errors:
        print(f"  could not read {p}: {why}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="chipaudio", description=f"{APP_NAME} {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, helptext in (("analyze", "identify the encoding and speed, without writing anything"),
                           ("convert", "identify, decode and write WAV files plus a report")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("paths", nargs="+", help="dump files or folders (BIN, Intel HEX, S-record)")
        sp.add_argument("--codec", choices=sorted(CODECS), help="force an encoding instead of detecting it")
        sp.add_argument("--layout", choices=sorted(LAYOUTS), default="normal")
        sp.add_argument("--channels", type=int, choices=(1, 2), default=1)
        sp.add_argument("--rate", type=int, help="playback speed in Hz")
        sp.add_argument("--profile", help="use a saved device profile (name or part of it)")
        if name == "convert":
            sp.add_argument("-o", "--out", required=True, help="output folder")
            sp.add_argument("--keep-padding", action="store_true", help="do not remove padding at the end of tracks")
            sp.add_argument("--no-chips", action="store_true", help="write joined tracks only")
            sp.add_argument("--no-joined", action="store_true", help="write single-chip files only")
    sub.add_parser("codecs", help="list the encodings this version knows")
    sub.add_parser("profiles", help="list saved device profiles")
    args = ap.parse_args(argv)

    if args.cmd == "codecs":
        for c in CODECS.values():
            print(f"{c.id:11s} {c.name}\n            {c.plain}")
        return 0
    if args.cmd == "profiles":
        for p in profiles.load_all():
            print(f"{p.name}: {p.hyp.label()}, {p.rate:,} Hz{' (built in)' if p.builtin else ''}")
        return 0

    coll = build(args.paths, _progress)
    if not coll.tracks:
        print("No dump files found.", file=sys.stderr)
        return 2
    _apply_overrides(coll, args)
    _summary(coll)
    if args.cmd == "convert":
        if coll.hyp is None:
            print("\nNothing written: no encoding was recognised. Use --codec to force one.", file=sys.stderr)
            return 3
        opts = ExportOptions(per_chip=not args.no_chips, joined=not args.no_joined,
                             trim_padding=not args.keep_padding)
        manifest = export(coll, args.out, opts, _progress)
        print(f"\nWrote {len(manifest['files'])} files to {Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
