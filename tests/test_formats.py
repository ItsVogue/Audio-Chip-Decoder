"""Chip formats with their own layout, CVSD, and splitting into sounds."""
import numpy as np
import pytest

from audiochip import chipformats as cf
from audiochip.codecs import CODECS, Hypothesis, decode, oki_encode, render
from audiochip.detect import detect
from audiochip.structure import analyse_structure
from conftest import encode, make_voice


def _detect(data):
    return detect(data, analyse_structure(data))


# ------------------------------------------------------------------- builders
def upd_encode(x):
    """Greedy uPD7759 ADPCM encoder: pick the code that lands nearest each sample."""
    sample = state = 0
    codes = []
    for s in (x.astype(np.int32) >> 7).tolist():
        row = cf._UPD_STEP[state]
        code = min(range(16), key=lambda c: abs(sample + row[c] - s))
        sample += row[code]
        state = max(0, min(15, state + cf._UPD_STATE[code]))
        codes.append(code)
    return codes


def upd_rom(sounds, fs=19):
    """Build a uPD7759 ROM: header, offset table, then one message per sound."""
    count = len(sounds)
    table_end = 5 + 2 * count
    body = bytearray()
    offsets = []
    for x in sounds:
        start = table_end + len(body)
        start += start & 1
        body.extend(b"\x00" * (start - table_end - len(body)))
        offsets.append(start // 2)
        body.append(0x00)                                   # dummy byte the chip skips
        codes = upd_encode(x)
        codes = codes[: len(codes) // 512 * 512]
        for i in range(0, len(codes), 256):
            body.append(0x40 | fs)
            blk = codes[i:i + 256]
            body.extend((blk[j] << 4) | blk[j + 1] for j in range(0, 256, 2))
        body.append(0x00)                                   # end of message
    head = bytearray([count - 1]) + cf.UPD_MAGIC
    for o in offsets:
        head += bytes([o >> 8, o & 255])
    rom = bytes(head) + bytes(body)
    return rom + b"\xFF" * (-len(rom) % 4096)


def oki_rom(sounds):
    table = bytearray(0x400)
    body = bytearray()
    for i, x in enumerate(sounds, start=1):
        codes = oki_encode(x)
        codes = codes[: len(codes) // 2 * 2]
        data = bytes((codes[j] << 4) | codes[j + 1] for j in range(0, len(codes), 2))
        start = 0x400 + len(body)
        stop = start + len(data) - 1
        table[i * 8: i * 8 + 6] = bytes([start >> 16, (start >> 8) & 255, start & 255,
                                          stop >> 16, (stop >> 8) & 255, stop & 255])
        body += data
    rom = bytes(table) + bytes(body)
    return rom + b"\xFF" * (-len(rom) % 0x10000)


def lpc_phrase(rng, frames=30, pitch_bits=6):
    """Frames that drift the way speech does: loudness rises and falls, pitch and filters move slowly."""
    out = []
    pitch = int(rng.integers(20, 40)) >> (6 - pitch_bits)
    k = [int(rng.integers(8, 24)), int(rng.integers(8, 24))] + [int(rng.integers(4, 12)) for _ in range(5)] + [3, 3, 3]
    for i in range(frames):
        e = max(1, min(14, int(round(9 * np.sin(np.pi * (i + 1) / (frames + 1)))) + int(rng.integers(-1, 2))))
        pitch = max(1, min((1 << pitch_bits) - 1, pitch + int(rng.integers(-1, 2))))
        widths = cf.KBITS
        k = [max(0, min((1 << widths[j]) - 1, k[j] + int(rng.integers(-1, 2)))) for j in range(10)]
        voiced = (i // 7) % 3 != 2
        if i % 4 == 3:
            out.append((e, 1, pitch if voiced else 0, None))
        else:
            out.append((e, 0, pitch if voiced else 0, list(k) if voiced else list(k[:4])))
    return out


def lpc_rom(pitch_bits, phrases=12, seed=3):
    rng = np.random.default_rng(seed)
    rom = b"".join(cf.lpc_encode(lpc_phrase(rng, int(rng.integers(14, 50)), pitch_bits), pitch_bits)
                   for _ in range(phrases))
    return rom + b"\xFF" * (-len(rom) % 1024)


# ---------------------------------------------------------------------- tests
def test_cvsd_is_recognised_and_decodes_back(voice):
    up = np.interp(np.arange(len(voice) * 3) / 3, np.arange(len(voice)), voice).astype(np.int16)
    data = encode(up, "cvsd")
    det = _detect(data)
    assert det.best.hyp == Hypothesis("cvsd"), [(c.hyp.key, c.score) for c in det.candidates[:4]]
    assert det.confidence == "medium" and "CVSD" in det.reason
    y = decode(data, Hypothesis("cvsd"))[:, 0].astype(np.float64)
    n = min(len(y), len(up))
    assert np.corrcoef(y[2000:n], up[2000:n])[0, 1] > 0.9


def test_cvsd_bit_order_is_told_apart(voice):
    up = np.interp(np.arange(len(voice) * 3) / 3, np.arange(len(voice)), voice).astype(np.int16)
    det = _detect(encode(up, "cvsd", layout="bitrev"))
    assert det.best.hyp == Hypothesis("cvsd", "bitrev")


def test_upd7759_rom_is_recognised_split_and_timed(voice):
    rom = upd_rom([voice[:16384], voice[30000:40240], voice[50000:58192]])
    det = _detect(rom)
    assert det.best.hyp == Hypothesis("upd7759") and det.confidence == "high"
    r = render(rom, Hypothesis("upd7759"))
    assert r.rate == 8000 and len(r.sounds) == 3
    first = r.samples[r.sounds[0].start: r.sounds[0].end].astype(np.float64)
    ref = voice[: len(first)].astype(np.float64)
    assert abs(len(first) - 16384) <= 600
    assert np.corrcoef(first[200:], ref[200: len(first)])[0, 1] > 0.9


def test_oki_phrase_table_is_recognised_and_split(voice):
    rom = oki_rom([voice[:12000], voice[20000:36000], voice[40000:50000], voice[60000:72000]])
    det = _detect(rom)
    assert det.best.hyp == Hypothesis("oki_table") and det.confidence == "high", det.reason
    r = render(rom, Hypothesis("oki_table"))
    assert [s.end - s.start for s in r.sounds] == [12000, 16000, 10000, 12000]
    assert r.sounds[1].offset == 0x400 + 6000


@pytest.mark.parametrize("pitch_bits,codecs", [(6, ("lpc_5220",)), (5, ("lpc_5110", "lpc_0281"))])
def test_lpc_speech_is_found_and_synthesised(pitch_bits, codecs):
    rom = lpc_rom(pitch_bits)
    det = _detect(rom)
    assert det.best.hyp.codec in codecs and det.best.hyp.layout == "normal", \
        [(c.hyp.key, c.score, c.note) for c in det.candidates[:5]]
    assert det.confidence == "medium"
    r = render(rom, det.best.hyp)
    assert r.rate == 8000 and len(r.sounds) == 12
    x = r.samples.astype(np.float64)
    assert 500 < x.std() < 12000 and (np.abs(r.samples) >= 32767).mean() < 0.02


def test_lpc_in_reversed_bit_order_is_found():
    rom = bytes(CODECS["u8"].decode(np.zeros(1, np.uint8)).tobytes()[:0])    # keep numpy import honest
    rom = lpc_rom(6, seed=5)
    rev = bytes(int(f"{v:08b}"[::-1], 2) for v in rom)
    det = _detect(rev)
    assert det.best.hyp == Hypothesis("lpc_5220", "bitrev")


def test_lpc_parse_round_trip():
    rng = np.random.default_rng(9)
    frames = lpc_phrase(rng, 25, 6)
    b = np.frombuffer(cf.lpc_encode(frames, 6), dtype=np.uint8)
    got, end, rough = cf._lpc_parse(cf._BitTable(b), 0, 6, False)
    assert [tuple(f[:3]) for f in got] == [tuple(f[:3]) for f in frames] and rough < 0.4


def test_noise_and_plain_audio_are_not_mistaken_for_chip_formats(voice):
    noise = np.random.default_rng(11).integers(0, 256, 150000, dtype=np.uint8).tobytes()
    det = _detect(noise)
    assert det.confidence == "none"
    assert not any(c.evidence for c in det.candidates)
    for codec in ("u8", "mulaw_inv", "s16le", "oki_hi"):
        det = _detect(encode(voice, codec))
        assert CODECS[det.best.hyp.codec].family not in ("delta", "chip"), (codec, det.best.hyp.key)


# ------------------------------------------------- splitting, clean-up, export
def test_silent_gaps_split_a_plain_chip(voice):
    from audiochip.sounds import find_by_silence
    quiet = np.zeros(4000, dtype=np.int16)
    x = np.concatenate([voice[:8000], quiet, voice[20000:25000], quiet, quiet, voice[34000:42000]])
    sounds = find_by_silence(x, 8000)
    assert len(sounds) == 3
    assert abs(sounds[1].start - 12000) < 200 and abs(sounds[1].end - 17000) < 200


def test_collection_takes_speed_and_sounds_from_a_upd7759_rom(tmp_path, voice):
    from audiochip.collection import build, track_audio, track_sounds
    (tmp_path / "Voice").mkdir()
    (tmp_path / "Voice" / "u5.bin").write_bytes(upd_rom([voice[:16384], voice[30000:40240]]))
    coll = build([tmp_path])
    assert coll.hyp == Hypothesis("upd7759") and coll.rate == 8000 and coll.rate_estimate.fixed
    t = coll.tracks[0]
    sounds, basis = track_sounds(coll, t)
    assert len(sounds) == 2 and "index" in basis
    assert sounds[-1].end == len(track_audio(coll, t))


def test_export_formats_cleanup_and_separate_sounds(tmp_path, voice):
    sf = pytest.importorskip("soundfile")
    from audiochip.cleanup import Cleanup
    from audiochip.collection import build
    from audiochip.export import ExportOptions, export
    src = tmp_path / "in" / "Board"
    src.mkdir(parents=True)
    quiet = np.full(4000, 0, dtype=np.int16)
    x = np.concatenate([voice[:16000], quiet, voice[20000:36000], quiet, voice[40000:56000]])
    (src / "1.bin").write_bytes(encode(x, "u8"))
    coll = build([src])
    coll.rate = 7575                                      # not an MP3 speed, on purpose
    for fmt in ("flac", "mp3"):
        out = tmp_path / fmt
        m = export(coll, out, ExportOptions(fmt=fmt, per_sound=True,
                                            cleanup=Cleanup(remove_offset=True, level=True)))
        kinds = [f["kind"] for f in m["files"]]
        assert kinds.count("separate sound") >= 3 and "joined track" in kinds
        joined = next(f for f in m["files"] if f["kind"] == "joined track")
        assert joined["wav"].endswith("." + fmt) and "gain_db" in joined["cleanup"]
        y, rate = sf.read(str(out / joined["wav"]), dtype="int16")
        assert rate == (7575 if fmt == "flac" else 8000)
        if fmt == "flac":
            assert len(y) == joined["frames"] and abs(int(np.abs(y).max()) - 29204) < 40
        assert "Clean-up applied" in (out / "Report.txt").read_text()


def test_plain_export_is_untouched_by_the_new_options(tmp_path, voice):
    from audiochip.collection import build, track_audio
    from audiochip.export import ExportOptions, export, wav_bytes
    src = tmp_path / "in" / "Board"
    src.mkdir(parents=True)
    (src / "1.bin").write_bytes(encode(voice, "mulaw_inv"))
    coll = build([src])
    m = export(coll, tmp_path / "out", ExportOptions())
    joined = next(f for f in m["files"] if f["kind"] == "joined track")
    assert (tmp_path / "out" / joined["wav"]).read_bytes() == wav_bytes(track_audio(coll, coll.tracks[0], trim=True),
                                                                         coll.rate)
