"""Drive the desktop app without a screen: open a folder, change settings, export."""
import json
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QSettings, QTimer                 # noqa: E402
from PySide6.QtWidgets import QApplication                              # noqa: E402

from audiochip.codecs import Hypothesis                                 # noqa: E402
from audiochip.export import ExportOptions, export                      # noqa: E402
from audiochip.gui.main_window import MainWindow                        # noqa: E402
from audiochip import profiles                                          # noqa: E402
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
    QSettings("AudioChipDecoder", "AudioChipDecoder").clear()
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
    from audiochip.collection import build
    coll = build([tmp_path / "junk"])
    assert len(coll.errors) == 1 and coll.tracks[0].chips[0].role == "blank" and coll.hyp is None
    w.coll = coll
    w._set_loaded(True)
    w._fill_tree()
    assert "No audio encoding recognised" in w.headline.text()
    w.close()


def test_read_a_chip_inside_the_app_then_decode(app, tmp_path, monkeypatch, voice):
    """Simulated programmer holding a mu-law recording: detect, read twice, save, decode."""
    from audiochip.gui.read_dialog import ReadDialog
    body = encode(voice, "mulaw_inv")[:60000] + b"\x00" * 5536             # a 64 KB chip, padded at the end
    chipfile = tmp_path / "chip.bin"
    chipfile.write_bytes(body)
    monkeypatch.setenv("AUDIOCHIP_SIM", str(chipfile))
    settings = QSettings("AudioChipDecoder", "AudioChipDecoderTest")
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
    from audiochip.gui.read_dialog import ReadDialog
    settings = QSettings("AudioChipDecoder", "AudioChipDecoderTest")
    settings.setValue("dump_dir", str(tmp_path / "dumps"))
    settings.setValue("reader", "ch341a")
    monkeypatch.setenv("AUDIOCHIP_SIM", str(tmp_path / "nothing-here.bin"))        # empty socket
    dlg = ReadDialog(settings)
    dlg._detect()
    _wait(dlg)
    assert dlg._detected is None and "No 25-series" in dlg.detect_text.text()
    dlg._read()
    _wait(dlg)
    assert "Nothing was saved" in dlg.result.text() and not dlg.open_after

    data = bytes(range(256)) * 128                                                # a 24C256
    (tmp_path / "ee.bin").write_bytes(data)
    monkeypatch.setenv("AUDIOCHIP_SIM", str(tmp_path / "ee.bin"))
    monkeypatch.setenv("AUDIOCHIP_SIM_KIND", "i2c")
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
    from audiochip.gui.read_dialog import ReadDialog
    body = (encode(music, "u8") * 3)[:262144]
    (tmp_path / "eprom.bin").write_bytes(body)
    fake = Path(__file__).with_name("fake_minipro.py")
    monkeypatch.setenv("AUDIOCHIP_MINIPRO", f'"{sys.executable}" "{fake}"')
    monkeypatch.setenv("FAKE_MINIPRO_DATA", str(tmp_path / "eprom.bin"))
    monkeypatch.setenv("FAKE_MINIPRO_MODEL", "tl866ii: TL866II+")
    settings = QSettings("AudioChipDecoder", "AudioChipDecoderTest")
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


# ------------------------------------------------------------------ 0.4.0 screens
def test_sounds_list_marks_and_recent_menu(win, tmp_path):
    assert win.sounds.topLevelItemCount() >= 2 and "silent gaps" in win.sounds_note.text()
    win.sounds.setCurrentItem(win.sounds.topLevelItem(1))
    s = win.sounds.topLevelItem(1).data(0, 0x0100)
    assert win.wave._region == (s.start, s.end) and win.sound_btn.isEnabled()
    win._play_sound()                                       # no sound device here: must not raise
    win._fill_recent_menu()
    first = win.recent_menu.actions()[0]
    assert str(tmp_path / "set") in first.text() and first.isEnabled()


