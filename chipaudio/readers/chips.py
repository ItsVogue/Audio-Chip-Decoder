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


@dataclass
class ChipInfo:
    family: str            # "spi_flash" | "spi_eeprom" | "i2c_eeprom"
    name: str
    size: int              # bytes; 0 when it could not be worked out
    addr_bytes: int = 3    # SPI only
    i2c_type: int = -1     # index into WCH's EEPROM type list, I2C only
    id_bytes: bytes = b""
    note: str = ""

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
            return ChipInfo("spi_flash", f"{maker} AT45 DataFlash", 0, id_bytes=bytes(jedec[:3]),
                            note="AT45 DataFlash uses its own read commands, which this version does not have.")
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
