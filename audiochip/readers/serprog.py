"""Raspberry Pi Pico, Arduino and other boards running "serprog" firmware: SPI chips. Read-only.

serprog is flashrom's serial programmer protocol (pico-serprog, frser-duino, stm32-vserprog
and others speak it). The board shows up as a COM port. Commands used, from flashrom's
serprog-protocol document:
    0x10 sync   0x01 interface version   0x02 command map   0x03 programmer name
    0x12 select bus (SPI)   0x14 set SPI clock   0x15 pin drivers on/off
    0x13 SPI operation: 24-bit send length, 24-bit receive length, data  ->  ACK + received bytes
"""
from __future__ import annotations

import time

from .base import DriverMissing, NoDevice, ReaderError

ACK, NAK = 0x06, 0x15
S_NOP, Q_IFACE, Q_CMDMAP, Q_PGMNAME = 0x00, 0x01, 0x02, 0x03
S_SYNCNOP, S_BUSTYPE, O_SPIOP, S_SPI_FREQ, S_PIN_STATE = 0x10, 0x12, 0x13, 0x14, 0x15
BUS_SPI = 0x08
SPI_HZ = 2_000_000


def list_ports() -> list[tuple[str, str]]:
    """(port, description) for every serial port, most likely candidates first."""
    try:
        from serial.tools import list_ports as lp
    except Exception:
        return []
    ports = [(p.device, p.description or p.device) for p in lp.comports()]
    likely = ("serprog", "pico", "arduino", "ch340", "cdc", "usb serial", "stm")
    ports.sort(key=lambda t: (not any(w in t[1].lower() for w in likely), t[0]))
    return ports


class SerprogTransport:
    name = "Pico / Arduino reader (serprog)"
    has_i2c = False
    max_chunk = 4096

    def __init__(self, port: str, baud: int = 115200, ser=None):
        if ser is None:
            try:
                import serial
            except Exception as e:
                raise DriverMissing(f"Serial port support is not available ({e}).") from None
            if not port:
                raise NoDevice("No serial port is selected. Plug the board in and pick its COM port.")
            try:
                ser = serial.Serial(port, baud, timeout=1.0, write_timeout=2.0)
            except Exception as e:
                raise NoDevice(f"{port} could not be opened ({e}). Close any other program using it.") from None
        self.ser = ser
        self.port = port
        try:
            self._sync()
            if self._ask(Q_IFACE, 2) != b"\x01\x00":
                raise ReaderError("The board speaks a serprog version this app does not know.")
            cmap = self._ask(Q_CMDMAP, 32)
            self.has = lambda c: bool(cmap[c >> 3] & (1 << (c & 7)))
            if not self.has(O_SPIOP):
                raise ReaderError("The board's firmware has no SPI support.")
            self.pgm = self._ask(Q_PGMNAME, 16).rstrip(b"\x00").decode("ascii", "replace") if self.has(Q_PGMNAME) else ""
            if self.has(S_BUSTYPE):
                self._ask(S_BUSTYPE, 0, bytes([BUS_SPI]))
            self.hz = 0
            if self.has(S_SPI_FREQ):
                self.hz = int.from_bytes(self._ask(S_SPI_FREQ, 4, SPI_HZ.to_bytes(4, "little")), "little")
            if self.has(S_PIN_STATE):
                self._ask(S_PIN_STATE, 0, b"\x01")
        except ReaderError:
            self.close()
            raise

    def _sync(self) -> None:
        """Get in step with the board. Boards that restart when the port opens need a moment."""
        for attempt in range(4):
            try:
                self.ser.reset_input_buffer()
                self.ser.write(bytes([S_NOP] * 8))
                self.ser.flush()
                time.sleep(0.05 if attempt == 0 else 0.3)
                self.ser.reset_input_buffer()
                self.ser.write(bytes([S_SYNCNOP]))
                seen = self.ser.read(2)
                for _ in range(10):                          # skip any late answers to the NOPs
                    if seen[-2:] == bytes([NAK, ACK]):
                        return
                    more = self.ser.read(1)
                    if not more:
                        break
                    seen += more
            except Exception as e:
                raise ReaderError(f"The serial port stopped working ({e}).") from None
            time.sleep(0.8)
        raise NoDevice(f"Nothing on {self.port} answered as a serprog reader. Check that the board has serprog "
                       "firmware on it and that this is its port.")

    def _ask(self, cmd: int, nreply: int, params: bytes = b"") -> bytes:
        try:
            self.ser.write(bytes([cmd]) + params)
            head = self.ser.read(1)
            if head != bytes([ACK]):
                raise ReaderError(f"The reader refused command 0x{cmd:02X}." if head == bytes([NAK]) else
                                  "The reader stopped answering. Check the USB cable.")
            got = bytearray()
            while len(got) < nreply:
                part = self.ser.read(nreply - len(got))
                if not part:
                    raise ReaderError("The reader stopped answering part-way through. Check the USB cable.")
                got += part
            return bytes(got)
        except ReaderError:
            raise
        except Exception as e:
            raise ReaderError(f"The serial port stopped working ({e}).") from None

    def describe(self) -> str:
        speed = f", SPI at {self.hz / 1e6:.2f} MHz" if self.hz else ""
        return f"serprog reader '{self.pgm or 'unnamed'}' on {self.port}{speed}"

    def transfer(self, cmd: bytes, n: int) -> bytes:
        params = len(cmd).to_bytes(3, "little") + n.to_bytes(3, "little") + bytes(cmd)
        return self._ask(O_SPIOP, n, params)

    def close(self) -> None:
        try:
            if getattr(self, "has", None) and self.has(S_PIN_STATE):
                self.ser.write(bytes([S_PIN_STATE, 0x00]))
                self.ser.read(1)
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass
