"""What the app knows about serial memory chips."""
from __future__ import annotations

from dataclasses import dataclass

MANUFACTURERS = {
    0x01: "Spansion / Cypress", 0x04: "Fujitsu", 0x0B: "XTX", 0x1C: "EON", 0x1F: "Atmel / Adesto",
    0x20: "ST / Micron / XMC", 0x37: "AMIC", 0x5E: "Zbit", 0x62: "Sanyo", 0x68: "BoYa", 0x85: "Puya",
    0x89: "Intel", 0x8C: "ESMT", 0x9D: "ISSI / PMC", 0xA1: "Fudan", 0xBF: "SST / Microchip",
    0xC2: "Macronix", 0xC8: "GigaDevice", 0xE0: "Paragon", 0xEF: "Winbond", 0xF8: "Fidelix",
}
_SST25 = {0x8C: 1 << 18, 0x8D: 1 << 19, 0x8E: 1 << 20,
          0x41: 1 << 21, 0x4A: 1 << 22, 0x4B: 1 << 23}


# AT45DB DataFlash by the density code in its ID: (pages, bytes per page as shipped)
AT45_GEOMETRY = {0x02: (512, 264), 0x03: (1024, 264), 0x04: (2048, 264), 0x05: (4096, 264),
                 0x06: (4096, 528), 0x07: (8192, 528), 0x08: (8192, 1056)}


@dataclass
class ChipInfo:
    family: str            # "spi_flash" | "spi_eeprom" | "i2c_eeprom" | "at45"
    name: str
    size: int              # bytes; 0 when it could not be worked out
    addr_bytes: int = 3    # SPI only
    i2c_type: int = -1     # index into WCH's EEPROM type list, I2C only
    id_bytes: bytes = b""
    note: str = ""
    pages: int = 0         # AT45 DataFlash only
    page_size: int = 0     # AT45 DataFlash only: 264, 528 or 1056 unless the chip was set to binary pages

    def describe(self) -> str:
        size = human_size(self.size) if self.size else "size unknown"
        ident = f" (ID {' '.join(f'{b:02X}' for b in self.id_bytes)})" if self.id_bytes else ""
        return f"{self.name}, {size}{ident}"


def human_size(n: int) -> str:
    if n >= 1 << 20 and n % (1 << 20) == 0:
        return f"{n >> 20} MB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    if n >= 1024 and n % 1024 == 0:
        return f"{n >> 10} KB"
    return f"{n:,} bytes"


def spi_flash_from_id(jedec: bytes) -> ChipInfo | None:
    """Turn a 3-byte JEDEC ID into a chip description. None when nothing answered."""
    if len(jedec) < 3 or jedec[:3] in (b"\x00\x00\x00", b"\xFF\xFF\xFF"):
        return None
    mfr, kind, cap = jedec[0], jedec[1], jedec[2]
    maker = MANUFACTURERS.get(mfr, f"maker code {mfr:02X}")
    size, note = 0, ""
    if mfr == 0xBF and kind == 0x25:
        size = _SST25.get(cap, 0)
    elif mfr == 0x1F:
        family, density = kind >> 5, kind & 0x1F
        if family == 1:
            pages, psize = AT45_GEOMETRY.get(density, (0, 0))
            return ChipInfo("at45", f"{maker} AT45DB DataFlash", pages * psize, id_bytes=bytes(jedec[:3]),
                            pages=pages, page_size=psize,
                            note="" if pages else "This AT45 density code is not one this version knows.")
        if 1 <= density <= 12:
            size = 1 << (density + 15)
    elif 0x08 <= cap <= 0x1F:
        size = 1 << cap
    elif 0x20 <= cap <= 0x25:
        size = 1 << (cap - 0x20 + 26)
    if not size:
        note = "The size code in the ID is not one this version knows. Pick the size by hand."
    return ChipInfo("spi_flash", f"{maker} 25-series SPI flash", size, addr_bytes=4 if size > 1 << 24 else 3,
                    id_bytes=bytes(jedec[:3]), note=note)


# 24-series I2C EEPROMs, in the order of WCH's EEPROM_TYPE list (CH341DLL.H).
I2C_TYPES = [("24C01", 128), ("24C02", 256), ("24C04", 512), ("24C08", 1024), ("24C16", 2048),
             ("24C32", 4096), ("24C64", 8192), ("24C128", 16384), ("24C256", 32768), ("24C512", 65536),
             ("24C1024 / 24CM01", 131072), ("24C2048 / 24CM02", 262144), ("24C4096", 524288)]

# 25/95-series SPI EEPROMs carry no ID. (label, bytes, address bytes)
SPI_EEPROM_TYPES = [("25x010 / 95010", 128, 1), ("25x020 / 95020", 256, 1), ("25x040 / 95040", 512, 1),
                    ("25x080 / 95080", 1024, 2), ("25x160 / 95160", 2048, 2), ("25x320 / 95320", 4096, 2),
                    ("25x640 / 95640", 8192, 2), ("25x128 / 95128", 16384, 2), ("25x256 / 95256", 32768, 2),
                    ("25x512 / 95512", 65536, 2), ("25x1024 / 95M01", 131072, 3), ("25x2048 / 95M02", 262144, 3)]

SPI_FLASH_SIZES = [1 << n for n in range(16, 27)]       # 64 KB to 64 MB, for chips whose ID gives no size


def i2c_chip(index: int) -> ChipInfo:
    name, size = I2C_TYPES[index]
    return ChipInfo("i2c_eeprom", f"{name} I2C EEPROM", size, i2c_type=index)


def spi_eeprom_chip(index: int) -> ChipInfo:
    name, size, addr = SPI_EEPROM_TYPES[index]
    return ChipInfo("spi_eeprom", f"{name} SPI EEPROM", size, addr_bytes=addr)


