"""A pin-level stand-in for an FT232H in MPSSE mode with a 24-series I2C EEPROM wired to it.

It executes the MPSSE command bytes the reader sends (pin sets, clocked bytes and bits), moves
the two bus lines accordingly, and runs an EEPROM that reacts to the lines the way the I2C
rules say: start and stop conditions, bits taken on the rising clock edge, the chip changing
its output on the falling edge. Anything a real bus would object to is counted:

    contention   the adapter drove the data line high while the chip was pulling it low
    hazards      both lines changed in one step, or data changed during a clocked high period
    writes       a data byte was written to the chip (the reader must never do that)
"""
from __future__ import annotations


class MpsseI2cSim:
    def __init__(self, memory: bytes, addr_bytes: int, open_drain_capable: bool = True):
        self.mem = bytes(memory)
        self.addr_bytes = addr_bytes
        self.od_capable = open_drain_capable
        self.od_mask = 0
        self.scl = 1
        self.m_val, self.m_dir = 1, 0            # adapter's data pin: level and whether it is an output
        self.s_sda = 1                           # chip's data pin: 1 = let go, 0 = pulling low
        self.contention = self.hazards = self.writes = 0
        self.three_phase = False
        self.divisor = None
        self.starts = self.stops = 0
        self.unknown: list[int] = []
        # chip state
        self.active = False
        self.mode = "idle"                       # "rx" receiving a byte, "ack" acknowledging, "tx" sending, "mack"
        self.shift = self.bits = 0
        self.ptr = 0
        self.got_dev = False
        self.addr_left = 0
        self.read_mode = False
        self.block = 0

    # ----------------------------------------------------------------- lines
    def _m_level(self) -> int:
        if not self.m_dir:
            return 1
        if self.m_val and (self.od_mask & 2):
            return 1
        return self.m_val

    def sda(self) -> int:
        return min(self._m_level(), self.s_sda)

    def _check(self) -> None:
        if self.m_dir and self.m_val and not (self.od_mask & 2) and not self.s_sda:
            self.contention += 1

    def _set(self, scl: int, m_val: int, m_dir: int) -> None:
        before = self.sda()
        old_scl = self.scl
        self.m_val, self.m_dir = m_val, m_dir
        after = self.sda()
        if scl != old_scl and after != before:
            self.hazards += 1
        if old_scl and scl and after != before:
            if after < before:
                self._start()
            else:
                self._stop()
        self.scl = scl
        if scl and not old_scl:
            self._rise()
        elif old_scl and not scl:
            self._fall()
        self._check()

    # ------------------------------------------------------------------ chip
    def _start(self) -> None:
        self.starts += 1
        self.active, self.mode, self.shift, self.bits, self.got_dev = True, "rx", 0, 0, False
        self.s_sda = 1

    def _stop(self) -> None:
        self.stops += 1
        self.active, self.mode = False, "idle"
        self.s_sda = 1

    def _rise(self) -> None:
        if not self.active:
            return
        if self.mode == "rx":
            self.shift = ((self.shift << 1) | self.sda()) & 0xFF
            self.bits += 1
        elif self.mode == "mack":
            self.mode = "tx_next" if self.sda() == 0 else "idle_until_stop"

    def _fall(self) -> None:
        if not self.active:
            return
        if self.mode == "rx" and self.bits == 8:
            self.bits = 0
            self.s_sda = 0 if self._byte(self.shift) else 1
            self.mode = "ack" if not self.s_sda else "idle_until_stop"
        elif self.mode == "ack":
            self.s_sda = 1
            if self.read_mode:
                self._load()
            else:
                self.mode = "rx"
        elif self.mode == "tx":
            self.bits += 1
            if self.bits == 8:
                self.s_sda = 1
                self.mode = "mack"
            else:
                self.s_sda = (self.cur >> (7 - self.bits)) & 1
        elif self.mode == "tx_next":
            self._load()
        elif self.mode == "idle_until_stop":
            self.s_sda = 1

    def _load(self) -> None:
        self.cur = self.mem[self.ptr % len(self.mem)]
        self.ptr = (self.ptr + 1) % len(self.mem)
        self.bits = 0
        self.mode = "tx"
        self.s_sda = (self.cur >> 7) & 1

    def _byte(self, value: int) -> bool:
        """A whole byte arrived. True to acknowledge it."""
        if not self.got_dev:
            if (value >> 4) != 0x0A:
                return False
            block_bits = max(0, (len(self.mem) - 1).bit_length() - 8 * self.addr_bytes)
            block = (value >> 1) & 7
            if block >> block_bits:
                return False                       # addressed to another chip on the bus
            self.got_dev = True
            self.read_mode = bool(value & 1)
            if not self.read_mode:
                self.block = block
                self.addr_left = self.addr_bytes
                self.new_addr = 0
            return True
        if self.addr_left:
            self.new_addr = (self.new_addr << 8) | value
            self.addr_left -= 1
            if not self.addr_left:
                self.ptr = ((self.block << (8 * self.addr_bytes)) | self.new_addr) % len(self.mem)
            return True
        self.writes += 1
        return True

    # -------------------------------------------------------------- commands
    def run(self, commands: bytes) -> bytes:
        out = bytearray()
        c = bytes(commands)
        i = 0
        while i < len(c):
            op = c[i]
            if op == 0x80:
                val, direction = c[i + 1], c[i + 2]
                self._set(val & 1 if direction & 1 else 1, (val >> 1) & 1, (direction >> 1) & 1)
                i += 3
            elif op in (0x11, 0x13):               # bytes / bits out, most significant first
                if op == 0x11:
                    n = (c[i + 1] | (c[i + 2] << 8)) + 1
                    data, i = c[i + 3:i + 3 + n], i + 3 + n
                    bits = [(b >> k) & 1 for b in data for k in range(7, -1, -1)]
                else:
                    n = c[i + 1] + 1
                    bits = [(c[i + 2] >> (7 - k)) & 1 for k in range(n)]
                    i += 3
                for bit in bits:
                    self._set(0, bit, self.m_dir)
                    self._set(1, bit, self.m_dir)
                    self._set(0, bit, self.m_dir)
            elif op in (0x20, 0x22):               # bytes / bits in, taken on the rising edge
                if op == 0x20:
                    n, i = ((c[i + 1] | (c[i + 2] << 8)) + 1) * 8, i + 3
                else:
                    n, i = c[i + 1] + 1, i + 2
                value = 0
                for k in range(n):
                    self._set(1, self.m_val, self.m_dir)
                    value = ((value << 1) | self.sda()) & 0xFF
                    self._set(0, self.m_val, self.m_dir)
                    if op == 0x20 and k % 8 == 7:
                        out.append(value)
                        value = 0
                if op == 0x22:
                    out.append(value)
            elif op == 0x86:
                self.divisor = c[i + 1] | (c[i + 2] << 8)
                i += 3
            elif op == 0x9E:
                if not self.od_capable:
                    out += bytes([0xFA, op])        # this chip does not know the command
                else:
                    self.od_mask = c[i + 1]
                i += 3
            elif op in (0x8C, 0x8D):
                self.three_phase = op == 0x8C
                i += 1
            elif op in (0x8A, 0x8B, 0x97, 0x96, 0x85, 0x84, 0x87):
                i += 1
            else:
                self.unknown.append(op)
                out += bytes([0xFA, op])
                i += 1
        return bytes(out)
