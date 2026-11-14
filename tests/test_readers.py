"""Chip reading, exercised against a simulated programmer and chip."""
import numpy as np
import pytest

from audiochip.readers import Cancelled, ReaderError
from audiochip.readers.base import read_verified
from audiochip.readers.ch341a import Ch341a, SimTransport
from audiochip.readers import cards
from audiochip.readers.chips import (I2C_TYPES, SPI_EEPROM_TYPES, i2c_chip, spi_eeprom_chip, spi_flash_chip,
                                     spi_flash_from_id)

READ_ONLY_OPCODES = {0x03, 0x0B, 0x13, 0x9F}        # read, read with address bit 8, 4-byte read, read ID


def _bytes(n, seed=0):
    return np.random.default_rng(seed).integers(0, 256, n, dtype=np.uint8).tobytes()


@pytest.mark.parametrize("jedec,size,name", [
    (b"\xEF\x40\x16", 4 << 20, "Winbond"), (b"\xC2\x20\x15", 2 << 20, "Macronix"),
    (b"\x20\x20\x14", 1 << 20, "ST"), (b"\xBF\x25\x8D", 512 << 10, "SST"),
    (b"\xBF\x25\x41", 2 << 20, "SST"), (b"\x1F\x44\x01", 512 << 10, "Atmel"),
    (b"\xEF\x40\x20", 64 << 20, "Winbond"), (b"\xC8\x40\x13", 512 << 10, "GigaDevice"),
])
def test_jedec_ids_give_maker_and_size(jedec, size, name):
    info = spi_flash_from_id(jedec)
    assert info.size == size and name in info.name
    assert info.addr_bytes == (4 if size > 16 << 20 else 3)


def test_no_answer_and_unknown_ids():
    assert spi_flash_from_id(b"\xFF\xFF\xFF") is None and spi_flash_from_id(b"\x00\x00\x00") is None
    odd = spi_flash_from_id(b"\x5A\x12\xF0")
    assert odd.size == 0 and "by hand" in odd.note
    assert spi_flash_from_id(b"\x1F\x2F\x00").size == 0            # AT45 with an unknown density code


def test_spi_flash_identify_and_read_back_exactly():
    data = _bytes(300_000)[:262144]
    sim = SimTransport(data, "spi_flash", b"\xEF\x40\x12")
    r = Ch341a(sim)
    info = r.identify()
    assert info.size == 262144 and info.id_bytes == b"\xEF\x40\x12"
    seen = []
    assert r.read(info, lambda i, n, t="": seen.append((i, n))) == data
    assert seen[-1] == (262144, 262144) and len(seen) == 128
    assert sim.opcodes <= READ_ONLY_OPCODES


def test_large_flash_uses_four_byte_addresses():
    data = _bytes(40_000)
    sim = SimTransport(data, "spi_flash")
    assert spi_flash_chip(32 << 20).addr_bytes == 4 and spi_flash_chip(16 << 20).addr_bytes == 3
    from audiochip.readers.chips import ChipInfo
    out = Ch341a(sim).read(ChipInfo("spi_flash", "test", 20_000, addr_bytes=4))
    assert out == data[:20_000] and 0x13 in sim.opcodes and 0x03 not in sim.opcodes


@pytest.mark.parametrize("index", range(len(SPI_EEPROM_TYPES)))
def test_every_spi_eeprom_type_reads_back(index):
    chip = spi_eeprom_chip(index)
    data = _bytes(chip.size, index)
    sim = SimTransport(data, "spi_eeprom", addr_bytes=chip.addr_bytes)
    assert Ch341a(sim).read(chip) == data
    assert sim.opcodes <= READ_ONLY_OPCODES


@pytest.mark.parametrize("index", range(len(I2C_TYPES)))
def test_every_i2c_type_reads_back(index):
    chip = i2c_chip(index)
    data = _bytes(chip.size, index)
    assert Ch341a(SimTransport(data, "i2c")).read(chip) == data


def test_empty_socket_reads_blank_and_identifies_nothing():
    r = Ch341a(SimTransport(kind="empty"))
    assert r.identify() is None
    assert r.read(spi_flash_chip(65536)) == b"\xFF" * 65536


