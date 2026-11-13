"""Read a chip or a memory card from inside the app."""
from __future__ import annotations

import datetime
import hashlib
import re
import threading
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton,
                               QSpinBox, QStackedWidget, QVBoxLayout)

from ..readers import Cancelled, DriverMissing, NoDevice, ReaderError, cards, xgecu
from ..readers.base import read_verified
from ..readers.ch341a import SPI_ONLY, install_driver, open_reader
from ..readers.serprog import list_ports
from ..readers.chips import (I2C_TYPES, SPI_EEPROM_TYPES, SPI_FLASH_SIZES, ChipInfo, chip_from_marking,
                             human_size, i2c_chip, spi_eeprom_chip, spi_flash_chip)
from ..structure import analyse_structure
from .widgets import Chip
from .worker import Worker

FAMILY_HINT = {
    "spi_flash": "25-series flash chips identify themselves, so Detect chip finds the size. Typical markings: "
                 "W25Q32, MX25L1606, SST25VF040, M25P80, GD25Q16. AT45DB DataFlash is detected too; its pin "
                 "order differs from 25-series, so wire it by its datasheet rather than using the 25-series socket.",
    "spi_eeprom": "Small SPI EEPROMs carry no ID. Pick the type printed on the chip: the digits after 25 or 95 "
                  "give the size in kilobits. Examples: 25LC256, AT25640, M95160, 25AA1024.",
    "i2c_eeprom": "I2C EEPROMs carry no ID. Pick the type printed on the chip: the digits after 24 give the size "
                  "in kilobits. Examples: 24C02, AT24C64, 24LC256, M24512.",
}
VOLTAGE_NOTE = {
    "ch341a": ("Voltage: most CH341A boards drive their data lines at 5 V. That suits 5 V chips, which most old "
               "ones are. For a 3.3 V or 1.8 V chip, use the board's 3.3 V setting or a level adapter first."),
    "ch347": ("Voltage: CH347 boards work at 3.3 V. That suits 3.3 V chips. A 5 V-only chip may not answer at "
              "3.3 V, and a 1.8 V chip needs a level adapter."),
    "ft232h": ("Voltage: the FT232H's pins are 3.3 V. Power a 3.3 V chip from the board's 3V3 pin. A 1.8 V chip "
               "needs a level adapter; never feed a 3.3 V chip from 5 V."),
    "serprog": ("Voltage: a Pico works at 3.3 V, most Arduinos at 5 V. Match the board to the chip, or use a "
                "level adapter."),
}


SERIAL = ("ch341a", "ch347", "ft232h", "serprog")
DETECT_PROMPT = {
    "ch341a": "Seat the chip in the socket or clip it, then press Detect chip.",
    "ch347": "Seat the chip in the socket or clip it, then press Detect chip.",
    "ft232h": "Wire the chip to the board as shown below, then press Detect chip.",
    "serprog": "Wire the chip to the board as its firmware's instructions show, then press Detect chip.",
}
WIRING = {
    "ch341a": "",
    "ch347": "",
    "ft232h": ("<b>Wiring (port A):</b> AD0 to the chip's clock (pin 6 on 25-series), AD1 to its data in (pin 5), "
               "AD2 to its data out (pin 2), AD3 to chip select (pin 1). GND to pin 4, 3.3 V to pin 8, and tie "
               "the WP and HOLD pins (3 and 7) to 3.3 V."),
    "serprog": ("<b>Firmware:</b> the board must be running serprog firmware (pico-serprog on a Pico, "
                "frser-duino on an Arduino). The pin assignment is fixed by that firmware; its instructions "
                "show which pin goes where."),
}


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "Track"


class ReadDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Read a chip")
        self.setMinimumWidth(780)
        self.settings = settings
        self.open_after: list[Path] = []          # folders to decode when the dialog closes
        self.session_log: list[str] = []
        self._worker: Worker | None = None
        self._cancel = threading.Event()
        self._detected: ChipInfo | None = None
        self._drives: list[cards.Drive] = []

        docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or str(Path.home())
        self._base = Path(settings.value("dump_dir", str(Path(docs) / "Audio Chip Decoder" / "Dumps")))

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # ---- source
        row = QHBoxLayout()
        row.addWidget(QLabel("<b>Read with</b>"))
        self.source = QComboBox()
        self.source.addItem("CH341A programmer  -  8-pin serial chips in SOIC-8 or DIP-8 (24, 25, 95 series, AT45)", "ch341a")
        self.source.addItem("XGecu T48 / TL866II Plus / TL866A/CS  -  parallel EPROM and flash, and serial chips",
                            "xgecu")
        self.source.addItem("CH347 adapter  -  8-pin serial chips in SOIC-8 or DIP-8 (24, 25, 95 series, AT45)", "ch347")
        self.source.addItem("FT232H / FT2232H board  -  SPI chips (25, 95 series, AT45), wired by hand", "ft232h")
        self.source.addItem("Raspberry Pi Pico or Arduino with serprog firmware  -  SPI chips", "serprog")
        self.source.addItem("USB card reader  -  memory cards and USB drives", "card")
        self.source.setCurrentIndex(max(self.source.findData(settings.value("reader", "ch341a")), 0))
        self.source.currentIndexChanged.connect(self._source_changed)
        row.addWidget(self.source, 1)
        lay.addLayout(row)

        self.pages = QStackedWidget()
        lay.addWidget(self.pages)

        # ---- page: chip
        chip_page = QFrame()
        chip_page.setObjectName("card")
        g = QGridLayout(chip_page)
        g.setContentsMargins(14, 12, 14, 12)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        g.addWidget(QLabel("1. Programmer"), 0, 0)
        self.reader_chip = Chip("Checking...", "warn")
        self.reader_text = QLabel()
        self.reader_text.setWordWrap(True)
        self.recheck_btn = QPushButton("Check again")
        self.recheck_btn.clicked.connect(self._check_reader)
        self.driver_btn = QPushButton("Install the driver")
        self.driver_btn.setProperty("primary", True)
        self.driver_btn.setToolTip("Downloads WCH's CH341 driver from wch-ic.com and starts its installer. "
                                   "Windows asks for permission; then press INSTALL in WCH's window.")
        self.driver_btn.clicked.connect(self._install_driver)
        self.port_label = QLabel("Port")
        self.port = QComboBox()
        self.port.setMinimumWidth(260)
        self.port.setToolTip("The COM port the board appears as. Unplug and replug it and press Check again "
                             "if you are not sure which one it is: the new entry is the board.")
        self.port.activated.connect(lambda *_: self._check_reader())
        r = QHBoxLayout()
        r.setSpacing(8)
        r.addWidget(self.port_label)
        r.addWidget(self.port)
        r.addWidget(self.reader_chip)
        r.addWidget(self.recheck_btn)
        r.addWidget(self.driver_btn)
        r.addStretch(1)
        g.addLayout(r, 0, 1)
        g.addWidget(self.reader_text, 1, 1)

        g.addWidget(QLabel("2. Chip"), 2, 0)
        self.detect_btn = QPushButton("Detect chip")
        self.detect_btn.setProperty("primary", True)
        self.detect_btn.setToolTip("Asks the chip for its ID (SPI command 9F). 25-series flash chips answer with "
                                   "their maker and size. Nothing is written.")
        self.detect_btn.clicked.connect(self._detect)
        self.detect_text = QLabel("Seat the chip in the socket or clip it, then press Detect chip.")
        self.detect_text.setWordWrap(True)
        r = QHBoxLayout()
        r.setSpacing(10)
        r.addWidget(self.detect_btn)
        r.addWidget(self.detect_text, 1)
        g.addLayout(r, 2, 1)

        self.family = QComboBox()
        self.family.addItem("25-series SPI flash", "spi_flash")
        self.family.addItem("24-series I2C EEPROM", "i2c_eeprom")
        self.family.addItem("25/95-series SPI EEPROM", "spi_eeprom")
        self.family.currentIndexChanged.connect(self._family_changed)
        self.variant = QComboBox()
        self.variant.setMinimumWidth(230)
        r = QHBoxLayout()
        r.setSpacing(8)
        r.addWidget(QLabel("Type"))
        r.addWidget(self.family)
        r.addWidget(self.variant)
        r.addStretch(1)
        g.addLayout(r, 4, 1)
        self.marking = QLineEdit()
        self.marking.setPlaceholderText("Or type what is printed on the chip, for example 24C256, 25LC512, W25Q64")
        self.marking.setClearButtonEnabled(True)
        self.marking.setToolTip("SOIC-8 and DIP-8 are package shapes, not chip types. The part number printed on "
                                "the chip says what is inside; type it here and the type and size are set for you.")
        self.marking.textChanged.connect(self._marking_changed)
        g.addWidget(self.marking, 3, 1)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setProperty("muted", True)
        self.hint.setMinimumHeight(54)
        self.hint.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        g.addWidget(self.hint, 5, 1)
        self.wiring = QLabel()
        self.wiring.setWordWrap(True)
        self.wiring.setProperty("muted", True)
        self.wiring.setTextFormat(Qt.RichText)
        g.addWidget(self.wiring, 7, 1)
        volt = self.volt = QLabel(VOLTAGE_NOTE["ch341a"])
        volt.setWordWrap(True)
        volt.setProperty("warning", True)
        volt.setMinimumHeight(58)
        g.addWidget(volt, 6, 1)
        g.setColumnStretch(1, 1)
        self.pages.addWidget(chip_page)

        # ---- page: XGecu
        x_page = QFrame()
        x_page.setObjectName("card")
        x = QGridLayout(x_page)
        x.setContentsMargins(14, 12, 14, 12)
        x.setHorizontalSpacing(10)
        x.setVerticalSpacing(8)
        x.addWidget(QLabel("1. Programmer"), 0, 0)
        self.x_chip = Chip("Checking...", "warn")
        x_check = QPushButton("Check again")
        x_check.clicked.connect(self._check_reader)
        self.x_text = QLabel()
        self.x_text.setWordWrap(True)
        self.x_text.setMinimumHeight(36)
        self.x_text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        r = QHBoxLayout()
        r.setSpacing(8)
        r.addWidget(self.x_chip)
        r.addWidget(x_check)
        r.addStretch(1)
        x.addLayout(r, 0, 1)
        x.addWidget(self.x_text, 1, 1)
        x.addWidget(QLabel("2. Chip"), 2, 0, Qt.AlignTop)
        self.x_search = QLineEdit()
        self.x_search.setPlaceholderText("Type what is printed on the chip, for example 27C256 or AM29F040")
        self.x_search.setClearButtonEnabled(True)
        self.x_search.textChanged.connect(self._x_search_changed)
        self.x_package = QComboBox()
        self.x_package.addItem("Any package", "")
        for key, label in xgecu.PACKAGES.items():
            self.x_package.addItem(label, key)
        self.x_package.setToolTip("The shape of the chip's body. DIP chips go straight into the socket; SOIC, "
                                  "PLCC and TSOP chips sit in an adapter board that goes into the socket.")
        self.x_package.currentIndexChanged.connect(self._x_search_changed)
        xr = QHBoxLayout()
        xr.setSpacing(8)
        xr.addWidget(self.x_search, 1)
        xr.addWidget(self.x_package)
        x.addLayout(xr, 2, 1)
        self.x_list = QListWidget()
        self.x_list.setFixedHeight(138)
        self.x_list.setUniformItemSizes(True)
        x.addWidget(self.x_list, 3, 1)
        x.setRowMinimumHeight(3, 144)
        self.x_count = QLabel()
        self.x_count.setProperty("muted", True)
        self.x_count.setWordWrap(True)
        self.x_count.setMinimumHeight(34)
        self.x_count.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        x.addWidget(self.x_count, 4, 1)
        self.x_id = QCheckBox("Ask the chip for its ID before reading")
        self.x_id.setToolTip("Off by default. Reading never needs the ID. On EPROMs the ID request puts 12 V on "
                             "address pin A9, and old chips from different makers often answer differently. "
                             "Turn it on to confirm a flash chip is the type you picked.")
        x.addWidget(self.x_id, 5, 1)
        x_note = QLabel("Seat the chip as the outline printed on the programmer shows, lever fully down. On a "
                        "TL866II Plus the pins are tested for contact before the read. Reading does not erase or "
                        "change a chip.")
        x_note.setWordWrap(True)
        x_note.setProperty("warning", True)
        x_note.setMinimumHeight(58)
        x.addWidget(x_note, 6, 1)
        x.setColumnStretch(1, 1)
        self.pages.addWidget(x_page)
        self._x_family: str | None = None
        self._x_ok = False

        # ---- page: card
        card_page = QFrame()
        card_page.setObjectName("card")
        c = QGridLayout(card_page)
        c.setContentsMargins(14, 12, 14, 12)
        c.setHorizontalSpacing(10)
        c.setVerticalSpacing(8)
        c.addWidget(QLabel("Drive"), 0, 0)
        self.drive_combo = QComboBox()
        self.drive_combo.setMinimumWidth(320)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._fill_drives)
        image = QPushButton("Use an image file...")
        image.setToolTip("Read from a card image file instead of a drive.")
        image.clicked.connect(self._pick_image)
        r = QHBoxLayout()
        r.addWidget(self.drive_combo, 1)
        r.addWidget(refresh)
        r.addWidget(image)
        c.addLayout(r, 0, 1)
        c.addWidget(QLabel("Amount"), 1, 0)
        self.amount = QComboBox()
        for size, label in cards.SIZE_CHOICES:
            self.amount.addItem(label, size)
        self.amount.setCurrentIndex(1)
        r = QHBoxLayout()
        r.addWidget(self.amount)
        r.addStretch(1)
        c.addLayout(r, 1, 1)
        self.card_note = QLabel()
        self.card_note.setWordWrap(True)
        self.card_note.setProperty("muted", True)
        self.card_note.setMinimumHeight(74)
        self.card_note.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        c.addWidget(self.card_note, 2, 1)
        self.admin_btn = QPushButton("Restart the app as administrator")
        self.admin_btn.clicked.connect(self._restart_admin)
        r = QHBoxLayout()
        r.addWidget(self.admin_btn)
        r.addStretch(1)
        c.addLayout(r, 3, 1)
        c.setColumnStretch(1, 1)
        c.setRowStretch(4, 1)
        self.pages.addWidget(card_page)
        self._image_path: str | None = None

        # ---- where it goes
        save = QFrame()
        save.setObjectName("card")
        s = QGridLayout(save)
        s.setContentsMargins(14, 12, 14, 12)
        s.setHorizontalSpacing(10)
        s.setVerticalSpacing(8)
        s.addWidget(QLabel("3. Save as"), 0, 0)
        self.track = QLineEdit(f"Read {datetime.datetime.now():%Y-%m-%d %H%M}")
        self.track.setToolTip("Name of the track. Chips read under the same name are joined in number order.")
        self.number = QSpinBox()
        self.number.setRange(1, 999)
        self.number.setPrefix("chip ")
        self.number.setToolTip("Position of this chip in the track. It goes up by one after each read.")
        r = QHBoxLayout()
        r.setSpacing(8)
        r.addWidget(QLabel("Track"))
        r.addWidget(self.track, 1)
        self.number.setMinimumWidth(96)
        r.addWidget(self.number)
        s.addLayout(r, 0, 1)
        self.folder_label = QLabel()
        self.folder_label.setProperty("muted", True)
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        change = QPushButton("Change folder")
        change.clicked.connect(self._change_folder)
        r = QHBoxLayout()
        r.addWidget(self.folder_label, 1)
        r.addWidget(change)
        s.addLayout(r, 1, 1)
        self.verify = QCheckBox("Read twice and compare (recommended)")
        self.verify.setChecked(True)
        self.verify.setToolTip("A poor contact gives different bytes each time. Two matching reads rule that out. "
                               "If they differ, a third read settles it.")
        s.addWidget(self.verify, 2, 1)
        s.setColumnStretch(1, 1)
        lay.addWidget(save)

        # ---- progress and result
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1)
        self.status = QLabel("Ready.")
        self.status.setProperty("muted", True)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result.hide()
        lay.addWidget(self.result)

        row = QHBoxLayout()
        self.read_btn = QPushButton("Read chip")
        self.read_btn.setProperty("primary", True)
        self.read_btn.setMinimumWidth(120)
        self.read_btn.clicked.connect(self._read)
        self.cancel_btn = QPushButton("Stop")
        self.cancel_btn.clicked.connect(self._cancel.set)
        self.cancel_btn.setEnabled(False)
        self.decode_btn = QPushButton("Decode what was read")
        self.decode_btn.setProperty("primary", True)
        self.decode_btn.setEnabled(False)
        self.decode_btn.clicked.connect(self.accept)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(self.read_btn)
        row.addWidget(self.cancel_btn)
        row.addStretch(1)
        row.addWidget(self.decode_btn)
        row.addWidget(close)
        lay.addLayout(row)

        self._reader_ok = False
        self._family_changed()
        self._update_folder()
        self.track.textChanged.connect(self._update_folder)
        self._source_changed()

    # ---------------------------------------------------------------- helpers
    def _log(self, text: str) -> None:
        self.session_log.append(text)

    def _target_dir(self) -> Path:
        return self._base / _safe(self.track.text())

    def _update_folder(self) -> None:
        self.folder_label.setText(f"Folder: {self._target_dir()}")

    def _change_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder for the dumps", str(self._base))
        if d:
            self._base = Path(d)
            self.settings.setValue("dump_dir", d)
            self._update_folder()

    def _src(self) -> str:
        return self.source.currentData()

    def _source_changed(self) -> None:
        src = self._src()
        self.settings.setValue("reader", src)
        self.pages.setCurrentIndex({"xgecu": 1, "card": 2}.get(src, 0))
        serial = src in SERIAL
        self.port_label.setVisible(src == "serprog")
        self.port.setVisible(src == "serprog")
        if serial:
            self._detected = None
            keep = self.family.currentData()
            self.family.blockSignals(True)
            self.family.clear()
            self.family.addItem("25-series SPI flash", "spi_flash")
            if src not in SPI_ONLY:
                self.family.addItem("24-series I2C EEPROM", "i2c_eeprom")
            self.family.addItem("25/95-series SPI EEPROM", "spi_eeprom")
            self.family.setCurrentIndex(max(self.family.findData(keep), 0))
            self.family.blockSignals(False)
            self._family_changed()
            self.detect_text.setText(DETECT_PROMPT[src])
            self.volt.setText(VOLTAGE_NOTE[src])
            self.wiring.setText(WIRING[src])
            self.wiring.setVisible(bool(WIRING[src]))
        self.read_btn.setText("Read card" if src == "card" else "Read chip")
        self.verify.setEnabled(src != "card")
        self.number.setEnabled(src != "card")
        if src == "card":
            self._fill_drives()
        else:
            self._check_reader()
        QTimer.singleShot(0, self.adjustSize)

    def _busy(self, on: bool, text: str = "") -> None:
        for w in (self.read_btn, self.detect_btn, self.recheck_btn, self.driver_btn, self.decode_btn,
                  self.source, self.x_search, self.x_list):
            w.setEnabled(not on)
        self.cancel_btn.setEnabled(on and self._src() != "xgecu")       # an XGecu read is left to finish
        if not on:
            self.decode_btn.setEnabled(bool(self.open_after))
            self.bar.setRange(0, 1)
            self.bar.setValue(0)
        else:
            self.bar.setRange(0, 0)
        if text:
            self.status.setText(text)

    def _run(self, fn, done, label: str) -> None:
        self._cancel.clear()
        self._busy(True, label)
        self._worker = Worker(fn, self)
        self._worker.progress.connect(self._progress)
        self._worker.finished_ok.connect(lambda r: (self._end(), done(r)))
        self._worker.failed_exc.connect(lambda e: (self._end(), self._failed(e)))
        self._worker.start()

    def _end(self) -> None:
        if self._worker is not None:
            self._worker.wait(3000)
            self._worker = None
        self._busy(False)

    def _progress(self, i: int, n: int, text: str) -> None:
        self.bar.setRange(0, max(n, 0))
        self.bar.setValue(i)
        if text:
            if n == 100:                                   # the XGecu reader reports a percentage
                self.status.setText(f"{text}: {i}%")
            else:
                self.status.setText(f"{text}: {human_size(i)} of {human_size(n)}" if n else text)

    def _failed(self, e: Exception) -> None:
        if isinstance(e, Cancelled):
            self.status.setText("Stopped. Nothing was saved.")
            return
        msg = str(e) if isinstance(e, ReaderError) else f"Unexpected error: {type(e).__name__}: {e}"
        self._log(f"Read failed: {msg}")
        self.status.setText("Did not finish.")
        self._show_result(msg, bad=True)
        if isinstance(e, (DriverMissing, NoDevice)):
            self._check_reader()

    def _show_result(self, html: str, bad: bool = False) -> None:
        self.result.setProperty("bad", bad)
        self.result.setProperty("good", not bad)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)
        self.result.setText(html)
        self.result.setContentsMargins(4, 2, 4, 2)
        self.result.show()
        self.result.updateGeometry()

    # ------------------------------------------------------------- programmer
    def _check_reader(self) -> None:
        if self._src() == "xgecu":
            self._check_xgecu()
            return
        if self._src() not in SERIAL:
            return
        src = self._src()
        wch = src in ("ch341a", "ch347")
        if src == "serprog":
            self._fill_ports()
        try:
            with self._open() as r:
                desc = r.describe()
        except DriverMissing as e:
            self.reader_chip.set("Driver not installed", "low")
            self.reader_text.setText(f"{e} Press Install the driver; it takes about a minute and is needed once."
                                     if wch else str(e))
            self.driver_btn.setVisible(wch)
            self._reader_ok = False
        except NoDevice as e:
            self.reader_chip.set("Programmer not found", "low")
            self.reader_text.setText(str(e))
            self.driver_btn.setVisible(wch)
            self._reader_ok = False
        except ReaderError as e:
            self.reader_chip.set("Problem", "low")
            self.reader_text.setText(str(e))
            self.driver_btn.hide()
            self._reader_ok = False
        else:
            self.reader_chip.set("Programmer connected", "ok")
            self.reader_text.setText(f"Found: {desc}.")
            self.driver_btn.hide()
            self._reader_ok = True

    def _check_xgecu(self) -> None:
        try:
            name, _opt, family = xgecu.presence()
        except NoDevice as e:
            self.x_chip.set("Programmer not found", "low")
            self.x_text.setText(str(e))
            self._x_ok = False
        except ReaderError as e:
            self.x_chip.set("Problem", "low")
            self.x_text.setText(str(e))
            self._x_ok = False
        else:
            self.x_chip.set(f"{name} connected", "ok")
            self.x_text.setText(f"Found an XGecu {name}. Its list has {xgecu.chip_count(family):,} memory chips.")
            self._x_family, self._x_ok = family, True
        if self._x_family is None:
            self._x_family = "ii"
        self._x_search_changed()

    def _x_search_changed(self) -> None:
        text = self.x_search.text().strip()
        self.x_list.clear()
        if len(text) < 2:
            self.x_count.setText("Type at least two characters, and pick the package on the right (DIP, SOIC, "
                                 "PLCC...). Any maker's entry for the same part number reads the same way.")
            return
        hits = xgecu.search(self._x_family or "ii", text, package=self.x_package.currentData() or None)
        for name, size, has_id in hits:
            item = QListWidgetItem(f"{name.split('@')[0]}    -    {xgecu.package_label(name)}    -    "
                                   f"{human_size(size)}")
            item.setData(Qt.UserRole, (name, size, has_id))
            self.x_list.addItem(item)
        if hits:
            self.x_list.setCurrentRow(0)
            self.x_count.setText(f"{len(hits)} match{'es' if len(hits) != 1 else ''}"
                                 + (" (first 250 shown)" if len(hits) >= 250 else "")
                                 + ". The top one is selected.")
        else:
            self.x_count.setText("No chip in the programmer's list matches. Try fewer characters, such as the "
                                 "digits only.")

    def _open(self):
        return open_reader(self._src(), self.port.currentData())

    def _fill_ports(self) -> None:
        keep = self.port.currentData() or self.settings.value("serprog_port", "")
        self.port.blockSignals(True)
        self.port.clear()
        for dev, desc in list_ports():
            self.port.addItem(desc if dev in desc else f"{dev}  -  {desc}", dev)
        if not self.port.count():
            self.port.addItem("No serial ports found", "")
        self.port.setCurrentIndex(max(self.port.findData(keep), 0))
        self.port.blockSignals(False)
        if self.port.currentData():
            self.settings.setValue("serprog_port", self.port.currentData())

    def _install_driver(self) -> None:
        def done(setup):
            self._log(f"Started WCH driver installer: {setup}")
            self.status.setText("Driver installer started.")
            QMessageBox.information(self, "Install the driver",
                                    "WCH's driver installer has been started.\n\n1. Allow it when Windows asks.\n"
                                    "2. Press INSTALL in its window and wait for the success message.\n"
                                    "3. Close it, unplug the programmer, plug it back in.\n\n"
                                    "Then press OK here.")
            self._check_reader()
        self._run(lambda prog: install_driver(prog), done, "Downloading the driver from wch-ic.com...")

    # ------------------------------------------------------------------- chip
    def _family_changed(self) -> None:
        fam = self.family.currentData()
        self.variant.clear()
        if fam == "spi_flash":
            if self._detected and self._detected.size:
                self.variant.addItem(f"{human_size(self._detected.size)} (detected)", self._detected.size)
            for size in SPI_FLASH_SIZES:
                self.variant.addItem(human_size(size), size)
            if not (self._detected and self._detected.size):
                self.variant.setCurrentIndex(self.variant.findData(1 << 20))
        elif fam == "i2c_eeprom":
            for i, (name, size) in enumerate(I2C_TYPES):
                self.variant.addItem(f"{name}  ({human_size(size)})", i)
            self.variant.setCurrentIndex(1)
        else:
            for i, (name, size, _) in enumerate(SPI_EEPROM_TYPES):
                self.variant.addItem(f"{name}  ({human_size(size)})", i)
            self.variant.setCurrentIndex(8)
        self.hint.setText(FAMILY_HINT[fam])

    def _marking_changed(self) -> None:
        chip, message = chip_from_marking(self.marking.text())
        self._marked = None
        if not message:
            self.hint.setText(FAMILY_HINT[self.family.currentData()])
            return
        if chip is not None:
            fam = "spi_flash" if chip.family == "at45" else chip.family
            row = self.family.findData(fam)
            if row < 0:
                self.hint.setText(message + " This reader handles SPI chips only; use a CH341A, CH347 or XGecu "
                                  "programmer for 24-series chips.")
                return
            self.family.setCurrentIndex(row)
            self._family_changed()
            if chip.family == "at45":
                self._marked = chip
                self.variant.insertItem(0, f"{human_size(chip.size)} AT45 DataFlash (from the marking)", chip.size)
                self.variant.setCurrentIndex(0)
            else:
                key = chip.size if fam == "spi_flash" else chip.i2c_type if fam == "i2c_eeprom" else \
                    next(i for i, (_n, sz, _a) in enumerate(SPI_EEPROM_TYPES) if sz == chip.size)
                at = self.variant.findData(key)
                if at >= 0:
                    self.variant.setCurrentIndex(at)
            message = "<b>" + message + "</b> Type and size are set."
        self.hint.setText(message)

    def _detect(self) -> None:
        def job(_prog):
            with self._open() as r:
                return r.identify()

        def done(info: ChipInfo | None):
            self._detected = info
            if info is None:
                self.detect_text.setText("No 25-series flash chip answered. If the chip is a 24-series or "
                                         "25/95-series EEPROM, that is normal: pick its type below. Otherwise "
                                         "check that it is seated the right way round and making contact.")
                self.status.setText("No ID received.")
                self._log("Detect: no JEDEC ID received.")
                return
            self.detect_text.setText(f"<b>Found: {info.describe()}.</b>" + (f" {info.note}" if info.note else ""))
            self.family.setCurrentIndex(self.family.findData("spi_flash"))
            self._family_changed()
            if info.family == "at45":
                self.variant.clear()
                self.variant.addItem(f"{human_size(info.size)} AT45 DataFlash (detected)", info.size)
            self.status.setText("Chip identified.")
            self._log(f"Detect: {info.describe()}")
        self._run(job, done, "Asking the chip for its ID...")

    def _chosen_chip(self) -> ChipInfo:
        fam = self.family.currentData()
        if fam == "spi_flash" and self._detected and self._detected.family == "at45" \
                and self.variant.currentIndex() == 0:
            return self._detected
        if fam == "spi_flash" and getattr(self, "_marked", None) and self.variant.currentIndex() == 0 \
                and "AT45" in self.variant.currentText():
            return self._marked
        if fam == "spi_flash":
            chip = spi_flash_chip(int(self.variant.currentData()))
            if self._detected and self._detected.size == chip.size:
                chip.name, chip.id_bytes = self._detected.name, self._detected.id_bytes
            return chip
        if fam == "i2c_eeprom":
            return i2c_chip(int(self.variant.currentData()))
        return spi_eeprom_chip(int(self.variant.currentData()))

    # ------------------------------------------------------------------- card
    def _fill_drives(self) -> None:
        self._image_path = None
        self._drives = cards.list_removable()
        self.drive_combo.clear()
        for d in self._drives:
            self.drive_combo.addItem(d.describe(), d.path)
        if not self._drives:
            self.drive_combo.addItem("No removable drive found", "")
        admin = cards.is_admin()
        self.admin_btn.setVisible(not admin)
        self.card_note.setText(
            "Reads the card's raw contents from the first byte, whatever file system is on it, and searches them "
            "for sound. Put the card in a USB card reader first. Nothing is written to the card."
            + ("" if admin else " Windows only allows this for an administrator: use the button below, then "
                                "come back to this screen."))

    def _pick_image(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Card image file", str(self._base), "All files (*)")
        if f:
            self._image_path = f
            self.drive_combo.clear()
            self.drive_combo.addItem(f"Image file: {Path(f).name}", f)

    def _restart_admin(self) -> None:
        if cards.relaunch_as_admin():
            self.status.setText("A second copy is starting with administrator rights. Close this one.")
        else:
            self.status.setText("Windows did not start the app as administrator.")

    # ------------------------------------------------------------------- read
    def _read(self) -> None:
        self.result.hide()
        if self._src() == "xgecu":
            item = self.x_list.currentItem()
            if item is None:
                self.status.setText("Pick the chip type first: type its marking in the box.")
                return
            name, size, _has_id = item.data(Qt.UserRole)
            chip = ChipInfo("xgecu", name, size)
            verify, check_id = self.verify.isChecked(), self.x_id.isChecked()

            def xjob(prog):
                return read_verified(lambda p: xgecu.read(name, None, p, check_id), prog, verify)
            self._run(xjob, lambda res: self._chip_done(chip, *res), f"Reading {name}...")
        elif self._src() in SERIAL:
            chip = self._chosen_chip()
            verify = self.verify.isChecked()

            def job(prog):
                with self._open() as r:
                    return read_verified(lambda p: r.read(chip, p, self._cancel.is_set), prog, verify)
            self._run(job, lambda res: self._chip_done(chip, *res), f"Reading {chip.name}...")
        else:
            path = self.drive_combo.currentData()
            if not path:
                self.status.setText("No drive selected.")
                return
            limit = int(self.amount.currentData())
            self._run(lambda prog: cards.read_raw(path, limit, prog, self._cancel.is_set),
                      lambda data: self._card_done(path, data), "Reading the card...")

    def _save(self, data: bytes, stem: str) -> Path:
        folder = self._target_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{stem}.bin"
        n = 2
        while path.exists():
            path = folder / f"{stem} (read {n}).bin"
            n += 1
        path.write_bytes(data)
        if folder not in self.open_after:
            self.open_after.append(folder)
        return path

    def _chip_done(self, chip: ChipInfo, data: bytes, report) -> None:
        st = analyse_structure(data)
        digest = hashlib.sha256(data).hexdigest()
        if st.blank:
            self._log(f"Read {chip.name}: every byte is 0x{st.lead_value:02X}; not saved.")
            self.status.setText("Read finished, but the result is blank.")
            self._show_result(f"Every byte came back as {st.lead_value:02X}. Nothing was saved. Either the chip "
                              "is blank, it is not making contact, it is in the wrong way round, or the type "
                              "chosen does not match the chip.", bad=True)
            return
        if not report.trustworthy:
            self._log(f"Read {chip.name}: unreliable. {report.summary()}")
            self.status.setText("Read finished, but the reads disagree.")
            self._show_result(report.summary() + " Nothing was saved.", bad=True)
            return
        path = self._save(data, str(self.number.value()))
        notes = [f"<b>Saved {path.name}</b> ({human_size(len(data))}) in {path.parent}.", report.summary()]
        if st.repeat_of:
            notes.append(f"The contents repeat every {human_size(st.repeat_of)}: the chip is probably smaller "
                         "than the type chosen. It will still decode; the repeats are flagged.")
        notes.append(f"SHA-256 {digest[:16]}...")
        self._log(f"Read {chip.describe()} -> {path} ({len(data):,} bytes, sha256 {digest}). {report.summary()}")
        self.status.setText("Read finished.")
        self._show_result(" ".join(notes), bad=not report.identical and report.passes > 1)
        self.number.setValue(self.number.value() + 1)
        self.decode_btn.setEnabled(True)
        self.read_btn.setText("Read next chip")

    def _card_done(self, source: str, data: bytes) -> None:
        st = analyse_structure(data)
        if st.blank:
            self.status.setText("Read finished, but the result is blank.")
            self._show_result(f"Every byte read was {st.lead_value:02X}. Nothing was saved.", bad=True)
            return
        path = self._save(data, "card image")
        self._log(f"Read {len(data):,} bytes from {source} -> {path}")
        self.status.setText("Read finished.")
        found = ", ".join(f"{e.kind} file" for e in st.embedded[:6])
        self._show_result(f"<b>Saved {path.name}</b> ({human_size(len(data))}) in {path.parent}."
                          + (f" Found inside: {found}." if found else ""))
        self.decode_btn.setEnabled(True)

    def reject(self) -> None:
        if self._worker is not None:
            self._cancel.set()
            return
        if self.open_after:
            box = QMessageBox(self)
            box.setWindowTitle("Read a chip")
            box.setText("Decode what was read?")
            yes = box.addButton("Decode", QMessageBox.AcceptRole)
            box.addButton("Not now", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not yes:
                self.open_after = []
        super().reject()


__all__ = ["ReadDialog"]
