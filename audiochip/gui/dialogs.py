"""Dialogs: listening check, export, reading a chip, help."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout)

from ..cleanup import Cleanup
from ..export import FORMATS

from .player import Player, why_no_sound

CLIP_SECONDS = 6
LADDER = [4000, 5512, 8000, 11025, 16000, 22050, 32000, 44100, 48000]


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


class ListeningCheck(QDialog):
    """Play the same clip at three speeds and let the user pick the natural one."""

    def __init__(self, audio: np.ndarray, rates: list[int], current: int, player: Player, parent=None,
                 reasons: dict[int, str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Check the speed by ear")
        self.setMinimumWidth(640)
        self._reasons = reasons or {}
        self._turn = -1
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._next_turn)
        self.player = player
        self.chosen: int | None = None
        self._rates = sorted(rates)
        self._audio = audio
        self._current = current

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        intro = QLabel("The playback speed is not stored on a chip, so it has to be confirmed by listening. "
                       "The same few seconds play at three speeds. Pick the one where voices and "
                       "instruments sound natural.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        top = QHBoxLayout()
        self.all_btn = QPushButton("Play all three in turn")
        self.all_btn.setProperty("primary", True)
        self.all_btn.setToolTip("Plays a short clip at each speed, one after the other, and shows which one "
                                "is playing. Keys 1, 2 and 3 play a single speed.")
        self.all_btn.clicked.connect(self._play_all)
        self.now = QLabel("Keys: 1, 2, 3 play a speed. Space plays all three.")
        self.now.setProperty("muted", True)
        top.addWidget(self.all_btn)
        top.addWidget(self.now, 1)
        lay.addLayout(top)
        for key, k in (("1", 0), ("2", 1), ("3", 2)):
            QShortcut(QKeySequence(key), self, activated=lambda k=k: self._play(k))
        QShortcut(QKeySequence("Space"), self, activated=self._play_all)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(8)
        lay.addLayout(self.grid)
        self._rows: list[tuple[QLabel, QPushButton, QPushButton]] = []
        for i in range(3):
            label = QLabel()
            label.setMinimumWidth(190)
            play = QPushButton("Play")
            pick = QPushButton("This one sounds natural")
            pick.setProperty("primary", True)
            play.clicked.connect(lambda _=False, k=i: self._play(k))
            pick.clicked.connect(lambda _=False, k=i: self._pick(k))
            self.grid.addWidget(label, i, 0)
            self.grid.addWidget(play, i, 1)
            self.grid.addWidget(pick, i, 2)
            self._rows.append((label, play, pick))

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        lay.addWidget(line)
        lay.addWidget(QLabel("<b>None of them right?</b>"))
        for text, button, direction in (("All three sound too fast or high-pitched", "Try slower speeds", -1),
                                        ("All three sound too slow or low-pitched", "Try faster speeds", +1)):
            row = QHBoxLayout()
            b = QPushButton(button)
            b.setMinimumWidth(160)
            b.clicked.connect(lambda _=False, d=direction: self._shift(d))
            row.addWidget(QLabel(text), 1)
            row.addWidget(b)
            lay.addLayout(row)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setProperty("muted", True)
        lay.addWidget(self.note)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        stop = buttons.addButton("Stop sound", QDialogButtonBox.ActionRole)
        stop.clicked.connect(self.player.stop)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._fill()
        if not self.player.available:
            self.note.setText(why_no_sound())

    def _fill(self) -> None:
        for i, ((label, _, _), rate) in enumerate(zip(self._rows, self._rates)):
            mark = "  (current)" if rate == self._current else ""
            why = self._reasons.get(rate, "")
            label.setText(f"<b>{i + 1}.&nbsp; {rate:,} Hz</b>{mark}"
                          + (f"<br><span style='color:#93A0B4'>{why}</span>" if why else ""))
        mid = self._rates[1]
        clip = loudest_clip(self._audio, CLIP_SECONDS * mid)
        self._clip = clip
        self.note.setText(f"Clip: {len(clip):,} samples from the busiest part of the selection. It lasts "
                          + ", ".join(f"{len(clip) / r:.1f} s at {r:,} Hz" for r in self._rates) + ".")

    def _play(self, k: int) -> None:
        self._timer.stop()
        self._turn = -1
        self._mark(k)
        if not self.player.play(self._clip, self._rates[k]):
            self.note.setText(why_no_sound())

    def _mark(self, k: int) -> None:
        for i, (label, play, _) in enumerate(self._rows):
            play.setText("Playing" if i == k else "Play")
        self.now.setText(f"Playing {self._rates[k]:,} Hz" if k >= 0 else
                         "Keys: 1, 2, 3 play a speed. Space plays all three.")

    def _play_all(self) -> None:
        """Half the clip at each speed, back to back, so the three can be compared in a few seconds."""
        self.player.stop()
        self._turn = -1
        self._next_turn()

    def _next_turn(self) -> None:
        self._turn += 1
        if self._turn >= len(self._rates):
            self._turn = -1
            self._mark(-1)
            return
        rate = self._rates[self._turn]
        clip = self._clip[: len(self._clip) // 2]
        self._mark(self._turn)
        if not self.player.play(clip, rate):
            self.note.setText(why_no_sound())
            self._turn = -1
            self._mark(-1)
            return
        self._timer.start(int(1000 * len(clip) / rate) + 450)

    def _pick(self, k: int) -> None:
        self._timer.stop()
        self.player.stop()
        self.chosen = self._rates[k]
        self.accept()

    def _shift(self, direction: int) -> None:
        """Slide the three options down or up a ladder of well-separated speeds."""
        self._timer.stop()
        self.player.stop()
        self._mark(-1)
        ladder = LADDER
        if direction < 0:
            i = min(range(len(ladder)), key=lambda k: abs(ladder[k] - self._rates[0]))
            if i == 0:
                self.note.setText("These are already the slowest standard speeds. You can type any speed "
                                  "into the Speed box in the main window.")
                return
            self._rates = ladder[max(0, i - 2): max(0, i - 2) + 3]
        else:
            j = min(range(len(ladder)), key=lambda k: abs(ladder[k] - self._rates[-1]))
            if j == len(ladder) - 1:
                self.note.setText("These are already the fastest standard speeds. You can type any speed "
                                  "into the Speed box in the main window.")
                return
            j = min(j, len(ladder) - 3)
            self._rates = ladder[j: j + 3]
        self._fill()

    def done(self, result):
        self._timer.stop()
        self.player.stop()
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
    <td>The speed is wrong. Press <i>Check speed by ear</i>; <i>Play all three in turn</i> compares the likely
        speeds in a few seconds.</td></tr>
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
