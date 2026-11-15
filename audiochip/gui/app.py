# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""Start the desktop app."""
from __future__ import annotations

import sys


def selftest(argv: list[str]) -> int:
    """Start everything once without showing anything, write what worked to a file, and exit.

    Used after building the standalone program, to prove on a real Windows PC that the
    window opens, a decode runs, and each bundled library and helper program loads.
    Usage: AudioChipDecoder.exe --selftest report.txt
    """
    import os
    import traceback
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["AUDIOCHIP_NO_UPDATE_CHECK"] = "1"
    i = argv.index("--selftest")
    report = argv[i + 1] if len(argv) > i + 1 else "selftest.txt"
    lines, failed = [], 0

    def step(name, fn):
        nonlocal failed
        try:
            lines.append(f"ok    {name}: {fn()}")
        except Exception:
            failed += 1
            lines.append(f"FAIL  {name}:\n{traceback.format_exc()}")

    def window():
        from PySide6.QtWidgets import QApplication
        from .main_window import MainWindow
        from .style import apply_dark
        app = QApplication.instance() or QApplication(argv[:1])
        apply_dark(app)
        w = MainWindow()
        w.show()
        app.processEvents()
        w.close()
        return "main window built"

    def decode():
        import numpy as np
        from ..detect import detect
        from ..structure import analyse_structure
        t = np.arange(40000)
        x = (np.sin(t / 9.0) * 90 + 128 + np.sin(t / 2.3) * 20).astype(np.uint8).tobytes()
        det = detect(x, analyse_structure(x))
        return f"detected {det.best.hyp.key} ({det.confidence})"

    def flac_mp3():
        import numpy as np
        from ..export import audio_bytes
        x = (np.sin(np.arange(8000) / 7.0) * 9000).astype(np.int16)
        return f"FLAC {len(audio_bytes(x, 8000, 'flac')[0])} bytes, MP3 {len(audio_bytes(x, 8000, 'mp3')[0])} bytes"

    def usb():
        import libusb_package
        import usb.core
        backend = libusb_package.get_libusb1_backend()
        if backend is None:
            raise RuntimeError("libusb did not load")
        return f"libusb loaded, CH341A present: {usb.core.find(idVendor=0x1A86, idProduct=0x5512, backend=backend) is not None}"

    def live_audio():
        import sounddevice as sd
        outputs = [d for d in sd.query_devices() if d.get("max_output_channels", 0) > 0]
        return f"PortAudio loaded ({sd.get_portaudio_version()[1]}), {len(outputs)} sound output(s)"

    def serial_ports():
        from ..readers.serprog import list_ports
        return f"{len(list_ports())} serial port(s)"

    def minipro():
        from ..readers import xgecu
        from ..readers.base import NoDevice
        family = next(iter(xgecu._index()))
        hits = xgecu.search(family, "27C256")
        if not hits:
            raise RuntimeError("the chip list is empty")
        try:
            state = "programmer found: " + str(xgecu.presence()[0])
        except NoDevice:
            state = "helper program ran; no programmer plugged in"
        except Exception:
            if sys.platform == "win32":             # on Windows the helper must at least start
                raise
            state = "helper program is Windows-only"
        return f"{len(hits)} chips match 27C256; {state}"

    def a1800_helper():
        from .. import a1800
        if not a1800.available():
            raise RuntimeError("a18helper/a1800.exe is missing")
        frames = bytes.fromhex(
            "83ca930719acfb47035169a7920c6f4ec79e972b3f2e8926c17092e6138dd96ac9db00f800000000"
            "83c2a71f5458f9e7d5536ab3f6427390077ff005cf5eff50dbc197c928c900000000000000000000"
            "c5cc040f8f65fc9fd44bb0b76885f9a49b924060af646096acb7989fff843c300000000000000000")
        x = a1800.decode_frames(frames)
        if len(x) != 960:
            raise RuntimeError(f"unexpected result: {len(x)} samples")
        return f"helper program ran and decoded {len(x):,} samples"

    def ffmpeg():
        import numpy as np
        from .. import ffcodecs
        if not ffcodecs.available():
            raise RuntimeError("ffhelper/ffmpeg.exe is missing")
        stream = (np.arange(4000) * 37 % 251).astype(np.uint8).tobytes()      # any bytes decode as G.726
        x, rate, _ = ffcodecs.decode_file(stream, "g726_16")
        if rate != 8000 or len(x) < 15000:
            raise RuntimeError(f"unexpected result: {len(x)} samples at {rate} Hz")
        return f"helper program ran and decoded {len(x):,} samples"

    for name, fn in (("window", window), ("decode", decode), ("flac and mp3", flac_mp3), ("usb", usb),
                     ("gapless audio", live_audio), ("serial ports", serial_ports), ("xgecu helper", minipro),
                     ("ffmpeg helper", ffmpeg), ("a1800 helper", a1800_helper)):
        step(name, fn)
    try:
        with open(report, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("The desktop app needs PySide6. Install it with:  pip install PySide6-Essentials", file=sys.stderr)
        return 1
    from .main_window import MainWindow
    from .style import apply_dark

    if "--selftest" in argv:
        return selftest(argv)

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
        QMessageBox.warning(None, "Audio Chip Decoder",
                            f"Something went wrong:\n\n{exc_type.__name__}: {exc}\n\n"
                            + (f"Details were saved to:\n{where}" if where else ""))

    sys.excepthook = report
    app = QApplication(argv)
    app.setApplicationName("Audio Chip Decoder")
    app.setOrganizationName("AudioChipDecoder")
    apply_dark(app)
    from pathlib import Path
    from PySide6.QtGui import QIcon
    icon = Path(__file__).with_name("icon.ico")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    win = MainWindow()
    win.show_fitted()
    paths = [a for a in argv[1:] if not a.startswith("-")]
    if paths:
        win.load_paths(paths)
    from PySide6.QtCore import QTimer
    QTimer.singleShot(1500, win.check_for_update)
    return app.exec()


def run(argv: list[str] | None = None) -> None:
    """Run the app as a program and end the process the moment it returns.

    Python and Qt each tear their objects down at exit, in orders that do not always agree;
    on Windows that occasionally ended the process with a crash code after the window had
    already closed. Everything that needs saving is saved when the window closes, so the
    process simply stops here instead.
    """
    from ..quit import stop_now
    stop_now(int(main(argv) or 0))


if __name__ == "__main__":
    run()