def spi_flash_chip(size: int) -> ChipInfo:
    return ChipInfo("spi_flash", "25-series SPI flash", size, addr_bytes=4 if size > 1 << 24 else 3)


# ------------------------------------------------------------ chip markings
_FLASH_PREFIX = ("W25Q", "W25X", "W25N", "MX25", "KH25", "GD25", "EN25", "SST25", "M25PX", "M25PE", "M25P", "S25F",
                 "N25Q", "MT25Q", "IS25", "PM25", "AT25DF", "AT25SF", "AT26", "XM25", "ZB25", "P25Q", "BY25", "FM25Q",
                 "XT25", "A25L", "LE25", "PY25")
_EEPROM_PREFIX = ("25LC", "25AA", "25C", "25A", "AT25", "M95", "CAT25", "BR25", "X25", "NV25", "S-25", "S25C", "95")
_AT45_DENSITY = {"011": 0x02, "021": 0x03, "041": 0x04, "081": 0x05, "161": 0x06, "321": 0x07, "642": 0x08}
_EEPROM_KBIT = {"010": 1, "020": 2, "040": 4, "080": 8, "160": 16, "320": 32, "640": 64, "128": 128, "256": 256,
                "512": 512, "1024": 1024, "M01": 1024, "M02": 2048, "01": 1, "02": 2, "04": 4, "08": 8, "16": 16,
                "32": 32, "64": 64}
_I2C_KBIT = {"00": 0, "01": 1, "02": 2, "04": 4, "08": 8, "16": 16, "32": 32, "64": 64, "128": 128, "256": 256,
             "512": 512, "1024": 1024, "1025": 1024, "1026": 1024, "M01": 1024, "2048": 2048, "M02": 2048,
             "4096": 4096}


def _flash_mbit(digits: str) -> float:
    """Megabits from the number in a 25-series flash part name (W25Q64 -> 64, MX25L1606 -> 16)."""
    d = digits.lstrip("0") or "0"
    for big in ("512", "256", "128"):
        if d.startswith(big):
            return float(big)
    two = d[:2]
    table = {"05": 0.5, "5": 0.5, "10": 1, "20": 2, "40": 4, "80": 8, "16": 16, "32": 32, "64": 64}
    if digits[:2] == "05":
        return 0.5
    return float(table.get(two, 0))


def chip_from_marking(text: str) -> tuple[ChipInfo | None, str]:
    """Work out an 8-pin serial chip from what is printed on it.

    Returns (chip, sentence for the user). The chip is None when the marking is not recognised
    or is a kind these readers cannot read.
    """
    import re
    t = re.sub(r"[\s._]", "", text.upper())
    if len(t) < 4:
        return None, ""
    m = re.search(r"AT45DB?(\d{3})", t)
    if m and m.group(1) in _AT45_DENSITY:
        pages, psize = AT45_GEOMETRY[_AT45_DENSITY[m.group(1)]]
        chip = ChipInfo("at45", f"AT45DB{m.group(1)} DataFlash", pages * psize, pages=pages, page_size=psize)
        return chip, (f"AT45DB{m.group(1)}: DataFlash, {human_size(chip.size)}. Its pin order differs from "
                      "25-series chips; Detect chip confirms it.")
    if re.search(r"(^|[A-Z-])93[A-Z]{0,2}\d{2}", t):
        return None, ("93-series (Microwire) chips cannot be read by this reader. They hold at most 2 KB. "
                      "An XGecu programmer reads them.")
    def flash(prefixes):
        for prefix in prefixes:
            i = t.find(prefix)
            if i >= 0:
                m = re.search(r"(\d{2,5})", t[i + len(prefix):])
                mbit = _flash_mbit(m.group(1)) if m else 0
                if not mbit:
                    return None, "This looks like 25-series flash. Press Detect chip: the chip reports its own size."
                chip = spi_flash_chip(int(mbit * 131072))
                return chip, (f"{t}: 25-series SPI flash, {human_size(chip.size)}. "
                              "Detect chip confirms this from the chip itself.")
        return None

    found = flash(_FLASH_PREFIX)
    if found:
        return found
    m = re.search(r"(?:^|[A-Z-])24[A-Z]{0,2}?(M0[12]|\d{2,4})(?!\d)", t)
    if m and _I2C_KBIT.get(m.group(1)):
        size = _I2C_KBIT[m.group(1)] * 128
        for idx, (_name, sz) in enumerate(I2C_TYPES):
            if sz == size:
                return i2c_chip(idx), f"{t}: 24-series I2C EEPROM, {human_size(size)} ({I2C_TYPES[idx][0]})."
    for prefix in _EEPROM_PREFIX:
        i = t.find(prefix)
        if i >= 0 and (i == 0 or prefix[0].isalpha() or not t[i - 1].isdigit()):
            m = re.match(r"(M0[12]|1024|\d{3}|\d{2})", t[i + len(prefix):])
            if m and m.group(1) in _EEPROM_KBIT:
                size = _EEPROM_KBIT[m.group(1)] * 128
                for idx, (_name, sz, _ab) in enumerate(SPI_EEPROM_TYPES):
                    if sz == size:
                        return spi_eeprom_chip(idx), (f"{t}: 25/95-series SPI EEPROM, {human_size(size)} "
                                                      f"({SPI_EEPROM_TYPES[idx][0]}).")
    found = flash(("25Q", "25X", "25L", "25P", "25VF", "25F", "25D", "25B", "25S")) if t.startswith("25") else None
    if found:
        return found
    return None, "Not a marking this app knows. Pick the type and size below, or press Detect chip."
