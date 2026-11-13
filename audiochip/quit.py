"""Stop the process at once, without running any shutdown code.

Used when the app's window has closed and when a test run has reported its results.
At that point everything is saved, but Qt objects are still alive, and the normal ways of
ending a process on Windows unload each library in turn; Qt's library then destroys its
internals underneath those objects and the process ends with a crash code (seen on the
build machine as 0xC0000005 and 0xC0000409, after every test had passed). Ending the
process directly skips that step.
"""
from __future__ import annotations

import os
import sys


def stop_now(code: int = 0) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except Exception:
            pass
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), int(code) & 0xFFFFFFFF)
    os._exit(int(code))
