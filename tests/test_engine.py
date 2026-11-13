import io
import json
import wave

import numpy as np
import pytest

from audiochip.codecs import CODECS, Hypothesis, decode
from audiochip.collection import build, move_chip, track_audio
from audiochip.detect import detect
from audiochip.export import ExportOptions, export, wav_bytes
from audiochip.ingest import DumpError, load_dump, parse_intel_hex, parse_srec
from audiochip.rate import estimate, measure
from audiochip.structure import analyse_structure
from conftest import encode

SIMPLE = [c.id for c in CODECS.values() if c.family != "adpcm"]


def _detect(data):
    st = analyse_structure(data)
    return detect(data, st), st


def _same_or_twin(found: Hypothesis, want: Hypothesis) -> bool:
    return found == want or found.is_twin_of(want)


@pytest.mark.parametrize("signal", ["voice", "music"])
@pytest.mark.parametrize("codec", SIMPLE)
def test_detects_every_simple_encoding(codec, signal, request):
    x = request.getfixturevalue(signal)
    det, _ = _detect(encode(x, codec))
    assert det.confidence in ("high", "medium"), det.reason
    assert _same_or_twin(det.best.hyp, Hypothesis(codec)), det.best.hyp.key


@pytest.mark.parametrize("codec,layout", [("u8", "bitrev"), ("mulaw_std", "bitrev"), ("s8", "nibswap"),
                                          ("mulaw_inv", "nibswap")])
def test_detects_scrambled_byte_layouts(codec, layout, music):
    det, _ = _detect(encode(music, codec, layout))
    assert _same_or_twin(det.best.hyp, Hypothesis(codec, layout)), det.best.hyp.key


@pytest.mark.parametrize("codec", ["u8", "mulaw_inv", "s16le"])
def test_detects_two_interleaved_channels(codec, voice, music):
    n = min(len(voice), len(music))
    stereo = np.stack([voice[:n], music[:n]], axis=1)
    det, _ = _detect(encode(stereo, codec, channels=2))
    assert _same_or_twin(det.best.hyp, Hypothesis(codec, "normal", 2)), det.best.hyp.key


@pytest.mark.parametrize("codec", ["oki_hi", "oki_lo"])
def test_detects_oki_adpcm(codec, voice):
    det, _ = _detect(encode(voice, codec))
    assert det.best.hyp.codec == codec, [(c.hyp.key, c.leak, c.note) for c in det.candidates[:4]]
    assert det.confidence != "none"


def test_random_bytes_are_not_called_audio():
    data = np.random.default_rng(7).integers(0, 256, 200000, dtype=np.uint8).tobytes()
    det, _ = _detect(data)
    assert det.confidence == "none"


def test_blank_chip_is_reported():
    det, st = _detect(b"\xFF" * 65536)
    assert st.blank and not det.candidates


def test_decode_round_trip_is_close(voice):
    for codec in ("u8", "mulaw_inv", "alaw_std", "s16be"):
        y = decode(encode(voice, codec), Hypothesis(codec))[:, 0].astype(np.int32)
        err = np.abs(y - voice[: len(y)]).max()
        assert err <= (1100 if CODECS[codec].family == "companded8" else 256), (codec, err)


def test_mulaw_matches_g711_reference_points():
    # G.711 mu-law: 0xFF is zero, 0x00 is the most negative value, 0x80 the most positive.
    std = CODECS["mulaw_std"].decode(np.array([0xFF, 0x7F, 0x00, 0x80], dtype=np.uint8))
    assert list(std) == [0, 0, -32124, 32124]
    alaw = CODECS["alaw_std"].decode(np.array([0xD5, 0x55, 0xAA, 0x2A], dtype=np.uint8))
    assert list(alaw) == [8, -8, 32256, -32256]


# ----------------------------------------------------------------- structure
def test_padding_marker_and_repeat(music):
    body = encode(music, "u8")[:40000]
    st = analyse_structure(b"\x00" * 4 + body + b"\x00" * 9000)
    assert st.has_start_marker and st.has_end_padding and st.trail_len >= 9000
    assert st.content_end == st.size - st.trail_len
    rep = analyse_structure(body[:16384] * 4)
    assert rep.repeat_of == 16384


def test_finds_embedded_wav_and_mp3(voice):
    wav = wav_bytes(voice[:8000], 16000)
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + b"\x00" * (417 - 4)      # MPEG-1 layer III, 128 kbps, 44.1 kHz
    data = b"\x12" * 300 + wav + b"\x34" * 100 + frame * 12 + b"\x56" * 50
    st = analyse_structure(data)
    kinds = {e.kind: e for e in st.embedded}
    assert kinds["WAV"].offset == 300 and kinds["WAV"].length == len(wav)
    assert kinds["MP3"].offset == 300 + len(wav) + 100 and kinds["MP3"].length == 417 * 12


