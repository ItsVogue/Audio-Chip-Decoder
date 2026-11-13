"""FTDI FT232H / FT2232H / FT4232H breakout boards: SPI chips. Read-only.

Uses FTDI's own library (ftd2xx.dll, installed with FTDI's driver, which Windows Update
normally fetches by itself) and the chip's MPSSE mode. Wiring on port A:
    AD0 = clock (SCK)   AD1 = data to the chip (MOSI/DI)   AD2 = data from the chip (MISO/DO)
    AD3 = chip select (CS)
MPSSE commands used (FTDI application note AN_108):
    0x80 set the pins   0x11 send bytes   0x20 receive bytes   0x86 clock divisor   0x87 flush
"""
from __future__ import annotations

import ctypes
import sys

from .base import DriverMissing, NoDevice, ReaderError

MPSSE_TYPES = {6: "FT2232H", 7: "FT4232H", 8: "FT232H"}
PINS_IDLE = bytes([0x80, 0x08, 0x0B])        # CS high; clock, data-out and CS are outputs
PINS_SELECT = bytes([0x80, 0x00, 0x0B])      # CS low
DIVISOR = 14                                 # 60 MHz / ((1 + 14) * 2) = 2 MHz


def mpsse_setup() -> bytes:
    return bytes([0x8A, 0x97, 0x8D, 0x85, 0x86, DIVISOR & 0xFF, DIVISOR >> 8]) + PINS_IDLE


def mpsse_transfer(cmd: bytes, n: int) -> bytes:
    """One chip-select-low exchange: send cmd, then clock in n bytes."""
    out = bytearray(PINS_SELECT)
    out += bytes([0x11, (len(cmd) - 1) & 0xFF, (len(cmd) - 1) >> 8]) + bytes(cmd)
    if n:
        out += bytes([0x20, (n - 1) & 0xFF, (n - 1) >> 8])
    out += PINS_IDLE + b"\x87"
    return bytes(out)


class FtdiTransport:
    name = "FT232H board"
    has_i2c = False
    max_chunk = 4096

    def __init__(self):
        if sys.platform != "win32":
            raise DriverMissing("Reading through an FT232H works on Windows only.")
        d = None
        for name in ("ftd2xx64.dll", "ftd2xx.dll"):
            try:
                d = ctypes.WinDLL(name)
                break
            except OSError:
                continue
        if d is None:
            raise DriverMissing("FTDI's driver is not installed on this PC. Plug the board in and let Windows "
                                "Update install it, or install FTDI's CDM driver from ftdichip.com.")
        self.dll = d
        self.h = ctypes.c_void_p()
        index, self.kind = self._pick()
        if d.FT_Open(index, ctypes.byref(self.h)):
            raise NoDevice("The FT232H board could not be opened. Close any other program using it and retry.")
        self.open = True
        try:
            self._check(d.FT_ResetDevice(self.h))
            self._check(d.FT_SetUSBParameters(self.h, 65536, 65535))
            self._check(d.FT_SetChars(self.h, 0, 0, 0, 0))
            self._check(d.FT_SetTimeouts(self.h, 3000, 3000))
            self._check(d.FT_SetLatencyTimer(self.h, 1))
            self._check(d.FT_SetBitMode(self.h, 0, 0x00))
            self._check(d.FT_SetBitMode(self.h, 0, 0x02))          # MPSSE
            self._check(d.FT_Purge(self.h, 3))
            self._write(b"\xAA")                                    # a bad command must be echoed as FA AA
            if self._read(2) != b"\xFA\xAA":
                raise ReaderError("The board did not enter MPSSE mode.")
            self._write(mpsse_setup())
        except ReaderError:
            self.close()
            raise

    def _pick(self) -> tuple[int, str]:
        d = self.dll
        count = ctypes.c_ulong()
        if d.FT_CreateDeviceInfoList(ctypes.byref(count)) or not count.value:
            raise NoDevice("No FTDI board was found. Plug it into a USB port, wait a few seconds and try again.")
        flags, kind, ident, loc = (ctypes.c_ulong() for _ in range(4))
        serial = ctypes.create_string_buffer(16)
        desc = ctypes.create_string_buffer(64)
        handle = ctypes.c_void_p()
        seen = []
        for i in range(count.value):
            if d.FT_GetDeviceInfoDetail(i, ctypes.byref(flags), ctypes.byref(kind), ctypes.byref(ident),
                                        ctypes.byref(loc), serial, desc, ctypes.byref(handle)):
                continue
            seen.append(desc.value.decode("ascii", "replace"))
            if kind.value in MPSSE_TYPES:
                return i, MPSSE_TYPES[kind.value]
        raise NoDevice("An FTDI device is connected (" + ", ".join(seen or ["unnamed"]) + ") but it is not an "
                       "FT232H, FT2232H or FT4232H, which are the ones that can read SPI chips.")

    @staticmethod
    def _check(status: int) -> None:
        if status:
            raise ReaderError(f"The FT232H board reported an error (FTDI status {status}). Unplug it, plug it "
                              "back in and retry.")

    def _write(self, data: bytes) -> None:
        done = ctypes.c_ulong()
        self._check(self.dll.FT_Write(self.h, data, len(data), ctypes.byref(done)))
        if done.value != len(data):
            raise ReaderError("The FT232H board did not take all the data sent to it. Check the USB cable.")

    def _read(self, n: int) -> bytes:
        buf = ctypes.create_string_buffer(n)
        done = ctypes.c_ulong()
        self._check(self.dll.FT_Read(self.h, buf, n, ctypes.byref(done)))
        if done.value != n:
            raise ReaderError("The FT232H board stopped answering. Check the USB cable and the wiring.")
        return buf.raw[:n]

    def describe(self) -> str:
        return f"{self.kind} in MPSSE mode, SPI at 2 MHz (AD0 clock, AD1 to chip, AD2 from chip, AD3 select)"

    def transfer(self, cmd: bytes, n: int) -> bytes:
        self._write(mpsse_transfer(cmd, n))
        return self._read(n) if n else b""

    def close(self) -> None:
        if getattr(self, "open", False):
            try:
                self.dll.FT_SetBitMode(self.h, 0, 0x00)       # back to plain pins, all inputs
            finally:
                self.dll.FT_Close(self.h)
                self.open = False
