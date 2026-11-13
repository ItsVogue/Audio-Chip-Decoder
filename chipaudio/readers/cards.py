"""Memory cards and USB drives: read the raw contents through a card reader. Read-only."""
from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass

from .base import Cancelled, CancelCheck, Progress, ReaderError

BLOCK = 1 << 20
SIZE_CHOICES = [(16 << 20, "first 16 MB"), (64 << 20, "first 64 MB"), (256 << 20, "first 256 MB")]


@dataclass
class Drive:
    letter: str        # "E"
    label: str
    size: int          # bytes; 0 if unknown

    @property
    def path(self) -> str:
        return rf"\\.\{self.letter}:"

    def describe(self) -> str:
        from .chips import human_size
        size = human_size(self.size) if self.size else "size unknown"
        return f"{self.letter}:  {self.label or 'no name'}  ({size})"


def list_removable() -> list[Drive]:
    """Removable drives Windows can see right now (card readers, USB sticks)."""
    if sys.platform != "win32":
        return []
    k = ctypes.windll.kernel32
    mask = k.GetLogicalDrives()
    out = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        letter = chr(65 + i)
        root = f"{letter}:\\"
        if k.GetDriveTypeW(root) != 2:               # DRIVE_REMOVABLE
            continue
        label = ctypes.create_unicode_buffer(261)
        k.GetVolumeInformationW(root, label, 261, None, None, None, None, 0)
        total = ctypes.c_ulonglong(0)
        k.GetDiskFreeSpaceExW(root, None, ctypes.byref(total), None)
        out.append(Drive(letter, label.value, int(total.value)))
    return out


def is_admin() -> bool:
    if sys.platform != "win32":
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin() -> bool:
    """Start a second copy of the app with administrator rights. True if Windows agreed."""
    if sys.platform != "win32":
        return False
    args = "-m chipaudio" if not getattr(sys, "frozen", False) else ""
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, os.getcwd(), 1)
    return rc > 32


def read_raw(path: str, limit: int, progress: Progress | None = None, cancel: CancelCheck | None = None) -> bytes:
    """Read up to `limit` bytes from the start of a drive or image file, in whole blocks."""
    out = bytearray()
    try:
        f = open(path, "rb", buffering=0)
    except PermissionError:
        raise ReaderError("Windows only lets an administrator read a card's raw contents. "
                          "Restart the app as administrator and read the card again.") from None
    except OSError as e:
        raise ReaderError(f"The drive could not be opened: {e.strerror or e}. Is a card in the reader?") from None
    with f:
        while len(out) < limit:
            if cancel and cancel():
                raise Cancelled("Reading was cancelled.")
            try:
                block = f.read(min(BLOCK, limit - len(out)))
            except OSError as e:
                if out:                      # reading past the end of a volume reports an error
                    break
                raise ReaderError(f"The drive could not be read: {e.strerror or e}.") from None
            if not block:
                break
            out += block
            if progress:
                progress(len(out), limit, "Reading the card")
    if not out:
        raise ReaderError("Nothing could be read from the drive.")
    return bytes(out)
