# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""Reading serial memory chips through any programmer that can do an SPI transfer. Read-only.

A programmer is represented by a small "transport" object:

    name        shown to the user
    max_chunk   most data bytes it should be asked for in one transfer
    has_i2c     True if it also offers read_eeprom(type_index, addr, length)
    describe()  one line about the hardware found
    transfer(cmd, n)   chip select low, send cmd, then clock in and return n bytes
    close()

Only read commands are ever sent:
  0x9F  read ID          0x03  read data (0x13 with a 4-byte address above 16 MB)
  0xD7  read status of an AT45 DataFlash, to learn its page size
No erase, write or write-enable opcode appears anywhere in the readers.
"""
from __future__ import annotations

from .base import Cancelled, CancelCheck, Progress, ReaderError
from .chips import ChipInfo, spi_flash_from_id

I2C_CHUNK = 256


class FullDuplex:
    """For programmers whose SPI call sends and receives at once: build transfer() from spi()."""

    def transfer(self, cmd: bytes, n: int) -> bytes:
        return self.spi(bytes(cmd) + b"\x00" * n)[len(cmd):len(cmd) + n]


class SerialReader:
    def __init__(self, transport):
        self.t = transport

    @property
    def name(self) -> str:
        return getattr(self.t, "name", "programmer")

    @property
    def has_i2c(self) -> bool:
        return bool(getattr(self.t, "has_i2c", False))

    def describe(self) -> str:
        return self.t.describe()

    def close(self) -> None:
        self.t.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------ identify
    def jedec_id(self) -> bytes:
        first = self.t.transfer(b"\x9F", 3)
        second = self.t.transfer(b"\x9F", 3)
        if first != second:
            raise ReaderError("The chip gave two different answers to the same ID request "
                              f"({first.hex(' ').upper()} then {second.hex(' ').upper()}). The contact is poor: "
                              "reseat the chip or clip and try again.")
        return first

    def identify(self) -> ChipInfo | None:
        """Ask a 25-series flash chip who it is. None when nothing answers."""
        return spi_flash_from_id(self.jedec_id())

    # ---------------------------------------------------------------- read
    def read(self, chip: ChipInfo, progress: Progress | None = None, cancel: CancelCheck | None = None) -> bytes:
        if not chip.size:
            raise ReaderError("The chip size is not known. Pick the size before reading.")
        if chip.family == "i2c_eeprom":
            if not self.has_i2c:
                raise ReaderError(f"The {self.name} reads SPI chips only. 24-series I2C chips need a CH341A, "
                                  "a CH347 or an FT232H board.")
            return self._read_i2c(chip, progress, cancel)
        if chip.family == "at45":
            return self._read_at45(chip, progress, cancel)
        return self._read_spi(chip, progress, cancel)

    def _chunk(self) -> int:
        return int(getattr(self.t, "max_chunk", 2048))

    def _read_at45(self, chip: ChipInfo, progress, cancel) -> bytes:
        """AT45DB DataFlash: pages of 264, 528 or 1056 bytes unless the chip was switched to binary pages."""
        status = self.t.transfer(b"\xD7", 1)[0]
        psize = chip.page_size
        binary = bool(status & 1)
        if binary:
            psize = {264: 256, 528: 512, 1056: 1024}[psize]
        shift = {256: 8, 264: 9, 512: 9, 528: 10, 1024: 10, 1056: 11}[psize]
        size = chip.pages * psize
        out = bytearray()
        while len(out) < size:
            if cancel and cancel():
                raise Cancelled("Reading was cancelled.")
            pos = len(out)
            n = min(self._chunk(), size - pos)
            addr = ((pos // psize) << shift) | (pos % psize)
            out += self.t.transfer(b"\x03" + addr.to_bytes(3, "big"), n)
            if progress:
                progress(len(out), size, "")
        return bytes(out)

    def _read_spi(self, chip: ChipInfo, progress, cancel) -> bytes:
        out = bytearray()
        size, ab = chip.size, chip.addr_bytes
        while len(out) < size:
            if cancel and cancel():
                raise Cancelled("Reading was cancelled.")
            addr = len(out)
            n = min(self._chunk(), size - addr)
            if ab == 1 and size > 256:
                n = min(n, 256 - (addr & 0xFF))                 # address bit 8 rides in the opcode
                cmd = bytes([0x03 | (0x08 if addr & 0x100 else 0), addr & 0xFF])
            elif ab == 4:
                cmd = b"\x13" + addr.to_bytes(4, "big")
            else:
                cmd = b"\x03" + addr.to_bytes(ab, "big")
            out += self.t.transfer(cmd, n)
            if progress:
                progress(len(out), size, "")
        return bytes(out)

    def _read_i2c(self, chip: ChipInfo, progress, cancel) -> bytes:
        out = bytearray()
        while len(out) < chip.size:
            if cancel and cancel():
                raise Cancelled("Reading was cancelled.")
            n = min(I2C_CHUNK, chip.size - len(out))
            out += self.t.read_eeprom(chip.i2c_type, len(out), n)
            if progress:
                progress(len(out), chip.size, "")
        return bytes(out)