def test_verify_identical_then_majority_then_unreliable():
    data = _bytes(65536)
    chip = spi_flash_chip(65536)

    good = Ch341a(SimTransport(data))
    out, rep = read_verified(lambda p: good.read(chip, p))
    assert out == data and rep.passes == 2 and rep.identical and rep.trustworthy

    flaky = Ch341a(SimTransport(data, flaky=7))                 # one wrong byte every 7th transfer
    out, rep = read_verified(lambda p: flaky.read(chip, p))
    assert rep.passes == 3 and not rep.identical and rep.differing_bytes > 0
    assert out == data and rep.trustworthy and "majority" in rep.summary()

    reads = iter([data, _bytes(65536, 1), _bytes(65536, 2)])
    out, rep = read_verified(lambda p: next(reads))
    assert not rep.trustworthy and "not reliable" in rep.summary()

    out, rep = read_verified(lambda p: good.read(chip, p), verify=False)
    assert rep.passes == 1 and out == data


def test_cancel_and_missing_size():
    r = Ch341a(SimTransport(_bytes(65536)))
    with pytest.raises(Cancelled):
        r.read(spi_flash_chip(65536), cancel=lambda: True)
    with pytest.raises(ReaderError, match="size"):
        r.read(spi_flash_from_id(b"\x5A\x12\xF0"))


def test_card_image_read_respects_limit(tmp_path):
    img = tmp_path / "card.img"
    img.write_bytes(_bytes(3_000_000))
    assert cards.read_raw(str(img), 1 << 20) == img.read_bytes()[: 1 << 20]
    assert len(cards.read_raw(str(img), 16 << 20)) == 3_000_000
    with pytest.raises(ReaderError):
        cards.read_raw(str(tmp_path / "missing.img"), 1 << 20)
    assert cards.list_removable() == [] or all(d.letter for d in cards.list_removable())


# ------------------------------------------------------------------- XGecu
import sys                                                               # noqa: E402
from pathlib import Path                                                 # noqa: E402

from audiochip.readers import NoDevice, xgecu                             # noqa: E402

FAKE = f'"{sys.executable}" "{Path(__file__).with_name("fake_minipro.py")}"'


@pytest.fixture()
def fake_xgecu(tmp_path, monkeypatch):
    data = _bytes(262144, 9)
    (tmp_path / "chip.bin").write_bytes(data)
    monkeypatch.setenv("AUDIOCHIP_MINIPRO", FAKE)
    monkeypatch.setenv("FAKE_MINIPRO_DATA", str(tmp_path / "chip.bin"))
    monkeypatch.setenv("FAKE_MINIPRO_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FAKE_MINIPRO_NEED_DB", "1")
    monkeypatch.delenv("FAKE_MINIPRO_MODE", raising=False)
    return data, tmp_path


def test_xgecu_chip_search_finds_common_audio_eproms():
    assert xgecu.chip_count("ii") > 15000 and xgecu.chip_count("a") > 9000
    for part, size in (("27C64", 8192), ("27C256", 32768), ("27C512", 65536), ("27C010", 131072),
                       ("27C020", 262144), ("27C040", 524288), ("27C080", 1048576), ("AM29F040", 524288),
                       ("27C4001", 524288), ("W27C512", 65536), ("AM28F010", 131072)):
        hits = xgecu.search("ii", part)
        assert hits and hits[0][1] == size, (part, hits[:2])
    assert xgecu.search("ii", "27c020 dip")[0][0] == "27C020@DIP32"
    assert xgecu.search("a", "27C256") and not xgecu.search("ii", "no-such-chip-zz")


def test_xgecu_presence_read_and_progress(fake_xgecu, monkeypatch):
    data, tmp = fake_xgecu
    assert xgecu.presence() == ("T48", "t48", "ii")
    monkeypatch.setenv("FAKE_MINIPRO_MODEL", "tl866ii: TL866II+")
    assert xgecu.presence()[0] == "TL866II Plus"
    seen = []
    assert xgecu.read("AM27C020@DIP32", progress=lambda i, n, t="": seen.append((i, n))) == data
    assert seen and seen[-1][1] == 100 and max(i for i, _ in seen) == 99
    call = (tmp / "calls.log").read_text().split()
    assert call[:2] == ["-p", "AM27C020@DIP32"] and "-r" in call and "-z" in call and "-x" in call
    out, rep = read_verified(lambda p: xgecu.read("AM27C020@DIP32", None, p))
    assert out == data and rep.identical


