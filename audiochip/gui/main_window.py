"""Main window: collection on the left, analysis in the middle, details on the right."""
from __future__ import annotations

import datetime
import html
import json
import os
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QEventLoop, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QScrollArea, QButtonGroup, QCheckBox, QComboBox, QDockWidget, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
                               QProgressDialog,
                               QPushButton, QRadioButton, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QTabWidget,
                               QTextBrowser, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, profiles, update
from .. import codecs as codec_state
from ..codecs import (CODECS, LAYOUTS, Cancelled, Hypothesis, decode, decode_seconds, frames_for_bytes, is_cached,
                      is_structured)
from ..collection import Chip as ChipDump
from ..collection import (Collection, Track, build, chip_audio, decode_cost, measure_rates, move_chip, track_audio,
                          track_sounds, warm)
from ..export import ExportOptions, describe_reading, export
from ..facts import selection_facts
from ..rate import STANDARD_RATES
from .dialogs import HELP_HTML, ExportDialog, ListeningCheck, TextDialog
from .player import Player, why_no_sound
from .read_dialog import ReadDialog
from .widgets import Chip, ElideLabel, SpectrogramView, WaveformView, hex_page
from .worker import Worker

ROLE_TEXT = {"chip": "in track", "duplicate": "duplicate", "prejoined": "joined copy", "blank": "blank"}
ROLE_LONG = {"chip": "part of the track", "duplicate": "duplicate, left out of the track",
             "prejoined": "pre-joined copy, left out of the track", "blank": "blank, left out of the track"}


