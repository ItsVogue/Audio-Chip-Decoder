"""Three console and arcade sound-chip formats, added alongside the existing decoders.

Each decoder here was written for this app from the published behaviour of the chip, and its
output was then compared, sample for sample, with an established implementation's own code on
every filter, shift and coefficient setting (tests/data/reference holds comparison vectors):

* SNES BRR (the S-DSP sample format)     compared with blargg's SPC_DSP, as shipped in Snes9x
* Yamaha ADPCM-A (YM2610, YM2608 rhythm)  compared with ymfm by Aaron Giles
* Nintendo DSP ADPCM in a plain .dsp file compared with vgmstream

Only the plain, single-channel forms are covered. BRR and DSP files are recognised by their
structure; ADPCM-A has none, so it is offered for manual selection only.
"""
from __future__ import annotations

import struct

import numpy as np

from .chipformats import Rendered, Sound

# id: (name, decode, encode, how)
CAPABILITIES = {
    "brr": ("SNES BRR samples (.brr, with or without the 2-byte loop header)", True, False,
            "built in, checked against blargg's SPC_DSP"),
    "ym2610a": ("Yamaha ADPCM-A (YM2610 / YM2608 sample ROMs)", True, False, "built in, checked against ymfm"),
    "dsp": ("Nintendo DSP ADPCM, single-channel .dsp file", True, False, "built in, checked against vgmstream"),
}


def _check_cancel() -> None:
    from .codecs import check_cancel
    check_cancel()


# ------------------------------------------------------------------- SNES BRR
def brr_decode(data: bytes) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """Decode BRR blocks. Returns (samples, [(first sample, end sample, byte offset)] per sound).

    Each 9-byte block: a header (shift in the high 4 bits, filter in bits 3-2, loop flag, end flag)
    and 16 four-bit codes, high nibble first. Arithmetic as in the S-DSP: the sample is clamped to
    16 bits, then doubled into a 16-bit value, which wraps exactly as the chip's 15-bit store does.
    """
    n = len(data) // 9
    out = np.zeros(n * 16, dtype=np.int16)
    p1 = p2 = 0                                   # previous two outputs, as stored (doubled)
    pos = start = 0
    start_byte = 0
    spans = []
    for i in range(n):
        if not i & 4095:
            _check_cancel()
        blk = data[i * 9: i * 9 + 9]
        head = blk[0]
        if not any(blk):                          # blank block between sounds
            if pos > start:
                spans.append((start, pos, start_byte))
            start, start_byte = pos, (i + 1) * 9
            p1 = p2 = 0
            continue
        shift, filt = head >> 4, (head >> 2) & 3
        for byte in blk[1:]:
            for nib in (byte >> 4, byte & 15):
                s = nib - 16 if nib & 8 else nib
                if shift <= 12:
                    s = (s << shift) >> 1
                else:
                    s = -2048 if s < 0 else 0     # shifts 13 to 15 are invalid; the chip gives this
                h2 = p2 >> 1
                if filt == 1:
                    s += (p1 >> 1) + ((-p1) >> 5)
                elif filt == 2:
                    s += p1 - h2 + (h2 >> 4) + ((p1 * -3) >> 6)
                elif filt == 3:
                    s += p1 - h2 + ((p1 * -13) >> 7) + ((h2 * 3) >> 4)
                s = -32768 if s < -32768 else 32767 if s > 32767 else s
                s = ((s * 2 + 32768) & 0xFFFF) - 32768
                out[pos] = s
                pos += 1
                p2, p1 = p1, s
        if head & 1:                              # last block of this sound
            spans.append((start, pos, start_byte))
            start, start_byte = pos, (i + 1) * 9
            p1 = p2 = 0
    if pos > start:
        spans.append((start, pos, start_byte))
    return out[:pos], spans


def _brr_body(data: bytes) -> tuple[bytes, int | None]:
    """The block data, and the loop point in bytes if the file starts with the 2-byte loop header."""
    if len(data) % 9 == 2 and len(data) >= 11:
        loop = struct.unpack("<H", data[:2])[0]
        if loop % 9 == 0 and loop < len(data) - 2:
            return data[2:], loop
    return data, None


