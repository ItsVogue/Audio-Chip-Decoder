# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""Check whether a newer version has been published.

Asks GitHub for the latest release of the project, at most once a day, and
only compares version numbers. Nothing about the PC or the user is sent.
Any failure (no internet, private repository, no releases yet) is silent.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

REPO = "ItsVogue/Audio-Chip-Decoder"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"


def parse(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version)[:4])


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse(candidate), parse(current)
    return bool(a) and a > b


def latest_release(timeout: float = 6.0) -> tuple[str, str] | None:
    """(version, page to download it from), or None if it could not be found out."""
    if os.environ.get("AUDIOCHIP_NO_UPDATE_CHECK"):
        return None
    try:
        req = urllib.request.Request(API, headers={"User-Agent": "AudioChipDecoder",
                                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            info = json.loads(r.read().decode("utf-8"))
        tag = str(info.get("tag_name") or "")
        url = str(info.get("html_url") or PAGE)
        if not tag or not url.startswith("https://github.com/"):
            return None
        return tag.lstrip("vV"), url
    except Exception:
        return None