def test_xgecu_problems_are_explained(fake_xgecu, monkeypatch):
    monkeypatch.setenv("FAKE_MINIPRO_MODE", "none")
    with pytest.raises(NoDevice, match="No XGecu programmer"):
        xgecu.presence()
    with pytest.raises(ReaderError, match="not found"):
        xgecu.read("AM27C020@DIP32")
    monkeypatch.setenv("FAKE_MINIPRO_MODE", "contact")
    with pytest.raises(ReaderError, match="Poor contact on pin 5, 28"):
        xgecu.read("AM27C020@DIP32")
    monkeypatch.setenv("FAKE_MINIPRO_MODE", "id")
    with pytest.raises(ReaderError, match="answered 0x1C04"):
        xgecu.read("AM27C020@DIP32", check_id=True)
    monkeypatch.setenv("FAKE_MINIPRO_MODEL", "t76: T76")
    monkeypatch.setenv("FAKE_MINIPRO_MODE", "ok")
    with pytest.raises(ReaderError, match="T76"):
        xgecu.presence()


def test_bundled_windows_tool_is_present_and_64_bit():
    exe = xgecu.HERE / "minipro.exe"
    blob = exe.read_bytes()
    assert blob[:2] == b"MZ"
    pe = int.from_bytes(blob[0x3C:0x40], "little")
    assert blob[pe:pe + 4] == b"PE\0\0" and blob[pe + 4:pe + 6] == b"\x64\x86"          # x86-64
    assert (xgecu.HERE / "infoic.xml").stat().st_size > 10_000_000
    assert (xgecu.HERE / "minipro-LICENSE.txt").exists()


# ------------------------------------------------ AT45 and the other programmers
from audiochip.readers.ch341a import open_reader as _open_reader          # noqa: E402
from audiochip.readers.spi import SerialReader                            # noqa: E402


def test_at45_dataflash_is_identified_and_read_page_by_page():
    info = spi_flash_from_id(b"\x1F\x24\x00")                              # AT45DB041: 2048 pages of 264 bytes
    assert info.family == "at45" and info.size == 2048 * 264
    data = bytes(np.random.default_rng(5).integers(0, 256, info.size, dtype=np.uint8))
    t = SimTransport(data, kind="at45", jedec=b"\x1F\x24\x00")
    r = SerialReader(t)
    assert r.identify().family == "at45"
    assert r.read(info) == data
    assert t.opcodes <= {0x9F, 0xD7, 0x03}


class FakeCh341Usb:
    """The CH341A as seen over raw USB: 32-byte command packets, data bits reversed."""

    def __init__(self, chip):
        self.chip, self.cs_low, self.tx, self.pending, self.log = chip, False, bytearray(), bytearray(), []

    def write(self, ep, data, timeout=None):
        data = bytes(data)
        assert ep == 0x02 and len(data) <= 32
        rev = bytes(int(f"{b:08b}"[::-1], 2) for b in range(256))
        if data[0] == 0xAB:                                                # pin control
            for b in data[1:]:
                if b & 0xC0 == 0x80:
                    low = not (b & 1)
                    if self.cs_low and not low:
                        self.tx.clear()
                    if low and not self.cs_low:
                        self.tx.clear()
                        self.done = 0
                    self.cs_low = low
        elif data[0] == 0xA8:
            assert self.cs_low, "SPI data sent with chip select high"
            self.tx += data[1:].translate(rev)
            full = self.chip.spi(bytes(self.tx))
            self.pending += full[self.done:].translate(rev)
            self.done = len(self.tx)
        else:
            assert data[0] == 0xAA
        self.log.append(data[0])
        return len(data)

    def read(self, ep, size, timeout=None):
        assert ep == 0x82
        out, self.pending = bytes(self.pending[:size]), self.pending[size:]
        return out


def test_ch341a_over_generic_usb_driver_reads_the_same_bytes():
    from audiochip.readers.ch341_usb import UsbTransport
    data = bytes(np.random.default_rng(6).integers(0, 256, 65536, dtype=np.uint8))
    chip = SimTransport(data, jedec=b"\xEF\x40\x10")
    r = SerialReader(UsbTransport(FakeCh341Usb(chip)))
    info = r.identify()
    assert info.size == 65536
    assert r.read(info) == data
    assert chip.opcodes == {0x9F, 0x03}


