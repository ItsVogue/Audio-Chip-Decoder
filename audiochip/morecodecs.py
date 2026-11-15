# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""Additional codecs, added alongside the original ones without changing them.

Everything here is registered into the existing codec table by `register()`; the decoders
that were already in the app are untouched. Three groups:

* Audio files. WAV (PCM of any width, Microsoft ADPCM, IMA ADPCM, G.721, G.723, GSM 06.10,
  A-law, mu-law), FLAC, Ogg Vorbis, Opus, MP3, AIFF, AU, CAF (including ALAC), VOC and W64
  are decoded by libsndfile, the established library the app already ships for FLAC/MP3 export.
* Headerless streams decoded by that same library: G.721 / G.726 at 32 kbit/s, G.723 at 24 and
  40 kbit/s, GSM 06.10. These are offered for manual selection; they are never picked automatically.
* Decoders written here from published formats: wide raw PCM (24 and 32 bit) and Sony
  PlayStation ADPCM (VAG files and raw SPU blocks).

CAPABILITIES records, per format, whether the app can decode it, encode it, or both.
"""
from __future__ import annotations

import io
import struct

import numpy as np

from .chipformats import Rendered, Sound

# ---------------------------------------------------------------- capabilities
# id: (name, decode, encode, how)
CAPABILITIES = {
    "wav": ("WAV: PCM 8/16/24/32-bit, float, MS ADPCM, IMA ADPCM, G.721, G.723, GSM 06.10, A-law, mu-law",
            True, True, "libsndfile (export writes 16-bit PCM)"),
    "flac": ("FLAC", True, True, "libsndfile"),
    "ogg": ("Ogg Vorbis", True, True, "libsndfile"),
    "opus": ("Opus", True, True, "libsndfile"),
    "mp3": ("MP3", True, True, "libsndfile (LAME, mpg123)"),
    "aiff": ("AIFF / AIFF-C", True, False, "libsndfile"),
    "au": ("AU / SND", True, False, "libsndfile"),
    "caf": ("CAF, including ALAC", True, False, "libsndfile"),
    "voc": ("Creative VOC (PCM, A-law, mu-law)", True, False, "libsndfile"),
    "g721": ("G.721 / G.726 32 kbit/s, headerless", True, True, "libsndfile"),
    "g723_24": ("G.723 24 kbit/s (G.726-24), headerless", True, True, "libsndfile"),
    "g723_40": ("G.723 40 kbit/s (G.726-40), headerless", True, True, "libsndfile"),
    "gsm610": ("GSM 06.10 full rate, headerless", True, True, "libsndfile"),
    "pcm_wide": ("Raw PCM 24/32-bit, signed or unsigned, either byte order", True, False, "built in"),
    "vag": ("Sony PlayStation ADPCM (VAG files, raw SPU blocks)", True, False, "built in"),
}

_SIGNATURES = ((b"RIFF", "WAV"), (b"RF64", "WAV"), (b"fLaC", "FLAC"), (b"OggS", "Ogg"), (b"FORM", "AIFF"),
               (b".snd", "AU"), (b"caff", "CAF"), (b"Creative Voice File\x1a", "VOC"), (b"riff", "W64"),
               (b"ID3", "MP3"))


def _sf():
    import soundfile
    return soundfile


def container_kind(data: bytes) -> str | None:
    """Name of the audio file format these bytes start with, by signature. None if not an audio file."""
    head = bytes(data[:32])
    for magic, name in _SIGNATURES:
        if head.startswith(magic):
            if name == "WAV" and head[8:12] not in (b"WAVE", b"\x00\x00\x00\x00") and magic == b"RIFF":
                return None
            if name == "AIFF" and head[8:12] not in (b"AIFF", b"AIFC"):
                return None
            return name
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0 and (head[1] & 0x06) != 0:
        return "MP3"                                         # MPEG audio frame sync at the very start
    return None


def read_container(data: bytes):
    """Decode an audio file held in memory. Returns (int16 frames x channels, rate, description)."""
    sf = _sf()
    with sf.SoundFile(io.BytesIO(bytes(data))) as f:
        desc = f"{f.format} / {f.subtype}, {f.channels} channel{'s' if f.channels != 1 else ''}, {f.samplerate:,} Hz"
        # A damaged header can claim billions of samples. No file holds more than a few hundred
        # samples per byte, so anything beyond that is refused rather than asked of memory.
        if f.frames < 0 or f.frames * f.channels > max(len(data) * 400, 1 << 16) or f.channels > 8:
            raise ValueError(f"the header claims {f.frames:,} samples in {f.channels} channels, which this "
                             f"{len(data):,}-byte file cannot hold")
        x = f.read(f.frames, dtype="int16", always_2d=True)      # a count is required for streams that cannot seek
        return x, int(f.samplerate), desc


def _container_render(b: np.ndarray) -> Rendered:
    data = b.tobytes()
    kind = container_kind(data)
    if kind is None:
        return Rendered(np.zeros(0, dtype=np.int16), None, [], "These bytes do not start with an audio file header.")
    try:
        x, rate, desc = read_container(data)
    except Exception as e:
        return Rendered(np.zeros(0, dtype=np.int16), None, [], f"The {kind} file could not be decoded: {e}")
    samples = x[:, 0] if x.shape[1] == 1 else x
    return Rendered(samples, rate, [], f"Audio file decoded by libsndfile: {desc}.")


# ------------------------------------------------------- headerless, libsndfile
_AU_ENCODING = {"g721": (23, "G721_32"), "g723_24": (25, "G723_24"), "g723_40": (26, "G723_40")}


def _au(body: bytes, encoding: int, rate: int = 8000) -> bytes:
    return b".snd" + struct.pack(">5I", 24, len(body), encoding, rate, 1) + body


def _g72x(codec: str):
    def run(b: np.ndarray) -> np.ndarray:
        if len(b) < 8:
            return np.zeros(0, dtype=np.int16)
        x, _, _ = read_container(_au(b.tobytes(), _AU_ENCODING[codec][0]))
        return x[:, 0]
    return run


def _gsm(b: np.ndarray) -> np.ndarray:
    n = len(b) // 33 * 33
    if not n:
        return np.zeros(0, dtype=np.int16)
    x, _ = _sf().read(io.BytesIO(b[:n].tobytes()), dtype="int16", format="RAW", subtype="GSM610",
                      samplerate=8000, channels=1)
    return x


def encode(codec: str, pcm: np.ndarray, rate: int = 8000) -> bytes:
    """Encode mono int16 audio. Only formats with an established encoder are offered."""
    sf = _sf()
    pcm = np.ascontiguousarray(pcm, dtype=np.int16)
    buf = io.BytesIO()
    if codec in _AU_ENCODING:
        sf.write(buf, pcm, rate, format="AU", subtype=_AU_ENCODING[codec][1])
        raw = buf.getvalue()
        return raw[struct.unpack(">I", raw[4:8])[0]:]         # strip the AU header: a headerless stream
    if codec == "gsm610":
        sf.write(buf, pcm, rate, format="RAW", subtype="GSM610")
        return buf.getvalue()
    raise ValueError(f"No encoder for {codec}")


# ------------------------------------------------------------------- wide PCM
def _wide(width: int, big: bool, unsigned: bool):
    def run(b: np.ndarray) -> np.ndarray:
        rows = b[: len(b) // width * width].reshape(-1, width)
        hi, lo = (rows[:, 0], rows[:, 1]) if big else (rows[:, width - 1], rows[:, width - 2])
        top = (hi.astype(np.int32) << 8) | lo                 # the 16 most significant bits
        return (top - 32768 if unsigned else np.where(top >= 32768, top - 65536, top)).astype(np.int16)
    return run


# ------------------------------------------------------- Sony PlayStation ADPCM
_PSX_F0 = (0, 60, 115, 98, 122)
_PSX_F1 = (0, 0, -52, -55, -60)
_PSX_FLAGS = (0, 1, 2, 3, 4, 6, 7)


def psx_blocks_valid(b: np.ndarray) -> tuple[int, float]:
    """(non-empty 16-byte blocks, share of them with a legal header) for data taken as SPU ADPCM."""
    n = len(b) // 16
    if n < 4:
        return 0, 0.0
    blk = b[: n * 16].reshape(n, 16)
    used = blk.any(axis=1)
    head, flag = blk[:, 0], blk[:, 1]
    ok = ((head >> 4) <= 4) & ((head & 15) <= 12) & np.isin(flag, _PSX_FLAGS)
    count = int(used.sum())
    return count, (float((ok & used).sum()) / count if count else 0.0)


def psx_decode(data: bytes) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Decode SPU ADPCM blocks. Returns (samples, [(start, end)] of each sound, split at end flags).

    Each 16-byte block: predictor filter and shift, a flag byte, then 28 four-bit codes, low
    nibble first. sample = (code << 12 >> shift) + (previous * f0 + one before * f1 + 32) >> 6.
    """
    n = len(data) // 16
    out = np.zeros(n * 28, dtype=np.int16)
    s1 = s2 = 0
    pos = 0
    start = 0
    spans = []
    for i in range(n):
        if not i & 4095:
            from .codecs import check_cancel
            check_cancel()
        blk = data[i * 16: i * 16 + 16]
        shift, filt, flag = blk[0] & 15, blk[0] >> 4, blk[1]
        if filt > 4 or not any(blk):
            if pos > start:
                spans.append((start, pos))
            start = pos
            s1 = s2 = 0
            continue
        if shift > 12:
            shift = 9
        f0, f1 = _PSX_F0[filt], _PSX_F1[filt]
        for byte in blk[2:]:
            for nib in (byte & 15, byte >> 4):
                v = (nib - 16 if nib & 8 else nib) << 12 >> shift
                v += (s1 * f0 + s2 * f1 + 32) >> 6
                v = -32768 if v < -32768 else 32767 if v > 32767 else v
                out[pos] = v
                pos += 1
                s2, s1 = s1, v
        if flag & 1:                                         # last block of this sound
            spans.append((start, pos))
            start = pos
            s1 = s2 = 0
    if pos > start:
        spans.append((start, pos))
    return out[:pos], spans


