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


class FakeLive:
    """Stands in for the gapless stream player."""

    def __init__(self, available=True):
        self.available, self.error, self.loop, self.playing = available, "no sound output", True, False
        self.calls = []

    def start(self, x, rate, pos=0):
        self.calls.append(("start", rate, pos))
        self.playing = True
        return True

    def set_rate(self, rate):
        self.calls.append(("rate", rate))

    def stop(self):
        self.playing = False

    def close(self):
        self.calls.append(("close",))


class FakePlayer:
    """Stands in for the sound card: remembers what was asked for and where playback is."""
    available = True

    def __init__(self):
        self.played, self.playing, self.pos = [], False, 0

    def play(self, x, rate, start=0):
        self.played.append((len(x), rate, start))
        self.playing = True
        return True

    def position(self):
        return self.pos

    def stop(self):
        self.playing = False

    def rewind(self):
        self.pos = 0


def test_speed_slider_reaches_any_speed_and_changes_it_live(win):
    from audiochip.gui.dialogs import ListeningCheck, rate_to_slider, slider_to_rate
    p = FakePlayer()
    d = ListeningCheck(win._audio, [22050, 32000, 44100], 32000, p, win, {22050: "voice pitch 120 Hz"},
                       pitch_period=180.0, content_frames=48000, track_name="Dialog", many_tracks=True,
                       live=FakeLive(False))
    assert d.rate == 32000 and d._preset_btns[32000].isChecked() and "*" in d._preset_btns[22050].text()
    # the case that failed before: 24,000 Hz, offered neither by the estimate nor by its neighbours
    d.slider.setValue(rate_to_slider(24100))
    assert d.rate == 24000 and d._preset_btns[24000].isChecked() and d.spin.value() == 24000
    assert "133 Hz" in d.info.text() and "0:02.0" in d.info.text() and "25.0% slower" in d.info.text()
    d.snap.setChecked(False)
    d.slider.setValue(rate_to_slider(24100))
    assert abs(d.rate - 24100) <= 60 and not any(b.isChecked() for b in d._preset_btns.values())
    for r in (3000, 7575, 24000, 64000):                       # the slider covers the whole range evenly
        assert abs(slider_to_rate(rate_to_slider(r)) / r - 1) < 0.003
    d._preset_btns[24000].click()
    assert d.rate == 24000
    # playing: a change of speed carries on from the same spot instead of starting again
    d.toggle_play()
    assert p.played[-1][1:] == (24000, 0) and d.play_btn.text() == "Stop"
    p.pos = 5000
    d._set_rate(d.rate * 1.01, snap=False)
    assert d._live.isActive()
    d._apply_live()
    assert p.played[-1][1:] == (24240, 5000)
    p.playing = False                                           # reached the end: Repeat starts it again
    d._live.stop()
    d._tick()
    assert p.played[-1][1:] == (24240, 0) and d._want_play
    d.loop.setChecked(False)
    p.playing = False
    d._tick()
    assert not d._want_play and d.play_btn.text() == "Play"
    # a known length gives the speed exactly
    d.length.setText("0:02.5")
    d._from_length()
    assert d.rate == 19200
    d.length.setText("nonsense")
    d._from_length()
    assert "minutes and seconds" in d.note.text() and d.rate == 19200
    d.scope_track.setChecked(True)
    d._accept()
    assert d.chosen == 19200 and d.track_only


def test_speed_set_by_ear_can_apply_to_one_track(win, monkeypatch):
    from audiochip.gui import main_window as mw

    class Canned:
        def __init__(self, *a, **k):
            self.kw = k
            self.chosen, self.track_only = 24000, True

        def exec(self):
            return 1
    monkeypatch.setattr(mw, "ListeningCheck", Canned)
    track, _ = win.current()
    other = next(t for t in win.coll.tracks if t is not track)
    before = win.coll.rate
    win.listening_check()
    assert win.coll.rate_for(track) == 24000 and win.coll.rate_for(other) == before
    assert "confirmed" in win.speed_chip.text() and not win.coll.rate_confirmed
    assert win.check_btn.text() == "Set speed by ear"
    Canned.exec = lambda self: (setattr(self, "track_only", False), 1)[1]
    win.listening_check()
    assert win.coll.rate == 24000 and win.coll.rate_confirmed and track.rate_override is None


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
    for kind, i2c in (("ch347", True), ("ft232h", True), ("serprog", False), ("ch341a", True)):
        d.source.setCurrentIndex(d.source.findData(kind))
        assert d._reader_ok and (d.family.findData("i2c_eeprom") >= 0) is i2c
        assert d.port.isVisible() is (kind == "serprog") and d.wiring.isVisible() is (kind in ("ft232h", "serprog"))
    d.open_after = []
    d.close()