class FakeSerprog:
    """A serial port with a serprog board on the other end."""

    def __init__(self, chip, junk=b""):
        self.chip, self.rx, self.buf = chip, bytearray(junk), bytearray()

    def reset_input_buffer(self):
        self.rx.clear()

    def flush(self):
        pass

    def close(self):
        pass

    def write(self, data):
        self.buf += bytes(data)
        while self.buf:
            c = self.buf[0]
            need = {0x12: 2, 0x14: 5, 0x15: 2}.get(c, 1)
            if c == 0x13:
                if len(self.buf) < 7:
                    return
                slen = int.from_bytes(self.buf[1:4], "little")
                rlen = int.from_bytes(self.buf[4:7], "little")
                if len(self.buf) < 7 + slen:
                    return
                tx = bytes(self.buf[7:7 + slen])
                self.rx += b"\x06" + self.chip.spi(tx + b"\x00" * rlen)[slen:]
                del self.buf[:7 + slen]
                continue
            if len(self.buf) < need:
                return
            if c == 0x00:
                self.rx += b"\x06"
            elif c == 0x10:
                self.rx += b"\x15\x06"
            elif c == 0x01:
                self.rx += b"\x06\x01\x00"
            elif c == 0x02:
                cmap = bytearray(32)
                for k in (0, 1, 2, 3, 0x10, 0x12, 0x13, 0x14, 0x15):
                    cmap[k >> 3] |= 1 << (k & 7)
                self.rx += b"\x06" + bytes(cmap)
            elif c == 0x03:
                self.rx += b"\x06" + b"pico-serprog".ljust(16, b"\x00")
            elif c == 0x14:
                self.rx += b"\x06" + bytes(self.buf[1:5])
            else:
                self.rx += b"\x06"
            del self.buf[:need]

    def read(self, n):
        out, self.rx = bytes(self.rx[:n]), self.rx[n:]
        return out


def test_serprog_board_identifies_and_reads():
    from audiochip.readers.serprog import SerprogTransport
    data = bytes(np.random.default_rng(8).integers(0, 256, 1 << 17, dtype=np.uint8))
    chip = SimTransport(data, jedec=b"\xC2\x20\x11")
    t = SerprogTransport("COM9", ser=FakeSerprog(chip))
    assert "pico-serprog" in t.describe() and "2.00 MHz" in t.describe()
    r = SerialReader(t)
    info = r.identify()
    assert info.size == 1 << 17 and r.read(info) == data
    assert chip.opcodes == {0x9F, 0x03}


def test_ft232h_command_bytes_follow_the_mpsse_spec():
    from audiochip.readers.ft232h import mpsse_setup, mpsse_transfer
    assert mpsse_setup() == bytes([0x8A, 0x97, 0x8D, 0x85, 0x86, 14, 0, 0x80, 0x08, 0x0B])
    b = mpsse_transfer(b"\x03\x00\x10\x00", 256)
    assert b[:3] == bytes([0x80, 0x00, 0x0B])                              # chip select low
    assert b[3:6] == bytes([0x11, 3, 0]) and b[6:10] == b"\x03\x00\x10\x00"   # 4 bytes out, falling edge
    assert b[10:13] == bytes([0x20, 255, 0])                               # 256 bytes in, rising edge
    assert b[13:] == bytes([0x80, 0x08, 0x0B, 0x87])                       # chip select high, flush


def test_ch347_settings_block_matches_wch_header_layout():
    import ctypes
    from audiochip.readers.ch347 import SpiConfig
    assert ctypes.sizeof(SpiConfig) == 20
    assert SpiConfig.iChipSelect.offset == 6 and SpiConfig.iDelayDeactive.offset == 16


def test_simulated_reader_of_each_kind_opens(tmp_path, monkeypatch):
    f = tmp_path / "chip.bin"
    f.write_bytes(bytes(range(256)) * 256)
    monkeypatch.setenv("AUDIOCHIP_SIM", str(f))
    for kind, i2c in (("ch341a", True), ("ch347", True), ("ft232h", True), ("serprog", False)):
        with _open_reader(kind) as r:
            assert r.has_i2c is i2c and r.identify().size == 65536


