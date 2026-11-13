"""Drawing widgets: waveform, spectrogram, confidence chip."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

INK = QColor("#E6E9EE")
MUTED = QColor("#98A2B3")
GRID = QColor("#2F3742")
PANEL = QColor("#171A20")
ACCENT = QColor("#FFD166")          # play position: stands out against both plots
WAVE = QColor("#6FA8FF")
PAD = QColor(242, 190, 99, 34)
MARK = QColor("#4A5564")

CONFIDENCE_STYLE = {
    "high": ("#7BD88F", "#16301F"),
    "medium": ("#F2BE63", "#3A2C12"),
    "low": ("#FF9385", "#3D1D1A"),
    "none": ("#FF9385", "#3D1D1A"),
    "ok": ("#7BD88F", "#16301F"),
    "warn": ("#F2BE63", "#3A2C12"),
}


class Chip(QLabel):
    """A small rounded status label."""

    def __init__(self, text: str = "", kind: str = "ok", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.set(text, kind)

    def set(self, text: str, kind: str) -> None:
        fg, bg = CONFIDENCE_STYLE.get(kind, CONFIDENCE_STYLE["warn"])
        self.setText(text)
        self.setStyleSheet(f"QLabel {{ color: {fg}; background: {bg}; border-radius: 10px; "
                           f"padding: 3px 10px; font-weight: 600; }}")


class WaveformView(QWidget):
    """Whole-selection waveform. Click to move the play position."""
    seek = Signal(float)          # 0..1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(84)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._x: np.ndarray | None = None
        self._marks: list[tuple[int, str]] = []
        self._pad_from: int | None = None
        self._playhead: float | None = None
        self._env: tuple[int, np.ndarray, np.ndarray] | None = None
        self._ticks: list[int] = []                    # starts of the separate sounds
        self._region: tuple[int, int] | None = None    # the sound picked in the list
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Waveform of the selection. Click anywhere to play from that point.")

    def set_audio(self, mono: np.ndarray | None, marks=(), pad_from: int | None = None) -> None:
        self._x = mono
        self._marks = list(marks)
        self._pad_from = pad_from
        self._env = None
        self._playhead = None
        self._ticks = []
        self._region = None
        self.update()

    def set_sounds(self, starts: list[int]) -> None:
        self._ticks = list(starts)
        self.update()

    def set_region(self, region: tuple[int, int] | None) -> None:
        self._region = region
        self.update()

    def set_playhead(self, frac: float | None) -> None:
        if frac != self._playhead:
            self._playhead = frac
            self.update()

    def _envelope(self, width: int):
        if self._env and self._env[0] == width:
            return self._env[1], self._env[2]
        x = self._x
        n = len(x)
        if n >= width * 2:
            edges = np.linspace(0, n, width + 1).astype(np.int64)[:-1]
            lo = np.minimum.reduceat(x, edges)
            hi = np.maximum.reduceat(x, edges)
        else:
            idx = np.clip(np.linspace(0, max(n - 1, 0), width).astype(np.int64), 0, max(n - 1, 0))
            lo = hi = x[idx] if n else np.zeros(width)
        self._env = (width, lo, hi)
        return lo, hi

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, PANEL)
        p.setPen(QPen(GRID, 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        w, h = r.width(), r.height()
        mid = h / 2
        p.drawLine(0, int(mid), w, int(mid))
        if self._x is None or len(self._x) == 0:
            p.setPen(MUTED)
            p.drawText(r, Qt.AlignCenter, "No audio to show")
            return
        n = len(self._x)
        if self._pad_from is not None and self._pad_from < n:
            x0 = int(w * self._pad_from / n)
            p.fillRect(QRectF(x0, 1, w - x0 - 1, h - 2), PAD)
        if self._region:
            a, b = (int(w * v / n) for v in self._region)
            p.fillRect(QRectF(a, 1, max(b - a, 2), h - 2), QColor(76, 141, 255, 46))
        lo, hi = self._envelope(max(w, 2))
        scale = (h / 2 - 6) / 32768.0
        p.setPen(QPen(WAVE, 1))
        for i in range(len(lo)):
            y1 = int(mid - hi[i] * scale)
            y2 = int(mid - lo[i] * scale)
            p.drawLine(i, y1, i, max(y2, y1 + 1))
        font = QFont(self.font())
        font.setPointSizeF(max(font.pointSizeF() - 1.5, 7))
        p.setFont(font)
        last_label_x = -1000
        for frame, label in self._marks:
            x = int(w * frame / n)
            if frame:
                p.setPen(QPen(MARK, 1, Qt.DashLine))
                p.drawLine(x, 1, x, h - 2)
            if x - last_label_x > 46:
                p.setPen(MUTED)
                p.drawText(x + 3, 12, label)
                last_label_x = x
        if self._ticks and len(self._ticks) <= w // 3:
            p.setPen(QPen(QColor("#5BD6A2"), 1))
            for frame in self._ticks:
                x = int(w * frame / n)
                p.drawLine(x, h - 8, x, h - 2)
        if self._pad_from is not None and self._pad_from < n and w - int(w * self._pad_from / n) > 60:
            p.setPen(QColor("#F2BE63"))
            p.drawText(int(w * self._pad_from / n) + 4, h - 6, "padding")
        if self._playhead is not None:
            x = int(w * self._playhead)
            p.setPen(QPen(ACCENT, 2))
            p.drawLine(x, 0, x, h)

    def mousePressEvent(self, e):
        if self._x is not None and len(self._x) and e.button() == Qt.LeftButton:
            self.seek.emit(min(max(e.position().x() / max(self.width(), 1), 0.0), 1.0))


def _colormap() -> np.ndarray:
    pts = np.array([(23, 26, 32), (26, 38, 78), (34, 70, 150), (40, 120, 190), (60, 175, 185),
                    (130, 215, 150), (235, 220, 95), (255, 245, 200)], dtype=np.float64)
    xs = np.linspace(0, 255, len(pts))
    lut = np.stack([np.interp(np.arange(256), xs, pts[:, c]) for c in range(3)], axis=1)
    return lut.astype(np.uint8)


_LUT = _colormap()


class SpectrogramView(QWidget):
    """Frequency content over time. Shows the recording's filter cut-off at a glance."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(104)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._img: QImage | None = None
        self._buf = None
        self._rate = 0
        self._playhead: float | None = None
        self.setToolTip("Spectrogram: time runs left to right, pitch bottom to top. Dark is quiet, blue is\n"
                        "louder, yellow is loudest.\n"
                        "A correct decode shows a clean empty strip at the very top.")

    def set_audio(self, mono: np.ndarray | None, rate: int) -> None:
        self._rate = rate
        self._img = None
        if mono is not None and len(mono) >= 1024:
            nfft = 512
            hop = max(64, len(mono) // 1400)
            count = min(1 + (len(mono) - nfft) // hop, 2000)
            idx = np.arange(nfft)[None, :] + hop * np.arange(count)[:, None]
            seg = mono[idx].astype(np.float32)
            seg -= seg.mean(axis=1, keepdims=True)
            mag = np.abs(np.fft.rfft(seg * np.hanning(nfft).astype(np.float32), axis=1))
            db = 20 * np.log10(mag + 1.0)
            top = float(db.max())
            norm = np.clip((db - (top - 78)) / 78, 0, 1)
            pix = (norm.T[::-1] * 255).astype(np.uint8)            # rows = frequency, top = high
            rgb = np.ascontiguousarray(_LUT[pix])
            self._buf = rgb
            self._img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.shape[1] * 3, QImage.Format_RGB888)
        self.update()

    def set_rate(self, rate: int) -> None:
        self._rate = rate
        self.update()

    def set_playhead(self, frac: float | None) -> None:
        if frac != self._playhead:
            self._playhead = frac
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, PANEL)
        axis = 62
        plot = QRectF(axis, 1, r.width() - axis - 1, r.height() - 2)
        if self._img is None:
            p.setPen(MUTED)
            p.drawText(r, Qt.AlignCenter, "No audio to show")
        else:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawImage(plot, self._img)
        p.setPen(QPen(GRID, 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        font = QFont(self.font())
        font.setPointSizeF(max(font.pointSizeF() - 1.5, 7))
        p.setFont(font)
        p.setPen(MUTED)
        nyq = self._rate / 2 if self._rate else 0
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = plot.bottom() - frac * plot.height()
            label = f"{nyq * frac / 1000:.1f} kHz" if nyq else ""
            ty = min(max(y + 4, 11), r.height() - 3)
            p.drawText(QRectF(0, ty - 11, axis - 5, 14), Qt.AlignRight | Qt.AlignVCenter, label)
            p.drawLine(QPointF(axis - 3, y), QPointF(axis, y))
        if self._playhead is not None:
            x = plot.left() + plot.width() * self._playhead
            p.setPen(QPen(ACCENT, 2))
            p.drawLine(QPointF(x, 0), QPointF(x, r.height()))


def hex_page(data: bytes, offset: int, length: int = 2048) -> str:
    """Classic hex dump: offset, 16 bytes, printable characters."""
    offset = max(0, min(offset, max(len(data) - 1, 0))) // 16 * 16
    lines = []
    for row in range(offset, min(offset + length, len(data)), 16):
        chunk = data[row:row + 16]
        hexs = " ".join(f"{b:02X}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{row:08X}  {hexs:<47}  {text}")
    return "\n".join(lines)


__all__ = ["Chip", "WaveformView", "SpectrogramView", "hex_page", "CONFIDENCE_STYLE"]


class FitDialog(QDialog):
    """A dialog that never opens larger than the screen: its contents scroll when there is not room for all of it."""

    def body(self) -> QVBoxLayout:
        """The layout to fill, in place of QVBoxLayout(self)."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._inner = QWidget()
        self._scroll.setWidget(self._inner)
        outer.addWidget(self._scroll)
        return QVBoxLayout(self._inner)

    def _room(self) -> QSize:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return QSize(1600, 1000)
        g = screen.availableGeometry()
        return QSize(g.width() - 24, g.height() - 64)          # leave space for the title bar and borders

    def sizeHint(self) -> QSize:
        if not hasattr(self, "_inner"):
            return super().sizeHint()
        want = self._inner.sizeHint().expandedTo(self._inner.minimumSizeHint())
        room = self._room()
        w = max(want.width(), self.minimumWidth()) + 2
        h = want.height() + 2
        bar = self._scroll.verticalScrollBar().sizeHint().width()
        if h > room.height():
            w += bar                                           # room for the scroll bar that will appear
        return QSize(min(w, room.width()), min(h, room.height()))

    def fit(self) -> None:
        room = self._room()
        if self.minimumWidth() > room.width():
            self.setMinimumWidth(room.width())
        self.resize(self.sizeHint())
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None and self.isVisible():
            g = screen.availableGeometry()
            f = self.frameGeometry()
            self.move(min(max(f.x(), g.x()), max(g.right() - f.width(), g.x())),
                      min(max(f.y(), g.y()), max(g.bottom() - f.height(), g.y())))

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_fitted", False):
            self._fitted = True
            self.fit()


class ElideLabel(QLabel):
    """A one-line label that shortens its text with "..." to fit whatever width it is given."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(48)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._refit()

    def full_text(self) -> str:
        return self._full

    def _refit(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(self.width() - 2, 20)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refit()

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 6, super().sizeHint().height())
