"""Facts about the selected file or track, for the "About this selection" box."""
from __future__ import annotations

import math

import numpy as np

from .codecs import CODECS, is_structured
from .detect import spectrum

# Sizes that old memory chips come in, with the usual part numbers.
CHIP_SIZES = {
    2048: "2716", 4096: "2732", 8192: "2764 / 28C64", 16384: "27128", 32768: "27C256 / 28C256",
    65536: "27C512 / 28F512", 131072: "27C010 / 29F010", 262144: "27C020 / 29F020", 524288: "27C040 / 29F040",
    1048576: "27C080 / 27C801 / 29F080", 2097152: "27C160 / 29F016", 4194304: "27C322 / 29F032",
    8388608: "8 MB flash", 16777216: "16 MB flash",
}


def human_bytes(n: int) -> str:
    if n >= 1 << 20:
        return f"{n:,} bytes ({n / (1 << 20):.4g} MB)"
    if n >= 1024:
        return f"{n:,} bytes ({n / 1024:.4g} KB)"
    return f"{n:,} bytes"


def _db(value: float) -> str:
    return "silent" if value <= 0 else f"{20 * math.log10(value / 32768.0):.1f} dB"


def audio_facts(x: np.ndarray | None, rate: int) -> dict:
    """Level and frequency measurements of decoded audio (int16, frames x channels)."""
    if x is None or not len(x):
        return {}
    mono = x.astype(np.float64).mean(axis=1) if x.ndim == 2 else x.astype(np.float64)
    peak = float(np.abs(x.astype(np.int32)).max())
    dc = float(mono.mean())
    rms = float(np.sqrt(np.mean((mono - dc) ** 2)))
    clipped = int((np.abs(x.astype(np.int32)) >= 32767).sum())
    part = mono[: 1 << 21]
    p = spectrum(part - part.mean())
    total = float(p[1:].sum())
    edge = 0.0
    if total > 0:
        cum = np.cumsum(p[1:]) / total
        edge = float((np.searchsorted(cum, 0.95) + 1) / (len(p) - 1))
    return {"peak": peak, "rms": rms, "dc": dc, "clipped": clipped, "edge": edge,
            "peak_text": f"{100 * peak / 32768:.0f}% of full ({_db(peak)})",
            "rms_text": _db(rms),
            "dc_text": f"{100 * dc / 32768:+.1f}% of full volume",
            "band_text": f"up to {edge * rate / 2000:.1f} kHz" if edge and rate else "",
            "clip_text": "none" if not clipped else f"{clipped:,} samples at the limit"}


def selection_facts(coll, track, chip, x: np.ndarray | None, rate: int, sounds: int, pad_from: int | None) -> list:
    """Rows of (label, value, explanation) describing the selection."""
    hyp = coll.hyp_for(track)
    rows: list[tuple[str, str, str]] = []
    add = lambda label, value, tip="": rows.append((label, value, tip))      # noqa: E731
    if chip is not None:
        d, st = chip.dump, chip.structure
        add("File", d.name, str(d.path))
        fmt = d.source_format
        add("Stored as", fmt, "The file format the dump was saved in. All of them hold the same bytes.")
        add("Size", human_bytes(d.size), "How many bytes the chip holds.")
        if d.size in CHIP_SIZES:
            add("Chips this size", CHIP_SIZES[d.size], "Memory chips come in fixed sizes, so the size of a dump "
                                                       "usually points to the chip type it was read from.")
        sound = st.content_end - st.content_start
        pad = d.size - sound
        fill = f" of {d.data[-1]:02X}" if st.has_end_padding and d.size else ""
        add("Sound data", f"{sound:,} bytes" + (f" + {pad:,}{fill} filler" if pad else ", no filler"),
            "Chips are filled out to their full size with a repeated byte after the sound ends.")
        add("Role", {"chip": f"chip {track.members.index(chip) + 1} of {len(track.members)} in {track.name}"
                     if chip in track.members else "in track"}.get(chip.role, chip.role),
            chip.role_note or "Where this file sits in its track.")
        add("Fingerprint", d.sha256[:16] + "...", "SHA-256 of the file: " + d.sha256 + "\n"
            "Two dumps with the same fingerprint are identical. Read a chip twice to check a read.")
        extra = []
        if st.embedded:
            extra.append(", ".join(sorted({e.kind for e in st.embedded})) + " file inside")
        if st.repeat_of:
            extra.append(f"repeats every {st.repeat_of:,} bytes")
        if extra:
            add("Also found", "; ".join(extra), "See Details, Files for the full list.")
    else:
        m = track.members
        total = sum(c.dump.size for c in m)
        add("Track", track.name, str(track.folder))
        add("Chips", f"{len(m)} in use" + (f", {len(track.chips) - len(m)} left out" if len(track.chips) > len(m)
                                           else ""), "Duplicates, blank dumps and pre-joined copies are left out.")
        sizes = sorted({c.dump.size for c in m})
        add("Size", human_bytes(total), "All the chips of the track together.")
        if len(sizes) == 1 and sizes[0] in CHIP_SIZES:
            add("Chips this size", CHIP_SIZES[sizes[0]], "Memory chips come in fixed sizes, so the size of each "
                                                         "dump usually points to the chip type it was read from.")
        add("Order", track.order_basis or "single dump", "How the playing order of the chips was decided.")
        if m and m[-1].structure.has_end_padding:
            add("Filler", f"{m[-1].structure.trail_len:,} bytes at the end",
                "Removed on export unless you untick it.")
    if hyp is not None and x is not None and len(x) and rate:
        frames = len(x)
        content = pad_from if pad_from else frames
        secs = content / rate
        add("Length", f"{int(secs // 60)}:{secs % 60:04.1f}, {content:,} samples",
            "At the speed now in force. Filler at the end is not counted.")
        c = CODECS[hyp.codec]
        if is_structured(hyp):
            add("Storage", c.name, c.plain)
        else:
            bits = c.bytes_per_sample * 8
            add("Storage", f"{bits:g}-bit, {'mono' if hyp.channels == 1 else f'{hyp.channels} channels'}, "
                           f"{bits * hyp.channels * rate / 1000:.4g} kbit per second",
                "How much chip space each second of sound takes with this encoding at this speed.")
        a = audio_facts(x[:content] if content <= frames else x, rate)
        add("Loudest peak", a["peak_text"], "The largest sample. Near 100% means the recording uses the full range.")
        add("Average level", a["rms_text"], "RMS level relative to full volume. Speech is typically -25 to -12 dB.")
        if abs(a["dc"]) > 330:
            add("Offset", a["dc_text"], "The wave is not centred on zero. Harmless; export can remove it.")
        if a["clipped"]:
            add("At the limit", a["clip_text"], "Samples at the largest possible value: the recording was clipped, "
                                                "or the encoding is wrong.")
        if a["band_text"]:
            add("Frequencies", f"95% of energy {a['band_text']}",
                "At the speed now in force. Speech sits mostly below 3.5 kHz; music reaches higher.")
        add("Separate sounds", str(sounds) if sounds else "none found", "Listed under Sounds in the selection.")
    return rows