# ------------------------------------------------------------ FT232H: 24-series I2C
def _ftdi_on_sim(sim, kind="FT232H"):
    """An FT232H transport whose USB writes go to the pin-level simulator instead of FTDI's driver."""
    from audiochip.readers.ft232h import FtdiTransport
    t = object.__new__(FtdiTransport)
    t.kind, t.open, t._back = kind, False, bytearray()
    t._write = lambda data: t._back.extend(sim.run(data))

    def read(n):
        from audiochip.readers.base import ReaderError
        if len(t._back) < n:
            raise ReaderError("short read")
        got = bytes(t._back[:n])
        del t._back[:n]
        return got
    t._read = read
    return t


@pytest.mark.parametrize("type_index, size, addr_bytes", [(1, 256, 1), (2, 512, 1), (4, 2048, 1), (5, 4096, 2),
                                                          (8, 32768, 2), (10, 131072, 2)])
@pytest.mark.parametrize("kind", ["FT232H", "FT2232H"])
def test_ft232h_reads_24_series_chips_over_a_simulated_i2c_bus(type_index, size, addr_bytes, kind):
    from mpsse_i2c_sim import MpsseI2cSim
    from audiochip.readers.chips import I2C_TYPES, i2c_chip
    from audiochip.readers.spi import SerialReader
    assert I2C_TYPES[type_index][1] == size
    rng = np.random.default_rng(size)
    mem = rng.integers(0, 256, size, dtype=np.uint8).tobytes()
    sim = MpsseI2cSim(mem, addr_bytes, open_drain_capable=kind == "FT232H")
    reader = SerialReader(_ftdi_on_sim(sim, kind))
    assert reader.has_i2c
    assert reader.read(i2c_chip(type_index)) == mem
    assert sim.writes == 0, "a data byte was written to the chip"
    assert sim.contention == 0 and sim.hazards == 0 and not sim.unknown
    assert sim.three_phase is False and sim.divisor == 14, "the port must be left set up for SPI afterwards"
    reads = -(-size // 64)
    assert sim.starts == 2 * reads                                  # each read: a start and a repeated start
    assert sim.stops >= reads


def test_ft232h_i2c_runs_at_100_khz_with_three_phase_clocking():
    from audiochip.readers.ft232h import I2C_DIVISOR, i2c_address, i2c_read_commands, i2c_setup
    assert 60e6 / ((1 + I2C_DIVISOR) * 2) * 2 / 3 == 100e3
    setup = i2c_setup(True)
    assert setup[:3] == bytes([0x8A, 0x97, 0x8C]) and bytes([0x9E, 0x06, 0x00]) in setup
    assert len(i2c_setup(False)) == len(setup) - 3 and i2c_setup(False)[:7] == setup[:7]
    assert i2c_address(1, 0x12) == (0x50, b"\x12")                 # 24C02
    assert i2c_address(4, 0x712) == (0x57, b"\x12")                # 24C16: block bits in the device address
    assert i2c_address(8, 0x7123) == (0x50, b"\x71\x23")           # 24C256
    assert i2c_address(10, 0x1ABCD) == (0x51, b"\xAB\xCD")         # 24C1024: bit 16 in the device address
    cmds, sent = i2c_read_commands(8, 0x0100, 4)
    assert sent == 4 and cmds.endswith(b"\x87")
    assert cmds.count(bytes([0x20, 0x00, 0x00])) == 4               # four data bytes clocked in
    assert bytes([0x13, 0x00, 0x80]) in cmds                        # the last one is not acknowledged


def test_ft232h_i2c_reports_a_missing_chip_and_restores_spi_mode():
    from mpsse_i2c_sim import MpsseI2cSim
    from audiochip.readers.base import ReaderError
    from audiochip.readers.chips import i2c_chip
    from audiochip.readers.spi import SerialReader

    class Nothing(MpsseI2cSim):
        def _byte(self, value):
            return False                                            # nobody acknowledges

    sim = Nothing(bytes(256), 1)
    with pytest.raises(ReaderError, match="No chip answered"):
        SerialReader(_ftdi_on_sim(sim)).read(i2c_chip(1))
    assert sim.three_phase is False and sim.divisor == 14 and sim.writes == 0
