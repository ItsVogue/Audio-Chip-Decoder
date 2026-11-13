"""Dialogs: listening check, export, reading a chip, help."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QSlider, QSpinBox,
                               QTextBrowser, QVBoxLayout)

from ..cleanup import Cleanup
from ..export import FORMATS

from .player import Player, why_no_sound

CLIP_SECONDS = 8


def loudest_clip(x: np.ndarray, frames: int) -> np.ndarray:
    """Pick the stretch with the most going on, so the comparison is not of silence."""
    if len(x) <= frames:
        return x
    block = max(frames // 8, 1)
    stride = max(len(x) // 4_000_000, 1)                  # sample long recordings instead of scanning it all
    mono = np.abs(x[::stride, 0].astype(np.int32))
    sums = np.add.reduceat(mono, np.arange(0, len(mono), max(block // stride, 1)))
    window = np.convolve(sums, np.ones(8), mode="valid")
    start = int(window.argmax()) * block
    return x[start:start + frames]


SPEED_MIN, SPEED_MAX = 3000, 64000
SLIDER_STEPS = 1200                      # about 0.25% per step across the whole range
SNAP_WITHIN = 0.015                      # snap to a standard speed when within 1.5% of it
PRESETS = (4000, 5512, 6000, 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)


def slider_to_rate(value: int) -> int:
    """The slider is logarithmic, so each step changes the pitch by the same small amount."""
    rate = SPEED_MIN * (SPEED_MAX / SPEED_MIN) ** (value / SLIDER_STEPS)
    return int(round(rate / 5.0) * 5)


def rate_to_slider(rate: float) -> int:
    rate = min(max(rate, SPEED_MIN), SPEED_MAX)
    return int(round(SLIDER_STEPS * np.log(rate / SPEED_MIN) / np.log(SPEED_MAX / SPEED_MIN)))


def parse_length(text: str) -> float | None:
    """Seconds from "83.5", "1:23" or "1:23.5"."""
    text = text.strip().replace(",", ".")
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if not 1 <= len(parts) <= 3 or any(p < 0 for p in parts):
        return None
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds if seconds > 0 else None


class ListeningCheck(QDialog):
    """Set the speed by ear: the sound keeps playing while a slider changes its speed."""

    def __init__(self, audio: np.ndarray, rates: list[int], current: int, player: Player, parent=None,
                 reasons: dict[int, str] | None = None, pitch_period: float = 0.0,
                 content_frames: int | None = None, track_name: str = "", track_only: bool = False,
                 many_tracks: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Set the speed by ear")
        self.setMinimumWidth(720)
        self.player = player
        self.chosen: int | None = None
        self.track_only = track_only
        self._audio = audio
        self._likely = sorted(rates)
        self._reasons = reasons or {}
        self._period = pitch_period
        self._content = content_frames or len(audio)
        self._rate = int(min(max(current or 8000, SPEED_MIN), SPEED_MAX))
        self._start_rate = self._rate
        self._want_play = False
        self._setting = False
        self._live = QTimer(self)                 # waits for the slider to settle before restarting the sound
        self._live.setSingleShot(True)
        self._live.setInterval(90)
        self._live.timeout.connect(self._apply_live)
        self._poll = QTimer(self)
        self._poll.setInterval(60)
        self._poll.timeout.connect(self._tick)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        intro = QLabel("Most chips do not store their playback speed, so it is set by listening. Press <b>Play</b>, "
                       "then drag the slider or click a speed until voices and instruments sound natural. "
                       "The sound keeps playing while you change it.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        top = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.play_btn.setProperty("primary", True)
        self.play_btn.setMinimumWidth(110)
        self.play_btn.clicked.connect(self.toggle_play)
        self.readout = QLabel()
        self.readout.setStyleSheet("font-size: 26px; font-weight: 600;")
        self.readout.setMinimumWidth(190)
        self.spin = QSpinBox()
        self.spin.setRange(SPEED_MIN, SPEED_MAX)
        self.spin.setSingleStep(50)
        self.spin.setSuffix(" Hz")
        self.spin.setGroupSeparatorShown(True)
        self.spin.setMinimumWidth(130)
        self.spin.setToolTip("Type an exact speed here if you know it, for example from the board's documentation.")
        self.spin.valueChanged.connect(lambda v: self._set_rate(v, snap=False))
        top.addWidget(self.play_btn)
        top.addSpacing(12)
        top.addWidget(self.readout)
        top.addStretch(1)
        top.addWidget(QLabel("Exact speed"))
        top.addWidget(self.spin)
        lay.addLayout(top)

        srow = QHBoxLayout()
        slow = QLabel("slower, lower")
        fast = QLabel("faster, higher")
        slow.setProperty("muted", True)
        fast.setProperty("muted", True)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, SLIDER_STEPS)
        self.slider.setSingleStep(4)                  # arrow keys: about 1%
        self.slider.setPageStep(20)
        self.slider.setMinimumHeight(28)
        self.slider.setToolTip("Drag while the sound plays. With the slider selected, the Left and Right arrow "
                               "keys change the speed by about 1%. Space starts and stops the sound.")
        self.slider.valueChanged.connect(self._slider_moved)
        srow.addWidget(slow)
        srow.addWidget(self.slider, 1)
        srow.addWidget(fast)
        lay.addLayout(srow)

        nrow = QHBoxLayout()
        nrow.setSpacing(6)
        for text, factor, tip in (("-5%", 0.95, "5% slower"), ("-1%", 0.99, "1% slower"),
                                  ("+1%", 1.01, "1% faster"), ("+5%", 1.05, "5% faster")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedWidth(64)
            b.clicked.connect(lambda _=False, f=factor: self._set_rate(self._rate * f, snap=False))
            nrow.addWidget(b)
        nrow.addSpacing(10)
        for text, factor, tip in (("Half", 0.5, "Half the speed: one octave lower"),
                                  ("Double", 2.0, "Twice the speed: one octave higher")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, f=factor: self._set_rate(self._rate * f, snap=True))
            nrow.addWidget(b)
        nrow.addStretch(1)
        self.snap = QCheckBox("Snap to standard speeds")
        self.snap.setChecked(True)
        self.snap.setToolTip("When the slider comes within 1.5% of a standard speed it lands exactly on it. "
                             "Untick for boards that run at an odd speed.")
        nrow.addWidget(self.snap)
        lay.addLayout(nrow)

        lay.addWidget(QLabel("<b>Standard speeds</b> (the app's best guesses are marked)"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        self._preset_btns: dict[int, QPushButton] = {}
        for i, r in enumerate(PRESETS):
            b = QPushButton(f"{r:,}")
            b.setCheckable(True)
            why = self._reasons.get(r, "")
            if r in self._likely:
                b.setText(f"{r:,}  *")
                b.setToolTip("One of the app's best guesses" + (f": {why}." if why else "."))
            elif why:
                b.setToolTip(why[0].upper() + why[1:] + ".")
            b.clicked.connect(lambda _=False, r=r: self._set_rate(r, snap=False))
            grid.addWidget(b, i // 6, i % 6)
            self._preset_btns[r] = b
        lay.addLayout(grid)

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setMinimumHeight(40)
        self.info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(self.info)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        lay.addWidget(line)

        opts = QHBoxLayout()
        self.loop = QCheckBox("Repeat")
        self.loop.setChecked(True)
        self.clip_box = QComboBox()
        self.clip_box.addItem("Play the busiest 8 seconds", "clip")
        self.clip_box.addItem("Play the whole selection", "all")
        self.clip_box.currentIndexChanged.connect(self._clip_changed)
        opts.addWidget(self.clip_box)
        opts.addWidget(self.loop)
        opts.addStretch(1)
        lay.addLayout(opts)

        lrow = QHBoxLayout()
        self.length = QLineEdit()
        self.length.setPlaceholderText("m:ss")
        self.length.setFixedWidth(90)
        self.length.returnPressed.connect(self._from_length)
        lbtn = QPushButton("Set speed from length")
        lbtn.setToolTip("If you know how long this sound should last (from the original record, film or game), "
                        "the speed follows exactly: samples divided by seconds.")
        lbtn.clicked.connect(self._from_length)
        lrow.addWidget(QLabel("Know how long it should last?"))
        lrow.addWidget(self.length)
        lrow.addWidget(lbtn)
        lrow.addStretch(1)
        lay.addLayout(lrow)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setProperty("muted", True)
        lay.addWidget(self.note)

        self.scope_all = QRadioButton("Use it for everything that is open")
        self.scope_track = QRadioButton(f"Use it for this track only ({track_name})" if track_name
                                        else "Use it for this track only")
        self.scope_track.setToolTip("For when boards that ran at different speeds are open together.")
        (self.scope_track if track_only else self.scope_all).setChecked(True)
        if many_tracks:
            sc = QHBoxLayout()
            sc.addWidget(self.scope_all)
            sc.addWidget(self.scope_track)
            sc.addStretch(1)
            lay.addLayout(sc)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok_btn = buttons.addButton("Use this speed", QDialogButtonBox.AcceptRole)
        self.ok_btn.setProperty("primary", True)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.toggle_play)
        self._clip_changed()
        self._set_rate(self._rate, snap=False)
        self.slider.setFocus()
        if not self.player.available:
            self.note.setText(why_no_sound())

    # ------------------------------------------------------------------ speed
    @property
    def rate(self) -> int:
        return self._rate

    def _slider_moved(self, value: int) -> None:
        if not self._setting:
            self._set_rate(slider_to_rate(value), snap=self.snap.isChecked(), from_slider=True)

    def _set_rate(self, rate: float, snap: bool, from_slider: bool = False) -> None:
        rate = float(min(max(rate, SPEED_MIN), SPEED_MAX))
        if snap:
            near = min(PRESETS, key=lambda p: abs(np.log(p / rate)))
            if abs(np.log(near / rate)) <= SNAP_WITHIN:
                rate = near
        rate = int(round(rate))
        changed = rate != self._rate
        self._rate = rate
        self._setting = True
        if not from_slider:
            self.slider.setValue(rate_to_slider(rate))
        self.spin.setValue(rate)
        self._setting = False
        self.readout.setText(f"{rate:,} Hz")
        for r, b in self._preset_btns.items():
            b.setChecked(r == rate)
        self._describe()
        if changed and self._want_play:
            self._live.start()

    def _describe(self) -> None:
        rate = self._rate
        parts = []
        if self._period > 0:
            parts.append(f"Voice pitch at this speed: <b>{rate / self._period:.0f} Hz</b>. Speaking voices are "
                         "usually 85 to 155 Hz for men, 165 to 255 Hz for women, and higher for children.")
        secs = self._content / rate
        parts.append(f"The selection lasts <b>{int(secs // 60)}:{secs % 60:04.1f}</b> at this speed.")
        if rate != self._start_rate:
            pct = 100 * (rate / self._start_rate - 1)
            parts.append(f"{abs(pct):.1f}% {'faster' if pct > 0 else 'slower'} than the {self._start_rate:,} Hz "
                         "it was set to.")
        self.info.setText(" ".join(parts))

    def _from_length(self) -> None:
        seconds = parse_length(self.length.text())
        if seconds is None:
            self.note.setText("Type the length as minutes and seconds, for example 1:23 or 0:07.5.")
            return
        rate = self._content / seconds
        if not SPEED_MIN <= rate <= SPEED_MAX:
            self.note.setText(f"That length would need {rate:,.0f} Hz, which is outside {SPEED_MIN:,} to "
                              f"{SPEED_MAX:,} Hz. Check the length, or whether the whole track is selected.")
            return
        self.note.setText(f"{self._content:,} samples in {seconds:.1f} seconds is {rate:,.0f} Hz.")
        self._set_rate(rate, snap=self.snap.isChecked())

    # --------------------------------------------------------------- playback
    def _clip_changed(self) -> None:
        was = self._want_play
        self._halt()
        if self.clip_box.currentData() == "all":
            self._clip = self._audio
        else:
            self._clip = loudest_clip(self._audio, CLIP_SECONDS * max(self._rate, 8000))
        if was:
            self.toggle_play()

    def toggle_play(self) -> None:
        if self._want_play:
            self._halt()
            return
        if not self.player.play(self._clip, self._rate, 0):
            self.note.setText(why_no_sound())
            return
        self._want_play = True
        self.play_btn.setText("Stop")
        self._poll.start()

    def _apply_live(self) -> None:
        """Carry on from the same spot at the new speed."""
        if not self._want_play:
            return
        pos = self.player.position() if self.player.playing else 0
        if not self.player.play(self._clip, self._rate, pos):
            self._halt()

    def _tick(self) -> None:
        if not self._want_play:
            return
        self.player.position()                       # lets the player notice it has reached the end
        if not self.player.playing and not self._live.isActive():
            if self.loop.isChecked() and self.player.play(self._clip, self._rate, 0):
                return
            self._halt()

    def _halt(self) -> None:
        self._want_play = False
        self._live.stop()
        self._poll.stop()
        self.player.stop()
        self.player.rewind()
        self.play_btn.setText("Play")

    def _accept(self) -> None:
        self._halt()
        self.chosen = self._rate
        self.track_only = self.scope_track.isChecked()
        self.accept()

    def done(self, result):
        self._halt()
        super().done(result)


class ExportDialog(QDialog):
    def __init__(self, default_dir: str, speed_confirmed: bool, summary: str, parent=None, settings=None,
                 sounds: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Export audio files")
        self.setMinimumWidth(600)
        self._settings = settings
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        head = QLabel(f"<b>Settings in force:</b> {summary}")
        head.setWordWrap(True)
        lay.addWidget(head)
        if not speed_confirmed:
            warn = QLabel("The speed is an estimate and has not been confirmed by ear. The files will still "
                          "be written; if they play too fast or slow, run the listening check and export again.")
            warn.setWordWrap(True)
            warn.setProperty("warning", True)
            warn.setMinimumHeight(46)
            lay.addWidget(warn)
        lay.addWidget(QLabel("<b>What to write</b>"))
        self.joined = QCheckBox("One file per track, with its chips joined in order")
        self.per_chip = QCheckBox("One file per chip")
        self.per_sound = QCheckBox("One file per separate sound"
                                   + (f"  ({sounds} found in the current selection)" if sounds else ""))
        self.per_sound.setToolTip("Splits each track where the chip's own index says a sound starts, or at "
                                  "silent gaps when the chip has no index. See the Sounds tab.")
        self.trim = QCheckBox("Remove the padding at the end of each track")
        self.trim.setToolTip("Chips are filled out to their full size with a repeated byte after the sound "
                             "ends. Leave this ticked for clean files. The dumps themselves are never changed.")
        for box in (self.joined, self.per_chip, self.per_sound, self.trim):
            box.setChecked(box is not self.per_sound)
            lay.addWidget(box)

        fmt_row = QHBoxLayout()
        self.fmt = QComboBox()
        for key, text in FORMATS.items():
            self.fmt.addItem(text, key)
        self.fmt.currentIndexChanged.connect(self._fmt_changed)
        fmt_row.addWidget(QLabel("<b>File type</b>"))
        fmt_row.addWidget(self.fmt)
        fmt_row.addStretch(1)
        lay.addLayout(fmt_row)

        lay.addWidget(QLabel("<b>Clean-up</b> (off by default: the plain export is the chip's exact output)"))
        self.c_offset = QCheckBox("Remove offset and rumble below 20 Hz")
        self.c_offset.setToolTip("Old 8-bit chips often sit off-centre, which thumps at the start and end of "
                                 "playback. A gentle high-pass filter at 20 Hz removes it.")
        self.c_clicks = QCheckBox("Repair isolated clicks")
        self.c_clicks.setToolTip("A single sample that jumps far from both its neighbours (a bad bit, or the "
                                 "join between two chips) is replaced by the value between them.")
        self.c_level = QCheckBox("Raise the level so the loudest peak is just below full volume")
        hum_row = QHBoxLayout()
        self.c_hum = QComboBox()
        self.c_hum.addItem("Leave hum alone", 0)
        self.c_hum.addItem("Remove 50 Hz mains hum", 50)
        self.c_hum.addItem("Remove 60 Hz mains hum", 60)
        self.c_hum.setToolTip("Narrow notches at the mains frequency and its harmonics. Only useful if the "
                              "original recording had hum on it.")
        hum_row.addWidget(self.c_hum)
        hum_row.addStretch(1)
        for w in (self.c_offset, self.c_clicks, self.c_level):
            lay.addWidget(w)
        lay.addLayout(hum_row)
        self.fmt_note = QLabel()
        self.fmt_note.setWordWrap(True)
        self.fmt_note.setProperty("muted", True)
        lay.addWidget(self.fmt_note)
        if settings is not None:
            self.fmt.setCurrentIndex(max(self.fmt.findData(settings.value("export_fmt", "wav")), 0))
            self.per_sound.setChecked(settings.value("export_per_sound", "false") == "true")
            self.c_offset.setChecked(settings.value("export_offset", "false") == "true")
            self.c_clicks.setChecked(settings.value("export_clicks", "false") == "true")
            self.c_level.setChecked(settings.value("export_level", "false") == "true")
            self.c_hum.setCurrentIndex(max(self.c_hum.findData(int(settings.value("export_hum", 0) or 0)), 0))
        self._fmt_changed()
        row = QHBoxLayout()
        self.folder = QLineEdit(default_dir)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row.addWidget(QLabel("Save into:"))
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        also = QLabel("A Report.txt and manifest.json are written alongside the audio. They record the exact "
                      "settings and a SHA-256 fingerprint of every source and output file.")
        also.setWordWrap(True)
        also.setProperty("muted", True)
        lay.addWidget(also)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        ok = buttons.addButton("Export", QDialogButtonBox.AcceptRole)
        ok.setProperty("primary", True)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder for the audio files", self.folder.text())
        if d:
            self.folder.setText(d)

    def _accept(self):
        if not self.folder.text().strip():
            return
        if not (self.joined.isChecked() or self.per_chip.isChecked() or self.per_sound.isChecked()):
            self.joined.setChecked(True)
        s = self._settings
        if s is not None:
            s.setValue("export_fmt", self.fmt.currentData())
            s.setValue("export_per_sound", "true" if self.per_sound.isChecked() else "false")
            s.setValue("export_offset", "true" if self.c_offset.isChecked() else "false")
            s.setValue("export_clicks", "true" if self.c_clicks.isChecked() else "false")
            s.setValue("export_level", "true" if self.c_level.isChecked() else "false")
            s.setValue("export_hum", int(self.c_hum.currentData()))
        self.accept()

    def _fmt_changed(self) -> None:
        self.fmt_note.setText({
            "wav": "WAV holds the samples exactly as decoded. Every player and editor opens it.",
            "flac": "FLAC holds exactly the same samples as WAV in about half the space.",
            "mp3": "MP3 is small but lossy: fine for listening, not for archiving. It only allows certain "
                   "speeds, so audio at any other speed is resampled to the next one up.",
        }[self.fmt.currentData()])

    def cleanup(self) -> Cleanup:
        return Cleanup(remove_offset=self.c_offset.isChecked(), hum_hz=int(self.c_hum.currentData()),
                       declick=self.c_clicks.isChecked(), level=self.c_level.isChecked())

    def out_dir(self) -> Path:
        return Path(self.folder.text().strip())


HELP_HTML = """
<h3>How it works</h3>
<p>Press <b>Read a chip</b>, or open dump files you already have. The app works out how the sound is stored,
shows its best reading already applied, and tells you how sure it is. You confirm the speed by ear, then
export audio files. You can also drop files or folders onto the window, or reopen something from
<i>Recent</i>.</p>
<h4>Reading chips</h4>
<p>Press <b>Read a chip</b> and pick your reader under <i>Read with</i>.</p>
<table cellpadding="5">
<tr><td><b>XGecu T48, TL866II Plus, TL866A/CS</b></td>
    <td>Reads 24- to 40-pin parallel EPROM and flash (27C, 28F, 29F, 39SF and similar) and serial chips.
        Type what is printed on the chip and pick it from the list. The programmer's USB driver must be on
        the PC; it comes with XGecu's Xgpro setup.</td></tr>
