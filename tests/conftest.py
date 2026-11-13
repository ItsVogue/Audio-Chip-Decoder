import numpy as np
import pytest

from audiochip.codecs import CODECS, LAYOUTS, cvsd_encode, ima_encode, oki_encode, yamaha_encode


def lowpass(x, frac):
    """Brick-wall low-pass at frac of Nyquist."""
    X = np.fft.rfft(x)
    X[int(len(X) * frac):] = 0
    return np.fft.irfft(X, len(x))


def make_voice(n=96000, seed=1):
    """A voice-like test signal: a pitched buzz through moving resonances, with pauses."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    pitch = 0.008 + 0.003 * np.sin(2 * np.pi * t / 23000) + 0.001 * np.sin(2 * np.pi * t / 5100)
    phase = np.cumsum(pitch) * 2 * np.pi
    x = sum(np.sin(k * phase) / k for k in range(1, 18))
    for centre, width in ((0.05, 0.02), (0.11, 0.03), (0.2, 0.04)):
        X = np.fft.rfft(x)
        f = np.linspace(0, 1, len(X))
        X *= 1 + 4 * np.exp(-0.5 * ((f - centre) / width) ** 2)
        x = np.fft.irfft(X, n)
    env = np.clip(np.sin(2 * np.pi * t / 17000) * 3 + 0.5, 0, 1)
    x = x * env + rng.normal(0, 0.01, n)
    x = lowpass(x, 0.8)
    return (x / np.abs(x).max() * 20000).astype(np.int16)


def make_music(n=96000, seed=2):
    """A music-like test signal: decaying chords with harmonics, filtered below the band top."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    x = np.zeros(n)
    for start in range(0, n, 12000):
        for f in rng.choice([0.011, 0.0138, 0.0165, 0.022, 0.0277, 0.033], 3, replace=False):
            seg = np.arange(n - start)
            note = sum(np.sin(2 * np.pi * f * k * seg + rng.uniform(0, 6)) / k ** 1.2 for k in range(1, 12))
            x[start:] += note * np.exp(-seg / 9000)
    x += rng.normal(0, 0.02, n)
    x = lowpass(x, 0.85)
    return (x / np.abs(x).max() * 22000).astype(np.int16)


def encode8(x, codec_id):
    """Encode int16 samples with any one-byte-per-sample decoder, by nearest code."""
    table = CODECS[codec_id].decode(np.arange(256, dtype=np.uint8)).astype(np.int32)
    order = np.argsort(table, kind="stable")
    sorted_vals = table[order]
    pos = np.clip(np.searchsorted(sorted_vals, x.astype(np.int32)), 1, 255)
    left, right = sorted_vals[pos - 1], sorted_vals[pos]
    pick = np.where(np.abs(x - left) <= np.abs(right - x), pos - 1, pos)
    return order[pick].astype(np.uint8)


def encode(x, codec_id, layout="normal", channels=1):
    """Build a dump: x is int16, shaped (n,) for mono or (n, channels)."""
    flat = x.reshape(-1) if channels > 1 else x
    fam = CODECS[codec_id].family
    if fam in ("linear8", "companded8"):
        b = encode8(flat, codec_id)
    elif fam == "linear16":
        dt = {"s16le": "<i2", "s16be": ">i2", "u16le": "<u2", "u16be": ">u2"}[codec_id]
        v = flat.astype(np.int32) + (32768 if codec_id.startswith("u") else 0)
        b = np.frombuffer(v.astype(dt).tobytes(), dtype=np.uint8)
    elif fam == "delta":
        bits = cvsd_encode(flat)
        b = np.packbits(np.array(bits[: len(bits) // 8 * 8], dtype=np.uint8))
    else:
        kind, order = codec_id.split("_")
        codes = {"oki": oki_encode, "ima": ima_encode, "yamaha": yamaha_encode}[kind](flat)
        codes = codes[: len(codes) // 2 * 2]
        hi, lo = (codes[0::2], codes[1::2]) if order == "hi" else (codes[1::2], codes[0::2])
        b = (np.array(hi, dtype=np.uint8) << 4) | np.array(lo, dtype=np.uint8)
    b = LAYOUTS[layout][1](np.ascontiguousarray(b))     # both extra layouts are their own inverse
    return bytes(b)


@pytest.fixture(scope="session")
def voice():
    return make_voice()


@pytest.fixture(scope="session")
def music():
    return make_music()