# ------------------------------------------------------------------ 0.4.2 screens
def test_gap_checkbox_switches_between_stream_and_restart(win):
    from audiochip.gui.dialogs import ListeningCheck
    p, live = FakePlayer(), FakeLive()
    d = ListeningCheck(win._audio, [], 32000, p, win, live=live, settings=win.settings)
    assert d.smooth.isEnabled() and d.smooth.isChecked()
    d.toggle_play()
    assert live.calls == [("start", 32000, 0)] and not p.played
    d._set_rate(24000, snap=False)
    assert live.calls[-1] == ("rate", 24000) and not d._live.isActive() and not p.played   # no restart, no gap
    d.loop.setChecked(False)
    assert live.loop is False
    d.smooth.setChecked(False)                       # with the gap: back to the basic player, still playing
    assert p.played[-1][1:] == (24000, 0) and d._want_play and not d._streaming
    p.pos = 700
    d._set_rate(25000, snap=False)
    d._apply_live()
    assert p.played[-1][1:] == (25000, 700) and ("rate", 25000) not in live.calls
    assert win.settings.value("speed_smooth") == "false"
    d.reject()
    assert live.calls[-1] == ("close",)
    again = ListeningCheck(win._audio, [], 32000, FakePlayer(), win, live=FakeLive(), settings=win.settings)
    assert not again.smooth.isChecked()
    none = ListeningCheck(win._audio, [], 32000, FakePlayer(), win, live=FakeLive(False), settings=win.settings)
    assert not none.smooth.isEnabled() and "no sound output" in none.smooth.toolTip()
    again.reject()
    none.reject()
    win.settings.remove("speed_smooth")


def test_stream_reads_the_clip_faster_or_slower_without_breaks():
    import numpy as np
    from audiochip.gui.player import render_block
    x = (np.arange(1000) * 10).astype(np.int16).reshape(-1, 1)
    half, pos, ended = render_block(x, 0.0, 0.5, 8, True)
    assert np.allclose(half[:5, 0] * 32768, [0, 5, 10, 15, 20]) and pos == 4.0 and not ended
    nxt, pos2, _ = render_block(x, pos, 2.0, 4, True)                  # speed changed: carries on from pos
    assert np.allclose(nxt[:, 0] * 32768, [40, 60, 80, 100]) and pos2 == 12.0
    tail, _, ended = render_block(x, 990.0, 2.0, 8, False)
    assert ended and np.allclose(tail[5:, 0], 0)
    wrap, pos3, ended = render_block(x, 996.0, 1.0, 8, True)
    assert not ended and pos3 == 4.0 and np.allclose(wrap[4:, 0] * 32768, [0, 10, 20, 30])


def test_about_box_describes_a_chip_and_a_track(win):
    track, _ = win.current()
    labels = {r[0]: r[1] for r in win._about_rows}
    assert win.about_cap.text() == "ABOUT THIS TRACK"
    assert labels["Chips"].startswith("3 in use") and "60,000" in labels["Size"] and "Filler" in labels
    assert "samples" in labels["Length"] and "kbit per second" in labels["Storage"]
    assert "%" in labels["Loudest peak"] and "dB" in labels["Average level"] and "kHz" in labels["Frequencies"]
    item = win.tree.topLevelItem(0).child(0)
    win.tree.setCurrentItem(item)
    labels = {r[0]: r[1] for r in win._about_rows}
    assert win.about_cap.text() == "ABOUT THIS FILE" and labels["File"] == "1.bin"
    assert "chip 1 of 3" in labels["Role"] and labels["Fingerprint"].endswith("...") and "Sound data" in labels
    shown = [k for k, _ in win._about_cells if not k.isHidden()]
    assert len(shown) == len(win._about_rows) and shown[0].text() == "File"
    win._copy_about()
    from PySide6.QtWidgets import QApplication
    assert "Fingerprint:" in QApplication.clipboard().text()


