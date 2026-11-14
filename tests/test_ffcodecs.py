"""Formats decoded through the bundled FFmpeg helper.

tests/data/ff holds one-second files of a 440 Hz tone made with encoders other than the
decoder under test where one exists: Speex by libspeex itself, AAC / WMA / ADX / ALAC /
Yamaha / G.723.1 / G.726 by FFmpeg's encoders. sample_nb.amr and sample_wb.amr are real
AMR recordings from the Android media library's test assets (Apache License 2.0).
"""
import glob
import os
import sys
import tempfile
import threading
from pathlib import Path

import numpy as np
import pytest

from audiochip import codecs, ffcodecs as ff
from audiochip.codecs import Hypothesis, decode, render
from audiochip.detect import detect
from audiochip.structure import analyse_structure

DATA = Path(__file__).parent / "data" / "ff"

if not ff.available():
    if sys.platform == "win32":
        raise RuntimeError("audiochip/ffhelper/ffmpeg.exe is missing: the Windows build must carry it")
    pytestmark = pytest.mark.skip(reason="no FFmpeg helper on this machine (set AUDIOCHIP_FFMPEG)")


def _load(name):
    return (DATA / name).read_bytes()


def _pitch(x, rate):
    x = (x if x.ndim == 1 else x[:, 0]).astype(float)
    mid = x[len(x) // 4: len(x) * 3 // 4]
    spec = np.abs(np.fft.rfft(mid * np.hanning(len(mid))))
    return np.argmax(spec) * rate / len(mid)


def _leftovers():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "audiochip_ff_*")))


TONES = [  # file, what it is called, FFmpeg's name for the audio, rate, channels
    ("tone.aac", "AAC (ADTS) stream", "AAC", 22050, 1),
    ("tone.m4a", "MP4 / M4A / 3GP file", "AAC", 22050, 1),
    ("tone_stereo.m4a", "MP4 / M4A / 3GP file", "AAC", 22050, 2),
    ("tone_alac.m4a", "MP4 / M4A / 3GP file", "Apple Lossless", 8000, 1),
    ("tone.wma", "WMA (ASF) file", "WMA 2", 22050, 1),
    ("tone_v1.wma", "WMA (ASF) file", "WMA 1", 22050, 1),
    ("tone.adx", "CRI ADX file", "CRI ADX ADPCM", 16000, 1),
    ("tone.spx", "Ogg Speex file", "Speex", 8000, 1),
    ("tone_wb.spx", "Ogg Speex file", "Speex", 16000, 1),
    ("tone_yamaha.wav", "WAV file", "Yamaha ADPCM", 8000, 1),
    ("tone_g723_1.wav", "WAV file", "G.723.1", 8000, 1),
]


@pytest.mark.parametrize("name, kind, codec, rate, channels", TONES)
def test_file_is_recognised_and_decoded(name, kind, codec, rate, channels):
    data = _load(name)
    before = _leftovers()
    det = detect(data, analyse_structure(data))
    assert det.best.hyp.codec == "ffaudio" and det.confidence == "high", det.reason
    assert kind in det.best.evidence and codec in det.best.evidence
    r = render(data, det.best.hyp)
    assert r.rate == rate and "FFmpeg" in r.note
    assert (r.samples.ndim == 1) == (channels == 1)
    assert 0.85 * rate <= len(r.samples) <= 1.15 * rate
    assert abs(_pitch(r.samples, rate) - 440) < 12
    assert np.abs(r.samples).max() > 5000
    x = decode(data, det.best.hyp)
    assert x.shape == (len(r.samples), channels)
    assert _leftovers() == before, "a temporary file was left behind"


@pytest.mark.parametrize("name, kind, rate, seconds", [("sample_nb.amr", "AMR-NB", 8000, 4.36),
                                                       ("sample_wb.amr", "AMR-WB", 16000, 3.38)])
def test_real_amr_recordings_decode(name, kind, rate, seconds):
    data = _load(name)
    det = detect(data, analyse_structure(data))
    assert det.best.hyp.codec == "ffaudio" and det.confidence == "high" and kind in det.best.evidence
    r = render(data, det.best.hyp)
    assert r.rate == rate and abs(len(r.samples) / rate - seconds) < 0.05
    assert np.abs(r.samples).max() > 5000


def test_amr_frames_without_the_file_header_are_recognised_by_their_frame_chain():
    whole = _load("sample_nb.amr")
    bare = whole[6:]
    found = ff.headerless_amr(bare)
    assert found and found.prefix == b"#!AMR\n" and found.length == len(bare)
    det = detect(bare, analyse_structure(bare))
    assert det.best.hyp.codec == "ffaudio" and det.confidence == "medium"       # no signature: never "high"
    assert np.array_equal(render(bare, det.best.hyp).samples, render(whole, det.best.hyp).samples)
    assert ff.headerless_amr(bytes(13 * 200)) is None                           # blank bytes chain too
    assert ff.headerless_amr(np.random.default_rng(1).integers(0, 256, 4000, dtype=np.uint8).tobytes()) is None


