"""Chip reading, exercised against a simulated programmer and chip."""
import numpy as np
import pytest

from chipaudio.readers import Cancelled, ReaderError
from chipaudio.readers.base import read_verified
from chipaudio.readers.ch341a import Ch341a, SimTransport
from chipaudio.readers import cards
from chipaudio.readers.chips import (I2C_TYPES, SPI_EEPROM_TYPES, i2c_chip, spi_eeprom_chip, spi_flash_chip,
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
    assert spi_flash_from_id(b"\x1F\x26\x00").size == 0            # AT45 DataFlash: recognised, not readable


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
    from chipaudio.readers.chips import ChipInfo
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

from chipaudio.readers import NoDevice, xgecu                             # noqa: E402

FAKE = f'"{sys.executable}" "{Path(__file__).with_name("fake_minipro.py")}"'


@pytest.fixture()
def fake_xgecu(tmp_path, monkeypatch):
    data = _bytes(262144, 9)
    (tmp_path / "chip.bin").write_bytes(data)
    monkeypatch.setenv("CHIPAUDIO_MINIPRO", FAKE)
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
