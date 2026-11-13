"""Build-machine probe: which combination makes the process end with a non-zero exit code?"""
import subprocess
import sys

CASES = {
    "qt only": "from PySide6.QtWidgets import QApplication; a = QApplication([])",
    "qt + clipboard": "from PySide6.QtWidgets import QApplication; a = QApplication([]); "
                      "QApplication.clipboard().setText('x')",
    "qt + sounddevice": "from PySide6.QtWidgets import QApplication; a = QApplication([]); "
                        "import sounddevice as sd; print(len(sd.query_devices()))",
    "qt + sounddevice, terminated first": "from PySide6.QtWidgets import QApplication; a = QApplication([]); "
                                          "import sounddevice as sd, atexit; print(len(sd.query_devices())); "
                                          "atexit.unregister(sd._exit_handler); sd._terminate()",
    "sounddevice then qt": "import sounddevice as sd; from PySide6.QtWidgets import QApplication; "
                           "a = QApplication([]); print(len(sd.query_devices()))",
    "qt + liveplayer": "import sys; sys.path.insert(0, '.'); from PySide6.QtWidgets import QApplication; "
                       "a = QApplication([]); from audiochip.gui.player import LivePlayer; p = LivePlayer(); "
                       "print(p.available, p.error)",
}
for name, code in CASES.items():
    r = subprocess.run([sys.executable, "-X", "faulthandler", "-c", code], capture_output=True, text=True)
    print(f"{name!r}: exit {r.returncode} out={r.stdout.strip()!r} err={r.stderr.strip()[-300:]!r}", flush=True)