<tr><td><b>CH341A programmer</b></td>
    <td>Reads 8-pin serial chips in DIP-8 or SOIC-8. 25-series flash and AT45DB DataFlash are identified
        automatically. 24-series and 25/95-series EEPROMs carry no ID, so you pick the type printed on the
        chip. Works with WCH's driver, or with the generic WinUSB driver that Zadig installs (slower, SPI
        chips only). It cannot read 28- or 32-pin parallel chips.</td></tr>
<tr><td><b>CH347 adapter</b></td>
    <td>The faster relative of the CH341A, for the same 8-pin chips. Uses the same WCH driver. Its mode
        jumper must select SPI/I2C.</td></tr>
<tr><td><b>FT232H / FT2232H board</b></td>
    <td>Reads SPI chips wired to port A (AD0 clock, AD1 to chip, AD2 from chip, AD3 select). Uses FTDI's
        driver, which Windows normally installs by itself.</td></tr>
<tr><td><b>Raspberry Pi Pico or Arduino</b></td>
    <td>Reads SPI chips if the board runs serprog firmware (pico-serprog, frser-duino). Pick its COM port.</td></tr>
<tr><td><b>USB card reader</b></td>
    <td>Reads a memory card's raw contents. Windows requires administrator rights for this.</td></tr>
