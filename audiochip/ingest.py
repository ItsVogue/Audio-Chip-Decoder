"""Load chip dumps: raw binary, Intel HEX and Motorola S-record."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

RAW_EXT = {".bin", ".rom", ".dat", ".dmp", ".dump", ".img", ".eep", ".eeprom", ".raw", ".epr", ".u"}
HEX_EXT = {".hex", ".ihx", ".ihex", ".h86", ".mcs"}
SREC_EXT = {".s19", ".s28", ".s37", ".srec", ".mot", ".s", ".sx", ".s1", ".s2", ".s3"}
AUDIO_EXT = {".wav", ".flac", ".ogg", ".oga", ".opus", ".mp3", ".aif", ".aiff", ".aifc", ".au", ".snd", ".caf",
             ".voc", ".w64", ".vag", ".vox", ".gsm", ".g721", ".g723", ".g726", ".pcm",
             ".aac", ".adts", ".m4a", ".m4b", ".mp4", ".3gp", ".3g2", ".wma", ".asf", ".xwm", ".xwma", ".amr", ".awb",
             ".spx", ".qcp", ".adx", ".bik", ".bk2", ".binka", ".brstm", ".bfstm", ".genh", ".dsp", ".brr", ".a18"}
# all of the above are read as raw bytes
DUMP_EXT = RAW_EXT | HEX_EXT | SREC_EXT | AUDIO_EXT


class DumpError(ValueError):
    """The file could not be read as a chip dump."""


@dataclass
class Dump:
    path: Path
    data: bytes
    source_format: str                 # "binary" | "Intel HEX" | "Motorola S-record"
    notes: list[str] = field(default_factory=list)
    sha256: str = ""

    def __post_init__(self):
        if not self.sha256:
            self.sha256 = hashlib.sha256(self.data).hexdigest()

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size(self) -> int:
        return len(self.data)


def _assemble(mem: dict[int, bytes], notes: list[str]) -> bytes:
    """Lay address->bytes records out as one image, filling holes with 0xFF."""
    if not mem:
        raise DumpError("the file holds no data records")
    start = min(mem)
    end = max(a + len(d) for a, d in mem.items())
    if end - start > 256 * 1024 * 1024:
        raise DumpError("the address range is larger than 256 MB; this does not look like a chip dump")
    image = bytearray(b"\xFF" * (end - start))
    covered = 0
    for addr, d in mem.items():
        image[addr - start: addr - start + len(d)] = d
        covered += len(d)
    if start:
        notes.append(f"Data starts at address 0x{start:X}; the image begins there.")
    if covered < len(image):
        notes.append(f"{len(image) - covered:,} bytes had no record and were filled with FF.")
    return bytes(image)


def parse_intel_hex(text: str) -> tuple[bytes, list[str]]:
    mem: dict[int, bytes] = {}
    base = 0
    notes: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise DumpError(f"line {lineno} does not start with ':'")
        try:
            rec = bytes.fromhex(line[1:])
        except ValueError:
            raise DumpError(f"line {lineno} has characters that are not hex digits") from None
        if len(rec) < 5 or len(rec) != rec[0] + 5:
            raise DumpError(f"line {lineno} has the wrong length")
        if sum(rec) & 0xFF:
            raise DumpError(f"line {lineno} fails its checksum")
        n, addr, typ, payload = rec[0], int.from_bytes(rec[1:3], "big"), rec[3], rec[4:4 + rec[0]]
        if typ == 0:
            mem[base + addr] = payload
        elif typ == 1:
            break
        elif typ == 2:
            base = int.from_bytes(payload, "big") << 4
        elif typ == 4:
            base = int.from_bytes(payload, "big") << 16
        # types 3 and 5 are start addresses: not data
    notes.append("Every Intel HEX record passed its checksum.")
    return _assemble(mem, notes), notes


def parse_srec(text: str) -> tuple[bytes, list[str]]:
    mem: dict[int, bytes] = {}
    notes: list[str] = []
    addr_len = {"1": 2, "2": 3, "3": 4}
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if line[0] not in "Ss" or len(line) < 4:
            raise DumpError(f"line {lineno} is not an S-record")
        kind = line[1]
        try:
            rec = bytes.fromhex(line[2:])
        except ValueError:
            raise DumpError(f"line {lineno} has characters that are not hex digits") from None
        if len(rec) != rec[0] + 1:
            raise DumpError(f"line {lineno} has the wrong length")
        if (sum(rec) & 0xFF) != 0xFF:
            raise DumpError(f"line {lineno} fails its checksum")
        if kind in addr_len:
            al = addr_len[kind]
            addr = int.from_bytes(rec[1:1 + al], "big")
            mem[addr] = rec[1 + al:-1]
    notes.append("Every S-record passed its checksum.")
    return _assemble(mem, notes), notes


def _looks_like(raw: bytes, first: bytes) -> bool:
    head = raw[:4096]
    if not head.lstrip().startswith(first) and not head.lstrip().startswith(first.lower()):
        return False
    try:
        head.decode("ascii")
    except UnicodeDecodeError:
        return False
    return True


def load_dump(path: str | Path) -> Dump:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise DumpError(f"could not read the file: {e.strerror or e}") from None
    if not raw:
        raise DumpError("the file is empty")
    ext = path.suffix.lower()
    if ext in HEX_EXT or (ext not in SREC_EXT and _looks_like(raw, b":")):
        if _looks_like(raw, b":"):
            data, notes = parse_intel_hex(raw.decode("ascii", "replace"))
            return Dump(path, data, "Intel HEX", notes)
    if ext in SREC_EXT or _looks_like(raw, b"S0") or _looks_like(raw, b"S1") or _looks_like(raw, b"S2") \
            or _looks_like(raw, b"S3"):
        if _looks_like(raw, b"S"):
            data, notes = parse_srec(raw.decode("ascii", "replace"))
            return Dump(path, data, "Motorola S-record", notes)
    return Dump(path, raw, "binary")


def is_dump_candidate(path: Path) -> bool:
    """Used when scanning folders: skip obvious non-dumps."""
    if not path.is_file() or path.name.startswith("."):
        return False
    return path.suffix.lower() in DUMP_EXT
