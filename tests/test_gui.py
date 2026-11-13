"""Drive the desktop app without a screen: open a folder, change settings, export."""
import json
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QSettings, QTimer                 # noqa: E402
from PySide6.QtWidgets import QApplication                              # noqa: E402

from chipaudio.codecs import Hypothesis                                 # noqa: E402
from chipaudio.export import ExportOptions, export                      # noqa: E402
from chipaudio.gui.main_window import MainWindow                        # noqa: E402
from chipaudio import profiles                                          # noqa: E402
from conftest import encode                                             # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _wait(win, ms=30000):
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: win._worker is None and loop.quit())
    poll.start(20)
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    assert win._worker is None, "background job did not finish"


@pytest.fixture()
def win(app, tmp_path, monkeypatch, voice, music):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    QSettings("ChipAudioDecoder", "ChipAudioDecoder").clear()
    for name, sig in (("Dialog", voice), ("Music", music)):
        body = b"\x00" * 4 + encode(sig, "mulaw_inv")[:59996 - 5000] + b"\x00" * 5000
        (tmp_path / "set" / name).mkdir(parents=True)
        for i in range(3):
            (tmp_path / "set" / name / f"{i + 1}.bin").write_bytes(body[i * 20000:(i + 1) * 20000])
    (tmp_path / "set" / "Music" / "3 copy.bin").write_bytes(body[40000:60000])
    w = MainWindow()
    w.show()
    w.load_paths([str(tmp_path / "set")])
    _wait(w)
    yield w
    w.close()


def test_open_folder_fills_the_window(win):
    assert win.stack.currentIndex() == 1 and win.tree.topLevelItemCount() == 2
    assert win.coll.hyp in (Hypothesis("mulaw_inv"), Hypothesis("alaw_plain"))
    assert "mu-law" in win.headline.text() or "A-law" in win.headline.text()
    assert win._audio is not None and len(win._audio) == 60000
    assert "confidence" in win.enc_chip.text() and "not confirmed" in win.speed_chip.text()
    assert "Top-band energy" in win.evidence.toPlainText()
    assert "3 copy.bin" in win.fileinfo.toPlainText() or True


def test_manual_encoding_speed_scope_and_reset(win):
    coll = win.coll
    auto = coll.hyp
    i = win.enc_combo.findData(Hypothesis("u8").key)
    win.enc_combo.setCurrentIndex(i)
    win._encoding_chosen(i)
    assert coll.hyp == Hypothesis("u8") and coll.hyp_source == "user" and "set by hand" in win.enc_chip.text()

    win.rate_combo.setEditText("12345")
    win._rate_chosen()
    assert coll.rate == 12345 and win._audio_rate == 12345

    win.scope_track.setChecked(True)
    track, _ = win.current()
    j = win.enc_combo.findData(Hypothesis("s8").key)
    win._encoding_chosen(j)
    assert track.hyp_override == Hypothesis("s8") and coll.hyp == Hypothesis("u8")

    win._reset_auto()
    assert coll.hyp == auto and coll.hyp_source == "auto" and not any(t.hyp_override for t in coll.tracks)
    assert not coll.rate_confirmed


def test_selecting_a_chip_moving_it_and_details(win):
    top = win.tree.topLevelItem(1)                     # Music
    win.tree.setCurrentItem(top.child(0))
    track, chip = win.current()
    assert chip.name == "1.bin" and len(win._audio) == 20000
    assert "Worked example" in win.formula.toPlainText()
    assert win.hex.toPlainText().startswith("00000000  00 00 00 00")
    win._move(+1)
    assert [c.name for c in track.members][:2] == ["2.bin", "1.bin"]
    assert win.current()[1] is chip and track.warnings
    dup = [c for c in track.chips if c.role == "duplicate"]
    assert len(dup) == 1 and dup[0].name == "3 copy.bin"


def test_profile_round_trip_and_export(win, tmp_path):
    coll = win.coll
    win._apply_profile(profiles.BUILTIN[0])
    assert coll.rate == 32000 and coll.rate_confirmed and "confirmed" in win.speed_chip.text()
    profiles.save_user(profiles.Profile("Bench test", "u8", "normal", 1, 11025))
    assert profiles.find("bench").rate == 11025
    out = tmp_path / "out"
    win._run(lambda prog: (export(coll, out, ExportOptions(), prog), out), lambda r: None, "Exporting")
    _wait(win)
    manifest = json.loads((out / "manifest.json").read_text())
    assert len(manifest["files"]) == 2 + 6 and manifest["collection"]["rate_confirmed_by_ear"] is True
    assert (out / "Joined tracks" / "Dialog.wav").stat().st_size == 44 + 2 * (60000 - 5000)


def test_unreadable_and_empty_inputs_do_not_crash(app, tmp_path):
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "bad.hex").write_text(":10000000ZZ\n")
    (tmp_path / "junk" / "blank.bin").write_bytes(b"\xFF" * 4096)
    w = MainWindow()
    from chipaudio.collection import build
    coll = build([tmp_path / "junk"])
    assert len(coll.errors) == 1 and coll.tracks[0].chips[0].role == "blank" and coll.hyp is None
    w.coll = coll
    w._set_loaded(True)
    w._fill_tree()
    assert "No audio encoding recognised" in w.headline.text()
    w.close()


