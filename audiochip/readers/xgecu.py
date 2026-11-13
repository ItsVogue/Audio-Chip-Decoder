"""XGecu universal programmers: TL866A/CS, TL866II Plus and T48. Read-only.

These read 24/28/32/40-pin parallel EPROMs and flash as well as serial chips.
The work is done by minipro, the open-source tool for these programmers,
which ships with the app and is run as a separate program. This module only
ever asks it to read (-r); it never passes a write or erase option.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from .base import DriverMissing, NoDevice, Progress, ReaderError

HERE = Path(__file__).with_name("xgecu")
# key printed by "minipro -k"  ->  (name shown, option for -q, chip index family)
MODELS = {
    "TL866A": ("TL866A", "tl866a", "a"), "TL866CS": ("TL866CS", "tl866a", "a"),
    "TL866II+": ("TL866II Plus", "tl866ii", "ii"), "T48": ("T48", "t48", "ii"),
    "T56": ("T56", "t56", None), "T76": ("T76", "t76", None),
}
_PROGRESS = re.compile(r"\r(?:\x1b\[K)?([^\r\n%]*?)\s*(\d{1,3})%")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x07")


def _tool() -> list[str]:
    override = os.environ.get("AUDIOCHIP_MINIPRO")
    if override:
        return shlex.split(override)
    if sys.platform == "win32":
        exe = HERE / "minipro.exe"
        if not exe.exists():
            raise DriverMissing("The XGecu reader component (minipro.exe) is missing from this installation. "
                                "Run Install.bat again.")
        return [str(exe)]
    found = shutil.which("minipro")
    if not found:
        raise DriverMissing("Reading through an XGecu programmer needs minipro. It is included on Windows.")
    return [found]


def _env() -> dict:
    env = dict(os.environ)
    if (HERE / "infoic.xml").exists():
        env["MINIPRO_HOME"] = str(HERE)
    return env


def _cwd() -> str | None:
    """minipro looks for its chip database in the folder it is started from."""
    return str(HERE) if (HERE / "infoic.xml").exists() else None


def _flags() -> int:
    return 0x08000000 if sys.platform == "win32" else 0        # CREATE_NO_WINDOW


def _clean(text: str) -> str:
    text = _ANSI.sub("", text).replace("\r", "\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def presence() -> tuple[str, str, str | None]:
    """Which programmer is plugged in: (name, -q option, chip-index family)."""
    try:
        p = subprocess.run(_tool() + ["-k"], capture_output=True, text=True, timeout=20, env=_env(),
                           cwd=_cwd(), creationflags=_flags())
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ReaderError(f"The XGecu reader component could not be started: {e}") from None
    out = _clean(p.stderr + "\n" + p.stdout)
    if "No programmer found" in out:
        raise NoDevice("No XGecu programmer was found. Plug it into a USB port and try again. If it is plugged "
                       "in, Windows has no driver for it yet: the driver comes with XGecu's own Xgpro setup, "
                       "which only needs installing once.")
    for line in out.splitlines():
        key = line.split(":")[-1].strip()
        if key in MODELS:
            name, opt, fam = MODELS[key]
            if fam is None:
                raise ReaderError(f"An XGecu {name} is connected. This version reads through the TL866A/CS, "
                                  "TL866II Plus and T48; the T56 and T76 need algorithm files it does not have.")
            return name, opt, fam
    raise ReaderError("The programmer answered, but its model was not recognised:\n" + out[:300])


@lru_cache(maxsize=1)
def _index() -> dict:
    with gzip.open(HERE / "devices.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


def chip_count(family: str) -> int:
    return len(_index().get(family, []))


def search(family: str, text: str, limit: int = 250) -> list[tuple[str, int, bool]]:
    """Memory chips whose name contains every word typed. Returns (name, bytes, has ID)."""
    words = [w for w in re.split(r"[\s,]+", text.upper()) if w]
    if not words:
        return []
    hits = []
    for name, size, _pins, has_id in _index().get(family, []):
        up = name.upper()
        if all(w in up for w in words):
            base = up.split("@")[0]
            rank = (0 if base == words[0] else 1 if base.startswith(words[0]) else 2 if up.startswith(words[0])
                    else 3, 0 if "@" not in up or "@DIP" in up else 1, len(name), name)
            hits.append((rank, name, size, bool(has_id)))
    hits.sort()
    return [(n, s, i) for _, n, s, i in hits[:limit]]


def read(device: str, model_opt: str | None = None, progress: Progress | None = None,
         check_id: bool = False) -> bytes:
    """Read a chip once. `device` is a name from the chip index, e.g. "AM27C020@DIP32".

    check_id=False passes -x, so no ID request is made. On EPROMs an ID request puts 12 V on
    address pin A9, which a plain read never needs.
    """
    tmp = Path(tempfile.mkdtemp(prefix="audiochip_x_")) / "read.bin"
    args = _tool() + ["-p", device, "-r", str(tmp), "-z"] + ([] if check_id else ["-x"])
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=_env(),
                                cwd=_cwd(), creationflags=_flags())
    except OSError as e:
        raise ReaderError(f"The XGecu reader component could not be started: {e}") from None
    captured = bytearray()
    while True:
        chunk = proc.stderr.read(64)
        if not chunk:
            break
        captured += chunk
        if progress:
            last = None
            for last in _PROGRESS.finditer(captured[-200:].decode("latin1")):
                pass
            if last:
                progress(min(int(last.group(2)), 100), 100, "")
    code = proc.wait()
    text = _clean(captured.decode("utf-8", "replace"))
    try:
        if code != 0 or not tmp.exists():
            raise ReaderError(_explain(text, device))
        data = tmp.read_bytes()
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    if not data:
        raise ReaderError("The programmer returned no data.\n" + text[-300:])
    return data


def _explain(text: str, device: str) -> str:
    low = text.lower()
    tail = "\n".join(text.splitlines()[-6:])
    if "no programmer found" in low:
        return "The programmer was not found. Check the USB cable and try again."
    if "bad contact" in low:
        pins = ", ".join(re.findall(r"Bad contact on pin:\s*(\d+)", text))
        return (f"Poor contact on pin {pins}. Nothing was read. Clean the chip's legs, reseat it with the lever "
                "fully down, and try again.")
    if "chip id mismatch" in low or "invalid chip id" in low:
        m = re.search(r"expected (0x[0-9A-Fa-f]+), got (0x[0-9A-Fa-f]+)", text)
        got = f" It answered {m.group(2)}; {device} should answer {m.group(1)}." if m else ""
        return ("The chip's ID does not match the type chosen." + got + " Pick the type printed on the chip, "
                "or untick the ID check to read it anyway.")
    if "overcurrent" in low:
        return ("The programmer cut the power: the chip drew too much current. It is probably in the socket "
                "the wrong way round, or it is the wrong type. Remove it and check before trying again.")
    if "not found" in low and "database" in low or "device" in low and "not found" in low:
        return f"The programmer's database has no chip called {device}."
    return "The read did not finish. The programmer reported:\n" + (tail or "(no message)")
