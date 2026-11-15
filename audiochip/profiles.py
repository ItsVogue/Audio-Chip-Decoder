# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""Device profiles: a confirmed reading and speed for one kind of equipment.

Once one chip from a machine has been decoded and confirmed by ear, the
same settings apply to every other chip from that machine.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .codecs import CODECS, LAYOUTS, Hypothesis


@dataclass
class Profile:
    name: str
    codec: str
    layout: str = "normal"
    channels: int = 1
    rate: int = 0
    notes: str = ""
    builtin: bool = False

    @property
    def hyp(self) -> Hypothesis:
        return Hypothesis(self.codec, self.layout, self.channels)

    def valid(self) -> bool:
        return self.codec in CODECS and self.layout in LAYOUTS and self.channels in (1, 2) and self.rate > 0


BUILTIN = [
    Profile("8-bit inverted mu-law, 32 kHz", "mulaw_inv", "normal", 1, 32000,
            "One byte per sample, mu-law with inverted storage, mono, 32,000 Hz.", builtin=True),
    Profile("8-bit unsigned, 8 kHz", "u8", "normal", 1, 8000,
            "One byte per sample, unsigned, mono, 8,000 Hz. Common on small voice boards.", builtin=True),
    Profile("OKI ADPCM, 8 kHz", "oki_hi", "normal", 1, 8000,
            "4-bit OKI ADPCM, high nibble first, 8,000 Hz. Common on OKI MSM voice chips.", builtin=True),
]


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "AudioChipDecoder"


def _file() -> Path:
    new = config_dir() / "profiles.json"
    old = config_dir().with_name("Chip" + "AudioDecoder") / "profiles.json"     # the app's earlier name
    if not new.exists() and old.exists():
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            new.write_bytes(old.read_bytes())
        except OSError:
            pass
    return new


def load_user() -> list[Profile]:
    try:
        raw = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for item in raw if isinstance(raw, list) else []:
        try:
            p = Profile(**{k: item[k] for k in ("name", "codec", "layout", "channels", "rate", "notes") if k in item})
        except TypeError:
            continue
        if p.valid():
            out.append(p)
    return out


def load_all() -> list[Profile]:
    return BUILTIN + load_user()


def save_user(profile: Profile) -> None:
    profiles = [p for p in load_user() if p.name != profile.name] + [profile]
    _file().parent.mkdir(parents=True, exist_ok=True)
    data = [{k: v for k, v in asdict(p).items() if k != "builtin"} for p in profiles]
    _file().write_text(json.dumps(data, indent=2), encoding="utf-8")


def delete_user(name: str) -> None:
    profiles = [p for p in load_user() if p.name != name]
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps([{k: v for k, v in asdict(p).items() if k != "builtin"} for p in profiles],
                                  indent=2), encoding="utf-8")


def find(name: str) -> Profile | None:
    for p in load_all():
        if p.name.lower() == name.lower():
            return p
    for p in load_all():
        if name.lower() in p.name.lower():
            return p
    return None
