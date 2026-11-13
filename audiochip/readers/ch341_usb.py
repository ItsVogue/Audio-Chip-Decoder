"""CH341A through a generic USB driver (WinUSB or libusb), for PCs where WCH's driver is not the one bound.

Tools such as Zadig replace WCH's driver with WinUSB so that flashrom can use the programmer.
WCH's library then no longer finds it. This module talks to the chip directly instead, using
the command set documented in flashrom's ch341a_spi.c. SPI only, and slower than WCH's driver
because each 31 bytes is a separate USB exchange.
"""
from __future__ import annotations

from .base import DriverMissing, NoDevice, ReaderError

VID, PID = 0x1A86, 0x5512
EP_OUT, EP_IN = 0x02, 0x82
PACKET = 32
CMD_SPI_STREAM, CMD_I2C_STREAM, CMD_UIO_STREAM = 0xA8, 0xAA, 0xAB
UIO_OUT, UIO_DIR, UIO_END = 0x80, 0x40, 0x20
I2C_SET, I2C_END = 0x60, 0x00
_REV = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))     # the chip shifts the low bit first


class UsbTransport:
    name = "CH341A programmer (generic USB driver)"
    has_i2c = False
    max_chunk = 31 * 16 - 5

    def __init__(self, dev=None):
        self.dev = dev if dev is not None else self._find()
        try:
            if dev is None:
                self.dev.set_configuration()
            self._out(bytes([CMD_I2C_STREAM, I2C_SET | 0x01, I2C_END]))                    # default speed
            self._out(bytes([CMD_UIO_STREAM, UIO_OUT | 0x37, UIO_DIR | 0x3F, UIO_END]))    # pins on, CS high
        except Exception as e:
            raise ReaderError(f"The CH341A was found but did not accept its settings ({e}). "
                              "Unplug it, plug it back in and retry.") from None

    @staticmethod
    def _find():
        try:
            import usb.core
            import libusb_package
        except Exception as e:
            raise DriverMissing(f"The generic USB library is not available ({e}).") from None
        try:
            dev = usb.core.find(idVendor=VID, idProduct=PID, backend=libusb_package.get_libusb1_backend())
        except Exception as e:
            raise DriverMissing(f"The generic USB library could not start ({e}).") from None
        if dev is None:
            raise NoDevice("No CH341A programmer was found through the generic USB driver.")
        return dev

    def _out(self, data: bytes) -> None:
        self.dev.write(EP_OUT, data, 2000)

    def _in(self, n: int) -> bytes:
        got = bytearray()
        while len(got) < n:
            part = bytes(self.dev.read(EP_IN, PACKET, 2000))
            if not part:
                raise ReaderError("The programmer stopped answering. Check the USB cable.")
            got += part
        return bytes(got[:n])

    def describe(self) -> str:
        return "CH341A through WinUSB/libusb (slower than WCH's driver; SPI chips only)"

    def transfer(self, cmd: bytes, n: int) -> bytes:
        tx = bytes(cmd) + b"\xFF" * n
        try:
            # chip select: high for a moment, then low
            self._out(bytes([CMD_UIO_STREAM, UIO_OUT | 0x37, UIO_OUT | 0x37, UIO_OUT | 0x36, UIO_END]))
            rx = bytearray()
            for i in range(0, len(tx), PACKET - 1):
                piece = tx[i:i + PACKET - 1]
                self._out(bytes([CMD_SPI_STREAM]) + piece.translate(_REV))
                rx += self._in(len(piece)).translate(_REV)
            self._out(bytes([CMD_UIO_STREAM, UIO_OUT | 0x37, UIO_END]))                    # chip select high
        except ReaderError:
            raise
        except Exception as e:
            raise ReaderError(f"The programmer stopped answering during an SPI transfer ({e}). "
                              "Check the USB cable.") from None
        return bytes(rx[len(cmd):])

    def close(self) -> None:
        try:
            self._out(bytes([CMD_UIO_STREAM, UIO_OUT | 0x37, UIO_DIR | 0x00, UIO_END]))    # release the pins
        except Exception:
            pass
        try:
            import usb.util
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass
