"""Codecs added in 0.5.0. The original decoders are covered by the other test files, unchanged."""
import io
import struct

import numpy as np
import pytest

from audiochip import framing, morecodecs as mc
from audiochip.codecs import CODECS, Hypothesis, decode, render
from audiochip.collection import build, track_audio
from audiochip.detect import auto_hypotheses, detect
from audiochip.export import ExportOptions, audio_bytes, export
from audiochip.structure import analyse_structure

sf = pytest.importorskip("soundfile")


def _detect(data):
    return detect(data, analyse_structure(data))


def _file(x, rate, fmt, subtype=None):
    buf = io.BytesIO()
    sf.write(buf, x, rate, format=fmt, subtype=subtype)
    return buf.getvalue()


@pytest.mark.parametrize("fmt,subtype,exact", [
    ("WAV", "PCM_16", True), ("WAV", "PCM_24", True), ("WAV", "PCM_U8", False), ("WAV", "MS_ADPCM", False),
    ("WAV", "IMA_ADPCM", False), ("WAV", "ULAW", False), ("WAV", "GSM610", False), ("WAV", "G721_32", False),
    ("FLAC", "PCM_16", True), ("OGG", "VORBIS", False), ("OGG", "OPUS", False), ("MP3", None, False),
    ("AIFF", "PCM_16", True), ("AU", "G723_24", False), ("CAF", "ALAC_16", True), ("VOC", "PCM_U8", False)])
def test_audio_files_are_recognised_and_decoded_at_their_own_rate(fmt, subtype, exact, voice):
    rate = 8000
    data = _file(voice[:32000], rate, fmt, subtype)
    det = _detect(data)
    assert det.best.hyp == Hypothesis("audiofile") and det.confidence == "high", (fmt, subtype, det.reason)
    r = render(data, det.best.hyp)
    assert r.rate == rate and abs(len(r.samples) - 32000) <= 3000
    if exact:
        assert np.array_equal(r.samples[:32000], voice[:32000])
    elif fmt not in ("OGG", "MP3"):                         # lossy transforms shift the signal in time
        n = min(len(r.samples), 32000)
        assert np.corrcoef(r.samples[400:n].astype(float), voice[400:n].astype(float))[0, 1] > 0.97


def test_a_stereo_file_keeps_both_channels_through_the_collection(tmp_path, voice, music):
    stereo = np.stack([voice[:24000], music[:24000]], axis=1)
    (tmp_path / "Song").mkdir()
    (tmp_path / "Song" / "song.flac").write_bytes(_file(stereo, 22050, "FLAC", "PCM_16"))
    coll = build([tmp_path])
    t = coll.tracks[0]
    assert coll.hyp == Hypothesis("audiofile") and coll.rate == 22050 and t.rate_stated == 22050
    assert coll.hyp.label().startswith("Audio file") and "mono" not in coll.hyp.label()
    x = track_audio(coll, t)
    assert x.shape == (24000, 2) and np.array_equal(x, stereo)
    m = export(coll, tmp_path / "out", ExportOptions(fmt="ogg"))
    y, r = sf.read(str(tmp_path / "out" / m["files"][0]["wav"]), dtype="int16")
    assert r == 22050 and y.shape[1] == 2


@pytest.mark.parametrize("codec,bps", [("g721", 0.5), ("g723_24", 0.375), ("g723_40", 0.625), ("gsm610", 33 / 160)])
def test_headerless_itu_and_gsm_streams_round_trip(codec, bps, voice):
    pcm = voice[:32000]
    data = mc.encode(codec, pcm)
    assert abs(len(data) - len(pcm) * bps) <= 40                                      # frame alignment
    y = decode(data, Hypothesis(codec))[:, 0]
    assert abs(len(y) - len(pcm)) <= 160
    n = min(len(y), len(pcm))
    assert np.corrcoef(y[400:n].astype(float), pcm[400:n].astype(float))[0, 1] > 0.95
    assert not CODECS[codec].auto and Hypothesis(codec) not in auto_hypotheses()


def test_g721_in_the_other_nibble_order_needs_the_swapped_layout(voice):
    data = mc.encode("g721", voice[:16000])
    swapped = bytes(((v << 4) | (v >> 4)) & 255 for v in data)
    good = decode(swapped, Hypothesis("g721", "nibswap"))[:, 0].astype(float)
    assert np.corrcoef(good[400:16000], voice[400:16000].astype(float))[0, 1] > 0.95


@pytest.mark.parametrize("cid,dtype,shift", [("s24le", "<i4", 8), ("s24be", ">i4", 8), ("s32le", "<i4", 16),
                                               ("s32be", ">i4", 16), ("u24le", "<u4", 8), ("u32be", ">u4", 16)])
def test_wide_raw_pcm_keeps_the_top_sixteen_bits(cid, dtype, shift, voice):
    v = voice[:4000].astype(np.int64) << shift
    if cid.startswith("u"):
        v = v + (1 << (shift + 15))
    raw = v.astype(dtype).tobytes()
    if "24" in cid:                                         # drop the unused byte of each 32-bit value
        rows = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 4)
        raw = (rows[:, 1:] if dtype[0] == ">" else rows[:, :3]).tobytes()
    assert np.array_equal(decode(raw, Hypothesis(cid))[:, 0], voice[:4000])
    assert Hypothesis(cid) not in auto_hypotheses()


def _psx_block(samples, shift, flag=0):
    codes = [max(-8, min(7, int(round(s / (1 << (12 - shift)))))) & 15 for s in samples]
    return bytes([shift, flag]) + bytes(codes[i] | (codes[i + 1] << 4) for i in range(0, 28, 2))


def test_playstation_adpcm_decodes_splits_and_reads_the_vag_header():
    t = np.arange(28 * 120)
    wave = np.sin(t / 11.0) * 6000
    body = b"".join(_psx_block(wave[i:i + 28], 1, flag=1 if i // 28 in (59, 119) else 0)
                    for i in range(0, len(wave), 28))
    samples, spans = mc.psx_decode(body)
    assert spans == [(0, 28 * 60), (28 * 60, 28 * 120)]
    assert np.corrcoef(samples.astype(float), wave)[0, 1] > 0.98
    # filter 1 adds 60/64 of the previous sample: code +1 at shift 0 is 4096, then 4096 + 3840, ...
    one = bytes([0x10, 0]) + bytes([0x11] + [0] * 13)
    assert mc.psx_decode(one)[0][:3].tolist() == [4096, 4096 + 3840, (7936 * 60 + 32) >> 6]
    vag = b"VAGp" + struct.pack(">3I", 0x20, 0, len(body)) + struct.pack(">I", 22050) + bytes(12) \
        + b"door".ljust(16, b"\x00") + body
    det = _detect(vag)
    assert det.best.hyp == Hypothesis("vag") and det.confidence == "high"
    r = render(vag, Hypothesis("vag"))
    assert r.rate == 22050 and len(r.sounds) == 2 and "door" in r.note
    raw = _detect(body)
    assert raw.best.hyp == Hypothesis("vag") and raw.confidence == "medium" and "legal" in raw.reason


def test_new_formats_do_not_claim_other_data(voice):
    from conftest import encode
    noise = np.random.default_rng(3).integers(0, 256, 120000, dtype=np.uint8).tobytes()
    for data in (noise, encode(voice, "u8"), encode(voice, "mulaw_inv"), encode(voice, "oki_hi")):
        det = _detect(data)
        assert det.best is None or det.best.hyp.codec not in ("audiofile", "vag")
        assert not any(c.evidence for c in det.candidates if c.hyp.codec in ("audiofile", "vag"))


def test_framed_unknown_data_is_reported_without_guessing_a_decoder():
    rng = np.random.default_rng(5)
    frames = b"".join(bytes([0x42, 0x40]) + rng.integers(0, 256, 40, dtype=np.uint8).tobytes()
                      for _ in range(900))
    size, fixed, share = framing.frame_period(frames)
    assert size == 42 and fixed[:2] == [(0, 0x42), (1, 0x40)] and share > 0.99
    det = _detect(frames)
    assert det.confidence == "none" and "frames of 42 bytes" in det.reason and "no decoder" in det.reason.lower()
    assert framing.frame_period(rng.integers(0, 256, 60000, dtype=np.uint8).tobytes()) is None


@pytest.mark.parametrize("fmt,rate,out_rate", [("ogg", 7575, 7575), ("opus", 7575, 8000), ("opus", 12000, 12000)])
def test_ogg_and_opus_export(fmt, rate, out_rate, voice):
    blob, wrote = audio_bytes(voice[:24000], rate, fmt)
    assert wrote == out_rate and blob[:4] == b"OggS"
    y, r = sf.read(io.BytesIO(blob), dtype="int16")
    assert abs(len(y) / r - 24000 / rate) < 0.15
    assert r in (out_rate, 48000)                           # Opus files always report 48 kHz on reading


def test_capability_table_only_claims_encoding_where_an_encoder_exists(voice):
    for cid, (_name, can_decode, can_encode, _how) in mc.CAPABILITIES.items():
        assert can_decode
        if cid in ("g721", "g723_24", "g723_40", "gsm610"):
            assert can_encode and mc.encode(cid, voice[:1600])
    with pytest.raises(ValueError):
        mc.encode("vag", voice[:1600])
    assert not mc.CAPABILITIES["vag"][2] and not mc.CAPABILITIES["pcm_wide"][2]
