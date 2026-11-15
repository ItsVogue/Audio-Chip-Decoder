# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""Sound-chip formats that are more than a plain stream of samples.

Some voice chips store an index of sounds, a command stream, or parameters for
a speech synthesiser rather than a waveform. Each format here turns a whole
dump into audio plus the list of separate sounds found in it.

* NEC uPD7759: header with a signature and an offset table, then blocks of
  4-bit ADPCM with their own playback speed.
* OKI MSM6295: table of up to 127 start/end addresses, then OKI ADPCM.
* TI LPC speech (TMS5220, TMS5110, TMC0281): frames of pitch, loudness and
  filter settings, played through a model of the chip's synthesiser.

The decoders follow the behaviour documented by the MAME project
(upd7759.cpp, okim6295.cpp, tms5220.cpp; BSD-3-Clause).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .lpc_tables import KBITS, TABLES


def _check_cancel() -> None:
    from .codecs import check_cancel
    check_cancel()


@dataclass
class Sound:
    start: int            # first sample
    end: int              # one past the last sample
    label: str = ""
    offset: int = -1      # byte address in the dump, when known


@dataclass
class Rendered:
    samples: np.ndarray                       # int16, mono
    rate: int | None = None                   # playback speed if the format states it
    sounds: list[Sound] = field(default_factory=list)
    note: str = ""


def _join(pieces: list[tuple[np.ndarray, str, int]], gap: int) -> tuple[np.ndarray, list[Sound]]:
    """Lay sounds end to end with a short silence between them."""
    out, sounds, pos = [], [], 0
    silence = np.zeros(gap, dtype=np.int16)
    for x, label, offset in pieces:
        if not len(x):
            continue
        if out:
            out.append(silence)
            pos += gap
        out.append(x)
        sounds.append(Sound(pos, pos + len(x), label, offset))
        pos += len(x)
    return (np.concatenate(out) if out else np.zeros(0, dtype=np.int16)), sounds


# ------------------------------------------------------------------ NEC uPD7759
UPD_MAGIC = bytes((0x5A, 0xA5, 0x69, 0x55))
UPD_CLOCK = 640_000
_UPD_STEP = (
    (0, 0, 1, 2, 3, 5, 7, 10, 0, 0, -1, -2, -3, -5, -7, -10),
    (0, 1, 2, 3, 4, 6, 8, 13, 0, -1, -2, -3, -4, -6, -8, -13),
    (0, 1, 2, 4, 5, 7, 10, 15, 0, -1, -2, -4, -5, -7, -10, -15),
    (0, 1, 3, 4, 6, 9, 13, 19, 0, -1, -3, -4, -6, -9, -13, -19),
    (0, 2, 3, 5, 8, 11, 15, 23, 0, -2, -3, -5, -8, -11, -15, -23),
    (0, 2, 4, 7, 10, 14, 19, 29, 0, -2, -4, -7, -10, -14, -19, -29),
    (0, 3, 5, 8, 12, 16, 22, 33, 0, -3, -5, -8, -12, -16, -22, -33),
    (1, 4, 7, 10, 15, 20, 29, 43, -1, -4, -7, -10, -15, -20, -29, -43),
    (1, 4, 8, 13, 18, 25, 35, 53, -1, -4, -8, -13, -18, -25, -35, -53),
    (1, 6, 10, 16, 22, 31, 43, 64, -1, -6, -10, -16, -22, -31, -43, -64),
    (2, 7, 12, 19, 27, 37, 51, 76, -2, -7, -12, -19, -27, -37, -51, -76),
    (2, 9, 16, 24, 34, 46, 64, 96, -2, -9, -16, -24, -34, -46, -64, -96),
    (3, 11, 19, 29, 41, 57, 79, 117, -3, -11, -19, -29, -41, -57, -79, -117),
    (4, 13, 24, 36, 50, 69, 96, 143, -4, -13, -24, -36, -50, -69, -96, -143),
    (4, 16, 29, 44, 62, 85, 118, 175, -4, -16, -29, -44, -62, -85, -118, -175),
    (6, 20, 36, 54, 76, 104, 144, 214, -6, -20, -36, -54, -76, -104, -144, -214),
)
_UPD_STATE = (-1, -1, 0, 0, 1, 2, 2, 3, -1, -1, 0, 0, 1, 2, 2, 3)
_UPD_MAX_SECONDS = 120          # per sound; guards against a corrupt stream that never ends


