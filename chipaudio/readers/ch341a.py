"""CH341A USB programmer: 25-series SPI and 24-series I2C chips. Read-only.

Talks to the programmer through WCH's own library (CH341DLLA64.DLL), which is
installed with WCH's CH341PAR driver. Function names, argument order and the
EEPROM type list below are taken from WCH's header, CH341DLL_EN.H.

Only read commands are ever sent:
  SPI  0x9F  read JEDEC ID
  SPI  0x03  read data (0x13 with a 4-byte address above 16 MB)
  I2C  read through CH341ReadEEPROM
No erase, write or write-enable opcode appears anywhere in this file.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from .base import Cancelled, CancelCheck, DriverMissing, NoDevice, Progress, ReaderError
from .chips import ChipInfo, spi_flash_from_id

DRIVER_ZIP_URL = "https://www.wch-ic.com/download/file?id=7"          # WCH's CH341PAR.ZIP
DRIVER_PAGE_URL = "https://www.wch-ic.com/downloads/CH341PAR_ZIP.html"
SPI_CHUNK = 2048            # data bytes per SPI transfer; WCH's limit is 4096 including the command
I2C_CHUNK = 256
STREAM_MODE = 0x81          # bit 7: SPI bytes most-significant-bit first; bits 1-0 = 01: I2C at 100 kHz
CS_D0 = 0x80                # use pin D0 as chip select, driven low for the transfer


def driver_cache_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
    return base / "ChipAudioDecoder" / "driver"


class DllTransport:
    """Thin wrapper over WCH's CH341 library."""

    def __init__(self, index: int = 0):
        if sys.platform != "win32":
            raise DriverMissing("Reading through a CH341A programmer works on Windows only.")
        self.index = index
        self.dll = self._load()
        d = self.dll
        ul = ctypes.c_ulong
        d.CH341OpenDevice.argtypes = [ul]
        d.CH341OpenDevice.restype = ctypes.c_void_p
        d.CH341CloseDevice.argtypes = [ul]
        d.CH341CloseDevice.restype = None
        d.CH341GetVerIC.argtypes = [ul]
        d.CH341GetVerIC.restype = ul
        d.CH341GetDrvVersion.argtypes = []
        d.CH341GetDrvVersion.restype = ul
        d.CH341SetStream.argtypes = [ul, ul]
        d.CH341SetStream.restype = ctypes.c_int
        d.CH341SetTimeout.argtypes = [ul, ul, ul]
        d.CH341SetTimeout.restype = ctypes.c_int
        d.CH341StreamSPI4.argtypes = [ul, ul, ul, ctypes.c_void_p]
        d.CH341StreamSPI4.restype = ctypes.c_int
        d.CH341ReadEEPROM.argtypes = [ul, ctypes.c_int, ul, ul, ctypes.c_void_p]
        d.CH341ReadEEPROM.restype = ctypes.c_int
        handle = d.CH341OpenDevice(index)
        if handle in (None, 0, ctypes.c_void_p(-1).value):
            raise NoDevice("No CH341A programmer was found. Plug it into a USB port, wait a few seconds, "
                           "and try again. If it is plugged in, the driver may not be installed.")
        self.open = True
        d.CH341SetTimeout(index, 5000, 5000)
        if not d.CH341SetStream(index, STREAM_MODE):
            self.close()
            raise ReaderError("The programmer did not accept its settings. Unplug it, plug it back in and retry.")

    @staticmethod
    def _load():
        name = "CH341DLLA64.DLL" if ctypes.sizeof(ctypes.c_void_p) == 8 else "CH341DLL.DLL"
        tried = []
        for candidate in (name, str(driver_cache_dir() / "CH341PAR" / name)):
            try:
                return ctypes.WinDLL(candidate)
            except OSError as e:
                tried.append(f"{candidate}: {e}")
        raise DriverMissing("The CH341A driver is not installed on this PC.")

    def describe(self) -> str:
        ver = self.dll.CH341GetVerIC(self.index)
        chip = {0x10: "CH341", 0x20: "CH341A", 0x30: "CH341A (rev 3)"}.get(ver, f"version 0x{ver:02X}")
        drv = self.dll.CH341GetDrvVersion()
        return f"{chip}, driver version 0x{drv:X}"

    def spi(self, tx: bytes) -> bytes:
        buf = ctypes.create_string_buffer(bytes(tx), len(tx))
        if not self.dll.CH341StreamSPI4(self.index, CS_D0, len(tx), buf):
            raise ReaderError("The programmer stopped answering during an SPI transfer. Check the USB cable.")
        return buf.raw

    def read_eeprom(self, type_index: int, addr: int, length: int) -> bytes:
        buf = ctypes.create_string_buffer(length)
        if not self.dll.CH341ReadEEPROM(self.index, type_index, addr, length, buf):
            raise ReaderError("The programmer stopped answering during an I2C read. Check the USB cable.")
        return buf.raw

    def close(self) -> None:
        if getattr(self, "open", False):
            self.dll.CH341CloseDevice(self.index)
            self.open = False