class StartCard(QFrame):
    """A large clickable card on the start page."""

    def __init__(self, head: str, text: str, slot, parent=None):
        super().__init__(parent)
        self.setObjectName("startCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(132)
        self._slot = slot
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        h = QLabel(head)
        h.setObjectName("cardHead")
        t = QLabel(text)
        t.setWordWrap(True)
        t.setProperty("muted", True)
        lay.addWidget(h)
        lay.addWidget(t)
        lay.addStretch(1)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self._slot()


def _mono_font() -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    f.setPointSizeF(max(f.pointSizeF(), 9))
    return f


HEAVY_SECONDS = 0.7       # decodes expected to take longer than this run in the background


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.setAcceptDrops(True)
        self.coll: Collection | None = None
        self.player = Player()
        self.settings = QSettings("AudioChipDecoder", "AudioChipDecoder")
        self._audio: np.ndarray | None = None
        self._audio_rate = 0
        self._worker: Worker | None = None
        self._updating = False
        self._log: list[str] = []

        self._build_toolbar()
        self._build_central()
        self._build_details()
        self._build_status()

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.log(f"{APP_NAME} {__version__} started.")
        self._set_loaded(False)
        self._fit_to_screen()

    # ------------------------------------------------------------- window size
    def _screen_room(self):
        screen = self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else None

    def _fit_to_screen(self) -> None:
        """Set the size the window has when it is not maximised: always within the screen, and centred."""
        room = self._screen_room()
        if room is None:
            self.resize(1400, 860)
            return
        frame = 40                                           # title bar and borders
        self.resize(min(1400, room.width() - 16), min(860, room.height() - frame))
        self.move(room.x() + max((room.width() - self.width()) // 2, 0),
                  room.y() + max((room.height() - self.height() - frame) // 2, 0))

    def show_fitted(self) -> None:
        """Start filling the screen. Restoring the window gives the fitted size set above."""
        self.showMaximized()

    # ------------------------------------------------------------------ build UI
    def _build_toolbar(self):
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)

        def act(text, tip, slot):
            a = QAction(text, self)
            a.setToolTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        act("Read a chip", "Read a chip or memory card with your reader, then decode it.", self.read_chip)
        act("Open files", "Open one or more dump files (BIN, Intel HEX, S-record).", self.open_files)
        act("Open folder", "Open a folder of dumps. Each sub-folder becomes one track.", self.open_folder)
        self.recent_btn = QToolButton()
        self.recent_btn.setText("Recent")
        self.recent_btn.setToolTip("Reopen files or folders you opened before.")
        self.recent_btn.setPopupMode(QToolButton.InstantPopup)
        self.recent_menu = QMenu(self)
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        self.recent_btn.setMenu(self.recent_menu)
        tb.addWidget(self.recent_btn)
        tb.addSeparator()
        self.profile_btn = QToolButton()
        self.profile_btn.setText("Device profiles")
        self.profile_btn.setToolTip("Apply a saved encoding and speed, or save the current ones.")
        self.profile_btn.setPopupMode(QToolButton.InstantPopup)
        self.profile_menu = QMenu(self)
        self.profile_menu.aboutToShow.connect(self._fill_profile_menu)
        self.profile_btn.setMenu(self.profile_menu)
        tb.addWidget(self.profile_btn)
        self.export_act = act("Export audio", "Write WAV, FLAC or MP3 files and a report for the whole collection.",
                              self.do_export)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.details_act = act("Details", "Show or hide the technical details.", self._toggle_details)
        self.details_act.setCheckable(True)
        act("Help", "How it works, troubleshooting and a glossary.", self.show_help)

    def _build_central(self):
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # ---- start page
        start = QWidget()
        lay = QVBoxLayout(start)
        lay.setContentsMargins(60, 40, 60, 40)
        lay.addStretch(1)
        title = QLabel(APP_NAME)
        title.setObjectName("startTitle")
        sub = QLabel("Read a memory chip, find the audio stored on it, and export WAV files. The app reads the "
                     "chip, works out the encoding, and shows its best reading already applied.")
        sub.setWordWrap(True)
        sub.setObjectName("startSub")
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(18)
        row = QHBoxLayout()
        row.setSpacing(16)
        for head, text, slot in (
                ("Read a chip", "Read a chip in your programmer, or a memory card, straight into the app "
                                "and decode it.", self.read_chip),
                ("Open dump files", "BIN, Intel HEX or Motorola S-record files already read from a chip.",
                 self.open_files),
                ("Open a folder", "A whole collection. Each sub-folder becomes one track, with its chips "
                                  "joined in order.", self.open_folder)):
            row.addWidget(StartCard(head, text, slot), 1)
        lay.addLayout(row)
        hint = QLabel("You can also drag files or folders onto this window.")
        hint.setProperty("muted", True)
        lay.addSpacing(10)
        lay.addWidget(hint)
        lay.addStretch(2)
        self.stack.addWidget(start)

        # ---- work page
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(10, 10, 4, 10)
        cap = QLabel("COLLECTION")
        cap.setProperty("caption", True)
        ll.addWidget(cap)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Track / chip", "Length", "Status"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setColumnWidth(0, 196)
        self.tree.setColumnWidth(1, 62)
        self.tree.currentItemChanged.connect(lambda *_: self.refresh())
        ll.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.up_btn = QPushButton("Move up")
        self.down_btn = QPushButton("Move down")
        self.up_btn.setToolTip("Move the selected chip earlier in its track.")
        self.down_btn.setToolTip("Move the selected chip later in its track.")
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(+1))
        row.addWidget(self.up_btn)
        row.addWidget(self.down_btn)
        row.addStretch(1)
        ll.addLayout(row)
        self.sounds_cap = QLabel("SOUNDS IN THE SELECTION")
        self.sounds_cap.setProperty("caption", True)
        ll.addSpacing(6)
        ll.addWidget(self.sounds_cap)
        self.sounds = QTreeWidget()
        self.sounds.setHeaderLabels(["Sound", "Starts", "Length"])
        self.sounds.setRootIsDecorated(False)
        self.sounds.setUniformRowHeights(True)
        self.sounds.setColumnWidth(0, 196)
        self.sounds.setColumnWidth(1, 70)
        self.sounds.setToolTip("The separate sounds found in the selected track or chip. Click one to mark it "
                               "on the waveform; double-click to play just that sound.")
        self.sounds.currentItemChanged.connect(lambda *_: self._sound_selected())
        self.sounds.itemDoubleClicked.connect(lambda *_: self._play_sound())
        ll.addWidget(self.sounds, 1)
        row = QHBoxLayout()
        self.sound_btn = QPushButton("Play sound")
        self.sound_btn.setToolTip("Play only the sound selected in the list.")
        self.sound_btn.clicked.connect(self._play_sound)
        self.sounds_note = QLabel()
        self.sounds_note.setProperty("muted", True)
        self.sounds_note.setWordWrap(True)
        row.addWidget(self.sound_btn)
        row.addWidget(self.sounds_note, 1)
        ll.addLayout(row)
        split.addWidget(left)

        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(8, 10, 10, 10)
        ml.setSpacing(8)

        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)
        self.sel_label = QLabel()
        self.sel_label.setProperty("caption", True)
        self.headline = QLabel()
        self.headline.setObjectName("headline")
        self.headline.setWordWrap(True)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.enc_chip = Chip()
        self.speed_chip = Chip()
        self.why_btn = QPushButton("Why this reading?")
        self.why_btn.setFlat(True)
        self.why_btn.setProperty("link", True)
        self.why_btn.clicked.connect(lambda: self._show_details(0))
        chips.addWidget(self.enc_chip)
        chips.addWidget(self.speed_chip)
        chips.addStretch(1)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setProperty("warning", True)
        caption = QHBoxLayout()                 # the link sits up here so the row of badges stays narrow
        caption.addWidget(self.sel_label)
        caption.addStretch(1)
        caption.addWidget(self.why_btn)
        cl.addLayout(caption)
        cl.addWidget(self.headline)
        cl.addLayout(chips)
        cl.addWidget(self.notice)
        ml.addWidget(card)

        about = self.about = QFrame()
        about.setObjectName("card")
        al = QVBoxLayout(about)
        al.setContentsMargins(14, 10, 14, 10)
        al.setSpacing(4)
        head = QHBoxLayout()
        self.about_cap = QLabel("ABOUT THIS FILE")
        self.about_cap.setProperty("caption", True)
        self.about_copy = QPushButton("Copy")
        self.about_copy.setFlat(True)
        self.about_copy.setProperty("link", True)
        self.about_copy.setToolTip("Copy these facts as text, for notes or to send to someone.")
        self.about_copy.clicked.connect(self._copy_about)
        self.about_toggle = QPushButton("Hide")
        self.about_toggle.setFlat(True)
        self.about_toggle.setProperty("link", True)
        self.about_toggle.clicked.connect(lambda: self._show_about(not self.about_body.isVisible()))
        head.addWidget(self.about_cap)
        head.addStretch(1)
        head.addWidget(self.about_copy)
        head.addWidget(self.about_toggle)
        al.addLayout(head)
        self.about_body = QWidget()
        self.about_grid = QGridLayout(self.about_body)
        self.about_grid.setContentsMargins(0, 0, 0, 0)
        self.about_grid.setHorizontalSpacing(10)
        self.about_grid.setVerticalSpacing(2)
        for col in (1, 3, 5):
            self.about_grid.setColumnStretch(col, 1)
        al.addWidget(self.about_body)
        self._about_rows: list[tuple[str, str, str]] = []
        self._about_cells: list[tuple[QLabel, QLabel]] = []
        self._about_cols = 0
        about.installEventFilter(self)
        ml.addWidget(about)
        self._show_about(self.settings.value("about_shown", "true") != "false")

        self.wave = WaveformView()
        self.wave.seek.connect(self._seek)
        self.spec = SpectrogramView()
        ml.addWidget(self.wave, 2)
        ml.addWidget(self.spec, 3)

        transport = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.play_btn.setProperty("primary", True)
        self.play_btn.setMinimumWidth(90)
        self.play_btn.clicked.connect(self.toggle_play)
        self.stop_btn = QPushButton("Back to start")
        self.stop_btn.clicked.connect(self._rewind)
        self.pos_label = QLabel("0:00.0 / 0:00.0")
        self.pos_label.setFont(_mono_font())
        self.check_btn = QPushButton("Set speed by ear")
        self.check_btn.setToolTip("Play the same clip at three speeds and pick the natural one.")
        self.check_btn.clicked.connect(self.listening_check)
        transport.addWidget(self.play_btn)
        transport.addWidget(self.stop_btn)
        transport.addWidget(self.pos_label)
        transport.addStretch(1)
        transport.addWidget(self.check_btn)
        ml.addLayout(transport)

        ctl = QFrame()
        ctl.setObjectName("card")
        g = QGridLayout(ctl)
        g.setContentsMargins(14, 10, 14, 10)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        g.addWidget(QLabel("Encoding"), 0, 0)
        self.enc_combo = QComboBox()
        self.enc_combo.setMinimumWidth(260)
        self.enc_combo.setToolTip("Readings ranked by the automatic test, best first, with the share of energy "
                                  "each leaves at the top of the band (lower is better). Below the line: every "
                                  "other encoding the app knows. Pick one to hear it.")
        self.enc_combo.activated.connect(self._encoding_chosen)
        g.addWidget(self.enc_combo, 0, 1, 1, 3)
        g.addWidget(QLabel("Speed"), 1, 0)
        self.rate_combo = QComboBox()
        self.rate_combo.setEditable(True)
        self.rate_combo.setMinimumWidth(250)
        self.rate_combo.setToolTip("Playback speed in samples per second (Hz). Pick a standard speed or type one.")
        self.rate_combo.activated.connect(lambda *_: self._rate_chosen())
        self.rate_combo.lineEdit().editingFinished.connect(self._rate_chosen)
        g.addWidget(self.rate_combo, 1, 1)
        g.addWidget(QLabel("Hz"), 1, 2)
        self.confirm_box = QCheckBox("Speed confirmed by ear")
        self.confirm_box.setToolTip("Tick once you are satisfied the speed is right. The report records it.")
        self.confirm_box.toggled.connect(self._confirm_toggled)
        g.addWidget(self.confirm_box, 1, 3)
        g.setColumnStretch(3, 1)
        ml.addWidget(ctl)

        scope = QHBoxLayout()
        scope.addWidget(QLabel("Changes apply to"))
        self.scope_all = QRadioButton("the whole collection")
        self.scope_track = QRadioButton("this track only")
        self.scope_all.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.scope_all)
        grp.addButton(self.scope_track)
        grp.buttonClicked.connect(lambda *_: setattr(self, "_scope_auto", False))
        scope.addWidget(self.scope_all)
        scope.addWidget(self.scope_track)
        self.reset_btn = QPushButton("Back to automatic")
        self.reset_btn.setToolTip("Drop manual choices and use the detected encoding and estimated speed again.")
        self.reset_btn.clicked.connect(self._reset_auto)
        scope.addStretch(1)
        scope.addWidget(self.reset_btn)
        ml.addLayout(scope)
        # the main view scrolls if the window is made too small to show all of it, so nothing is cut off
        mid_scroll = QScrollArea()
        mid_scroll.setWidgetResizable(True)
        mid_scroll.setFrameShape(QFrame.NoFrame)
        mid_scroll.setWidget(mid)
        split.addWidget(mid_scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([420, 700])
        left.setMinimumWidth(380)
        self.stack.addWidget(split)

    def _build_details(self):
        self.dock = QDockWidget("Details", self)
        self.dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable
                              | QDockWidget.DockWidgetFloatable)
        self.dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.BottomDockWidgetArea)
        self.tabs = QTabWidget()
        self.evidence = QTextBrowser()
        self.fileinfo = QTextBrowser()
        self.formula = QTextBrowser()
        hexw = QWidget()
        hl = QVBoxLayout(hexw)
        hl.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        row.addWidget(QLabel("Offset"))
        self.hex_off = QSpinBox()
        self.hex_off.setRange(0, 0)
        self.hex_off.setSingleStep(0x200)
        self.hex_off.setDisplayIntegerBase(16)
        self.hex_off.setPrefix("0x")
        self.hex_off.valueChanged.connect(lambda *_: self._refresh_hex())
        row.addWidget(self.hex_off)
        self._hex_buttons = {}
        for text, where in (("Start", "start"), ("Padding", "pad"), ("End", "end")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, w=where: self._hex_jump(w))
            row.addWidget(b)
            self._hex_buttons[where] = b
        self._hex_buttons["pad"].setToolTip("Jump to where the padding at the end of this dump begins.")
        row.addStretch(1)
        hl.addLayout(row)
        self.hex_note = QLabel()
        self.hex_note.setProperty("muted", True)
        hl.addWidget(self.hex_note)
        self.hex = QPlainTextEdit()
        self.hex.setReadOnly(True)
        self.hex.setFont(_mono_font())
        self.hex.setLineWrapMode(QPlainTextEdit.NoWrap)
        hl.addWidget(self.hex, 1)
        self.logview = QPlainTextEdit()
        self.logview.setReadOnly(True)
        self.logview.setFont(_mono_font())
        self.tabs.addTab(self.evidence, "Why")
        self.tabs.addTab(self.fileinfo, "Files")
        self.tabs.addTab(self.formula, "Formula")
        self.tabs.addTab(hexw, "Hex")
        self.tabs.addTab(self.logview, "Log")
        self.dock.setWidget(self.tabs)
        self.dock.setMinimumWidth(440)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.visibilityChanged.connect(self.details_act.setChecked)
        self.dock.hide()                                     # always starts hidden; the Details button opens it

    def _build_status(self):
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.update_btn = QPushButton()
        self.update_btn.setProperty("primary", True)
        self.update_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(getattr(self, "_update_url", update.PAGE))))
        self.update_btn.hide()
        self.statusBar().addPermanentWidget(self.update_btn)
        self.statusBar().showMessage("Ready")

    # --------------------------------------------------------------------- utils
    def log(self, text: str) -> None:
        line = f"{datetime.datetime.now():%H:%M:%S}  {text}"
        self._log.append(line)
        if hasattr(self, "logview"):
            self.logview.appendPlainText(line)

    def _set_loaded(self, loaded: bool) -> None:
        self.stack.setCurrentIndex(1 if loaded else 0)
        self.export_act.setEnabled(loaded)
        self.profile_btn.setEnabled(loaded)

    def _toggle_details(self):
        if self.dock.isVisible():
            self.dock.hide()
        else:
            self._open_details()

    def _show_details(self, tab: int):
        self.tabs.setCurrentIndex(tab)
        self._open_details()

    def _open_details(self) -> None:
        """Show the Details panel without pushing anything off the screen.

        Beside the main view when there is room for both; otherwise as its own window on top,
        which can be moved, resized and closed.
        """
        room = self._screen_room()
        beside = 400 + 620 + 600                             # list, main view, details
        if self.isMaximized() or self.isFullScreen():
            docked = self.width() >= beside
        else:
            docked = room is None or room.width() - 16 >= beside
            if docked and self.width() < beside:             # widen the window, keeping it on the screen
                self.resize(beside, self.height())
                if room is not None and self.frameGeometry().right() > room.right():
                    self.move(max(room.right() - self.frameGeometry().width(), room.x()), self.y())
        self.dock.setFloating(not docked)
        self.dock.show()
        if not docked:
            w = min(760, (room.width() - 40) if room else 760)
            h = min(640, (room.height() - 80) if room else 640)
            self.dock.resize(w, h)
            if room is not None:
                self.dock.move(room.right() - w - 20, room.y() + 60)
            self.dock.raise_()

    def current(self) -> tuple[Track | None, ChipDump | None]:
        item = self.tree.currentItem()
        if item is None or self.coll is None:
            return None, None
        data = item.data(0, Qt.UserRole)
        if isinstance(data, Track):
            return data, None
        if isinstance(data, tuple):
            return data
        return None, None

    # ------------------------------------------------------------------- opening
    def open_files(self):
        start = self.settings.value("last_dir", str(Path.home()))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Open dump files", start,
            "Chip dumps (*.bin *.rom *.hex *.ihx *.s19 *.s28 *.s37 *.srec *.mot *.eep *.dat *.dmp *.img);;"
            "Audio files (*.wav *.flac *.ogg *.opus *.mp3 *.aif *.aiff *.au *.caf *.voc *.vag *.vox *.gsm);;"
            "All files (*)")
        if files:
            self.settings.setValue("last_dir", str(Path(files[0]).parent))
            self.load_paths(files)

    def open_folder(self):
        start = self.settings.value("last_dir", str(Path.home()))
        d = QFileDialog.getExistingDirectory(self, "Open a folder of dumps", start)
        if d:
            self.settings.setValue("last_dir", d)
            self.load_paths([d])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.load_paths(paths)

    # -------------------------------------------------------------------- recent
    def _recent(self) -> list[list[str]]:
        try:
            items = json.loads(self.settings.value("recent", "[]") or "[]")
        except (TypeError, ValueError):
            return []
        return [e for e in items if isinstance(e, list) and e and all(isinstance(p, str) for p in e)][:10]

    def _remember(self, paths: list[str]) -> None:
        entry = [str(Path(p)) for p in paths]
        items = [entry] + [e for e in self._recent() if e != entry]
        self.settings.setValue("recent", json.dumps(items[:10]))

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        items = self._recent()
        if not items:
            self.recent_menu.addAction("Nothing opened yet").setEnabled(False)
            return
        for entry in items:
            first = Path(entry[0])
            text = str(first) if len(entry) == 1 else f"{first.name} and {len(entry) - 1} more, in {first.parent}"
            a = self.recent_menu.addAction(text)
            a.setEnabled(all(Path(p).exists() for p in entry))
            if not a.isEnabled():
                a.setText(text + "  (not found)")
            a.triggered.connect(lambda _=False, e=entry: self.load_paths(e))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear this list").triggered.connect(
            lambda: self.settings.setValue("recent", "[]"))

    # ------------------------------------------------------------ version notice
    def check_for_update(self) -> None:
        """Once a day, ask GitHub whether a newer release exists. Silent unless there is one."""
        today = datetime.date.today().isoformat()
        if os.environ.get("AUDIOCHIP_NO_UPDATE_CHECK") or self.settings.value("update_checked", "") == today:
            return
        self.settings.setValue("update_checked", today)
        self._update_worker = Worker(lambda _prog: update.latest_release(), self)
        self._update_worker.finished_ok.connect(self._update_result)
        self._update_worker.start()

    def _update_result(self, found) -> None:
        if not found or not update.is_newer(found[0], __version__):
            return
        version, url = found
        self.log(f"Version {version} is available: {url}")
        self.update_btn.setText(f"Version {version} is available - get it")
        self.update_btn.setToolTip(f"You have {__version__}. Opens the download page:\n{url}")
        self._update_url = url
        self.update_btn.show()

    def load_paths(self, paths: list[str]):
        if self._worker is not None:
            return
        self._stop()
        self._remember(paths)
        self.log("Opening: " + "; ".join(paths))
        def job(prog):
            coll = build(paths, prog)
            try:                                  # decode what is about to be shown while still off the UI thread
                if coll.tracks and decode_cost(coll) > 0:
                    codec_state.cancel.clear()
                    warm(coll, prog, remeasure=False)
            except Exception:
                pass                              # the window reports any problem when it shows the track
            return coll
        self._run(job, self._loaded, "Analysing dumps")

    def _run(self, fn, on_done, label: str):
        self._worker = Worker(fn, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(lambda result: (self._end_work(), on_done(result)))
        self._worker.failed.connect(lambda msg: (self._end_work(), self._error(label, msg)))
        self.progress.setRange(0, 0)
        self.progress.show()
        self.statusBar().showMessage(label + "...")
        self.setCursor(Qt.BusyCursor)
        self._worker.start()

    def _on_progress(self, i: int, n: int, text: str):
        self.progress.setRange(0, max(n, 1))
        self.progress.setValue(i)
        self.statusBar().showMessage(text)

    def _end_work(self):
        self.progress.hide()
        self.unsetCursor()
        if self._worker is not None:
            self._worker.wait(2000)
            self._worker = None
        self._bg: list[Worker] = []           # background decodes still running
        self._decode_problem = ""

    def _error(self, what: str, msg: str):
        self.log(f"{what} failed: {msg}")
        self.statusBar().showMessage(f"{what} failed")
        QMessageBox.warning(self, what, f"{what} did not finish.\n\n{msg}")

    def _loaded(self, coll: Collection):
        for p, why in coll.errors:
            self.log(f"Could not read {p.name}: {why}")
        if not coll.tracks:
            self.statusBar().showMessage("No dump files found")
            detail = "\n".join(f"{p.name}: {why}" for p, why in coll.errors[:8])
            QMessageBox.information(self, "Nothing to open",
                                    "No dump files were found in what you opened. Dumps are usually .bin, "
                                    ".hex or .s19 files." + (f"\n\n{detail}" if detail else ""))
            return
        self.coll = coll
        n_chips = sum(len(t.members) for t in coll.tracks)
        self.log(f"Loaded {len(coll.tracks)} track(s), {n_chips} chip(s) in use, "
                 f"{len(coll.chips) - n_chips} left out.")
        self.log(f"Encoding: {coll.hyp.label() if coll.hyp else 'not recognised'} "
                 f"(confidence {coll.detection.confidence}). {coll.detection.reason}")
        self.log(f"Speed estimate: {coll.rate:,} Hz. {coll.rate_estimate.reason}")
        self._set_loaded(True)
        self._fill_tree()
        self.statusBar().showMessage(f"{len(coll.tracks)} track(s), {n_chips} chip(s)")

    # ---------------------------------------------------------------------- tree
    def _duration(self, nbytes: int, track: Track, chip: ChipDump | None = None) -> str:
        hyp, rate = self.coll.hyp_for(track), self.coll.rate_for(track)
        if not hyp or not rate:
            return ""
        if is_structured(hyp):
            chips = [chip] if chip is not None else track.members
            if not all(is_cached(c.dump.data, hyp) for c in chips):
                return ""                         # not decoded yet; decoding just for this column would stall
            try:
                return _clock(sum(len(decode(c.dump.data, hyp)) for c in chips) / rate)
            except Exception:
                return ""
        return _clock(frames_for_bytes(nbytes, hyp) / rate)

    def _fill_tree(self, keep: tuple | None = None):
        self._updating = True
        self.tree.clear()
        select = None
        for t in self.coll.tracks:
            members = t.members
            status = f"{len(members)} chip{'s' if len(members) != 1 else ''}"
            if t.warnings:
                status += ", check"
            ti = QTreeWidgetItem([t.name, self._duration(sum(c.dump.size for c in members), t), status])
            ti.setData(0, Qt.UserRole, t)
            f = ti.font(0)
            f.setBold(True)
            ti.setFont(0, f)
            if t.warnings:
                ti.setToolTip(2, "\n".join(t.warnings))
                ti.setForeground(2, QColor("#F2BE63"))
            self.tree.addTopLevelItem(ti)
            if keep and keep[0] is t and keep[1] is None:
                select = ti
            for c in t.chips:
                ci = QTreeWidgetItem([c.name, self._duration(c.dump.size, t, c) if c.role != "blank" else "",
                                      ROLE_TEXT.get(c.role, c.role)])
                ci.setData(0, Qt.UserRole, (t, c))
                if not c.in_track:
                    for col in range(3):
                        ci.setForeground(col, QColor("#6F7A8A"))
                    ci.setToolTip(2, c.role_note)
                ti.addChild(ci)
                if keep and keep[1] is c:
                    select = ci
            ti.setExpanded(len(self.coll.tracks) <= 3 or (keep is not None and keep[0] is t))
        self._updating = False
        self.tree.setCurrentItem(select or self.tree.topLevelItem(0))

    def _move(self, delta: int):
        track, chip = self.current()
        if track is None or chip is None or not chip.in_track:
            return
        if move_chip(track, chip, delta):
            self.log(f"Moved {chip.name} {'earlier' if delta < 0 else 'later'} in {track.name}.")
            self._stop()
            self._fill_tree(keep=(track, chip))

    # ------------------------------------------------------------------- refresh
    # ------------------------------------------------------- slow work, off the UI thread
    def _background(self, job, text: str):
        """Run job(progress) in a thread behind a progress window, keeping the app responsive.

        Returns ("ok", result), ("cancelled", None) or ("failed", exception). Cancel returns at
        once; a decoder that cannot stop mid-way finishes quietly in the background.
        """
        codec_state.cancel.clear()
        dlg = QProgressDialog(text, "Cancel", 0, 0, self)
        dlg.setWindowTitle(APP_NAME)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setMinimumWidth(480)
        loop = QEventLoop(self)
        out = {"state": None, "value": None}

        def finish(state, value=None):
            if out["state"] is None:
                out.update(state=state, value=value)
            loop.quit()

        def cancelled():
            codec_state.cancel.set()
            finish("cancelled")

        def progress(i, n, t):
            if out["state"] is None:
                dlg.setMaximum(max(n, 1))
                dlg.setValue(min(i, max(n, 1)))
                dlg.setLabelText(text + ("\n\n" + t if t else ""))

        worker = Worker(job, self)
        worker.finished_ok.connect(lambda v: finish("ok", v))
        worker.failed_exc.connect(lambda e: finish("cancelled" if isinstance(e, Cancelled) else "failed", e))
        worker.progress.connect(progress)
        dlg.canceled.connect(cancelled)
        self._bg.append(worker)
        worker.finished.connect(lambda w=worker: self._bg.remove(w) if w in self._bg else None)
        worker.start()
        dlg.show()
        if out["state"] is None:
            loop.exec()
        dlg.canceled.disconnect(cancelled)          # closing the window must not count as Cancel
        dlg.reset()
        dlg.hide()
        dlg.deleteLater()
        return out["state"], out["value"]

    def _selection_cost(self, track: Track, chip: ChipDump | None) -> float:
        hyp = self.coll.hyp_for(track)
        if hyp is None:
            return 0.0
        if chip is not None:
            return 0.0 if is_cached(chip.dump.data, hyp) else decode_seconds(hyp, chip.dump.size)
        return decode_cost(self.coll, [track])

    def _selection_audio(self, track: Track, chip: ChipDump | None):
        """Decode the selection without ever stalling or bringing down the window.

        A decode expected to take a while runs in the background behind a progress window; any
        error becomes a line under the headline instead of an unhandled exception.
        """
        self._decode_problem = ""
        hyp, rate = self.coll.hyp_for(track), self.coll.rate_for(track)
        try:
            if self._selection_cost(track, chip) > HEAVY_SECONDS:
                name = CODECS[hyp.codec].name
                state, value = self._background(
                    lambda _prog: self._selection_audio_now(track, chip),
                    f"Decoding as {name}.\nThis decoder works one sample at a time, so larger files take a while.")
                if state == "ok":
                    return value
                self._decode_problem = ("Decoding was cancelled, so there is nothing to show for this encoding. "
                                        "Pick another encoding, or select the item again to retry."
                                        if state == "cancelled" else self._explain_decode_error(hyp, value))
                return None, rate, [], None
            return self._selection_audio_now(track, chip)
        except Exception as e:
            self._decode_problem = self._explain_decode_error(hyp, e)
            return None, rate, [], None

    def _explain_decode_error(self, hyp, error) -> str:
        name = CODECS[hyp.codec].name if hyp else "this encoding"
        self.log(f"Decoding as {name} failed: {type(error).__name__}: {error}")
        return (f"This file could not be decoded as {name}: {error} Nothing was changed in the file. "
                "Pick another encoding from the list.")

    def _selection_audio_now(self, track: Track, chip: ChipDump | None):
        """Decode the selection. Returns (audio, rate, chip-boundary marks, frame where padding starts)."""
        hyp, rate = self.coll.hyp_for(track), self.coll.rate_for(track)
        if hyp is None:
            return None, rate, [], None
        marks, pad_from = [], None
        if is_structured(hyp):                 # the format has its own layout: no byte-for-byte positions
            x = decode(chip.dump.data, hyp) if chip is not None else track_audio(self.coll, track)
            return x, rate, marks, pad_from
        if chip is not None:
            x = decode(chip.dump.data, hyp) if not chip.in_track else chip_audio(self.coll, track, chip)
            if chip.structure.has_end_padding:
                pad_from = frames_for_bytes(chip.structure.content_end, hyp)
        else:
            x = track_audio(self.coll, track)
            pos = 0
            members = track.members
            for c in members[:-1]:
                pos += c.dump.size
                marks.append((frames_for_bytes(pos, hyp), ""))
            if len(members) > 1:
                starts = [0] + [m[0] for m in marks]
                marks = [(s, Path(c.name).stem) for s, c in zip(starts, members)]
            if members and members[-1].structure.has_end_padding:
                total = sum(c.dump.size for c in members)
                pad_from = frames_for_bytes(total - members[-1].structure.trail_len, hyp)
        return x, rate, marks, pad_from

    def refresh(self):
        if self._updating or self.coll is None:
            return
        track, chip = self.current()
        if track is None:
            return
        self._stop()
        self.player.rewind()
        coll = self.coll
        hyp, rate = coll.hyp_for(track), coll.rate_for(track)
        x, rate, marks, pad_from = self._selection_audio(track, chip)
        self._audio, self._audio_rate = x, rate
        mono = to_mono(x) if x is not None and len(x) else None
        self.wave.set_audio(mono, marks, pad_from)
        self.spec.set_audio(mono, rate)

        what = f"TRACK: {track.name}" if chip is None else f"CHIP: {track.name} / {chip.name}"
        self.sel_label.setText(what)
        self.headline.setText(describe_reading(coll, track) if hyp else "No audio encoding recognised")
        det = coll.detection if not track.hyp_override else track.detection
        if track.hyp_override and track.override_auto:
            self.enc_chip.set(f"Encoding: {track.detection.confidence} confidence", track.detection.confidence)
        elif coll.hyp_source != "auto" or track.hyp_override:
            src = "set by hand" if coll.hyp_source == "user" or track.hyp_override else coll.hyp_source
            self.enc_chip.set(f"Encoding: {src}", "warn" if src == "set by hand" else "ok")
        else:
            self.enc_chip.set(f"Encoding: {det.confidence} confidence", det.confidence)
        if coll.rate_confirmed or track.rate_confirmed:
            self.speed_chip.set("Speed: confirmed", "ok")
        elif track.rate_stated and rate == track.rate_stated:
            self.speed_chip.set("Speed: stated by the file" if hyp and hyp.codec == "audiofile"
                                else "Speed: set by the chip format", "ok")
        else:
            self.speed_chip.set("Speed: estimated, not confirmed by ear", "warn")
        notes = list(track.warnings)
        if chip is not None:
            if not chip.in_track:
                notes.insert(0, f"{chip.name} is not part of the track: {chip.role_note}")
            own = chip.detection
            if (hyp and own.best and own.confidence == "high" and own.best.hyp != hyp
                    and not own.best.hyp.is_twin_of(hyp) and chip.in_track):
                notes.append(f"On its own this chip decodes best as {own.best.hyp.label()}. "
                             "It follows the collection setting; compare by ear if it sounds wrong.")
        if hyp is None:
            notes.insert(0, coll.detection.reason + " You can still pick an encoding from the list to hear it.")
        if getattr(self, "_decode_problem", ""):
            notes.insert(0, self._decode_problem)
        self.notice.setText("\n".join(notes))
        self.notice.setVisible(bool(notes))

        self._fill_controls(track)
        self.up_btn.setEnabled(chip is not None and chip.in_track)
        self.down_btn.setEnabled(chip is not None and chip.in_track)
        self.play_btn.setEnabled(x is not None and len(x) > 0)
        self.check_btn.setEnabled(x is not None and len(x) > 0)
        self.check_btn.setProperty("primary", not (coll.rate_confirmed or track.rate_confirmed or track.rate_stated))
        self.check_btn.style().unpolish(self.check_btn)
        self.check_btn.style().polish(self.check_btn)
        self._update_pos(0)
        self._fill_sounds(track, chip, x)
        self._fill_about(track, chip, x, rate, pad_from)
        self._refresh_details(track, chip)

    # --------------------------------------------------------------------- about
    def _fill_about(self, track: Track, chip: ChipDump | None, x, rate: int, pad_from) -> None:
        try:
            rows = selection_facts(self.coll, track, chip, x, rate, len(getattr(self, "_sounds", [])), pad_from)
        except Exception as e:                          # facts are a convenience; never block the decode
            self.log(f"Could not gather the facts for this selection: {e}")
            rows = []
        self._about_rows = rows
        self.about_cap.setText("ABOUT THIS FILE" if chip is not None else "ABOUT THIS TRACK")
        cells = self._about_cells
        # The labels are made once and reused: widgets deleted on every selection change would
        # still be waiting for deletion when the app closes, which can crash on exit.
        while len(cells) < len(rows):
            k = QLabel(self.about_body)
            k.setProperty("muted", True)
            v = ElideLabel(self.about_body)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            cells.append((k, v))
        for i, (k, v) in enumerate(cells):
            used = i < len(rows)
            k.setVisible(used)
            v.setVisible(used)
            if used:
                label, value, tip = rows[i]
                k.setText(label)
                v.set_full_text(value)
                v.setToolTip(f"{label}: {value}" + ("\n\n" + tip if tip else ""))
                k.setToolTip(tip)
        self._about_cols = 0
        self._layout_about()

    def _layout_about(self) -> None:
        """Three columns of facts when there is room, two in a narrow window."""
        n = len(self._about_rows)
        cols = 3 if self.about.width() >= 760 else 2
        if not n or cols == self._about_cols:
            return
        self._about_cols = cols
        g = self.about_grid
        per_col = max((n + cols - 1) // cols, 1)
        for c in range(6):
            g.setColumnStretch(c, 1 if c % 2 and c < cols * 2 else 0)
        for i, (k, v) in enumerate(self._about_cells[:n]):
            col = (i // per_col) * 2
            g.addWidget(k, i % per_col, col)
            g.addWidget(v, i % per_col, col + 1)

    def eventFilter(self, obj, event):
        if obj is getattr(self, "about", None) and event.type() == QEvent.Resize:
            self._layout_about()
        return super().eventFilter(obj, event)

    def _show_about(self, on: bool) -> None:
        self.about_body.setVisible(on)
        self.about_toggle.setText("Hide" if on else "Show")
        self.settings.setValue("about_shown", "true" if on else "false")

    def _copy_about(self) -> None:
        text = "\n".join(f"{label}: {value}" for label, value, _ in self._about_rows)
        head = self.sel_label.text() + "\n" + self.headline.text() + "\n"
        QApplication.clipboard().setText(head + text)
        self.statusBar().showMessage("Copied to the clipboard")

    # -------------------------------------------------------------------- sounds
    def _fill_sounds(self, track: Track, chip: ChipDump | None, x) -> None:
        self.sounds.blockSignals(True)
        self.sounds.clear()
        found, basis = [], ""
        rate = self._audio_rate or 1
        if x is not None and len(x):
            try:
                found, basis = track_sounds(self.coll, track, x, chip)
            except Exception as e:                      # splitting is a convenience; never block the decode
                self.log(f"Could not split into sounds: {e}")
        self._sounds = found
        for s in found:
            it = QTreeWidgetItem([s.label, _clock(s.start / rate), _clock((s.end - s.start) / rate)])
            it.setData(0, Qt.UserRole, s)
            if s.offset >= 0:
                it.setToolTip(0, f"Starts at byte 0x{s.offset:X} of the dump")
            self.sounds.addTopLevelItem(it)
        self.sounds.blockSignals(False)
        self.sounds_cap.setText(f"SOUNDS IN THE SELECTION ({len(found)})" if found else "SOUNDS IN THE SELECTION")
        self.sounds_note.setText(f"Found by {basis}." if found else
                                 "No separate sounds found." if x is not None and len(x) else "")
        self.sound_btn.setEnabled(False)
        self.wave.set_sounds([s.start for s in found] if len(found) > 1 else [])

    def _sound_selected(self) -> None:
        it = self.sounds.currentItem()
        s = it.data(0, Qt.UserRole) if it else None
        self.sound_btn.setEnabled(s is not None)
        self.wave.set_region((s.start, s.end) if s else None)

    def _play_sound(self) -> None:
        it = self.sounds.currentItem()
        s = it.data(0, Qt.UserRole) if it else None
        if s is None or self._audio is None:
            return
        self._stop()
        self.player.rewind()
        if self.player.play(self._audio[s.start:s.end], self._audio_rate, 0):
            self.statusBar().showMessage(f"Playing {s.label}")
        else:
            self.statusBar().showMessage(why_no_sound())

    def _fill_controls(self, track: Track):
        coll = self.coll
        self._updating = True
        hyp = coll.hyp_for(track)
        det = track.detection if track.detection.candidates and track.hyp_override else coll.detection
        self.enc_combo.clear()
        seen = set()
        for i, c in enumerate(det.candidates[:8]):
            tag = "  (best match)" if i == 0 else ""
            measure = "recognised" if c.evidence else f"{100 * c.leak:.2f}%"
            self.enc_combo.addItem(f"{c.hyp.label()}   {measure}{tag}", c.hyp.key)
            seen.add(c.hyp.key)
        self.enc_combo.insertSeparator(self.enc_combo.count())
        for codec in CODECS.values():
            key = Hypothesis(codec.id).key
            if key not in seen:
                self.enc_combo.addItem(Hypothesis(codec.id).label(), key)
                seen.add(key)
        if hyp and hyp.key not in seen:
            self.enc_combo.addItem(hyp.label(), hyp.key)
        idx = self.enc_combo.findData(hyp.key) if hyp else -1
        self.enc_combo.setCurrentIndex(idx)

        rate = coll.rate_for(track)
        self.rate_combo.clear()
        best = coll.rate_estimate.best if coll.rate_estimate.informed else None
        for r in STANDARD_RATES:
            tag = ("  (from the chip format)" if r == track.rate_stated else
                   "  (estimate)" if r == best and not coll.rate_estimate.fixed else "")
            self.rate_combo.addItem(f"{r}" + tag, r)
        i = self.rate_combo.findData(rate)
        if i >= 0:
            self.rate_combo.setCurrentIndex(i)
        else:
            self.rate_combo.setEditText(str(rate))
        self.confirm_box.setChecked(coll.rate_confirmed)
        has_override = bool(track.hyp_override or track.rate_override)
        if has_override:                       # a track with its own setting: edits should stay with it
            if not self.scope_track.isChecked():
                self._scope_auto = True
            self.scope_track.setChecked(True)
        elif getattr(self, "_scope_auto", False):
            self._scope_auto = False
            self.scope_all.setChecked(True)
        self.reset_btn.setEnabled(coll.hyp_source != "auto" or coll.rate_confirmed or has_override
                                  or any(t.hyp_override or t.rate_override for t in coll.tracks))
        self._updating = False

    # ------------------------------------------------------------ settings edits
    def _after_settings_change(self, remeasure: bool, undo=None):
        track, chip = self.current()
        measured = False
        if decode_cost(self.coll) > HEAVY_SECONDS:
            # Slow decoders run in the background behind a progress window; the results are kept,
            # so everything below finds them ready.
            hyp = self.coll.hyp_for(track) if track is not None else self.coll.hyp
            name = CODECS[hyp.codec].name if hyp else "the chosen encoding"
            coll = self.coll
            state, value = self._background(
                lambda prog: warm(coll, prog, remeasure),
                f"Decoding as {name}.\nThis decoder works one sample at a time, so larger files take a while.")
            measured = state == "ok"
            if state != "ok":
                if undo is not None:
                    undo()
                    self.log("The previous encoding was restored.")
                if state == "failed":
                    QMessageBox.warning(self, "Encoding", f"That encoding could not be applied.\n\n{value}"
                                        + ("\n\nThe previous encoding was restored." if undo else ""))
                else:
                    self.statusBar().showMessage("Cancelled" + ("; the previous encoding was restored" if undo else ""))
        if remeasure and not measured:          # keeps a confirmed speed; refreshes what each format states
            self.setCursor(Qt.BusyCursor)
            try:
                measure_rates(self.coll)
            except Exception as e:
                self.log(f"Could not measure the speed: {type(e).__name__}: {e}")
            self.unsetCursor()
        self._fill_tree(keep=(track, chip))

    def _encoding_chosen(self, index: int):
        if self._updating or self.coll is None:
            return
        key = self.enc_combo.itemData(index)
        track, _ = self.current()
        if not key or track is None:
            return
        hyp = Hypothesis.from_key(key)
        coll = self.coll
        before = (coll.hyp, coll.hyp_source, [(t, t.hyp_override, t.override_auto) for t in coll.tracks])

        def undo():
            coll.hyp, coll.hyp_source = before[0], before[1]
            for t, override, auto in before[2]:
                t.hyp_override, t.override_auto = override, auto
        if self.scope_track.isChecked():
            track.hyp_override = hyp if hyp != self.coll.hyp else None
            track.override_auto = False
            self.log(f"Encoding for track {track.name} set by hand: {hyp.label()}.")
        else:
            best = self.coll.detection.best
            self.coll.hyp = hyp
            self.coll.hyp_source = "auto" if best and best.hyp == hyp else "user"
            for t in self.coll.tracks:
                t.hyp_override = None
            self.log(f"Encoding for the collection set by hand: {hyp.label()}.")
        self._after_settings_change(remeasure=True, undo=undo)

    def _rate_chosen(self):
        if self._updating or self.coll is None:
            return
        words = self.rate_combo.currentText().replace(",", "").split()
        try:
            rate = int(float(words[0])) if words else 0
        except (ValueError, OverflowError):          # letters, "inf", or a number too large to mean anything
            rate = 0
        if not 1000 <= rate <= 192000:
            self.statusBar().showMessage("Speed must be between 1,000 and 192,000 Hz")
            return
        track, _ = self.current()
        if track is None or rate == self.coll.rate_for(track):
            return
        if self.scope_track.isChecked():
            track.rate_override = rate if rate != self.coll.rate else None
            track.rate_confirmed = False
            self.log(f"Speed for track {track.name} set to {rate:,} Hz.")
        else:
            self.coll.rate = rate
            for t in self.coll.tracks:
                t.rate_override = None
                t.rate_confirmed = False
            self.log(f"Speed for the collection set to {rate:,} Hz.")
        self._after_settings_change(remeasure=False)

    def _confirm_toggled(self, on: bool):
        if self._updating or self.coll is None:
            return
        self.coll.rate_confirmed = on
        self.log(f"Speed marked as {'confirmed' if on else 'not confirmed'} at {self.coll.rate:,} Hz.")
        if not on:
            self.coll.rate = self.coll.rate or self.coll.rate_estimate.best
        self.refresh()

    def _reset_auto(self):
        if self.coll is None:
            return
        coll = self.coll
        best = coll.detection.best
        coll.hyp = best.hyp if best and coll.detection.confidence != "none" else None
        coll.hyp_source = "auto"
        coll.rate_confirmed = False
        for t in coll.tracks:
            t.rate_override = None
            t.rate_confirmed = False
            tb = t.detection.best
            t.hyp_override = (tb.hyp if coll.hyp and tb and t.detection.confidence == "high" and tb.hyp != coll.hyp
                              and not tb.hyp.is_twin_of(coll.hyp) else None)
            t.override_auto = t.hyp_override is not None
        self.scope_all.setChecked(True)
        self.log("Back to the automatic encoding and speed estimate.")
        self._after_settings_change(remeasure=True)

    # ------------------------------------------------------------------ profiles
    def _fill_profile_menu(self):
        m = self.profile_menu
        m.clear()
        for p in profiles.load_all():
            a = m.addAction(f"{p.name}   -   {p.hyp.label()}, {p.rate:,} Hz")
            a.setToolTip(p.notes)
            a.triggered.connect(lambda _=False, prof=p: self._apply_profile(prof))
        m.addSeparator()
        m.addAction("Save current settings as a profile...").triggered.connect(self._save_profile)
        user = profiles.load_user()
        if user:
            sub = m.addMenu("Delete a saved profile")
            for p in user:
                sub.addAction(p.name).triggered.connect(lambda _=False, n=p.name: profiles.delete_user(n))

    def _apply_profile(self, p: profiles.Profile):
        coll = self.coll
        coll.hyp, coll.hyp_source = p.hyp, f"profile \"{p.name}\""
        coll.rate, coll.rate_confirmed = p.rate, True
        for t in coll.tracks:
            t.hyp_override = t.rate_override = None
            t.rate_confirmed = False
        self.scope_all.setChecked(True)
        self.log(f"Applied profile {p.name}: {p.hyp.label()}, {p.rate:,} Hz.")
        self._after_settings_change(remeasure=False)

    def _save_profile(self):
        coll = self.coll
        if coll is None or coll.hyp is None:
            return
        name, ok = QInputDialog.getText(self, "Save device profile",
                                        f"Name for these settings ({describe_reading(coll)}):")
        name = name.strip()
        if not ok or not name:
            return
        try:
            profiles.save_user(profiles.Profile(name, coll.hyp.codec, coll.hyp.layout, coll.hyp.channels, coll.rate,
                                                "Speed confirmed by ear." if coll.rate_confirmed
                                                else "Speed not confirmed by ear when saved."))
        except OSError as e:
            QMessageBox.warning(self, "Save device profile", f"The profile could not be saved.\n\n{e}")
            return
        self.log(f"Saved profile {name}.")
        self.statusBar().showMessage(f"Profile \"{name}\" saved")

    # ------------------------------------------------------------------ playback
    def _stop(self):
        self.player.stop()
        self._timer.stop()
        self.play_btn.setText("Play")

    def _rewind(self):
        self._stop()
        self.player.rewind()
        self._update_pos(0)

    def toggle_play(self):
        if self.player.playing:
            self._stop()
            return
        if self._audio is None or len(self._audio) == 0:
            return
        start = self.player.position()
        if start >= len(self._audio) - 1:
            start = 0
        if self.player.play(self._audio, self._audio_rate, start):
            self.play_btn.setText("Pause")
            self._timer.start()
        else:
            self.statusBar().showMessage(why_no_sound())

    def _seek(self, frac: float):
        if self._audio is None or len(self._audio) == 0:
            return
        frame = int(frac * len(self._audio))
        self._stop()
        self.player.set_position(frame)
        self._update_pos(frame)
        if self.player.play(self._audio, self._audio_rate, frame):
            self.play_btn.setText("Pause")
            self._timer.start()

    def _tick(self):
        pos = self.player.position()
        if not self.player.playing:
            self._stop()
            self._update_pos(0)
            return
        self._update_pos(pos)

    def _update_pos(self, frame: int):
        n = len(self._audio) if self._audio is not None else 0
        rate = self._audio_rate or 1
        self.pos_label.setText(f"{_clock(frame / rate)} / {_clock(n / rate)}")
        frac = frame / n if n and frame else None
        self.wave.set_playhead(frac)
        self.spec.set_playhead(frac)

    def listening_check(self):
        if self.coll is None or self._audio is None or len(self._audio) == 0:
            return
        self._stop()
        track, chip = self.current()
        coll = self.coll
        current = coll.rate_for(track)
        est = coll.rate_estimate
        informed = est.informed and not est.fixed
        reasons = {g.rate: g.why() for g in est.shortlist} if informed else {}
        # the measured pitch period lets the dialog show what voice pitch each speed gives
        ev = [c.rate_evidence for c in ([chip] if chip is not None else track.members)
              if c.rate_evidence and c.rate_evidence.speech_like]
        period = float(np.median([e.period for e in ev])) if ev else 0.0
        _, _, _, pad_from = self._selection_audio(track, chip)
        dlg = ListeningCheck(self._audio, est.top(3) if informed else [], current, self.player, self, reasons,
                             pitch_period=period, content_frames=pad_from or len(self._audio),
                             track_name=track.name, track_only=self.scope_track.isChecked(),
                             many_tracks=len(coll.tracks) > 1, settings=self.settings)
        if dlg.exec() and dlg.chosen:
            if dlg.track_only and len(coll.tracks) > 1:
                track.rate_override = dlg.chosen if dlg.chosen != coll.rate or track.rate_stated else None
                track.rate_confirmed = True
                self.scope_track.setChecked(True)
                self.log(f"Speed for track {track.name} set by ear: {dlg.chosen:,} Hz.")
            else:
                coll.rate = dlg.chosen
                coll.rate_confirmed = True
                for t in coll.tracks:
                    t.rate_override = None
                    t.rate_confirmed = False
                self.scope_all.setChecked(True)
                self.log(f"Speed set by ear: {dlg.chosen:,} Hz.")
            self._after_settings_change(remeasure=False)

    # -------------------------------------------------------------------- export
    def do_export(self):
        coll = self.coll
        if coll is None:
            return
        if coll.hyp is None:
            QMessageBox.information(self, "Export audio files", "No encoding is set yet. Pick one from the "
                                                                "Encoding list, check it by ear, then export.")
            return
        self._stop()
        base = Path(self.settings.value("last_export", "") or coll.tracks[0].folder.parent)
        name = (coll.tracks[0].folder.parent.name if len(coll.tracks) > 1 else coll.tracks[0].name) or "dumps"
        dlg = ExportDialog(str(base / f"{name} - audio"), coll.rate_confirmed or coll.rate_estimate.fixed,
                           describe_reading(coll), self, self.settings, len(getattr(self, "_sounds", [])))
        if not dlg.exec():
            return
        out = dlg.out_dir()
        opts = ExportOptions(per_chip=dlg.per_chip.isChecked(), joined=dlg.joined.isChecked(),
                             trim_padding=dlg.trim.isChecked(), per_sound=dlg.per_sound.isChecked(),
                             fmt=dlg.fmt.currentData(), cleanup=dlg.cleanup())
        self.settings.setValue("last_export", str(out.parent))
        self.log(f"Exporting to {out}")
        self._run(lambda prog: (export(coll, out, opts, prog), out), self._exported, "Exporting")

    def _exported(self, result):
        manifest, out = result
        n = len(manifest["files"])
        self.log(f"Wrote {n} file(s), Report.txt and manifest.json to {out}")
        self.statusBar().showMessage(f"Exported {n} file(s)")
        box = QMessageBox(self)
        box.setWindowTitle("Export finished")
        bad = manifest.get("failed") or []
        box.setText(f"{n} file(s) written to:\n{out}"
                    + (f"\n\n{len(bad)} file(s) could not be written; Report.txt lists them and why. "
                       f"First: {bad[0]['file']}: {bad[0]['error']}" if bad else ""))
        if bad:
            self.log(f"{len(bad)} file(s) could not be written: " + "; ".join(f["file"] for f in bad[:5]))
        open_btn = box.addButton("Open folder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))

    # ------------------------------------------------------------------- details
    def _refresh_details(self, track: Track, chip: ChipDump | None):
        coll = self.coll
        hyp = coll.hyp_for(track)
        e = html.escape
        H: list[str] = []

        def table(det, limit=8):
            rows = ["<table cellspacing='0' cellpadding='4' width='100%'>"
                    "<tr><th align='left'>Reading</th><th align='right'>Top-band energy</th>"
                    "<th align='right'>Shape</th></tr>"]
            for i, c in enumerate(det.candidates[:limit]):
                style = " style='font-weight:600'" if hyp and c.hyp == hyp else ""
                rows.append(f"<tr{style}><td>{i + 1}. {e(c.hyp.label())}"
                            + (f"<br><span style='color:#98A2B3'>{e(c.note)}</span>" if c.note else "")
                            + f"</td><td align='right'>{100 * c.leak:.3f}%</td>"
                            f"<td align='right'>{c.kurtosis:.1f}</td></tr>")
            return "".join(rows) + "</table>"

        H.append("<h3>Encoding</h3>")
        H.append(f"<p><b>Whole collection: {e(coll.detection.confidence)} confidence.</b> "
                 f"{e(coll.detection.reason)}</p>")
        if coll.detection.candidates:
            H.append(table(coll.detection))
            H.append("<p style='color:#98A2B3'>Top-band energy is the share of the decoded sound that lands in "
                     "the highest sixteenth of the frequency range; lower is better. Shape is the kurtosis of "
                     "the loudness distribution; natural sound is usually above 3, a wrong linear reading of "
                     "compressed data is near 2. The reading in force is in bold.</p>")
        if len(coll.tracks) > 1 and track.detection.candidates:
            H.append(f"<p><b>This track alone ({e(track.name)}): {e(track.detection.confidence)} confidence.</b> "
                     f"{e(track.detection.reason)}</p>")
            H.append(table(track.detection, 4))
        if chip is not None and chip.detection.candidates:
            H.append(f"<p><b>This chip alone ({e(chip.name)}): {e(chip.detection.confidence)} confidence.</b> "
                     f"{e(chip.detection.reason)} Analysed {chip.detection.analysed_bytes:,} bytes.</p>")
            H.append(table(chip.detection, 4))
        elif chip is not None:
            H.append(f"<p><b>This chip alone:</b> {e(chip.detection.reason)}</p>")

        H.append("<h3>Speed</h3>")
        H.append(f"<p><b>{coll.rate_for(track):,} Hz, "
                 f"{'confirmed' if coll.rate_confirmed else 'set by the chip format' if coll.rate_estimate.fixed else 'estimated, not confirmed by ear'}.</b> "
                 f"{e(coll.rate_estimate.reason)}</p>")
        if coll.rate_estimate.informed:
            rows = ["<table cellspacing='0' cellpadding='4' width='100%'><tr><th align='left'>Speed</th>"
                    "<th align='right'>Voice pitch</th><th align='right'>Speech reaches</th>"
                    "<th align='right'>Fit</th></tr>"]
            for g in coll.rate_estimate.shortlist[:6]:
                rows.append(f"<tr><td>{g.rate:,} Hz</td><td align='right'>{g.pitch_hz:.0f} Hz</td>"
                            f"<td align='right'>{g.voice_top_hz / 1000:.1f} kHz</td>"
                            f"<td align='right'>{g.score:.2f}</td></tr>")
            H.append("".join(rows) + "</table>")
            H.append("<p style='color:#98A2B3'>Fit combines two expectations: a speaking voice sits near "
                     "170 Hz, give or take an octave, and speech carries 95% of its energy below about "
                     "3.2 kHz. It is a guide for the listening check, not a measurement of the speed.</p>")
        if chip is not None and chip.rate_evidence:
            H.append("<p><b>This chip:</b> " + " ".join(e(s) for s in chip.rate_evidence.describe()) + "</p>")
        self.evidence.setHtml("".join(H))

        # ---- files
        F: list[str] = []
        if chip is not None:
            st, d = chip.structure, chip.dump
            F.append(f"<h3>{e(chip.name)}</h3><table cellpadding='3'>"
                     f"<tr><td>Path</td><td>{e(str(d.path))}</td></tr>"
                     f"<tr><td>Read as</td><td>{e(d.source_format)}</td></tr>"
                     f"<tr><td>Size</td><td>{d.size:,} bytes (0x{d.size:X})</td></tr>"
                     f"<tr><td>SHA-256</td><td><code>{d.sha256}</code></td></tr>"
                     f"<tr><td>Role</td><td>{e(ROLE_LONG.get(chip.role, chip.role))}. {e(chip.role_note)}</td></tr>"
                     f"<tr><td>Byte variety</td><td>{st.entropy:.2f} bits per byte "
                     f"(8 is random, speech and music usually 6 to 7.7)</td></tr></table>")
            lines = st.describe() + d.notes
            if lines:
                F.append("<ul>" + "".join(f"<li>{e(s)}</li>" for s in lines) + "</ul>")
            if st.string_count:
                F.append(f"<p><b>Readable text:</b> {st.string_count} run(s) of 8 or more characters.</p><pre>"
                         + "\n".join(f"0x{o:06X}  {e(s)}" for o, s in st.strings) + "</pre>")
        F.append(f"<h3>Track: {e(track.name)}</h3><p>Folder: {e(str(track.folder))}<br>"
                 f"Chip order from: {e(track.order_basis)}</p>")
        rows = ["<table cellspacing='0' cellpadding='4' width='100%'><tr><th align='left'>#</th>"
                "<th align='left'>File</th><th align='right'>Bytes</th><th align='left'>Notes</th></tr>"]
        n = 0
        for c in track.chips:
            notes = []
            if c.in_track:
                n += 1
                if c.structure.has_start_marker:
                    notes.append("start marker")
                if c.structure.has_end_padding:
                    notes.append(f"{c.structure.trail_len:,} bytes of end padding")
            else:
                notes.append(f"{ROLE_LONG.get(c.role, c.role)}: {c.role_note}")
            rows.append(f"<tr><td>{n if c.in_track else '-'}</td><td>{e(c.name)}</td>"
                        f"<td align='right'>{c.dump.size:,}</td><td>{e('; '.join(notes))}</td></tr>")
        F.append("".join(rows) + "</table>")
        if track.warnings:
            F.append("<p><b>To check:</b></p><ul>" + "".join(f"<li>{e(w)}</li>" for w in track.warnings) + "</ul>")
        if coll.errors:
            F.append("<h3>Files that could not be read</h3><ul>"
                     + "".join(f"<li>{e(p.name)}: {e(why)}</li>" for p, why in coll.errors) + "</ul>")
        self.fileinfo.setHtml("".join(F))

        # ---- formula
        if hyp:
            c = CODECS[hyp.codec]
            G = [f"<h3>{e(c.name)}</h3><p>{e(c.plain)}</p>",
                 f"<p>Bytes are taken {e(LAYOUTS[hyp.layout][0])}, "
                 + ("as one channel." if hyp.channels == 1 else f"alternating between {hyp.channels} channels.")
                 + ("" if c.structured else f" Each sample uses {c.bytes_per_sample:g} "
                    f"byte{'s' if c.bytes_per_sample != 1 else ''}.") + "</p>",
                 f"<p><b>Formula</b> (b is a stored byte; the result is a 16-bit sample):</p><pre style='white-space: pre-wrap'>{e(c.formula)}</pre>"]
            src = chip or (track.members[0] if track.members else None)
            if src is not None and c.family not in ("adpcm", "delta", "chip"):
                start = src.structure.lead_len if src.structure.has_start_marker else 0
                piece = src.dump.data[start:start + 8 * max(int(c.bytes_per_sample), 1) * hyp.channels]
                vals = decode(piece, hyp).reshape(-1)[:8]
                G.append(f"<p><b>Worked example</b> from {e(src.name)} at offset 0x{start:X}:</p><pre>"
                         f"bytes   : {' '.join(f'{b:02X}' for b in piece)}\n"
                         f"samples : {' '.join(str(int(v)) for v in vals)}</pre>")
            G.append(f"<p>Playback: {coll.rate_for(track):,} samples per second. A plain export writes these "
                     "samples unchanged; any clean-up you tick on export is listed in Report.txt.</p>")
            self.formula.setHtml("".join(G))
        else:
            self.formula.setHtml("<p>No encoding is in force.</p>")

        # ---- hex
        self._hex_chip = chip or (track.members[0] if track.members else None)
        self._updating = True
        size = self._hex_chip.dump.size if self._hex_chip else 0
        self.hex_off.setRange(0, max(size - 1, 0))
        self.hex_off.setValue(0)
        self._updating = False
        self._refresh_hex()

    def _refresh_hex(self):
        if self._updating:
            return
        c = getattr(self, "_hex_chip", None)
        if c is None:
            self.hex.setPlainText("")
            self.hex_note.setText("")
            return
        st = c.structure
        note = f"{c.name}: {c.dump.size:,} bytes."
        if st.has_end_padding:
            note += f" Padding (0x{st.trail_value:02X}) starts at 0x{st.content_end:X}."
        else:
            note += " No padding at the end."
        self._hex_buttons["pad"].setEnabled(st.has_end_padding)
        self.hex_note.setText(note)
        self.hex.setPlainText(hex_page(c.dump.data, self.hex_off.value()))

    def _hex_jump(self, where: str):
        c = getattr(self, "_hex_chip", None)
        if c is None:
            return
        target = {"start": 0, "pad": max(c.structure.content_end - 0x40, 0),
                  "end": max(c.dump.size - 0x200, 0)}[where]
        self.hex_off.setValue(target // 16 * 16)

    # --------------------------------------------------------------------- other
    def read_chip(self):
        if self._worker is not None:
            return
        self._stop()
        dlg = ReadDialog(self.settings, self)
        dlg.exec()
        for line in dlg.session_log:
            self.log(line)
        if dlg.open_after:
            self.load_paths([str(p) for p in dlg.open_after])

    def show_help(self):
        TextDialog(f"{APP_NAME} help", HELP_HTML, self).exec()

    def closeEvent(self, e):
        self.player.close()
        self.settings.sync()                      # the process stops right after the window closes
        if self._worker is not None:              # let a running export finish its file before the process stops
            self.statusBar().showMessage("Finishing the current job before closing...")
            QApplication.processEvents()
            self._worker.wait(30000)
        super().closeEvent(e)


def to_mono(x: np.ndarray) -> np.ndarray:
    """One channel for drawing, without making a large floating-point copy."""
    if x.shape[1] == 1:
        return x[:, 0]
    return (x.astype(np.int32).sum(axis=1) // x.shape[1]).astype(np.int16)


def _clock(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    m = int(seconds // 60)
    return f"{m}:{seconds - 60 * m:04.1f}"


__all__ = ["MainWindow"]
