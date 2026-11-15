# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""FTDI FT232H / FT2232H / FT4232H breakout boards: SPI chips and 24-series I2C EEPROMs. Read-only.

Uses FTDI's own library (ftd2xx.dll, installed with FTDI's driver, which Windows Update
normally fetches by itself) and the chip's MPSSE mode. Wiring on port A:
    AD0 = clock (SCK)   AD1 = data to the chip (MOSI/DI)   AD2 = data from the chip (MISO/DO)
    AD3 = chip select (CS)
MPSSE commands used (FTDI application note AN_108):
    0x80 set the pins   0x11 send bytes   0x20 receive bytes   0x86 clock divisor   0x87 flush

I2C (FTDI application note AN_113) uses different wiring on the same port:
    AD0 = clock (SCL)   AD1 and AD2 joined together = data (SDA)
with a pull-up resistor (about 4.7 k) from SDA to 3.3 V. While the chip is the one talking, the data
pin is switched to an input so the chip can answer on it. Extra commands:
    0x8C three-phase clocking   0x13 send bits   0x22 receive bits   0x9E pull-low-only pins (FT232H)
Only I2C reads are built: start, device address, memory address, start again, read, stop.
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


# ------------------------------------------------------------------------ I2C
I2C_DIVISOR = 199                            # 60 MHz / ((1 + 199) * 2) * 2/3 = 100 kHz with three-phase clocking
I2C_FIRST_2BYTE = 5                          # index in chips.I2C_TYPES of the first type with a 2-byte address
_HOLD = 10                                   # a pin command takes about 0.5 us; the bus needs 4.7 us between edges


def _pins(scl: int, sda: int, n: int = 1) -> bytes:
    """Drive both bus lines (AD0 = SCL, AD1 = SDA out), held for n pin commands."""
    return bytes([0x80, (1 if scl else 0) | (2 if sda else 0), 0x03]) * n


_SDA_FREE = bytes([0x80, 0x00, 0x01])        # clock low, data line let go so the chip can drive it
I2C_IDLE = _pins(1, 1)
I2C_START = _pins(1, 1, _HOLD) + _pins(1, 0, _HOLD) + _pins(0, 0, _HOLD)      # data falls while the clock is high
I2C_RESTART = _pins(0, 1, _HOLD) + I2C_START
I2C_STOP = _pins(0, 0, _HOLD) + _pins(1, 0, _HOLD) + _pins(1, 1, _HOLD)       # data rises while the clock is high


def i2c_setup(open_drain: bool) -> bytes:
    """Put the port in I2C mode at 100 kHz. open_drain: the FT232H can pull its data pins low without ever
    driving them high, which is what an I2C data line wants. The 2232H and 4232H lack that command."""
    out = bytes([0x8A, 0x97, 0x8C, 0x85, 0x86, I2C_DIVISOR & 0xFF, I2C_DIVISOR >> 8])
    if open_drain:
        out += bytes([0x9E, 0x06, 0x00])
    return out + _pins(0, 1, _HOLD) + _pins(1, 1, _HOLD)    # data up first, then the clock: no false start


def i2c_leave() -> bytes:
    """Stop, then bring the clock low before anything else moves, and go back to the SPI settings."""
    return _pins(0, 1, _HOLD) + I2C_STOP + _pins(0, 1, _HOLD) + mpsse_setup()


def _i2c_send(byte: int, open_drain: bool = True) -> bytes:
    """Clock one byte out, then read the chip's acknowledge bit (one byte comes back; bit 0 = 0 means yes).

    With pull-low-only pins (FT232H) the byte is clocked out by the MPSSE engine. Without them (FT2232H,
    FT4232H) that would leave the adapter driving the data line high at the moment the chip pulls it low
    to acknowledge, so there each bit is set with pin commands instead: low is driven, high is let go.
    """
    if open_drain:
        return _pins(0, 1) + bytes([0x11, 0x00, 0x00, byte & 0xFF]) + _SDA_FREE + bytes([0x22, 0x00])
    out = bytearray()
    for k in range(7, -1, -1):
        direction = 0x01 if (byte >> k) & 1 else 0x03
        out += (bytes([0x80, 0x00, direction]) * (2 * _HOLD) + bytes([0x80, 0x01, direction]) * (2 * _HOLD)
                + bytes([0x80, 0x00, direction]) * _HOLD)
    return bytes(out) + _SDA_FREE + bytes([0x22, 0x00])