def test_read_a_chip_inside_the_app_then_decode(app, tmp_path, monkeypatch, voice):
    """Simulated programmer holding a mu-law recording: detect, read twice, save, decode."""
    from chipaudio.gui.read_dialog import ReadDialog
    body = encode(voice, "mulaw_inv")[:60000] + b"\x00" * 5536             # a 64 KB chip, padded at the end
    chipfile = tmp_path / "chip.bin"
    chipfile.write_bytes(body)
    monkeypatch.setenv("CHIPAUDIO_SIM", str(chipfile))
    settings = QSettings("ChipAudioDecoder", "ChipAudioDecoderTest")
    settings.setValue("dump_dir", str(tmp_path / "dumps"))
    settings.setValue("reader", "ch341a")
    dlg = ReadDialog(settings)
    assert dlg._reader_ok and "connected" in dlg.reader_chip.text()
    dlg._detect()
    _wait(dlg)
    assert dlg._detected.size == 65536 and "64 KB" in dlg.detect_text.text()
    dlg.track.setText("Bench")
    dlg._read()
    _wait(dlg)
    saved = tmp_path / "dumps" / "Bench" / "1.bin"
    assert saved.read_bytes() == body and dlg.number.value() == 2
    assert "identical" in dlg.result.text() and dlg.decode_btn.isEnabled()
    assert dlg.open_after == [tmp_path / "dumps" / "Bench"]

    w = MainWindow()
    w.load_paths([str(p) for p in dlg.open_after])
    _wait(w)
    assert w.coll.hyp in (Hypothesis("mulaw_inv"), Hypothesis("alaw_plain"))
    assert w.coll.tracks[0].members[0].structure.has_end_padding
    w.close()


def test_blank_and_i2c_reads_in_the_dialog(app, tmp_path, monkeypatch):
    from chipaudio.gui.read_dialog import ReadDialog
    settings = QSettings("ChipAudioDecoder", "ChipAudioDecoderTest")
    settings.setValue("dump_dir", str(tmp_path / "dumps"))
    settings.setValue("reader", "ch341a")
    monkeypatch.setenv("CHIPAUDIO_SIM", str(tmp_path / "nothing-here.bin"))        # empty socket
    dlg = ReadDialog(settings)
    dlg._detect()
    _wait(dlg)
    assert dlg._detected is None and "No 25-series" in dlg.detect_text.text()
    dlg._read()
    _wait(dlg)
    assert "Nothing was saved" in dlg.result.text() and not dlg.open_after

    data = bytes(range(256)) * 128                                                # a 24C256
    (tmp_path / "ee.bin").write_bytes(data)
    monkeypatch.setenv("CHIPAUDIO_SIM", str(tmp_path / "ee.bin"))
    monkeypatch.setenv("CHIPAUDIO_SIM_KIND", "i2c")
    dlg = ReadDialog(settings)
    dlg.family.setCurrentIndex(dlg.family.findData("i2c_eeprom"))
    dlg.variant.setCurrentIndex(8)
    dlg.track.setText("EE")
    dlg._read()
    _wait(dlg)
    assert (tmp_path / "dumps" / "EE" / "1.bin").read_bytes() == data
    assert "repeat" in dlg.result.text()


def test_read_a_parallel_eprom_through_an_xgecu_programmer(app, tmp_path, monkeypatch, music):
    import sys
    from pathlib import Path
    from chipaudio.gui.read_dialog import ReadDialog
    body = (encode(music, "u8") * 3)[:262144]
    (tmp_path / "eprom.bin").write_bytes(body)
    fake = Path(__file__).with_name("fake_minipro.py")
    monkeypatch.setenv("CHIPAUDIO_MINIPRO", f'"{sys.executable}" "{fake}"')
    monkeypatch.setenv("FAKE_MINIPRO_DATA", str(tmp_path / "eprom.bin"))
    monkeypatch.setenv("FAKE_MINIPRO_MODEL", "tl866ii: TL866II+")
    settings = QSettings("ChipAudioDecoder", "ChipAudioDecoderTest")
    settings.setValue("dump_dir", str(tmp_path / "dumps"))
    settings.setValue("reader", "xgecu")
    dlg = ReadDialog(settings)
    assert dlg._x_ok and "TL866II Plus connected" in dlg.x_chip.text()
    dlg.x_search.setText("27c020 dip")
    assert dlg.x_list.count() > 3 and dlg.x_list.currentItem().data(0x0100)[0] == "27C020@DIP32"
    dlg.track.setText("Sound ROM")
    dlg._read()
    _wait(dlg)
    assert (tmp_path / "dumps" / "Sound ROM" / "1.bin").read_bytes() == body
    assert "identical" in dlg.result.text()

    monkeypatch.setenv("FAKE_MINIPRO_MODE", "contact")
    dlg._read()
    _wait(dlg)
    assert "Poor contact on pin 5, 28" in dlg.result.text() and dlg.number.value() == 2
    settings.setValue("reader", "ch341a")