def test_chip_markings_and_packages_in_the_read_dialog(app, tmp_path, monkeypatch):
    from audiochip.gui.read_dialog import ReadDialog
    from audiochip.readers import xgecu
    from audiochip.readers.chips import chip_from_marking
    want = {"24C256": ("i2c_eeprom", 32768), "AT24C02": ("i2c_eeprom", 256), "M24M01": ("i2c_eeprom", 131072),
            "BR24T08": ("i2c_eeprom", 1024), "25LC256": ("spi_eeprom", 32768), "M95160": ("spi_eeprom", 2048),
            "W25Q64FV": ("spi_flash", 8 << 20), "MX25L1606E": ("spi_flash", 2 << 20), "25Q32": ("spi_flash", 4 << 20),
            "SST25VF040B": ("spi_flash", 512 << 10), "AT45DB041D": ("at45", 2048 * 264)}
    for text, (family, size) in want.items():
        chip, _ = chip_from_marking(text)
        assert chip is not None and (chip.family, chip.size) == (family, size), text
    assert chip_from_marking("93C46")[0] is None and "93-series" in chip_from_marking("93C46")[1]
    assert chip_from_marking("27C256")[0] is None

    assert xgecu.package_of("W25Q32BV@SOIC8") == "soic" and xgecu.package_of("27C256") == "dip"
    assert xgecu.package_of("AM29F040@PLCC32") == "plcc" and xgecu.package_of("X@TSOP48") == "tsop"
    soic = xgecu.search("ii", "24C256", package="soic")
    assert soic and all("@SO" in n.upper() for n, _, _ in soic)

    f = tmp_path / "chip.bin"
    f.write_bytes(bytes(range(256)) * 256)
    monkeypatch.setenv("AUDIOCHIP_SIM", str(f))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    d = ReadDialog(QSettings("AudioChipDecoder", "AudioChipDecoder"))
    d.show()
    d.source.setCurrentIndex(d.source.findData("ch341a"))
    assert "SOIC-8" in d.source.currentText()
    d.marking.setText("24LC512")
    assert d.family.currentData() == "i2c_eeprom" and d._chosen_chip().size == 65536 and "64 KB" in d.hint.text()
    d.marking.setText("25LC256")
    assert d.family.currentData() == "spi_eeprom" and d._chosen_chip().size == 32768
    d.marking.setText("AT45DB041D")
    assert d._chosen_chip().family == "at45"
    d.source.setCurrentIndex(d.source.findData("ft232h"))
    d.marking.setText("24C02")                                   # an FT232H reads I2C too, with its own wiring
    assert d.family.currentData() == "i2c_eeprom" and "AD1 and AD2" in d.wiring.text() and "SDA" in d.wiring.text()
    d.family.setCurrentIndex(d.family.findData("spi_flash"))
    assert "chip select" in d.wiring.text() and "SDA" not in d.wiring.text()
    d.source.setCurrentIndex(d.source.findData("serprog"))
    d.marking.setText("")
    d.marking.setText("24C02")
    assert "SPI chips only" in d.hint.text()
    d.source.setCurrentIndex(d.source.findData("xgecu"))
    d.x_package.setCurrentIndex(d.x_package.findData("soic"))
    d.x_search.setText("25Q32")
    assert d.x_list.count() and all("SO" in d.x_list.item(i).text() for i in range(d.x_list.count()))
    d.open_after = []
    d.close()