def _i2c_receive(last: bool) -> bytes:
    """Clock one byte in, then acknowledge it, or not if it is the last one wanted."""
    return _SDA_FREE + bytes([0x20, 0x00, 0x00]) + _pins(0, 1 if last else 0) + bytes([0x13, 0x00, 0x80 if last else 0x00])


def i2c_address(type_index: int, addr: int) -> tuple[int, bytes]:
    """(7-bit device address, memory address bytes) for a 24-series chip of the given type."""
    if type_index < I2C_FIRST_2BYTE:                        # 24C01 to 24C16: upper address bits ride in the device address
        return 0x50 | ((addr >> 8) & 7), bytes([addr & 0xFF])
    return 0x50 | ((addr >> 16) & 7), bytes([(addr >> 8) & 0xFF, addr & 0xFF])


def i2c_read_commands(type_index: int, addr: int, n: int, open_drain: bool = True) -> tuple[bytes, int]:
    """MPSSE commands for one sequential read. Returns (commands, bytes sent before the data comes back)."""
    dev, mem = i2c_address(type_index, addr)
    sent = bytes([dev << 1]) + mem
    out = bytearray(I2C_START)
    for b in sent:
        out += _i2c_send(b, open_drain)
    out += I2C_RESTART                                     # start again without a stop
    out += _i2c_send((dev << 1) | 1, open_drain)
    for i in range(n):
        out += _i2c_receive(i == n - 1)
    out += I2C_STOP + b"\x87"
    return bytes(out), len(sent) + 1


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
    has_i2c = True
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
        return (f"{self.kind} in MPSSE mode: SPI at 2 MHz (AD0 clock, AD1 to chip, AD2 from chip, AD3 select) "
                "or I2C at 100 kHz (AD0 clock, AD1+AD2 data)")

    def transfer(self, cmd: bytes, n: int) -> bytes:
        self._write(mpsse_transfer(cmd, n))
        return self._read(n) if n else b""

    def read_eeprom(self, type_index: int, addr: int, length: int) -> bytes:
        """Read from a 24-series I2C EEPROM wired to AD0 (SCL) and AD1+AD2 (SDA)."""
        out = bytearray()
        block = 256 if type_index < I2C_FIRST_2BYTE else 65536      # the device address changes at these borders
        od = self.kind == "FT232H"
        self._write(i2c_setup(od))
        try:
            while len(out) < length:
                at = addr + len(out)
                n = min(length - len(out), block - at % block, 64)
                cmds, sent = i2c_read_commands(type_index, at, n, od)
                self._write(cmds)
                back = self._read(sent + n)
                if back[0] & 1:
                    raise ReaderError("No chip answered on the I2C lines. Check the wiring: AD0 to the chip's "
                                      "SCL (pin 6), AD1 and AD2 both to SDA (pin 5), and a pull-up resistor "
                                      "of about 4.7 k from SDA to 3.3 V.")
                if any(b & 1 for b in back[1:sent]):
                    raise ReaderError("The chip stopped acknowledging part-way through. The chip type chosen "
                                      "may be larger than the chip fitted, or the contact is poor.")
                out += back[sent:]
        finally:
            self._write(i2c_leave())                                # leave the port as the SPI code expects it
        return bytes(out)

    def close(self) -> None:
        if getattr(self, "open", False):
            try:
                self.dll.FT_SetBitMode(self.h, 0, 0x00)       # back to plain pins, all inputs
            finally:
                self.dll.FT_Close(self.h)
                self.open = False
