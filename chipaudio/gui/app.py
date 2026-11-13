"""Start the desktop app."""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("The desktop app needs PySide6. Install it with:  pip install PySide6-Essentials", file=sys.stderr)
        return 1
    from .main_window import MainWindow
    from .style import apply_dark

    def report(exc_type, exc, tb):
        """Never die silently: keep a log and tell the user where it is."""
        import traceback
        from PySide6.QtWidgets import QMessageBox
        from ..profiles import config_dir
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        where = config_dir() / "error.log"
        try:
            where.parent.mkdir(parents=True, exist_ok=True)
            with open(where, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            where = None
        if sys.stderr:
            print(text, file=sys.stderr)
        QMessageBox.warning(None, "Chip Audio Decoder",
                            f"Something went wrong:\n\n{exc_type.__name__}: {exc}\n\n"
                            + (f"Details were saved to:\n{where}" if where else ""))

    sys.excepthook = report
    app = QApplication(argv)
    app.setApplicationName("Chip Audio Decoder")
    app.setOrganizationName("ChipAudioDecoder")
    apply_dark(app)
    from pathlib import Path
    from PySide6.QtGui import QIcon
    icon = Path(__file__).with_name("icon.ico")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    win = MainWindow()
    win.show()
    paths = [a for a in argv[1:] if not a.startswith("-")]
    if paths:
        win.load_paths(paths)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