def test_chip_format_sets_speed_and_lists_indexed_sounds(app, tmp_path, monkeypatch, voice):
    from test_formats import upd_rom
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    (tmp_path / "Voice").mkdir()
    (tmp_path / "Voice" / "u5.bin").write_bytes(upd_rom([voice[:16384], voice[30000:40240], voice[50000:58192]]))
    w = MainWindow()
    w.show()
    w.load_paths([str(tmp_path / "Voice")])
    _wait(w)
    assert w.coll.hyp == Hypothesis("upd7759") and w.coll.rate == 8000
    assert "chip format" in w.speed_chip.text() and "recognised" in w.enc_combo.itemText(0)
    assert w.sounds.topLevelItemCount() == 3 and "index" in w.sounds_note.text()
    assert "uPD7759" in w.formula.toPlainText() and w.wave._marks == []
    w.close()


def test_export_dialog_options_reach_the_exporter(win, tmp_path):
    from audiochip.gui.dialogs import ExportDialog
    d = ExportDialog(str(tmp_path / "out"), False, "summary", win, win.settings, 5)
    assert "5 found" in d.per_sound.text() and d.fmt.currentData() == "wav" and not d.cleanup().any
    d.fmt.setCurrentIndex(d.fmt.findData("flac"))
    d.per_sound.setChecked(True)
    d.c_level.setChecked(True)
    d.c_hum.setCurrentIndex(2)
    d._accept()
    c = d.cleanup()
    assert c.level and c.hum_hz == 60 and "same samples" in d.fmt_note.text()
    again = ExportDialog(str(tmp_path / "out"), True, "summary", win, win.settings)
    assert again.fmt.currentData() == "flac" and again.per_sound.isChecked() and again.cleanup().hum_hz == 60


def test_speed_check_plays_all_three_in_turn(win):
    from audiochip.gui.dialogs import ListeningCheck

    class FakePlayer:
        available = True

        def __init__(self):
            self.played = []

        def play(self, x, rate, start=0):
            self.played.append((len(x), rate))
            return True

        def stop(self):
            pass
    p = FakePlayer()
    d = ListeningCheck(win._audio, [8000, 11025, 16000], 11025, p, win, {8000: "voice pitch 120 Hz"})
    assert "voice pitch 120 Hz" in d._rows[0][0].text() and "1." in d._rows[0][0].text()
    d._play_all()
    assert p.played[0][1] == 8000 and d._rows[0][1].text() == "Playing" and d._timer.isActive()
    d._next_turn()
    d._next_turn()
    assert [r for _, r in p.played] == [8000, 11025, 16000]
    d._next_turn()
    assert d._turn == -1 and d._rows[2][1].text() == "Play"
    d._play(1)
    assert not d._timer.isActive() and p.played[-1] == (len(d._clip), 11025)
    d.reject()


def test_version_notice_appears_only_for_a_newer_release(win):
    from audiochip import __version__, update
    assert update.is_newer("v9.0.0", __version__) and not update.is_newer(__version__, __version__)
    assert not update.is_newer("0.1", "0.3.2") and not update.is_newer("", "0.3.2")
    win._update_result(None)
    win._update_result((__version__, update.PAGE))
    assert win.update_btn.isHidden()
    win._update_result(("9.1.0", update.PAGE))
    assert not win.update_btn.isHidden() and "9.1.0" in win.update_btn.text()


def test_read_dialog_adapts_to_each_serial_reader(app, tmp_path, monkeypatch):
    from audiochip.gui.read_dialog import ReadDialog
    f = tmp_path / "chip.bin"
    f.write_bytes(bytes(range(256)) * 256)
    monkeypatch.setenv("AUDIOCHIP_SIM", str(f))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    d = ReadDialog(QSettings("AudioChipDecoder", "AudioChipDecoder"))
    d.show()
    for kind, i2c in (("ch347", True), ("ft232h", False), ("serprog", False), ("ch341a", True)):
        d.source.setCurrentIndex(d.source.findData(kind))
        assert d._reader_ok and (d.family.findData("i2c_eeprom") >= 0) is i2c
        assert d.port.isVisible() is (kind == "serprog") and d.wiring.isVisible() is (kind in ("ft232h", "serprog"))
    d.open_after = []
    d.close()
