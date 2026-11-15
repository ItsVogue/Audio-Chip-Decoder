# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""SNES BRR, Yamaha ADPCM-A and Nintendo DSP ADPCM.

tests/data/reference holds inputs together with the output of each format's reference
implementation (blargg's SPC_DSP from Snes9x, ymfm, vgmstream), produced by compiling that
project's own decoding code. The decoders here must reproduce them exactly.
"""
import struct
from pathlib import Path

import numpy as np
import pytest

from audiochip import consolecodecs as cc
from audiochip.codecs import CODECS, Hypothesis, decode, render
from audiochip.detect import detect
from audiochip.structure import analyse_structure

REF = Path(__file__).parent / "data" / "reference"
DSP_COEF = (0, 0, 2048, 0, 4096, -2048, 3584, -1536, 3072, -1024, 4608, -2560, 4200, -2248, 4800, -2300)


def _ref(name):
    return (REF / f"{name}.bin").read_bytes(), np.fromfile(REF / f"{name}.s16", dtype="<i2")


# ------------------------------------------------------------------ encoders
def brr_encode(x: np.ndarray, loop_header: bool = False) -> bytes:
    """A plain BRR encoder: for each block of 16 samples, try every shift with filter 0 and 1, keep the best."""
    x = np.asarray(x, dtype=np.int64)
    x = np.concatenate([x, np.zeros(-len(x) % 16, dtype=np.int64)])
    out = bytearray()
    p1 = 0
    blocks = len(x) // 16
    for b in range(blocks):
        target = x[b * 16:(b + 1) * 16]
        best = None
        for filt in ((0,) if b == 0 else (0, 1)):
            for shift in range(13):
                q, nibs, err = p1, [], 0
                for t in target:
                    pred = (q >> 1) + ((-q) >> 5) if filt else 0
                    step = (1 << shift) >> 1
                    n = int(np.clip(round((t / 2 - pred) / step) if step else 0, -8, 7))
                    s = ((n << shift) >> 1) + pred
                    s = max(-32768, min(32767, s))
                    s = ((s * 2 + 32768) & 0xFFFF) - 32768
                    err += (s - t) ** 2
                    nibs.append(n & 15)
                    q = s
                if best is None or err < best[0]:
                    best = (err, shift, filt, nibs, q)
        _, shift, filt, nibs, p1 = best
        out.append((shift << 4) | (filt << 2) | (1 if b == blocks - 1 else 0))
        out += bytes((nibs[i] << 4) | nibs[i + 1] for i in range(0, 16, 2))
    return (struct.pack("<H", 0) if loop_header else b"") + bytes(out)


def adpcm_a_encode(x: np.ndarray) -> bytes:
    """Encode 16-bit samples as ADPCM-A (12-bit accumulator), choosing each code by trial."""
    acc = step = 0
    nibs = []
    for t in (np.asarray(x, dtype=np.int64) >> 4):
        cur = acc - 4096 if acc & 0x800 else acc
        best = min(range(16), key=lambda n: abs(max(-2048, min(2047, cur + cc._A_DELTA[step][n])) - t)
                   if -2048 <= cur + cc._A_DELTA[step][n] <= 2047 else 1 << 30)
        acc = (acc + cc._A_DELTA[step][best]) & 0xFFF
        step = max(0, min(48, step + cc._A_STEP_INC[best & 7]))
        nibs.append(best)
    if len(nibs) & 1:
        nibs.append(0)
    return bytes((nibs[i] << 4) | nibs[i + 1] for i in range(0, len(nibs), 2))


def dsp_encode(x: np.ndarray, rate: int = 22050, loop: bool = False) -> bytes:
    """A plain .dsp writer: per 14-sample frame, try every coefficient pair and scale, keep the best."""
    x = np.asarray(x, dtype=np.int64)
    n = len(x)
    padded = np.concatenate([x, np.zeros(-n % 14, dtype=np.int64)])
    body = bytearray()
    h1 = h2 = 0
    for f in range(len(padded) // 14):
        target = padded[f * 14:(f + 1) * 14]
        best = None
        for idx in range(8):
            c1, c2 = DSP_COEF[idx * 2], DSP_COEF[idx * 2 + 1]
            for exp in range(12):
                a, b, nibs, err = h1, h2, [], 0
                for t in target:
                    pred = (1024 + c1 * a + c2 * b) >> 11
                    q = int(np.clip(round((t - pred) / (1 << exp)), -8, 7))
                    s = (((q << exp) << 11) + 1024 + c1 * a + c2 * b) >> 11
                    s = max(-32768, min(32767, s))
                    err += (s - t) ** 2
                    nibs.append(q & 15)
                    b, a = a, s
                if best is None or err < best[0]:
                    best = (err, idx, exp, nibs, a, b)
        _, idx, exp, nibs, h1, h2 = best
        body.append((idx << 4) | exp)
        body += bytes((nibs[i] << 4) | nibs[i + 1] for i in range(0, 14, 2))
    whole, rem = divmod(n, 14)
    nibbles = whole * 16 + (rem + 2 if rem else 0)
    head = (struct.pack(">IIIHHIII", n, nibbles, rate, 1 if loop else 0, 0, 2, nibbles - 1 if loop else 0, 2)
            + struct.pack(">16h", *DSP_COEF) + struct.pack(">HHhh", 0, body[0], 0, 0))
    return head + bytes(0x60 - len(head)) + bytes(body)


def _snr(a, b):
    n = min(len(a), len(b))
    a, b = a[:n].astype(float), b[:n].astype(float)
    return 10 * np.log10(np.sum(a ** 2) / max(np.sum((a - b) ** 2), 1e-9))


# ------------------------------------------------------- against the references
@pytest.mark.parametrize("name", ["brr_ref_0", "brr_ref_1"])
def test_brr_matches_the_reference_decoder_exactly(name):
    data, expected = _ref(name)
    got, _ = cc.brr_decode(data)
    assert np.array_equal(got, expected)


@pytest.mark.parametrize("name", ["adpcma_ref_0", "adpcma_ref_1", "adpcma_ref_2"])
def test_adpcm_a_matches_the_reference_decoder_exactly(name):
    data, expected = _ref(name)
    got, _ = cc.adpcm_a_decode(data, split=False)
    assert np.array_equal(got, expected)


@pytest.mark.parametrize("name", ["dsp_ref_0", "dsp_ref_1", "dsp_ref_5"])
def test_dsp_matches_the_reference_decoder_exactly(name):
    data, expected = _ref(name)
    coef = struct.unpack(">16h", data[0x1C:0x3C])
    h1, h2 = struct.unpack(">hh", data[0x40:0x44])
    got = cc.dsp_decode(data[0x60:], coef, h1, h2)
    assert np.array_equal(got, expected)


def test_reference_vectors_cover_every_setting():
    heads = set()
    for name in ("brr_ref_0", "brr_ref_1"):
        data, _ = _ref(name)
        heads |= {(b >> 4, (b >> 2) & 3) for b in data[::9]}
    assert {f for _, f in heads} == {0, 1, 2, 3} and {s for s, _ in heads} == set(range(16))
    frames = set()
    for name in ("dsp_ref_0", "dsp_ref_1", "dsp_ref_5"):
        data, _ = _ref(name)
        frames |= {b >> 4 for b in data[0x60::8]}
    assert frames == set(range(8))


# ------------------------------------------------------------------------ BRR
def test_brr_file_is_recognised_decoded_and_not_given_a_speed(voice):
    x = voice[:16000]
    data = brr_encode(x)
    det = detect(data, analyse_structure(data))
    assert det.best.hyp.codec == "brr" and det.confidence == "medium"
    assert "end flag" in det.best.evidence
    r = render(data, det.best.hyp)
    assert r.rate is None and len(r.samples) == 16000
    assert _snr(x, r.samples) > 15


def test_brr_file_with_loop_header_is_recognised(voice):
    data = brr_encode(voice[:8000], loop_header=True)
    assert len(data) % 9 == 2
    det = detect(data, analyse_structure(data))
    assert det.best.hyp.codec == "brr"
    r = render(data, Hypothesis("brr"))
    assert len(r.samples) == 8000 and "loop header" in r.note


def test_several_brr_samples_become_separate_sounds(voice, music):
    data = brr_encode(voice[:4000]) + bytes(9) + brr_encode(music[:3200]) + brr_encode(voice[4000:6400])
    r = render(data, Hypothesis("brr"))
    assert [s.end - s.start for s in r.sounds] == [4000, 3200, 2400]
    assert [s.offset for s in r.sounds] == [0, 4000 // 16 * 9 + 9, (4000 + 3200) // 16 * 9 + 9]
    # more than one end flag: not the shape of a single .brr file, so it is never chosen automatically
    assert detect(data, analyse_structure(data)).best.hyp.codec != "brr"


@pytest.mark.parametrize("codec", ["u8", "s8", "signmag8", "mulaw_std", "alaw_std", "s16le", "u16be", "oki_hi",
                                   "ima_lo", "yamaha_hi", "cvsd"])
def test_ordinary_dumps_are_not_taken_for_brr_or_dsp(voice, codec):
    from conftest import encode
    for n in (9 * 1000, 9 * 1000 + 2, 20000):
        data = encode(voice[:n * 2], codec)[:n]
        det = detect(data, analyse_structure(data))
        assert det.best.hyp.codec not in ("brr", "dsp", "ym2610a", "ffaudio"), (codec, n)
        assert not cc.brr_file_shape(data)[0] and cc.dsp_header(data) is None


# -------------------------------------------------------------------- ADPCM-A
def test_adpcm_a_round_trip_and_stated_speed(voice):
    x = voice[:12000]
    data = adpcm_a_encode(x)
    r = render(data, Hypothesis("ym2610a"))
    assert r.rate == cc.YM2610_RATE == 18518
    assert len(r.samples) == 12000 and _snr(x, r.samples) > 12
    assert CODECS["ym2610a"].structured and not any(c for c in detect(data, analyse_structure(data)).candidates
                                                    if c.hyp.codec == "ym2610a")


def test_adpcm_a_restarts_after_padding_at_a_256_byte_boundary(voice):
    first = adpcm_a_encode(voice[:3000])
    first += b"\x80" * (-len(first) % 256 or 256)
    second = adpcm_a_encode(voice[3000:6000])
    r = render(first + second, Hypothesis("ym2610a"))
    assert len(r.sounds) == 2 and r.sounds[1].offset == len(first)
    tail = r.samples[r.sounds[1].start:r.sounds[1].start + 3000]
    assert _snr(voice[3000:6000], tail) > 12              # the second sound decodes from a clean start


def test_adpcm_a_accumulator_wraps_at_12_bits():
    out, _ = cc.adpcm_a_decode(b"\x77" * 64, split=False)   # the largest upward step, over and over
    assert out.max() <= 2047 << 4 and out.min() >= -2048 << 4
    assert np.any(np.diff(out.astype(np.int32)) < -30000)  # it wrapped rather than sticking at the top


# ------------------------------------------------------------------------ DSP
def test_dsp_file_is_recognised_with_its_speed(voice):
    x = voice[:14000 + 5]
    data = dsp_encode(x, rate=32000)
    det = detect(data, analyse_structure(data))
    assert det.best.hyp.codec == "dsp" and det.confidence == "medium"
    r = render(data, det.best.hyp)
    assert r.rate == 32000 and len(r.samples) == len(x)
    assert _snr(x, r.samples) > 20


def test_dsp_looping_and_cut_short_files_are_reported(voice):
    data = dsp_encode(voice[:7000], loop=True)
    assert "looping" in render(data, Hypothesis("dsp")).note
    cut = data[:0x60 + 8 * 100]
    r = render(cut, Hypothesis("dsp"))
    assert len(r.samples) == 1400 and "cut short" in r.note
    assert detect(cut, analyse_structure(cut)).best.hyp.codec != "dsp"      # incomplete: not chosen automatically


def test_dsp_header_checks_reject_near_misses(voice):
    data = bytearray(dsp_encode(voice[:2800]))
    assert cc.dsp_header(bytes(data))
    for offset, value in ((0x0E, 1), (0x3D, 1), (0x0A, 0xFF), (0x3F, data[0x60] ^ 0x10), (0x03, 0)):
        bad = bytearray(data)
        bad[offset] = value if offset != 0x03 else bad[offset]
        if offset == 0x03:
            bad[0:4] = bytes(4)
        assert cc.dsp_header(bytes(bad)) is None, hex(offset)
    assert cc.dsp_header(bytes(200)) is None and cc.dsp_header(b"") is None
    assert render(bytes(500), Hypothesis("dsp")).samples.size == 0


# ------------------------------------------------------------------ robustness
@pytest.mark.parametrize("codec", ["brr", "ym2610a", "dsp"])
def test_console_decoders_survive_awkward_input(codec, voice):
    rng = np.random.default_rng(3)
    good = {"brr": brr_encode(voice[:1600]), "ym2610a": adpcm_a_encode(voice[:1600]),
            "dsp": dsp_encode(voice[:1600])}[codec]
    cases = [b"", b"\x00", bytes(8), bytes(9), bytes(95), bytes(96), bytes(4096), b"\xff" * 4096,
             rng.integers(0, 256, 5000, dtype=np.uint8).tobytes(), good[:-1], good[:len(good) // 2], good[1:],
             good + b"\x01", good * 3]
    for data in cases:
        x = decode(data, Hypothesis(codec))
        assert x.ndim == 2 and x.dtype == np.int16
        det = detect(data, analyse_structure(data)) if data else None
        assert det is None or det.candidates is not None


def test_random_data_does_not_become_thousands_of_sounds():
    data = np.random.default_rng(1).integers(0, 256, 200000, dtype=np.uint8).tobytes()
    for codec in ("brr", "vag", "ym2610a"):
        r = render(data, Hypothesis(codec))
        assert len(r.sounds) <= 1000, codec
    assert "probably not BRR" in render(data, Hypothesis("brr")).note