def brr_file_shape(data: bytes) -> tuple[bool, str]:
    """True if the bytes are laid out exactly as one BRR sample file, with the reason."""
    body, loop = _brr_body(data)
    n = len(body) // 9
    if len(body) % 9 or n < 8:
        return False, "The length is not a whole number of 9-byte BRR blocks."
    heads = np.frombuffer(body, dtype=np.uint8)[::9]
    ends = heads & 1
    if not ends[-1] or ends[:-1].any():
        return False, "The end flag is not set on the last block only, as it is in a BRR sample file."
    if ((heads >> 4) > 12).any():
        return False, "Some blocks use a shift above 12, which BRR encoders do not produce."
    if heads[0] & 0x0C:
        return False, "The first block uses a filter that needs earlier samples, so it does not start a sample."
    if not np.frombuffer(body, dtype=np.uint8).reshape(n, 9)[:, 1:].any():
        return False, "The blocks hold no sound."
    return True, (f"{n:,} nine-byte BRR blocks: the end flag is set on the last block and no other, every shift is "
                  f"12 or less and the first block uses filter 0"
                  + (f"; a 2-byte loop header points at block {loop // 9}" if loop is not None else "") + ".")


MAX_BRR_SOUNDS = 1000


def _brr_render(b: np.ndarray) -> Rendered:
    body, loop = _brr_body(b.tobytes())
    samples, spans = brr_decode(body)
    sounds = [Sound(a, e, f"Sample {i}", off + (2 if loop is not None else 0))
              for i, (a, e, off) in enumerate(spans, 1) if e - a >= 64]
    note = "SNES BRR samples. The format carries no playback speed; the console plays them at any pitch."
    if loop is not None:
        note += f" The file's loop header points at block {loop // 9} (sample {loop // 9 * 16:,})."
    if len(sounds) > MAX_BRR_SOUNDS or len(spans) > 4 * max(len(sounds), 1) + 8:
        sounds = []                               # end flags all over the place: this is not BRR sample data
        note += " End flags occur far too often for real samples, so the data is shown as one stream; it is "
        note += "probably not BRR."
    return Rendered(samples, None, sounds if len(sounds) > 1 else [], note)


# ------------------------------------------------------------- Yamaha ADPCM-A
_A_STEPS = (16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
            157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
            1060, 1166, 1282, 1411, 1552)