<tr><td><b>Other programmers</b><br>GQ-4X4, TOP, RT809H, Willem and similar</td>
    <td>Cannot be driven by this app. Dumps saved from their own software open with Open files and decode
        the same way.</td></tr>
</table>
<p><b>Pin 1</b> is marked with a dot or a notch on the chip; a clip's red wire goes to pin 1.
<b>All FF or all 00</b> means the chip is blank, backwards or not making contact.
<b>Reads that differ</b> mean a poor contact: reseat the chip or clip, or take the chip off its board.</p>
<h4>Separate sounds</h4>
<p>Voice chips usually hold many short clips. The <b>Sounds</b> tab lists them for the selected track or
chip: double-click one to play it. Chips with their own index (NEC uPD7759, OKI MSM6295, LPC speech) are
split exactly; other chips are split at silent gaps. Export can write each sound as its own file.</p>
<h4>Export</h4>
<p>WAV and FLAC hold exactly the decoded samples; MP3 is smaller but lossy. The clean-up options (offset,
hum, clicks, level) are off unless you tick them, and anything applied is recorded in Report.txt.</p>
<h4>If something is wrong</h4>
<table cellpadding="5">
<tr><td><b>Sounds like static or harsh noise</b></td>
    <td>The encoding is wrong. Pick the next entry in the Encoding list and play again. If every entry is
        noise, the chip probably holds a compressed format this version does not know, or it is not audio.</td></tr>
