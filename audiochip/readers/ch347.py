"""WCH CH347 high-speed USB adapter: 25-series SPI and 24-series I2C chips. Read-only.

Uses WCH's library (CH347DLLA64.DLL, installed with WCH's CH347 driver, CH341PAR.EXE).
Function names, argument order and the SPI settings block are taken from WCH's header,
CH347DLL_EN.H.
"""
from __future__ import annotations

import ctypes
import sys

from .base import DriverMissing, NoDevice, ReaderError
from .spi import FullDuplex

CS1 = 0x80                   # bit 7: use chip select; bits 1-0 = 00: the CS1 pin


class SpiConfig(ctypes.Structure):
    """mSpiCfgS from CH347DLL_EN.H (declared there under #pragma pack(1))."""
    _pack_ = 1
    _fields_ = [("iMode", ctypes.c_ubyte), ("iClock", ctypes.c_ubyte), ("iByteOrder", ctypes.c_ubyte),
                ("iSpiWriteReadInterval", ctypes.c_ushort), ("iSpiOutDefaultData", ctypes.c_ubyte),
                ("iChipSelect", ctypes.c_uint32), ("CS1Polarity", ctypes.c_ubyte), ("CS2Polarity", ctypes.c_ubyte),
                ("iIsAutoDeativeCS", ctypes.c_ushort), ("iActiveDelay", ctypes.c_ushort),
                ("iDelayDeactive", ctypes.c_uint32)]


class Ch347Transport(FullDuplex):
    name = "CH347 adapter"
    has_i2c = True
    max_chunk = 2048

    def __init__(self, index: int = 0):
        if sys.platform != "win32":
            raise DriverMissing("Reading through a CH347 works on Windows only.")
        self.index = index
        name = "CH347DLLA64.DLL" if ctypes.sizeof(ctypes.c_void_p) == 8 else "CH347DLL.DLL"
        try:
            d = self.dll = ctypes.WinDLL(name)
        except OSError:
            raise DriverMissing("The CH347 driver is not installed on this PC. It comes in WCH's CH341PAR "
                                "driver package: use Install driver, then plug the adapter in again.") from None
        ul = ctypes.c_ulong
        d.CH347OpenDevice.argtypes = [ul]
        d.CH347OpenDevice.restype = ctypes.c_void_p
        d.CH347CloseDevice.argtypes = [ul]
        d.CH347CloseDevice.restype = ctypes.c_int
        d.CH347SetTimeout.argtypes = [ul, ul, ul]
        d.CH347SetTimeout.restype = ctypes.c_int
        d.CH347SPI_Init.argtypes = [ul, ctypes.POINTER(SpiConfig)]
        d.CH347SPI_Init.restype = ctypes.c_int
        d.CH347StreamSPI4.argtypes = [ul, ul, ul, ctypes.c_void_p]
        d.CH347StreamSPI4.restype = ctypes.c_int
        d.CH347I2C_Set.argtypes = [ul, ul]
        d.CH347I2C_Set.restype = ctypes.c_int
        d.CH347ReadEEPROM.argtypes = [ul, ctypes.c_int, ul, ul, ctypes.c_void_p]
        d.CH347ReadEEPROM.restype = ctypes.c_int
        handle = d.CH347OpenDevice(index)
        if handle in (None, 0, ctypes.c_void_p(-1).value):
            raise NoDevice("No CH347 adapter was found. Plug it into a USB port, check that its mode jumper "
                           "selects SPI/I2C, wait a few seconds and try again.")
        self.open = True
        d.CH347SetTimeout(index, 5000, 5000)
        cfg = SpiConfig(iMode=0, iClock=5, iByteOrder=1, iSpiWriteReadInterval=0, iSpiOutDefaultData=0xFF,
                        iChipSelect=CS1, CS1Polarity=0, CS2Polarity=0, iIsAutoDeativeCS=0, iActiveDelay=0,
                        iDelayDeactive=0)                    # mode 0, 1.875 MHz, most significant bit first
        if not d.CH347SPI_Init(index, ctypes.byref(cfg)):
            self.close()
            raise ReaderError("The CH347 did not accept its SPI settings. Check that its mode jumper selects "
                              "SPI/I2C, then unplug it, plug it back in and retry.")
        d.CH347I2C_Set(index, 1)                             # 100 kHz

    def describe(self) -> str:
        return "CH347, SPI at 1.875 MHz"

    def spi(self, tx: bytes) -> bytes:
        buf = ctypes.create_string_buffer(bytes(tx), len(tx))
        if not self.dll.CH347StreamSPI4(self.index, CS1, len(tx), buf):
            raise ReaderError("The CH347 stopped answering during an SPI transfer. Check the USB cable.")
        return buf.raw

    def read_eeprom(self, type_index: int, addr: int, length: int) -> bytes:
        buf = ctypes.create_string_buffer(length)
        if not self.dll.CH347ReadEEPROM(self.index, type_index, addr, length, buf):
            raise ReaderError("The CH347 stopped answering during an I2C read. Check the USB cable.")
        return buf.raw

    def close(self) -> None:
        if getattr(self, "open", False):
            self.dll.CH347CloseDevice(self.index)
            self.open = False