class SimTransport:
    """A pretend programmer with a pretend chip, for tests and for trying the screens.

    kind: "spi_flash" (answers the ID command), "spi_eeprom", "i2c", or "empty" (nothing fitted).
    flaky: every Nth read returns one wrong byte, to exercise read verification.
    """

    def __init__(self, data: bytes = b"", kind: str = "spi_flash", jedec: bytes = b"\xEF\x40\x12",
                 addr_bytes: int = 3, flaky: int = 0):
        self.data, self.kind, self.jedec, self.addr_bytes, self.flaky = data, kind, jedec, addr_bytes, flaky
        self.calls = 0
        self.opcodes: set[int] = set()

    def describe(self) -> str:
        return "simulated CH341A"

    def _mem(self, addr: int, n: int) -> bytes:
        size = len(self.data)
        if not size:
            out = b"\xFF" * n
        else:                                    # real chips wrap round at the end of their memory
            start = addr % size
            out = (self.data[start:] + self.data * (n // size + 1))[:n]
        self.calls += 1
        if self.flaky and self.calls % self.flaky == 0 and out:
            out = bytes([out[0] ^ 0x10]) + out[1:]
        return out

    def spi(self, tx: bytes) -> bytes:
        op = tx[0]
        self.opcodes.add(op)
        if self.kind in ("empty", "i2c"):
            return b"\xFF" * len(tx)
        if op == 0x9F:
            ident = self.jedec if self.kind == "spi_flash" else b"\xFF\xFF\xFF"
            return (b"\xFF" + ident + b"\xFF" * len(tx))[: len(tx)]
        if op in (0x03, 0x0B, 0x13):
            ab = 4 if op == 0x13 else self.addr_bytes
            addr = int.from_bytes(tx[1:1 + ab], "big")
            if ab == 1 and op & 0x08:
                addr |= 0x100
            return b"\xFF" * (1 + ab) + self._mem(addr, len(tx) - 1 - ab)
        raise AssertionError(f"unexpected SPI opcode 0x{op:02X}: only read commands may be sent")

    def read_eeprom(self, type_index: int, addr: int, length: int) -> bytes:
        if self.kind != "i2c":
            return b"\xFF" * length
        return self._mem(addr, length)

    def close(self) -> None:
        pass


class Ch341a:
    def __init__(self, transport=None):
        self.t = transport if transport is not None else DllTransport()

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
        first = self.t.spi(b"\x9F\x00\x00\x00")[1:4]
        second = self.t.spi(b"\x9F\x00\x00\x00")[1:4]
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
            return self._read_i2c(chip, progress, cancel)
        return self._read_spi(chip, progress, cancel)

    def _read_spi(self, chip: ChipInfo, progress, cancel) -> bytes:
        out = bytearray()
        size, ab = chip.size, chip.addr_bytes
        while len(out) < size:
            if cancel and cancel():
                raise Cancelled("Reading was cancelled.")
            addr = len(out)
            n = min(SPI_CHUNK, size - addr)
            if ab == 1 and size > 256:
                n = min(n, 256 - (addr & 0xFF))                 # address bit 8 rides in the opcode
                cmd = bytes([0x03 | (0x08 if addr & 0x100 else 0), addr & 0xFF])
            elif ab == 4:
                cmd = b"\x13" + addr.to_bytes(4, "big")
            else:
                cmd = b"\x03" + addr.to_bytes(ab, "big")
            rx = self.t.spi(cmd + b"\x00" * n)
            out += rx[len(cmd):len(cmd) + n]
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


def open_reader() -> Ch341a:
    """Open the programmer. With CHIPAUDIO_SIM set to a file, use a simulated chip holding that file."""
    sim = os.environ.get("CHIPAUDIO_SIM")
    if sim:
        data = Path(sim).read_bytes() if Path(sim).is_file() else b""
        kind = os.environ.get("CHIPAUDIO_SIM_KIND", "spi_flash") if data else "empty"
        cap = max(len(data) - 1, 1).bit_length()
        return Ch341a(SimTransport(data, kind, bytes([0xEF, 0x40, cap])))
    return Ch341a(DllTransport())


def install_driver(progress: Progress | None = None) -> Path:
    """Download WCH's driver package from WCH and start its installer. Returns the setup program's path.

    Windows asks for permission, then WCH's window appears: press INSTALL there once.
    """
    import urllib.request
    import zipfile

    target = driver_cache_dir()
    target.mkdir(parents=True, exist_ok=True)
    zpath = target / "CH341PAR.ZIP"
    if progress:
        progress(0, 0, "Downloading the driver from wch-ic.com")
    req = urllib.request.Request(DRIVER_ZIP_URL, headers={"User-Agent": "ChipAudioDecoder"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            blob = r.read()
    except OSError as e:
        raise ReaderError(f"The driver could not be downloaded ({e}). Check the internet connection, or get "
                          f"CH341PAR from {DRIVER_PAGE_URL} and run its SETUP.EXE.") from None
    if blob[:2] != b"PK":
        raise ReaderError(f"WCH's site did not return the driver package. Get CH341PAR from {DRIVER_PAGE_URL} "
                          "and run its SETUP.EXE.")
    zpath.write_bytes(blob)
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("CH341PAR/") and ".." not in name:
                z.extract(info, target)
    setup = target / "CH341PAR" / "SETUP.EXE"
    if not setup.exists():
        raise ReaderError("The driver package did not contain SETUP.EXE.")
    if sys.platform == "win32":
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(setup), None, str(setup.parent), 1)
        if rc <= 32:
            raise ReaderError("Windows did not start the driver installer. It needs administrator permission.")
    return setup
