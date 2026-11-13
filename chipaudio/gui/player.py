"""Audio playback.

On Windows this uses the built-in winsound module, so nothing extra has to
be installed. Playback position is tracked with a clock: the sound is
written to a temporary WAV starting at the requested position and played
from there.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from ..export import wav_bytes

try:                                   # Windows only
    import winsound
except ImportError:                    # pragma: no cover - other systems
    winsound = None


class Player:
    def __init__(self):
        self._dir = Path(tempfile.mkdtemp(prefix="chipaudio_"))
        self._n = 0
        self._proc: subprocess.Popen | None = None
        self._t0 = 0.0
        self._start = 0
        self._frames = 0
        self._rate = 1
        self.playing = False
        self._tool = None
        if winsound is None:
            for tool in ("afplay", "paplay", "aplay"):
                if shutil.which(tool):
                    self._tool = tool
                    break

    @property
    def available(self) -> bool:
        return winsound is not None or self._tool is not None

    def play(self, x: np.ndarray, rate: int, start: int = 0) -> bool:
        """Play int16 audio shaped (frames, channels) from frame `start`."""
        self.stop()
        if not self.available or len(x) == 0:
            return False
        start = max(0, min(int(start), len(x) - 1))
        self._n += 1
        path = self._dir / f"play{self._n % 4}.wav"
        try:
            path.write_bytes(wav_bytes(x[start:], rate))
            if winsound is not None:
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            else:
                self._proc = subprocess.Popen([self._tool, str(path)], stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
        except (OSError, RuntimeError):
            return False
        self._t0 = time.monotonic()
        self._start, self._frames, self._rate = start, len(x), max(int(rate), 1)
        self.playing = True
        return True

    def position(self) -> int:
        """Current frame. Stops itself when the end is reached."""
        if not self.playing:
            return self._start
        pos = self._start + int((time.monotonic() - self._t0) * self._rate)
        if pos >= self._frames:
            self.playing = False
            self._start = 0
            return 0
        return pos

    def stop(self) -> None:
        if self.playing:
            self._start = self.position()
        self.playing = False
        if winsound is not None:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except RuntimeError:
                pass
        if self._proc is not None:
            self._proc.terminate()
            self._proc = None

    def rewind(self) -> None:
        self._start = 0

    def set_position(self, frame: int) -> None:
        """Set where the next play() resumes from. Only meaningful while stopped."""
        self._start = max(int(frame), 0)

    def close(self) -> None:
        self.stop()
        shutil.rmtree(self._dir, ignore_errors=True)


def why_no_sound() -> str:
    if sys.platform == "win32":
        return "Windows could not play the sound. Check that an audio output device is connected."
    return ("No command-line audio player was found on this system. On Windows, playback uses the "
            "built-in sound system and needs nothing extra.")