def test_headerless_g726_16_is_offered_by_hand_only():
    for name, codec in (("tone_g726_16.raw", "g726_16"), ("tone_g726le_16.raw", "g726le_16")):
        data = _load(name)
        assert not codecs.CODECS[codec].auto
        assert detect(data, analyse_structure(data)).best.hyp.codec not in (codec, "ffaudio")
        x = decode(data, Hypothesis(codec))
        assert x.shape[1] == 1 and abs(len(x) - 4 * len(data)) <= 64
        assert abs(_pitch(x, 8000) - 440) < 12


def test_files_inside_a_dump_are_found_and_listed_as_sounds():
    parts = [_load("tone.aac"), _load("tone.adx"), _load("sample_nb.amr"), _load("tone.wma"), _load("tone.spx")]
    dump, offsets = bytearray(b"\xff" * 300), []
    for p in parts:
        offsets.append(len(dump))
        dump += p + b"\xff" * 257
    dump = bytes(dump)
    found = ff.find_files(dump)
    assert [f.offset for f in found] == offsets
    assert [f.length for f in found] == [len(p) for p in parts]
    det = detect(dump, analyse_structure(dump))
    assert det.best.hyp.codec == "ffaudio" and det.confidence == "medium"
    assert "5 audio files" in det.best.evidence
    r = render(dump, det.best.hyp)
    assert [s.offset for s in r.sounds] == offsets and len(r.sounds) == 5
    assert r.rate == 22050 and "do not all share one sample rate" in r.note
    first = r.samples[r.sounds[0].start:r.sounds[0].end]
    assert abs(_pitch(first, r.rate) - 440) < 12
    adx = r.samples[r.sounds[1].start:r.sounds[1].end]                           # 16 kHz file, converted
    assert abs(_pitch(adx, r.rate) - 440) < 12 and abs(len(adx) / r.rate - 0.96) < 0.1


def test_one_small_file_inside_a_large_dump_is_reported_but_not_chosen(voice):
    from conftest import encode
    dump = encode(voice, "u8") + _load("tone.aac") + encode(voice[:20000], "u8")
    det = detect(dump, analyse_structure(dump))
    assert det.best.hyp.codec in ("u8", "s8")                  # a plain 8-bit reading, as before this format existed
    c = det.find(Hypothesis("ffaudio"))
    assert c is not None and not c.evidence and "not chosen automatically" in c.note
    r = render(dump, Hypothesis("ffaudio"))
    assert r.rate == 22050 and abs(_pitch(r.samples, 22050) - 440) < 12
    assert "occupies" in r.note


def test_formats_the_built_in_reader_handles_stay_with_it(voice):
    import io
    sf = pytest.importorskip("soundfile")
    for fmt, sub in (("WAV", "PCM_16"), ("WAV", "IMA_ADPCM"), ("FLAC", "PCM_16"), ("OGG", "VORBIS")):
        buf = io.BytesIO()
        sf.write(buf, voice[:8000], 16000, format=fmt, subtype=sub)
        data = buf.getvalue()
        assert ff.find_files(data) == [], (fmt, sub)
        assert detect(data, analyse_structure(data)).best.hyp.codec == "audiofile"


def test_a_signature_with_nothing_decodable_behind_it_is_not_offered():
    rng = np.random.default_rng(5)
    junk = rng.integers(0, 256, 3000, dtype=np.uint8).tobytes()
    for data in (ff._ASF + junk, b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00" + junk, _load("tone.wma")[:400],
                 _load("tone.m4a")[:60], b"RIFF\x40\x00\x00\x00QLCM" + junk, b"SCHl\x10\x00\x00\x00" + junk,
                 b"BIKi" + junk, b"OggS" + bytes(24) + b"Speex   " + junk):
        det = detect(data, analyse_structure(data))
        assert det.best is None or det.best.hyp.codec != "ffaudio"
        r = render(data, Hypothesis("ffaudio"))
        assert r.samples.size == 0 and r.note