def _vag_render(b: np.ndarray) -> Rendered:
    data = b.tobytes()
    rate = None
    note = "Raw PlayStation SPU ADPCM blocks."
    if data[:4] == b"VAGp" and len(data) >= 64:
        rate = struct.unpack(">I", data[16:20])[0] or None
        name = data[32:48].split(b"\x00")[0].decode("ascii", "replace")
        note = f"PlayStation VAG file '{name}'" + (f", {rate:,} Hz from its header." if rate else ".")
        data = data[48:]
    samples, spans = psx_decode(data)
    sounds = [Sound(a, e, f"Sound {i}") for i, (a, e) in enumerate(spans, 1) if e - a >= 28]
    if len(sounds) > 1000 and len(samples) < 28 * 8 * len(sounds):
        sounds = []                 # end flags on nearly every block: not real sounds, so no list of thousands
        note += " End flags occur too often for real sounds, so the data is shown as one stream."
    return Rendered(samples, rate, sounds if len(sounds) > 1 else [], note)


# ----------------------------------------------------------------- detection
def extra_candidates(data: bytes, Candidate, Hypothesis, not_this: float) -> list:
    """Candidates for the formats above that can be recognised with certainty, by their headers."""
    out = []

    def add(codec, found, ev, miss, cap="high"):
        hyp = Hypothesis(codec)
        if found:
            out.append(Candidate(hyp, 0.001, 0.0, 0.0, 0.0, ev, worst_channel=0.001, evidence=ev, cap=cap))
        else:
            out.append(Candidate(hyp, 1.0, not_this, 0.0, 0.0, miss))

    kind = container_kind(data)
    desc = ""
    if kind:
        try:
            _, _, desc = read_container(data)
        except Exception:
            kind = None
    add("audiofile", bool(kind), f"The file starts with a {kind} header and decodes as an audio file ({desc}).",
        "Not an audio file: no known file header at the start.")
    b = np.frombuffer(data, dtype=np.uint8)
    vag = bytes(data[:4]) == b"VAGp"
    blocks, share = (0, 0.0) if vag else psx_blocks_valid(b[: 1 << 18])
    spu = blocks >= 64 and share >= 0.98
    add("vag", vag or spu,
        "The file starts with the PlayStation VAG signature (VAGp)." if vag else
        f"{100 * share:.0f}% of the {blocks:,} sixteen-byte blocks carry a legal PlayStation ADPCM header "
        "(filter 0 to 4, shift 0 to 12, a known flag). Random data manages under 1%.",
        "No VAG signature, and the bytes are not laid out as PlayStation ADPCM blocks.",
        cap="high" if vag else "medium")
    return out