<tr><td><b>Too fast and high, or too slow and low</b></td>
    <td>The speed is wrong. Press <i>Set speed by ear</i>, press Play, and drag the slider until it sounds
        natural. The sound keeps playing while you change it. Click a standard speed to jump to it, use
        -1% / +1% for fine steps, or type the exact speed if you know it.</td></tr>
<tr><td><b>Right pitch, but stutters or repeats every moment</b></td>
    <td>The data is being read as two channels or as 16-bit when it is not, or the reverse. Try the
        matching entry in the Encoding list.</td></tr>
<tr><td><b>A track jumps or changes subject mid-way</b></td>
    <td>The chips are in the wrong order. Select a chip and use Move up or Move down.</td></tr>
<tr><td><b>Long silence at the end</b></td>
    <td>That is padding. It is shaded on the waveform and removed on export by default.</td></tr>
<tr><td><b>"Blank" next to a dump</b></td>
    <td>Every byte is the same. Read the chip again: check its direction and the contact.</td></tr>
</table>
<h4>Words used in the app</h4>
<table cellpadding="5">
<tr><td><b>Dump</b></td><td>A file holding an exact copy of a chip's contents.</td></tr>
<tr><td><b>Encoding</b></td><td>The rule that turns each stored byte into a point on the sound wave.</td></tr>
<tr><td><b>Mu-law, A-law</b></td><td>Encodings that squeeze a wide loudness range into one byte by giving
    quiet sounds finer steps than loud ones. Used in telephones and many sound boards.</td></tr>