@pytest.mark.parametrize("name", [t[0] for t in TONES] + ["sample_nb.amr", "sample_wb.amr"])
def test_damaged_files_never_raise(name):
    good = _load(name)
    rng = np.random.default_rng(len(good))
    cases = [good[:n] for n in (1, 7, 33, 64, 200, len(good) // 2, len(good) - 1)]
    for _ in range(6):
        bad = bytearray(good)
        for _ in range(int(rng.integers(1, 12))):
            bad[int(rng.integers(0, len(bad)))] = int(rng.integers(0, 256))
        cases.append(bytes(bad))
    bad = bytearray(good)
    bad[40:60] = b"\xff" * 20
    cases += [bytes(bad), good + good[:100], good[5:]]
    before = _leftovers()
    for data in cases:
        det = detect(data, analyse_structure(data))
        assert det.candidates is not None
        x = decode(data, Hypothesis("ffaudio"))
        assert x.ndim == 2 and x.dtype == np.int16
    assert _leftovers() == before


def test_missing_helper_is_explained_not_fatal(monkeypatch):
    data = _load("tone.aac")
    monkeypatch.setenv("AUDIOCHIP_FFMPEG", "")
    codecs._cache.clear()
    ff._info_cache.clear()
    assert not ff.available()
    det = detect(data, analyse_structure(data))
    assert det.best.hyp.codec != "ffaudio"
    assert "helper is missing" in det.find(Hypothesis("ffaudio")).note
    r = render(data, Hypothesis("ffaudio"))
    assert r.samples.size == 0 and "missing" in r.note
    assert decode(_load("tone_g726_16.raw"), Hypothesis("g726_16")).size == 0
    codecs._cache.clear()


def test_helper_that_cannot_start_is_explained_not_fatal(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIOCHIP_FFMPEG", f'"{(tmp_path / "no-such-program").as_posix()}"')
    codecs._cache.clear()
    ff._info_cache.clear()
    data = _load("tone.adx")
    assert detect(data, analyse_structure(data)).best.hyp.codec != "ffaudio"
    r = render(data, Hypothesis("ffaudio"))
    assert r.samples.size == 0 and "could not be started" in r.note
    codecs._cache.clear()


def _stuck_helper(monkeypatch, tmp_path):
    script = tmp_path / "stuck.py"
    script.write_text("import sys, time\nsys.stdout.write('x')\nsys.stdout.flush()\ntime.sleep(60)\n")
    exe = sys.executable.replace("\\", "/")
    monkeypatch.setenv("AUDIOCHIP_FFMPEG", f'"{exe}" "{script.as_posix()}"')


def test_helper_that_hangs_is_stopped_at_the_time_limit(monkeypatch, tmp_path):
    import time
    _stuck_helper(monkeypatch, tmp_path)
    start = time.monotonic()
    with pytest.raises(ff.HelperError, match="did not finish"):
        ff._run(["-i", "x"], 1 << 20, 1.0)
    assert time.monotonic() - start < 20


def test_cancel_stops_the_helper(monkeypatch, tmp_path):
    import time
    _stuck_helper(monkeypatch, tmp_path)
    result = {}

    def work():
        try:
            ff._run(["-i", "x"], 1 << 20, 60.0)
        except BaseException as e:           # noqa: BLE001
            result["error"] = e

    t = threading.Thread(target=work)
    codecs.cancel.clear()
    start = time.monotonic()
    t.start()
    time.sleep(0.5)
    codecs.cancel.set()
    t.join(20)
    codecs.cancel.clear()
    assert not t.is_alive() and isinstance(result.get("error"), codecs.Cancelled)
    assert time.monotonic() - start < 20


def test_very_long_audio_is_cut_at_the_memory_limit(monkeypatch):
    data = _load("tone.adx")
    monkeypatch.setattr(ff, "MAX_OUTPUT", 8000)
    codecs._cache.clear()
    r = ff.render_files(np.frombuffer(data, dtype=np.uint8))
    assert 0 < len(r.samples) < 16000 and "cut short" in r.note
    codecs._cache.clear()


def test_length_rules_for_each_container():
    aac = _load("tone.aac")
    assert ff._adts_chain(aac, 0)[1] == len(aac)
    amr = _load("sample_nb.amr")
    assert ff._amr_chain(amr, 6, ff._AMR_NB)[1] == len(amr)
    assert ff._amr_chain(_load("sample_wb.amr"), 9, ff._AMR_WB)[1] == len(_load("sample_wb.amr"))
    for name in ("tone.m4a", "tone.wma", "tone.adx", "tone.spx"):
        d = _load(name)
        f = ff._at(d + b"\xff" * 500, 0)
        assert f is not None and f.length == len(d), name
    assert ff._at(b"\x00" * 64, 0) is None and ff._at(b"", 0) is None and ff.find_files(b"") == []


def test_wav_stream_parser_rejects_anything_unexpected():
    for raw in (b"", b"RIFF", b"RIFF\x00\x00\x00\x00WAVE", b"RIFF\xff\xff\xff\xffWAVEdata\x00\x00\x00\x00\x01\x02",
                b"RIFF\xff\xff\xff\xffWAVEfmt \x10\x00\x00\x00\x03\x00\x01\x00\x40\x1f\x00\x00" + bytes(8)):
        with pytest.raises(ff.HelperError):
            ff._parse_wav(raw)
