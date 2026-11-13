"""Regression against the real Jaws 93 EPROM set and the WAV hashes from the original conversion.

Skipped unless AUDIOCHIP_JAWS_DIR (the extracted archive) and AUDIOCHIP_JAWS_MANIFEST are set.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from audiochip.codecs import Hypothesis, decode
from audiochip.collection import build, track_audio
from audiochip.export import wav_bytes

ROOT = os.environ.get("AUDIOCHIP_JAWS_DIR")
MANIFEST = os.environ.get("AUDIOCHIP_JAWS_MANIFEST")
pytestmark = pytest.mark.skipif(not (ROOT and MANIFEST), reason="Jaws test data not configured")
MULAW = Hypothesis("mulaw_inv")


@pytest.fixture(scope="module")
def manifest():
    return json.loads(Path(MANIFEST).read_text())["files"]


@pytest.fixture(scope="module")
def coll():
    return build([ROOT])


def test_every_single_chip_wav_hash_is_reproduced(manifest):
    entries = [e for e in manifest if e["wav"].startswith("Individual") and "HEX" not in e["source"]]
    assert len(entries) == 72
    for e in entries:
        data = (Path(ROOT) / e["source"].replace("\\", "/")).read_bytes()
        assert hashlib.sha256(wav_bytes(decode(data, MULAW), 32000)).hexdigest() == e["wav_sha256"], e["source"]


def test_detection_is_unaided_and_confident(coll):
    assert coll.hyp == MULAW and coll.detection.confidence == "high"
    assert not any(t.hyp_override for t in coll.tracks)


def test_speed_estimate_puts_32k_first(coll):
    assert coll.rate_estimate.informed and coll.rate == 32000
    assert 32000 in coll.rate_estimate.top(3)


def test_every_joined_track_hash_is_reproduced(coll, manifest):
    joined = {Path(e["wav"]).stem: e for e in manifest if e["wav"].startswith("Continuous")}
    assert len(joined) == 13 == len(coll.tracks)
    for t in coll.tracks:
        e = joined[t.name]
        assert " + ".join(c.name for c in t.members) == e["source"], t.name
        got = hashlib.sha256(wav_bytes(track_audio(coll, t, trim=False), 32000)).hexdigest()
        assert got == e["wav_sha256"], t.name


def test_copies_are_left_out(coll):
    roles = {(t.name, c.name): c.role for t in coll.tracks for c in t.chips}
    assert roles[("Scene 1 Dialog", "combined.bin")] == "prejoined"
    assert roles[("Scene 5 Music", "1 16 bits.BIN")] == "duplicate"
    assert roles[("Show Engine", "4 non flash.BIN")] == "duplicate"
    assert roles[("Scene 3 Music", "1.HEX")] == "duplicate"
    assert not any(t.warnings for t in coll.tracks), [t.warnings for t in coll.tracks if t.warnings]