# ---------------------------------------------------------------- registration
def register(CODECS: dict, RENDERERS: dict, Codec) -> None:
    def put(codec):
        if codec.id not in CODECS:                          # never replace a decoder that is already there
            CODECS[codec.id] = codec

    RENDERERS.setdefault("audiofile", _container_render)
    RENDERERS.setdefault("vag", _vag_render)
    put(Codec("audiofile", "Audio file (WAV, FLAC, Ogg, Opus, MP3, AIFF, AU, CAF, VOC)", "chip", 1,
              "An ordinary audio file rather than a chip dump. It is decoded by libsndfile, including WAV files "
              "that hold Microsoft ADPCM, IMA ADPCM, G.721, G.723, GSM 06.10, A-law or mu-law.",
              "Decoded by the libsndfile library according to the file's own header.\n"
              "The file states its sample rate and channel count; nothing is estimated.",
              lambda b: _container_render(b).samples, structured=True, layouts=("normal",), own_channels=True))
    put(Codec("vag", "Sony PlayStation ADPCM (VAG / SPU)", "chip", 16 / 28,
              "The ADPCM used by the PlayStation sound chip: 16-byte blocks of 28 samples, each with its own "
              "predictor and scale.",
              "Block: byte 0 = filter (high 4 bits) and shift (low 4), byte 1 = flags, then 28 four-bit codes.\n"
              "sample = (code << 12 >> shift) + (prev * f0 + prev2 * f1 + 32) >> 6\n"
              "f0 = 0, 60, 115, 98, 122 ; f1 = 0, 0, -52, -55, -60 for filters 0 to 4",
              lambda b: _vag_render(b).samples, structured=True, layouts=("normal",)))
    g726 = ("4 bits per sample. The ITU telephone ADPCM (G.721, later G.726 at 32 kbit/s), used by cordless "
            "phones, answering machines and voice modules. Decoded by libsndfile.")
    put(Codec("g721", "G.721 / G.726 ADPCM, 32 kbit/s", "adpcm", 0.5, g726,
              "ITU-T G.721 adaptive predictor (two poles, six zeros) with a 4-bit adaptive quantiser.\n"
              "Decoded by the libsndfile library. Try the swapped-halves layout if this one is noise.",
              _g72x("g721"), auto=False, layouts=("normal", "nibswap")))
    put(Codec("g723_24", "G.723 / G.726 ADPCM, 24 kbit/s", "adpcm", 0.375,
              "3 bits per sample. The 24 kbit/s member of the same ITU ADPCM family. Decoded by libsndfile.",
              "ITU-T G.723 (1988) 3-bit ADPCM, decoded by the libsndfile library.",
              _g72x("g723_24"), auto=False, layouts=("normal",)))
    put(Codec("g723_40", "G.723 / G.726 ADPCM, 40 kbit/s", "adpcm", 0.625,
              "5 bits per sample. The 40 kbit/s member of the same ITU ADPCM family. Decoded by libsndfile.",
              "ITU-T G.723 (1988) 5-bit ADPCM, decoded by the libsndfile library.",
              _g72x("g723_40"), auto=False, layouts=("normal",)))
    put(Codec("gsm610", "GSM 06.10 full rate", "adpcm", 33 / 160,
              "The original mobile-phone speech codec: 33-byte frames of 20 ms at 8,000 Hz. Decoded by libsndfile.",
              "GSM 06.10 RPE-LTP, 13 kbit/s, 160 samples per 33-byte frame. Decoded by the libsndfile library.",
              _gsm, auto=False, layouts=("normal",)))
    for width, bits in ((3, 24), (4, 32)):
        for unsigned in (False, True):
            for big in (False, True):
                cid = f"{'u' if unsigned else 's'}{bits}{'be' if big else 'le'}"
                order = "high byte first" if big else "low byte first"
                put(Codec(cid, f"{bits}-bit {'unsigned' if unsigned else 'signed'}, {order}", "manual", width,
                          f"{width} bytes per sample, {order}. The app works in 16 bits, so the 16 most "
                          "significant bits of each sample are kept.",
                          f"sample = the top 16 bits of each {bits}-bit value" + (" - 32768" if unsigned else ""),
                          _wide(width, big, unsigned), auto=False, layouts=("normal",)))
