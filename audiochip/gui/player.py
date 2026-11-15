# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
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
        self._dir = Path(tempfile.mkdtemp(prefix="audiochip_"))
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


def render_block(x: np.ndarray, pos: float, step: float, n: int, loop: bool):
    """Next n output frames of x, read at `step` source frames per output frame.

    This is what makes speed changes gapless: the sound card keeps running at its own rate
    and the clip is simply read faster or slower. Values between two source samples are
    interpolated. Returns (float32 block shaped (n, channels), new position, reached the end).
    """
    frames = len(x)
    out = np.zeros((n, x.shape[1]), dtype=np.float32)
    if frames < 2 or step <= 0:
        return out, pos, True
    idx = pos + step * np.arange(n)
    ended = False
    if loop:
        idx = np.mod(idx, frames)
        new_pos = float((pos + step * n) % frames)
        valid = n
    else:
        valid = int(np.searchsorted(idx, frames - 1, side="left"))
        ended = valid < n
        idx = idx[:valid]
        new_pos = float(pos + step * n)
    if valid:
        i0 = np.floor(idx).astype(np.int64)
        frac = (idx - i0).astype(np.float32)[:, None]
        i1 = (i0 + 1) % frames if loop else np.minimum(i0 + 1, frames - 1)
        out[:valid] = (x[i0] * (1.0 - frac) + x[i1] * frac) / 32768.0
    return out, new_pos, ended


class LivePlayer:
    """Plays a clip through a continuous stream so its speed can change without a gap.

    Uses the sounddevice package (PortAudio). When that is missing, or the PC has no sound
    output, `available` is False and the caller falls back to the ordinary Player.
    """

    def __init__(self):
        self._sd = None
        self._stream = None
        self._x = None
        self._pos = 0.0
        self._step = 1.0
        self._device_rate = 48000
        self.loop = True
        self.playing = False
        self.error = ""
        try:
            import sounddevice as sd
            info = sd.query_devices(kind="output")
            self._device_rate = int(info.get("default_samplerate") or 48000)
            self._sd = sd
        except Exception as e:                       # package missing, PortAudio missing, or no output device
            self.error = str(e)

    @property
    def available(self) -> bool:
        return self._sd is not None

    def _callback(self, outdata, frames, _time, _status):
        x = self._x
        if x is None or not self.playing:
            outdata.fill(0)
            return
        block, self._pos, ended = render_block(x, self._pos, self._step, frames, self.loop)
        outdata[:] = block if block.shape[1] == outdata.shape[1] else np.repeat(block[:, :1], outdata.shape[1], axis=1)
        if ended:
            self.playing = False

    def start(self, x: np.ndarray, rate: int, pos: int = 0) -> bool:
        self.stop()
        if not self.available or len(x) < 2:
            return False
        self._x = x if x.ndim == 2 else x.reshape(-1, 1)
        self._pos = float(max(0, min(pos, len(x) - 2)))
        self.set_rate(rate)
        try:
            self._stream = self._sd.OutputStream(samplerate=self._device_rate, channels=2, dtype="float32",
                                                 callback=self._callback, latency="low")
            self.playing = True
            self._stream.start()
        except Exception as e:
            self.error = str(e)
            self.playing = False
            self._stream = None
            return False
        return True

    def set_rate(self, rate: float) -> None:
        self._step = float(rate) / self._device_rate

    def position(self) -> int:
        return int(self._pos)

    def stop(self) -> None:
        self.playing = False
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def close(self) -> None:
        self.stop()


def why_no_sound() -> str:
    if sys.platform == "win32":
        return "Windows could not play the sound. Check that an audio output device is connected."
    return ("No command-line audio player was found on this system. On Windows, playback uses the "
            "built-in sound system and needs nothing extra.")
