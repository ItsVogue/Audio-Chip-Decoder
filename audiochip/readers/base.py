# Audio Chip Decoder. Copyright (c) 2026 ItsVogue. All rights reserved.
"""Shared pieces for all readers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

Progress = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


class ReaderError(Exception):
    """A problem the user can act on. The message is shown as written."""


class DriverMissing(ReaderError):
    pass


class NoDevice(ReaderError):
    pass


class Cancelled(ReaderError):
    pass


@dataclass
class VerifyReport:
    passes: int = 1
    identical: bool = True
    differing_bytes: int = 0          # positions where the first two reads disagreed
    unresolved_bytes: int = 0         # positions where no two reads agreed
    notes: list[str] = field(default_factory=list)

    @property
    def trustworthy(self) -> bool:
        return self.unresolved_bytes == 0

    def summary(self) -> str:
        if self.passes == 1:
            return "Read once, not verified."
        if self.identical:
            return f"Read {self.passes} times; every read was identical."
        if self.unresolved_bytes == 0:
            return (f"The first two reads differed in {self.differing_bytes:,} bytes. A third read settled every "
                    "one of them by majority. The contact is marginal: reseat the chip or clip.")
        return (f"The reads disagree in {self.unresolved_bytes:,} bytes even after three tries. "
                "This dump is not reliable. Reseat the chip or clip, check the voltage, and read again.")


def read_verified(read_once: Callable[[Progress | None], bytes], progress: Progress | None = None,
                  verify: bool = True) -> tuple[bytes, VerifyReport]:
    """Read once, or read twice and compare, with a third read to settle differences."""
    def stage(label: str) -> Progress | None:
        if progress is None:
            return None
        return lambda i, n, _t="": progress(i, n, label)

    first = read_once(stage("Reading the chip" if not verify else "Reading the chip (1st pass)"))
    if not verify:
        return first, VerifyReport(passes=1)
    second = read_once(stage("Reading again to verify (2nd pass)"))
    if first == second:
        return first, VerifyReport(passes=2, identical=True)
    a = np.frombuffer(first, dtype=np.uint8)
    b = np.frombuffer(second, dtype=np.uint8)
    n = min(len(a), len(b))
    diff = int(np.count_nonzero(a[:n] != b[:n])) + abs(len(a) - len(b))
    third = read_once(stage("Reads differ: reading a 3rd time to settle it"))
    c = np.frombuffer(third, dtype=np.uint8)
    n = min(n, len(c))
    a, b, c = a[:n], b[:n], c[:n]
    out = np.where(a == b, a, np.where(a == c, a, b))
    unresolved = int(np.count_nonzero((a != b) & (a != c) & (b != c)))
    return out.tobytes(), VerifyReport(passes=3, identical=False, differing_bytes=diff,
                                       unresolved_bytes=unresolved)