def upd7759_present(b: np.ndarray) -> bool:
    return len(b) >= 16 and bytes(b[1:5]) == UPD_MAGIC


def _upd_message(b: list[int], off: int) -> list[tuple[int, list[int]]]:
    """Decode one sound. Returns pieces of (clock ticks per sample, samples)."""
    n = len(b)
    pieces: list[tuple[int, list[int]]] = []
    sample = state = 0
    first_valid = False
    repeat_count = repeat_off = 0
    ticks = 0
    limit = _UPD_MAX_SECONDS * UPD_CLOCK
    while off < n and ticks < limit:
        if repeat_count:
            repeat_count -= 1
            off = repeat_off
        h = b[off]
        off += 1
        kind = h & 0xC0
        if kind == 0x00:                                   # silence
            if h == 0 and first_valid:
                break
            length = 1024 * ((h & 0x3F) + 1)
            sample = state = 0
            pieces.append((length, [0]))
            ticks += length
        elif kind == 0xC0:                                 # repeat the next block
            repeat_count = (h & 7) + 1
            repeat_off = off
        else:
            per = ((h & 0x3F) + 1) * 4
            if kind == 0x40:
                count = 256
            else:
                if off >= n:
                    break
                count = b[off] + 1
                off += 1
            out = []
            while count > 0 and off < n:
                byte = b[off]
                off += 1
                for nib in (byte >> 4, byte & 15):
                    sample += _UPD_STEP[state][nib]
                    state += _UPD_STATE[nib]
                    state = 0 if state < 0 else 15 if state > 15 else state
                    out.append(sample)
                    count -= 1
                    if count == 0:
                        break
            pieces.append((per, out))
            ticks += per * len(out)
        if h:
            first_valid = True
    return pieces


