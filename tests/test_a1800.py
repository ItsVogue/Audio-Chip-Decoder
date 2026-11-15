"""GeneralPlus A1800: toy flash containers and .a18 files, decoded by the bundled helper.

tests/data/a1800/toy_rom.bin is a small made-up flash image: three clips (440, 880 and 660 Hz
tones encoded with the a1800_codec encoder) in the container layout found in a Fisher-Price
flash dump, three headers that break one rule each, and the whole image repeated once.
"""
import hashlib
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

from audiochip import a1800, codecs
from audiochip.codecs import Hypothesis, decode, render
from audiochip.detect import detect
from audiochip.structure import analyse_structure

DATA = Path(__file__).parent / "data" / "a1800"
ROM = (DATA / "toy_rom.bin").read_bytes()
A18 = (DATA / "tone.a18").read_bytes()

needs_helper = pytest.mark.skipif(not a1800.available() and sys.platform != "win32",
                                  reason="no A1800 helper on this machine (set AUDIOCHIP_A1800)")


def _pitch(x):
    mid = x[len(x) // 4: len(x) * 3 // 4].astype(float)
    return np.argmax(np.abs(np.fft.rfft(mid * np.hanning(len(mid))))) * 16000 / len(mid)


def test_helper_is_part_of_the_windows_build():
    if sys.platform == "win32":
        assert a1800.available(), "audiochip/a18helper/a1800.exe is missing"


def test_containers_are_found_by_every_rule_of_the_header():
    clips = a1800.find_containers(ROM)
    assert [(c.offset, c.length, c.kind) for c in clips] == [(512, 1200, 0x42), (1762, 800, 0x40), (2612, 1000, 0x42)]
    assert [c.frames for c in clips] == [30, 20, 25] and abs(clips[0].seconds - 0.6) < 1e-9
    assert a1800.mirrored_half(ROM) and len(a1800.find_containers(ROM[:len(ROM) // 2])) == 3
    unmirrored = ROM[:len(ROM) // 2] + bytes(len(ROM) // 2)
    assert not a1800.mirrored_half(unmirrored) and len(a1800.find_containers(unmirrored)) == 3
    twice = ROM[:len(ROM) // 2] + b"\x00" + ROM[len(ROM) // 2:-1]          # not a mirror: both copies count
    assert len(a1800.find_containers(twice)) == 6
    for bad in (b"", b"\xff\x42\x00\xe0\x2e", b"\xff\x41\x00\xe0\x2e" + struct.pack("<II", 40, 320) + bytes(40),
                b"\xff\x42\x01\xe0\x2e" + struct.pack("<II", 40, 320) + bytes(40),
                b"\xff\x42\x00\xe0\x2f" + struct.pack("<II", 40, 320) + bytes(40),
                b"\xff\x42\x00\xe0\x2e" + struct.pack("<II", 0, 320) + bytes(40),
                b"\xff\x42\x00\xe0\x2e" + struct.pack("<II", 80, 320) + bytes(79)):
        assert a1800.find_containers(bad) == []


def test_a18_file_header_must_agree_with_the_file():
    c = a1800.a18_file(A18)
    assert c and (c.start, c.length, c.bitrate, c.frames) == (6, 1200, 16000, 30)
    assert a1800.a18_file(A18[:-40]) is None and a1800.a18_file(A18 + bytes(100)) is None
    assert a1800.a18_file(struct.pack("<IH", 1200, 16001) + A18[6:]) is None
    assert a1800.a18_file(struct.pack("<IH", 1199, 16000) + A18[6:-1]) is None
    assert a1800.a18_file(bytes(2000)) is None and a1800.a18_file(b"") is None


@needs_helper
def test_toy_flash_dump_is_recognised_and_every_clip_decoded():
    det = detect(ROM, analyse_structure(ROM))
    assert det.best.hyp.codec == "a1800" and det.confidence == "high"
    assert "3 GeneralPlus A1800 containers" in det.best.evidence
    r = render(ROM, det.best.hyp)
    assert r.rate == 16000 and "scanned once" in r.note
    assert [s.offset for s in r.sounds] == [512, 1762, 2612]
    assert [s.end - s.start for s in r.sounds] == [30 * 320, 20 * 320, 25 * 320]       # frames x 320, exactly
    assert [s.label for s in r.sounds] == ["Clip 1 (type 42)", "Clip 2 (type 40)", "Clip 3 (type 42)"]
    for s, hz in zip(r.sounds, (440, 880, 660)):
        clip = r.samples[s.start:s.end]
        assert abs(_pitch(clip) - hz) < 8 and np.abs(clip).max() > 6000
    # integer arithmetic throughout: every build of the decoder must give these exact samples
    assert hashlib.sha256(r.samples.astype("<i2").tobytes()).hexdigest().startswith("500f29dc7913e5e8")


@needs_helper
def test_a18_file_is_recognised_and_decoded():
    det = detect(A18, analyse_structure(A18))
    assert det.best.hyp.codec == "a1800" and det.confidence == "high"
    r = render(A18, det.best.hyp)
    assert r.rate == 16000 and len(r.samples) == 9600 and not r.sounds and abs(_pitch(r.samples) - 440) < 8
    assert hashlib.sha256(r.samples.astype("<i2").tobytes()).hexdigest().startswith("63e4f08fa386f216")


@needs_helper
def test_damaged_dumps_never_raise():
    rng = np.random.default_rng(4)
    cases = [ROM[:n] for n in (1, 12, 513, 525, 600, 1761, 3000)] + [A18[:n] for n in (5, 6, 46, 600)]
    for _ in range(25):
        bad = bytearray(ROM[:4096])
        for _ in range(int(rng.integers(1, 40))):
            bad[int(rng.integers(0, len(bad)))] = int(rng.integers(0, 256))
        cases.append(bytes(bad))
    for data in cases:
        det = detect(data, analyse_structure(data))
        assert det.candidates is not None
        x = decode(data, Hypothesis("a1800"))
        assert x.ndim == 2 and x.dtype == np.int16


def test_ordinary_dumps_are_not_taken_for_a1800(voice):
    from conftest import encode
    for codec in ("u8", "s16le", "oki_hi", "mulaw_std"):
        data = encode(voice, codec)
        assert a1800.find_clips(data) == []
        assert detect(data, analyse_structure(data)).best.hyp.codec != "a1800"
    r = render(encode(voice, "u8"), Hypothesis("a1800"))
    assert r.samples.size == 0 and "No GeneralPlus A1800" in r.note


def test_missing_or_broken_helper_is_explained_not_fatal(monkeypatch, tmp_path):
    for value, words in (("", "missing"), (f'"{(tmp_path / "nothing-here").as_posix()}"', "could not")):
        monkeypatch.setenv("AUDIOCHIP_A1800", value)
        codecs._cache.clear()
        det = detect(ROM, analyse_structure(ROM))
        assert det.best is None or det.best.hyp.codec != "a1800"
        assert words in det.find(Hypothesis("a1800")).note
        r = render(ROM, Hypothesis("a1800"))
        assert r.samples.size == 0 and r.note
    codecs._cache.clear()


@needs_helper
def test_a_clip_with_invalid_frames_keeps_its_good_frames():
    good = A18[6:]
    broken = good[:400] + bytes(80) + good[480:]                 # two all-zero frames, which the decoder rejects
    with pytest.raises(a1800.HelperError):
        a1800.decode_frames(broken)
    x, bad = a1800.decode_tolerant(broken)
    assert bad == 2 and len(x) == 9600
    assert not x[3200:3840].any() and abs(_pitch(x[5000:9600]) - 440) < 12 and np.abs(x[:3000]).max() > 6000
    rom = bytearray(ROM[:len(ROM) // 2])
    rom[512 + 13 + 400:512 + 13 + 480] = bytes(80)
    r = render(bytes(rom), Hypothesis("a1800"))
    assert len(r.sounds) == 3 and "2 invalid frame(s) were left silent" in r.note
    assert a1800.decode_tolerant(bytes(40))[1] == 1 and len(a1800.decode_tolerant(bytes(40))[0]) == 320