_A_STEP_INC = (-1, -1, -1, -1, 2, 5, 7, 9)
_A_DELTA = tuple(tuple(((2 * (n & 7) + 1) * step // 8) * (-1 if n & 8 else 1) for n in range(16)) for step in _A_STEPS)
YM2610_RATE = 18518                               # 8 MHz clock / 432, as on the Neo Geo
_PADDING = (0x80, 0x00, 0xFF, 0x08)


def adpcm_a_decode(data: bytes, split: bool = True) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """Decode ADPCM-A. Returns (samples, [(first sample, end sample, byte offset)] per sound).

    Two 4-bit codes per byte, high nibble first. delta = (2 * (code & 7) + 1) * step / 8, negated
    when bit 3 is set; the 12-bit accumulator wraps; the step index moves by -1,-1,-1,-1,2,5,7,9.
    The chip clears the accumulator and step at each key-on, and samples start on 256-byte
    boundaries, so with split=True the decoder restarts at a boundary that follows padding.
    """
    n = len(data)
    out = np.zeros(n * 2, dtype=np.int16)
    acc = step = 0
    spans = []
    start = start_byte = 0
    pos = 0
    for i in range(n):
        if not i & 0xFF:
            if not i & 0xFFFF:
                _check_cancel()
            if split and i >= 16 and data[i - 1] in _PADDING and data[i - 16:i] == data[i - 1:i] * 16:
                if pos > start:
                    spans.append((start, pos, start_byte))
                start, start_byte = pos, i
                acc = step = 0
        byte = data[i]
        for nib in (byte >> 4, byte & 15):
            acc = (acc + _A_DELTA[step][nib]) & 0xFFF
            step += _A_STEP_INC[nib & 7]
            step = 0 if step < 0 else 48 if step > 48 else step
            out[pos] = (acc - 4096 if acc & 0x800 else acc) << 4
            pos += 1
    if pos > start:
        spans.append((start, pos, start_byte))
    return out, spans


def _adpcm_a_render(b: np.ndarray) -> Rendered:
    samples, spans = adpcm_a_decode(b.tobytes())
    sounds = [Sound(a, e, f"Sound {i}", off) for i, (a, e, off) in enumerate(spans, 1)
              if e - a >= 512 and np.any(samples[a:e])]
    return Rendered(samples, YM2610_RATE, sounds if len(sounds) > 1 else [],
                    f"Yamaha ADPCM-A. The chip plays it at its clock divided by 432: {YM2610_RATE:,} Hz with the "
                    "usual 8 MHz clock. A board with a different clock needs the speed changed to match. Sounds "
                    "are split where padding runs up to a 256-byte boundary, which is where the chip can start one.")


# -------------------------------------------------------- Nintendo DSP ADPCM
DSP_HEADER = 0x60


def dsp_header(data: bytes) -> dict | None:
    """The fields of a standard single-channel .dsp header, or None if these bytes are not one.

    The header has no signature, so it is accepted only when its fields agree with each other and
    with the data that follows, using the same checks as vgmstream plus the exact sample/nibble relation.
    """
    if len(data) < DSP_HEADER + 8:
        return None
    samples, nibbles, rate, loop_flag, fmt, loop_start, loop_end, ca = struct.unpack(">IIIHHIII", data[:0x1C])
    coef = struct.unpack(">16h", data[0x1C:0x3C])
    gain, ps, hist1, hist2 = struct.unpack(">HHhh", data[0x3C:0x44])
    if not 0 < samples <= 0x10000000 or not 0 < nibbles <= 0x20000000:
        return None
    if not 5000 <= rate <= 48000 or loop_flag > 1 or fmt != 0 or gain != 0:
        return None
    if ca not in (0, 2, loop_start) or not any(coef):
        return None
    if ps != data[DSP_HEADER] or (ps >> 4) > 7:
        return None
    whole, rem = divmod(samples, 14)
    exact = nibbles == whole * 16 + (rem + 2 if rem else 0)
    have = (len(data) - DSP_HEADER) // 8 * 14
    if have < min(samples, 14):
        return None
    return {"samples": samples, "rate": rate, "coef": coef, "hist1": hist1, "hist2": hist2, "exact": exact,
            "loop": (loop_start, loop_end) if loop_flag else None, "complete": have >= samples}


def dsp_decode(data: bytes, coef, hist1: int = 0, hist2: int = 0, samples: int | None = None) -> np.ndarray:
    """Decode 8-byte DSP ADPCM frames: a header byte (coefficient pair, scale) then 14 four-bit codes.

    sample = ((code * scale) << 11 + 1024 + c1 * prev + c2 * prev2) >> 11, clamped to 16 bits.
    """
    frames = len(data) // 8
    total = frames * 14 if samples is None else min(samples, frames * 14)
    out = np.zeros(frames * 14, dtype=np.int16)
    pos = 0
    for f in range(frames):
        if pos >= total:
            break
        if not f & 4095:
            _check_cancel()
        frame = data[f * 8: f * 8 + 8]
        scale = 1 << (frame[0] & 15)
        idx = min(frame[0] >> 4, 7)
        c1, c2 = coef[idx * 2], coef[idx * 2 + 1]
        for byte in frame[1:]:
            for nib in (byte >> 4, byte & 15):
                s = nib - 16 if nib & 8 else nib
                s = (((s * scale) << 11) + 1024 + c1 * hist1 + c2 * hist2) >> 11
                s = -32768 if s < -32768 else 32767 if s > 32767 else s
                out[pos] = s
                pos += 1
                hist2, hist1 = hist1, s
    return out[:total]


def _dsp_render(b: np.ndarray) -> Rendered:
    data = b.tobytes()
    h = dsp_header(data)
    if h is None:
        return Rendered(np.zeros(0, dtype=np.int16), None, [],
                        "These bytes do not start with a standard single-channel Nintendo .dsp header.")
    x = dsp_decode(data[DSP_HEADER:], h["coef"], h["hist1"], h["hist2"], h["samples"])
    note = f"Nintendo DSP ADPCM: {h['samples']:,} samples at {h['rate']:,} Hz, from the file's header."
    if h["loop"]:
        note += " The file is marked as looping."
    if not h["complete"]:
        note += f" The file is cut short: it holds {len(x):,} of them."
    return Rendered(x, h["rate"], [], note)


# ------------------------------------------------------------------ detection
def extra_candidates(data: bytes, Candidate, Hypothesis, not_this: float) -> list:
    out = []

    def add(codec, found, ev, miss, cap):
        hyp = Hypothesis(codec)
        if found:
            out.append(Candidate(hyp, 0.001, 0.0, 0.0, 0.0, ev, worst_channel=0.001, evidence=ev, cap=cap))
        else:
            out.append(Candidate(hyp, 1.0, not_this, 0.0, 0.0, miss))

    h = dsp_header(data)
    ok = bool(h and h["exact"] and h["complete"])
    add("dsp", ok,
        (f"The first 96 bytes are a Nintendo .dsp header whose fields agree with each other and with the data: "
         f"{h['samples']:,} samples at {h['rate']:,} Hz, the matching nibble count, and a first-frame byte equal "
         "to the one the header records.") if ok else "",
        "The file does not start with a consistent Nintendo .dsp header.", "medium")
    shaped, why = brr_file_shape(data)
    add("brr", shaped, "The file is laid out as one SNES BRR sample: " + why, "Not a BRR sample file. " + why, "medium")
    return out


# --------------------------------------------------------------- registration
def register(CODECS: dict, RENDERERS: dict, Codec, COST: dict) -> None:
    def put(codec):
        if codec.id not in CODECS:                          # never replace a decoder that is already there
            CODECS[codec.id] = codec

    RENDERERS.setdefault("brr", _brr_render)
    RENDERERS.setdefault("ym2610a", _adpcm_a_render)
    RENDERERS.setdefault("dsp", _dsp_render)
    COST.setdefault("brr", 3.0)                             # seconds per megabyte, for the progress window
    COST.setdefault("ym2610a", 2.5)
    COST.setdefault("dsp", 3.0)
    put(Codec("brr", "SNES BRR samples", "chip", 9 / 16,
              "The sample format of the Super Nintendo's sound chip: 9-byte blocks of 16 samples, each block with "
              "its own shift and one of four prediction filters.",
              "Block: byte 0 = shift (high 4 bits), filter (bits 3-2), loop flag, end flag; then 16 four-bit codes.\n"
              "s = (code << shift) >> 1, plus the filter's share of the two previous samples:\n"
              "  1: p1 * 15/16     2: p1 * 61/32 - p2 * 15/16     3: p1 * 115/64 - p2 * 13/16\n"
              "Clamped to 16 bits, then doubled with 16-bit wrap, exactly as the chip stores it.",
              lambda b: _brr_render(b).samples, structured=True, layouts=("normal",)))
    put(Codec("ym2610a", "Yamaha ADPCM-A (YM2610 / YM2608)", "chip", 0.5,
              "The 4-bit ADPCM of Yamaha's YM2610 (Neo Geo and many arcade boards) and the YM2608's rhythm ROM. "
              "It differs from OKI ADPCM in its step changes and in a 12-bit accumulator that wraps.",
              "Two codes per byte, high nibble first.\n"
              "delta = (2 * (code & 7) + 1) * step / 8, negative if bit 3 is set\n"
              "acc = (acc + delta) & 0xFFF ; step index += -1, -1, -1, -1, 2, 5, 7, 9 (kept in 0 to 48)\n"
              "Output is the 12-bit accumulator. Played at clock / 432 = 18,518 Hz with an 8 MHz clock.",
              lambda b: _adpcm_a_render(b).samples, structured=True, layouts=("normal",)))
    put(Codec("dsp", "Nintendo DSP ADPCM (.dsp)", "chip", 8 / 14,
              "The ADPCM of the GameCube and Wii sound hardware, in its standard single-channel .dsp file: a "
              "96-byte header with the sample rate and eight coefficient pairs, then 8-byte frames of 14 samples.",
              "Frame: byte 0 = coefficient pair (high 4 bits) and scale exponent (low 4), then 14 four-bit codes.\n"
              "sample = ((code << exponent) * 2048 + 1024 + c1 * prev + c2 * prev2) >> 11, clamped to 16 bits",
              lambda b: _dsp_render(b).samples, structured=True, layouts=("normal",)))