# -------------------------------------------------------------------- ingest
def _ihex(data, base=0):
    lines = []
    if base >> 16:
        rec = bytes([2, 0, 0, 4]) + (base >> 16).to_bytes(2, "big")
        lines.append(":" + (rec + bytes([-sum(rec) & 0xFF])).hex().upper())
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        rec = bytes([len(chunk)]) + ((base + off) & 0xFFFF).to_bytes(2, "big") + b"\x00" + chunk
        lines.append(":" + (rec + bytes([-sum(rec) & 0xFF])).hex().upper())
    return "\n".join(lines + [":00000001FF"]) + "\n"


def _srec(data):
    lines = ["S00600004844521B"]
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        rec = bytes([len(chunk) + 4]) + off.to_bytes(3, "big") + chunk
        lines.append("S2" + (rec + bytes([~sum(rec) & 0xFF])).hex().upper())
    return "\n".join(lines + ["S804000000FB"]) + "\n"


def test_hex_and_srec_load_to_the_same_bytes(tmp_path, music):
    data = encode(music, "mulaw_inv")[:5000]
    (tmp_path / "a.bin").write_bytes(data)
    (tmp_path / "a.hex").write_text(_ihex(data))
    (tmp_path / "a.s28").write_text(_srec(data))
    dumps = [load_dump(tmp_path / n) for n in ("a.bin", "a.hex", "a.s28")]
    assert [d.source_format for d in dumps] == ["binary", "Intel HEX", "Motorola S-record"]
    assert dumps[0].data == dumps[1].data == dumps[2].data


def test_bad_hex_is_rejected_with_a_reason():
    with pytest.raises(DumpError, match="checksum"):
        parse_intel_hex(":0400000001020304F5\n:00000001FF\n")
    with pytest.raises(DumpError, match="checksum"):
        parse_srec("S1070000010203040A\n")


# ---------------------------------------------------------------- collection
def _make_set(folder, voice, codec="mulaw_inv", chips=4, chip_size=20000):
    body = b"\x00" * 4 + encode(voice, codec)
    assert len(body) >= chips * chip_size - 6000
    body = body[: chips * chip_size - 6000] + b"\x00" * 6000
    parts = [body[i * chip_size:(i + 1) * chip_size] for i in range(chips)]
    folder.mkdir(parents=True)
    for i, p in enumerate(parts, 1):
        (folder / f"snd {i}.bin").write_bytes(p)
    return body, parts


def test_collection_orders_joins_and_flags_copies(tmp_path, voice):
    body, parts = _make_set(tmp_path / "Scene A", voice, chips=11, chip_size=8000)
    (tmp_path / "Scene A" / "snd 3 copy.bin").write_bytes(parts[2])
    (tmp_path / "Scene A" / "combined.bin").write_bytes(body)
    (tmp_path / "Scene A" / "notes.txt").write_text("not a dump")
    coll = build([tmp_path])
    assert len(coll.tracks) == 1
    t = coll.tracks[0]
    assert [c.name for c in t.members] == [f"snd {i}.bin" for i in range(1, 12)]     # 10 after 9, not after 1
    roles = {c.name: c.role for c in t.chips}
    assert roles["snd 3 copy.bin"] == "duplicate" and roles["combined.bin"] == "prejoined"
    assert t.joined_bytes() == body
    assert coll.hyp == Hypothesis("mulaw_inv") or coll.hyp.is_twin_of(Hypothesis("mulaw_inv"))
    assert not t.warnings
    assert len(track_audio(coll, t, trim=True)) == len(body) - 6000
    assert move_chip(t, t.members[0], +1) and t.members[1].name == "snd 1.bin"
    assert any("like a first chip" in w for w in t.warnings), t.warnings
    assert move_chip(t, t.members[-1], -1)
    assert any("not the last chip" in w for w in t.warnings), t.warnings


def test_export_writes_wavs_report_and_manifest(tmp_path, voice):
    body, parts = _make_set(tmp_path / "in" / "Track 1", voice, codec="u8", chips=3)
    coll = build([tmp_path / "in"])
    coll.rate, coll.rate_confirmed = 16000, True
    manifest = export(coll, tmp_path / "out", ExportOptions(trim_padding=False))
    joined = tmp_path / "out" / "Joined tracks" / "Track 1.wav"
    with wave.open(str(joined)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, len(body))
        pcm = w.readframes(w.getnframes())
    assert pcm == decode(body, Hypothesis("u8")).astype("<i2").tobytes()
    assert len(manifest["files"]) == 4
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["collection"]["rate"] == 16000
    assert "confirmed by ear" in (tmp_path / "out" / "Report.txt").read_text()


def test_wav_bytes_reopen():
    x = (np.arange(1000) * 13 % 2000 - 1000).astype(np.int16)
    with wave.open(io.BytesIO(wav_bytes(x, 22050))) as w:
        assert w.getframerate() == 22050 and w.readframes(1000) == x.astype("<i2").tobytes()


# ---------------------------------------------------------------------- rate
def test_rate_shortlist_without_voice_is_marked_uninformed():
    from conftest import lowpass
    noise = lowpass(np.random.default_rng(3).normal(0, 4000, 96000), 0.9)
    est = estimate([measure(noise)])
    assert not est.informed and len(est.top(3)) == 3 and est.top(3) == sorted(est.top(3))
    assert "listening check" in est.reason