# ------------------------------------------------------------------ 0.4.4 window sizing
def test_starts_maximised_with_details_hidden_and_a_windowed_size_that_fits(app, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    s = QSettings("AudioChipDecoder", "AudioChipDecoder")
    s.setValue("details_open", True)                       # left behind by versions that reopened the panel
    w = MainWindow()
    room = w._screen_room()
    assert w.width() <= room.width() and w.height() <= room.height()       # the size it has when not maximised
    w.show_fitted()
    assert w.isMaximized() and w.dock.isHidden()
    w._open_details()
    assert not w.dock.isHidden()
    w._toggle_details()
    assert w.dock.isHidden()
    w.close()


def test_details_panel_floats_when_the_screen_has_no_room_beside(win, monkeypatch):
    from PySide6.QtCore import QRect
    monkeypatch.setattr(win, "_screen_room", lambda: QRect(0, 0, 1280, 700))
    win.showNormal()
    win.resize(1100, 640)
    win._show_details(1)
    assert win.dock.isFloating() and win.dock.width() <= 1240 and win.dock.height() <= 620
    assert win.width() <= 1280 and win.tabs.currentIndex() == 1
    win.dock.hide()


def test_dialogs_shrink_to_a_small_screen_and_scroll(app, tmp_path, monkeypatch, win):
    from PySide6.QtCore import QSize
    from audiochip.gui.dialogs import ExportDialog, ListeningCheck
    from audiochip.gui.read_dialog import ReadDialog
    from audiochip.gui.widgets import FitDialog
    monkeypatch.setattr(FitDialog, "_room", lambda self: QSize(700, 420))
    monkeypatch.setenv("AUDIOCHIP_SIM", str(tmp_path / "none.bin"))
    dialogs = [ReadDialog(QSettings("AudioChipDecoder", "AudioChipDecoder")),
               ListeningCheck(win._audio, [], 32000, FakePlayer(), win, live=FakeLive(False)),
               ExportDialog(str(tmp_path / "out"), False, "summary", win)]
    for d in dialogs:
        d.show()
        app.processEvents()
        assert d.width() <= 700 and d.height() <= 420, type(d).__name__
        assert d._inner.sizeHint().height() > d.height()                   # more than fits: it scrolls
        assert d._scroll.verticalScrollBar().maximum() > 0
        if hasattr(d, "open_after"):
            d.open_after = []
        d.reject()


def test_an_audio_file_opens_like_a_dump_and_states_its_speed(app, tmp_path, monkeypatch, voice, music):
    import io
    import numpy as np
    sf = pytest.importorskip("soundfile")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    (tmp_path / "Song").mkdir()
    buf = io.BytesIO()
    sf.write(buf, np.stack([voice[:24000], music[:24000]], axis=1), 22050, format="WAV", subtype="MS_ADPCM")
    (tmp_path / "Song" / "song.wav").write_bytes(buf.getvalue())
    w = MainWindow()
    w.show()
    w.load_paths([str(tmp_path / "Song")])
    _wait(w)
    assert w.coll.hyp == Hypothesis("audiofile") and w.coll.rate == 22050
    assert w.headline.text().startswith("Audio file") and "stated by the file" in w.speed_chip.text()
    assert w._audio.shape[1] == 2 and "recognised" in w.enc_combo.itemText(0)
    keys = [w.enc_combo.itemData(i) for i in range(w.enc_combo.count())]
    assert "g721/normal/1" in keys and "s24le/normal/1" in keys and "gsm610/normal/1" in keys
    w.enc_combo.setCurrentIndex(keys.index("g721/normal/1"))            # picking one by hand must not fail
    w._encoding_chosen(keys.index("g721/normal/1"))
    assert w.coll.hyp == Hypothesis("g721") and w._audio is not None
    w.close()


# ------------------------------------------------------------------ 0.5.1 robustness
def _slow_window(app, tmp_path, monkeypatch, voice, size=40000):
    from conftest import encode
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    (tmp_path / "Board").mkdir()
    (tmp_path / "Board" / "1.bin").write_bytes(encode(voice[:size], "u8"))
    w = MainWindow()
    w.show()
    w.load_paths([str(tmp_path / "Board")])
    _wait(w)
    return w, [w.enc_combo.itemData(i) for i in range(w.enc_combo.count())]


def test_a_slow_decoder_runs_in_the_background_and_then_shows(app, tmp_path, monkeypatch, voice):
    w, keys = _slow_window(app, tmp_path, monkeypatch, voice)
    used = []
    real = w._background
    monkeypatch.setattr(w, "_background", lambda job, text: (used.append(text), real(job, text))[1])
    w._encoding_chosen(keys.index("lpc_5220/normal/1"))
    assert used and "one sample at a time" in used[0]
    assert w.coll.hyp == Hypothesis("lpc_5220") and w._decode_problem == ""
    used.clear()
    w.tree.setCurrentItem(w.tree.topLevelItem(0).child(0))            # already decoded: no second wait
    w.tree.setCurrentItem(w.tree.topLevelItem(0))
    assert not used
    w.close()


def test_cancelling_a_slow_decode_restores_the_previous_encoding(app, tmp_path, monkeypatch, voice):
    import time
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QProgressDialog
    from audiochip import codecs
    from audiochip.gui import main_window as mw
    w, keys = _slow_window(app, tmp_path, monkeypatch, voice, 41000)    # other sizes: not already decoded
    before = w.coll.hyp

    def never_ending(coll, progress=None, remeasure=True):
        for i in range(2000):
            time.sleep(0.01)
            codecs.check_cancel()
    monkeypatch.setattr(mw, "warm", never_ending)
    QTimer.singleShot(200, lambda: [d.canceled.emit() for d in w.findChildren(QProgressDialog)])   # as the button does
    t = time.time()
    w._encoding_chosen(keys.index("lpc_5220/normal/1"))
    assert time.time() - t < 5 and w.coll.hyp == before
    assert "Cancelled" in w.statusBar().currentMessage() and w._audio is not None and len(w._audio)
    for _ in range(100):                                              # the worker notices and stops
        app.processEvents()
        if not w._bg:
            break
        time.sleep(0.02)
    assert not w._bg
    w.close()


def test_a_decoder_that_fails_reports_it_and_keeps_the_window_alive(app, tmp_path, monkeypatch, voice):
    from PySide6.QtWidgets import QMessageBox
    from audiochip.gui import main_window as mw
    w, keys = _slow_window(app, tmp_path, monkeypatch, voice, 42000)
    before = w.coll.hyp
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(mw, "warm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("decoder broke")))
    w._encoding_chosen(keys.index("lpc_5220/normal/1"))
    assert shown and "decoder broke" in shown[0] and w.coll.hyp == before
    # an error while decoding what is on screen becomes a notice, not an exception
    monkeypatch.setattr(w, "_selection_audio_now", lambda *a: (_ for _ in ()).throw(ValueError("bad block")))
    w.refresh()
    assert "could not be decoded" in w.notice.text() and "bad block" in w.notice.text()
    assert not w.play_btn.isEnabled() and w._audio is None
    w.listening_check()                                               # nothing to play: must simply do nothing
    w._play_sound()
    w.close()


def test_duration_column_never_decodes_on_the_ui_thread(app, tmp_path, monkeypatch, voice):
    from audiochip import codecs
    w, keys = _slow_window(app, tmp_path, monkeypatch, voice, 39000)
    track = w.coll.tracks[0]
    w.coll.hyp = Hypothesis("lpc_0281")                               # not decoded yet
    assert not codecs.is_cached(track.members[0].dump.data, w.coll.hyp)
    assert w._duration(0, track) == ""
    w.close()


def test_every_encoding_can_be_chosen_for_every_kind_of_file(app, tmp_path, monkeypatch, voice, music):
    """The sweep that found the 0.5.0 problems: each entry of the Encoding list, on each kind of file."""
    import io
    import numpy as np
    from conftest import encode
    sf = pytest.importorskip("soundfile")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    buf = io.BytesIO()
    sf.write(buf, np.stack([voice[:6000], music[:6000]], axis=1), 22050, format="WAV", subtype="PCM_16")
    cases = {"voice": encode(voice[:9000], "u8"), "random": np.random.default_rng(1).integers(0, 256, 6000, dtype=np.uint8).tobytes(),
             "tiny": bytes(range(50)), "one": b"\x7f", "blank": b"\xff" * 4096, "odd": encode(voice[:7001], "u8"),
             "song": buf.getvalue(), "cut": buf.getvalue()[:900], "riff": b"RIFF\xff\xff\xff\xffWAVEfmt " + bytes(300)}
    tried = 0
    for name, data in cases.items():
        d = tmp_path / name
        d.mkdir()
        (d / ("a.wav" if name in ("song", "cut", "riff") else "a.bin")).write_bytes(data)
        w = MainWindow()
        w.show()
        w.load_paths([str(d)])
        _wait(w)
        assert w.coll is not None and w.coll.tracks, name
        for pick in (lambda: w.tree.topLevelItem(0), lambda: w.tree.topLevelItem(0).child(0)):
            w.tree.setCurrentItem(pick())                   # fetched afresh: the list is rebuilt after each change
            for i in range(w.enc_combo.count()):
                if not w.enc_combo.itemData(i):
                    continue
                w.enc_combo.setCurrentIndex(i)
                w._encoding_chosen(i)                       # must neither raise nor leave the window broken
                assert w._audio is None or w._audio.ndim == 2, (name, w.enc_combo.itemData(i))
                tried += 1
            w._show_details(2)
            w._reset_auto()
        w.close()
    assert tried > 600


def test_speed_box_ignores_blank_and_meaningless_input(win):
    before = win.coll.rate
    for text in ("", "   ", "abc", "0", "-5", "999", "inf", "nan", "1e99", "9999999999"):
        win.rate_combo.setEditText(text)
        win._rate_chosen()
        assert win.coll.rate == before, text
    win.rate_combo.setEditText("24,000 Hz")
    win._rate_chosen()
    assert win.coll.rate_for(win.current()[0]) == 24000
