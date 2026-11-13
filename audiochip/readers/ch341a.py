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

from .base import DriverMissing, NoDevice, Progress, ReaderError
from .spi import FullDuplex, SerialReader

Ch341a = SerialReader          # the name older code and tests use

DRIVER_ZIP_URL = "https://www.wch-ic.com/download/file?id=7"          # WCH's CH341PAR.ZIP
DRIVER_PAGE_URL = "https://www.wch-ic.com/downloads/CH341PAR_ZIP.html"
STREAM_MODE = 0x81          # bit 7: SPI bytes most-significant-bit first; bits 1-0 = 01: I2C at 100 kHz
CS_D0 = 0x80                # use pin D0 as chip select, driven low for the transfer


def driver_cache_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
    return base / "AudioChipDecoder" / "driver"


class DllTransport(FullDuplex):
    """Thin wrapper over WCH's CH341 library."""

    name = "CH341A programmer"
    has_i2c = True
    max_chunk = 2048

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


class SimTransport(FullDuplex):
    """A pretend programmer with a pretend chip, for tests and for trying the screens.

    kind: "spi_flash" (answers the ID command), "spi_eeprom", "i2c", or "empty" (nothing fitted).
    flaky: every Nth read returns one wrong byte, to exercise read verification.
    """

    name = "simulated programmer"
    has_i2c = True
    max_chunk = 2048

    def __init__(self, data: bytes = b"", kind: str = "spi_flash", jedec: bytes = b"\xEF\x40\x12",
                 addr_bytes: int = 3, flaky: int = 0):
        self.data, self.kind, self.jedec, self.addr_bytes, self.flaky = data, kind, jedec, addr_bytes, flaky
        self.calls = 0
        self.opcodes: set[int] = set()
        self.page_size = 264

    def describe(self) -> str:
        return self.name

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
            ident = self.jedec if self.kind in ("spi_flash", "at45") else b"\xFF\xFF\xFF"
            return (b"\xFF" + ident + b"\xFF" * len(tx))[: len(tx)]
        if op == 0xD7:                                        # AT45 status: bit 0 clear = DataFlash page size
            return b"\xFF" + bytes([0xBC if self.kind == "at45" else 0xFF]) * (len(tx) - 1)
        if self.kind == "at45" and op == 0x03:
            raw = int.from_bytes(tx[1:4], "big")
            shift = {264: 9, 528: 10, 1056: 11}[self.page_size]
            addr = (raw >> shift) * self.page_size + (raw & ((1 << shift) - 1))
            return b"\xFF" * 4 + self._mem(addr, len(tx) - 4)
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


SERIAL_READERS = {
    "ch341a": "CH341A programmer",
    "ch347": "CH347 adapter",
    "ft232h": "FT232H board",
    "serprog": "Pico / Arduino reader (serprog)",
}
SPI_ONLY = ("ft232h", "serprog")


def open_reader(kind: str = "ch341a", port: str | None = None) -> SerialReader:
    """Open a serial-chip programmer.

    With AUDIOCHIP_SIM set to a file, a simulated chip holding that file is used instead.
    For a CH341A, WCH's driver is tried first; if it is missing or finds nothing, the generic
    USB driver (WinUSB/libusb, as installed by Zadig) is tried before giving up.
    """
    sim = os.environ.get("AUDIOCHIP_SIM")
    if sim:
        data = Path(sim).read_bytes() if Path(sim).is_file() else b""
        chip_kind = os.environ.get("AUDIOCHIP_SIM_KIND", "spi_flash") if data else "empty"
        cap = max(len(data) - 1, 1).bit_length()
        t = SimTransport(data, chip_kind, bytes([0xEF, 0x40, cap]))
        t.name = "simulated " + SERIAL_READERS.get(kind, kind)
        t.has_i2c = kind not in SPI_ONLY
        return SerialReader(t)
    if kind == "ch347":
        from .ch347 import Ch347Transport
        return SerialReader(Ch347Transport())
    if kind == "ft232h":
        from .ft232h import FtdiTransport
        return SerialReader(FtdiTransport())
    if kind == "serprog":
        from .serprog import SerprogTransport
        return SerialReader(SerprogTransport(port or ""))
    try:
        return SerialReader(DllTransport())
    except (DriverMissing, NoDevice) as first:
        from .ch341_usb import UsbTransport
        try:
            return SerialReader(UsbTransport())
        except (DriverMissing, NoDevice):
            raise first from None


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
    req = urllib.request.Request(DRIVER_ZIP_URL, headers={"User-Agent": "AudioChipDecoder"})
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