def upd7759_render(b: np.ndarray) -> Rendered:
    if not upd7759_present(b):
        return Rendered(np.zeros(0, dtype=np.int16), None, [], "No uPD7759 signature at the start of the dump.")
    data = b.tolist()
    count = data[0] + 1
    offsets = [(data[5 + 2 * i] << 8) | data[6 + 2 * i] for i in range(count) if 6 + 2 * i < len(data)]
    # uPD7759 tables hold half the address; the smaller uPD7756 holds it directly.
    shift = 1 if all((o << 1) < len(data) for o in offsets) else 0
    messages = []
    weight: dict[int, int] = {}
    for i, o in enumerate(offsets):
        start = (o << shift) + 1
        pieces = _upd_message(data, start) if start < len(data) else []
        for per, xs in pieces:
            if len(xs) > 1:
                weight[per] = weight.get(per, 0) + len(xs)
        messages.append((i, start, pieces))
    per_out = max(weight, key=weight.get) if weight else 80
    rate = round(UPD_CLOCK / per_out)
    rendered = []
    for i, start, pieces in messages:
        parts = []
        for per, xs in pieces:
            if len(xs) == 1:                               # silence: a duration, not samples
                parts.append(np.zeros(max(per // per_out, 1), dtype=np.float64))
            elif per == per_out:
                parts.append(np.asarray(xs, dtype=np.float64))
            else:                                          # block at another speed: resample
                src = np.asarray(xs, dtype=np.float64)
                m = max(int(len(src) * per / per_out), 1)
                parts.append(np.interp(np.arange(m) * per_out / per, np.arange(len(src)), src))
        x = np.concatenate(parts) if parts else np.zeros(0)
        # trim the lead-in and tail silence blocks so each sound starts and ends on audio
        nz = np.flatnonzero(x)
        x = x[nz[0]: nz[-1] + 1] if len(nz) else x[:0]
        rendered.append((np.clip(x * 128, -32768, 32767).astype(np.int16), f"Sound {i}", start))
    samples, sounds = _join(rendered, rate // 4)
    note = (f"NEC uPD7759 signature found. The header lists {count} sounds; {len(sounds)} hold audio. "
            f"The blocks ask for {rate:,} Hz on the standard 640 kHz clock.")
    return Rendered(samples, rate, sounds, note)


# ----------------------------------------------------------------- OKI MSM6295
OKI_BANK = 0x40000


def oki_table_entries(b: np.ndarray, base: int = 0) -> list[tuple[int, int, int]]:
    """Valid (number, start, end) phrases of the table at `base`, or [] if it is not a table."""
    bank = b[base: base + OKI_BANK]
    if len(bank) < 0x800:
        return []
    t = bank[:0x400].reshape(128, 8).astype(np.int64)
    start = (t[:, 0] << 16) | (t[:, 1] << 8) | t[:, 2]
    stop = (t[:, 3] << 16) | (t[:, 4] << 8) | t[:, 5]
    blank = (t == 0).all(axis=1) | (t == 0xFF).all(axis=1)
    valid = ~blank & (start >= 0x400) & (stop > start) & (stop < len(bank)) \
        & (t[:, 6] == 0) & (t[:, 7] == 0)
    valid[0] = False
    used = ~blank
    used[0] = False
    # every used slot must be a sensible phrase, and there must be a few of them
    if valid.sum() < 2 or (used & ~valid).sum() > 0:
        return []
    return [(i, int(start[i]), int(stop[i])) for i in np.flatnonzero(valid)]


def oki_table_render(b: np.ndarray) -> Rendered:
    from .codecs import oki_decode_nibbles, _nibbles
    pieces = []
    banks = 0
    for base in range(0, len(b), OKI_BANK):
        entries = oki_table_entries(b, base)
        if not entries:
            continue
        banks += 1
        seen = set()
        for num, start, stop in entries:
            if (start, stop) in seen:                      # several slots often share one phrase
                continue
            seen.add((start, stop))
            x, _, _ = oki_decode_nibbles(_nibbles(b[base + start: base + stop + 1], True))
            label = f"Phrase {num}" if base == 0 else f"Bank {base // OKI_BANK}, phrase {num}"
            pieces.append((x, label, base + start))
    samples, sounds = _join(pieces, 2000)
    note = (f"OKI MSM6295 phrase table found in {banks} bank{'s' if banks != 1 else ''}: "
            f"{len(sounds)} separate phrases." if sounds else "No MSM6295 phrase table found.")
    return Rendered(samples, None, sounds, note)


# -------------------------------------------------------------------- TI LPC
LPC_RATE = 8000
LPC_MAX_BYTES = 4 << 20
_LPC_MAX_FRAMES = 800           # 20 seconds; longer than any real phrase
_LPC_MIN_FRAMES = 8
_LPC_ROUGH_LIMIT = 0.55         # random data scores about 1.0, speech about 0.25


class _BitTable:
    """Value of the next n bits at any bit position, for the widths LPC frames use."""

    def __init__(self, b: np.ndarray):
        bits = np.unpackbits(b, bitorder="little")          # the chip takes bit 0 of each byte first
        self.nbits = len(bits)
        pad = np.concatenate([bits, np.zeros(8, dtype=np.uint8)]).astype(np.int32)
        self.v = {}
        self.a = {}                                         # the same values as arrays, for whole-dump tests
        for n in (1, 3, 4, 5, 6):
            acc = np.zeros(self.nbits, dtype=np.int32)
            for i in range(n):
                acc = (acc << 1) | pad[i: i + self.nbits]
            self.a[n] = acc
            self.v[n] = acc.tolist()

    def can_reach_stop(self, pitch_bits: int) -> np.ndarray:
        """For every byte offset: does a phrase starting there reach a stop code within 1,024 frames?

        Where a frame starts decides where the next one starts, so every start position has one
        fixed path through the data. Following all paths at once (doubling the look-ahead each
        round) answers the question for the whole dump in a moment. Offsets that fail cannot
        hold a phrase and are skipped; without this, data that is not LPC at all made the search
        walk hundreds of frames from every single byte.
        """
        n = self.nbits
        e = self.a[4]
        idx = np.arange(n, dtype=np.int64)
        rep = self.a[1][np.minimum(idx + 4, n - 1)]
        pitch = self.a[pitch_bits][np.minimum(idx + 5, n - 1)]
        length = np.where(e == 0, 4, np.where(rep == 1, 5 + pitch_bits,
                                              5 + pitch_bits + 18 + np.where(pitch != 0, 21, 0)))
        jump = np.minimum(idx + length, n).astype(np.int32)   # n is the "ran off the end" position
        hit = e == 15
        hit[max(n - 3, 0):] = False                           # too close to the end to be read as a code
        jump = np.append(np.where(hit, n, jump), n).astype(np.int32)
        hit = np.append(hit, False)
        for _ in range(10):
            hit = hit | hit[jump]
            jump = jump[jump]
        return hit[:-1][::8].copy()


def _lpc_parse(bt: _BitTable, pos: int, pitch_bits: int, strict: bool):
    """Read one phrase starting at bit `pos`.

    Returns (frames, end bit, roughness) or None. A frame is (energy, repeat, pitch, k or None).
    With `strict`, parsing stops as soon as the frames stop looking like speech; this is what
    makes scanning a whole dump for phrases affordable.
    """
    v1, v3, v4, v5 = bt.v[1], bt.v[3], bt.v[4], bt.v[5]
    vp = bt.v[pitch_bits]
    pitch_norm = (1 << pitch_bits) / 3.0
    limit = bt.nbits - 4
    frames = []
    rough = 0.0
    terms = 0
    pe = pp = -1
    last = None
    while pos <= limit and len(frames) < _LPC_MAX_FRAMES:
        e = v4[pos]
        pos += 4
        if e == 15:
            # real phrases rise and fall; a run of identical frames is a fill pattern, not speech
            if len(frames) < _LPC_MIN_FRAMES or not terms or len({f[0] for f in frames}) < 3:
                return None
            r = rough / terms
            if strict and r > _LPC_ROUGH_LIMIT:
                return None
            return frames, pos, r
        if e == 0:
            frames.append((0, 0, 0, None))
            pe = pp = -1
            continue
        if pos + 1 + pitch_bits > bt.nbits:
            return None
        rep = v1[pos]
        p = vp[pos + 1]
        pos += 1 + pitch_bits
        k = None
        if not rep:
            if pos + 18 > bt.nbits:
                return None
            k = [v5[pos], v5[pos + 5], v4[pos + 10], v4[pos + 14]]
            pos += 18
            if p:
                if pos + 21 > bt.nbits:
                    return None
                k += [v4[pos], v4[pos + 4], v4[pos + 8], v3[pos + 12], v3[pos + 15], v3[pos + 18]]
                pos += 21
        elif last is None:
            return None                                    # a repeat frame with nothing to repeat
        if pe > 0:
            rough += abs(e - pe) / 5.0
            terms += 1
            if p and pp > 0:
                rough += abs(p - pp) / pitch_norm
                terms += 1
            if k is not None and last is not None:
                rough += (abs(k[0] - last[0]) + abs(k[1] - last[1])) / 10.7 \
                    + (abs(k[2] - last[2]) + abs(k[3] - last[3])) / 5.3
                terms += 4
                if len(k) > 4 and len(last) > 4:
                    rough += (abs(k[4] - last[4]) + abs(k[5] - last[5]) + abs(k[6] - last[6])) / 5.3
                    terms += 3
        pe, pp = e, p
        if k is not None:
            last = k
        frames.append((e, rep, p, k))
        if strict and terms >= 12 and rough / terms > _LPC_ROUGH_LIMIT * 1.6:
            return None
    return None


def lpc_scan(b: np.ndarray, pitch_bits: int, max_bytes: int | None = None):
    """Find every phrase in a dump. Returns [(byte offset, frames, end byte)]."""
    if max_bytes:
        b = b[:max_bytes]
    bt = _BitTable(np.ascontiguousarray(b))
    found = []
    o = 0
    n = len(b)
    if n < 2:
        return found
    possible = bt.can_reach_stop(pitch_bits).tolist()
    while o < n:
        if not o & 2047:
            _check_cancel()
        got = _lpc_parse(bt, o * 8, pitch_bits, True) if possible[o] else None
        if got:
            frames, end_bit, _ = got
            end = (end_bit + 7) // 8
            found.append((o, frames, end))
            o = end
        else:
            o += 1
    return found


def lpc_synth(frames, variant: str) -> np.ndarray:
    """Play frames through a model of the chip: excitation, then a 10-stage lattice filter."""
    t = TABLES[variant]
    energy_t, pitch_t, k_t, chirp = t["energy"], t["pitch"], t["k"], t["chirp"]
    out = np.zeros(len(frames) * 200 + 200, dtype=np.float64)
    old_e = old_p = 0.0
    old_k = [0.0] * 10
    x = [0.0] * 10
    rng = 0x1FFF
    pitch_count = 0
    old_silent, old_unvoiced = True, True
    n = 0
    kidx = [0] * 10
    for (e, rep, p, k) in list(frames) + [(0, 0, 0, None)]:
        if e and k is not None:
            kidx = list(k) + [0] * (10 - len(k))
        silent = e == 0
        unvoiced = p == 0
        tgt_e = float(energy_t[e])
        tgt_p = 0.0 if silent else float(pitch_t[p])
        tgt_k = [0.0] * 10 if silent else [k_t[i][kidx[i]] / 512.0 if (i < 4 or not unvoiced) else 0.0
                                           for i in range(10)]
        # the chip does not glide between voiced and unvoiced sound, or out of silence
        inhibit = ((not old_unvoiced and unvoiced) or (old_unvoiced and not unvoiced)
                   or (old_silent and not silent) or (old_unvoiced and silent))
        for ip in range(8):
            f = (1.0 if ip == 7 else 0.0) if inhibit else (ip + 1) / 8.0
            cur_e = old_e + (tgt_e - old_e) * f
            cur_p = old_p + (tgt_p - old_p) * f
            cur_k = [old_k[i] + (tgt_k[i] - old_k[i]) * f for i in range(10)]
            voiced = (not unvoiced and not silent) if ip == 7 else (not old_unvoiced and not old_silent)
            for _ in range(25):
                if voiced:
                    exc = chirp[pitch_count] if pitch_count < 51 else chirp[51]
                else:
                    exc = -64 if rng & 1 else 64
                    for _i in range(5):
                        bit = ((rng >> 12) ^ (rng >> 3) ^ (rng >> 2) ^ rng) & 1
                        rng = ((rng << 1) | bit) & 0x1FFF
                u = cur_e * exc / 8.0
                for i in range(9, -1, -1):
                    u -= cur_k[i] * x[i]
                    if i < 9:
                        x[i + 1] = x[i] + cur_k[i] * u
                x[0] = u
                out[n] = u
                n += 1
                pitch_count += 1
                if pitch_count >= cur_p:
                    pitch_count = 0
        old_e, old_p, old_k = tgt_e, tgt_p, tgt_k
        old_silent, old_unvoiced = silent, unvoiced
    return np.clip(out[:n] * 10.0, -32768, 32767).astype(np.int16)


def lpc_render(b: np.ndarray, variant: str) -> Rendered:
    pitch_bits = TABLES[variant]["pitch_bits"]
    if len(b) > LPC_MAX_BYTES:
        raise ValueError(f"LPC speech chips hold a few hundred kilobytes at most; this dump is "
                         f"{len(b) / (1 << 20):.1f} MB, more than the {LPC_MAX_BYTES >> 20} MB this decoder accepts.")
    found = lpc_scan(b, pitch_bits)
    pieces = []
    for off, frames, _ in found:
        _check_cancel()
        pieces.append((lpc_synth(frames, variant), f"Phrase at 0x{off:05X}", off))
    samples, sounds = _join(pieces, LPC_RATE // 4)
    covered = sum(end - off for off, _, end in found)
    note = (f"{len(sounds)} LPC speech phrases found, covering {covered:,} of {len(b):,} bytes. "
            "Each is played through a model of the speech chip at 8,000 Hz."
            if sounds else "No LPC speech phrases found.")
    return Rendered(samples, LPC_RATE, sounds, note)


def lpc_evidence(b: np.ndarray, pitch_bits: int, max_bytes: int = 1 << 17) -> tuple[int, float, float]:
    """(phrases found, share of bytes they cover, mean roughness) for detection."""
    b = b[:max_bytes]
    found = lpc_scan(b, pitch_bits)
    if not found:
        return 0, 0.0, 1.0
    bt = _BitTable(np.ascontiguousarray(b))
    rough = [(_lpc_parse(bt, off * 8, pitch_bits, False) or (0, 0, 1.0))[2] for off, _, _ in found]
    covered = sum(end - off for off, _, end in found)
    return len(found), covered / max(len(b), 1), float(np.mean(rough))


def lpc_encode(frames, pitch_bits: int) -> bytes:
    """Pack frames into the chip's bit stream (used to build test dumps)."""
    bits: list[int] = []

    def put(value: int, n: int) -> None:
        bits.extend((value >> (n - 1 - i)) & 1 for i in range(n))
    widths = KBITS
    for e, rep, p, k in frames:
        put(e, 4)
        if e == 0:
            continue
        put(rep, 1)
        put(p, pitch_bits)
        if rep:
            continue
        for i, kv in enumerate(k):
            put(kv, widths[i])
    put(15, 4)
    while len(bits) % 8:
        bits.append(0)
    return np.packbits(np.array(bits, dtype=np.uint8), bitorder="little").tobytes()