<tr><td><b>ADPCM</b></td><td>An encoding that stores only the change from the previous sample, in four bits.
    OKI, IMA, Yamaha and NEC chips each use their own step tables.</td></tr>
<tr><td><b>CVSD</b></td><td>One bit per sample: each bit only says "up" or "down". Used for speech on 1980s
    pinball and arcade boards.</td></tr>
<tr><td><b>LPC speech</b></td><td>Not a recording at all: the chip stores pitch, loudness and filter
    settings and a speech chip (TMS5220 and relatives) rebuilds the voice from them.</td></tr>
<tr><td><b>Speed (sample rate)</b></td><td>How many samples play each second, in Hz. Twice the speed plays
    twice as fast and an octave higher.</td></tr>
<tr><td><b>Padding</b></td><td>The same byte repeated to fill the rest of a chip after the sound ends.</td></tr>
<tr><td><b>Energy at the top of the band</b></td><td>The measure used to pick the encoding. Recordings were
    filtered before storage, so a correct decode has almost nothing in the highest frequencies. A wrong
    decode distorts the wave and fills them.</td></tr>
<tr><td><b>Confidence</b></td><td>High: one reading is clearly cleaner than every other. Medium: ahead, but
    worth comparing with the runner-up. Low: several readings are close, so judge by ear.</td></tr>
<tr><td><b>Device profile</b></td><td>A saved encoding and speed for one kind of equipment, so its other
    chips decode in one step.</td></tr>
<tr><td><b>SHA-256</b></td><td>A fingerprint of a file. Two files with the same fingerprint are identical.</td></tr>
</table>
"""


class TextDialog(QDialog):
    def __init__(self, title: str, html: str, parent=None, size=(680, 620)):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(*size)
        lay = QVBoxLayout(self)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setHtml(html)
        lay.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)


__all__ = ["ListeningCheck", "ExportDialog", "TextDialog", "HELP_HTML"]
