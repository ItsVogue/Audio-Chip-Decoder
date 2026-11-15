# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""Run slow jobs off the UI thread."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    progress = Signal(int, int, str)
    finished_ok = Signal(object)
    failed = Signal(str)
    failed_exc = Signal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.finished_ok.emit(self._fn(lambda i, n, t="": self.progress.emit(int(i), int(n), t)))
        except Exception as e:                      # shown to the user; never crash the app
            self.failed_exc.emit(e)
            self.failed.emit(f"{type(e).__name__}: {e}")
