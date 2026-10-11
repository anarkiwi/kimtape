"""The KIM-1 ROM monitor as seen down a TTY line.

Commands, uppercase only:

====================  ==================================================
``AAAA<space>``       open an address and show its contents
``DD.``               deposit a byte and advance
``CR``                advance and show
``L``                 load a paper tape
``Q``                 punch a paper tape from the open address to EAL/EAH
``G``                 execute from the open address
====================  ==================================================

The monitor pads its CR/LF with NUL fill characters, sized for a real
teletype's carriage, so output never follows a newline immediately.
"""

import re
import sys
import time

from . import timing
from .tape import RECORD, Tape, checksum, end_record

SAL = 0x17F5  # monitor scratch: start lo/hi then end lo/hi
EAL = 0x17F7
CNTL30 = 0x17F2  # the monitor's bit time, CNTH30 after it
PROMPT = re.compile(rb"([0-9A-F]{4}) ([0-9A-F]{2})")
FLUSH = b";0000000000"  # an end record, to close whatever L was reading


class Monitor:  # pylint: disable=too-many-instance-attributes
    """Drives one Port through the monitor's command set."""

    def __init__(self, port, log=None, record=timing.RECORD, lock=False):
        # resolved here rather than as a default argument, so that a caller who
        # redirects sys.stderr after import still gets its output captured
        self.p, self.log = port, sys.stderr if log is None else log
        self.record, self.lock, self.frame = record, lock, None
        self.home, self.count = port.baud, None

    def say(self, fmt, *args):
        """Write a diagnostic line to the log stream."""
        self.log.write((fmt % args if args else fmt) + "\n")
        self.log.flush()

    def ping(self):
        """Send CR; True if the monitor answers it."""
        self.p.take()
        self.p.write(b"\r")
        self.p.drain()
        reply = self.p.take()
        if b"KIM" in reply or PROMPT.search(reply):
            self.say("monitor: %s", reply.strip().decode("ascii", "replace"))
            return True
        return False

    def await_monitor(self, seconds):
        """Ping until the monitor answers or seconds pass; True if it did."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.ping():
                return True
            time.sleep(0.2)
        return False

    def sync(self, tries=3):
        """Wake the monitor.  The first CR after RS is what sets its bit rate."""
        for attempt in range(1, tries + 1):
            if self.ping():
                return
            self.say("no response (%d/%d) - press RS on the board", attempt, tries)
        raise SystemExit(
            "kimtape: no response from the monitor.  Check the TTY jumper "
            "(application connector V to 21), the TX/RX wiring and the baud "
            "rate, then press RS and retry."
        )

    def open_addr(self, addr):
        """Open an address so the next command acts on it."""
        self.p.write(("%04X " % addr).encode())
        self.p.drain()

    def deposit(self, addr, data):
        """Write bytes from addr, advancing as it goes.

        Each deposit draws a fresh prompt, and the monitor cannot receive while
        it prints one, so every byte waits for the line to go quiet.
        """
        self.open_addr(addr)
        for byte in data:
            self.p.write(("%02X." % byte).encode())
            self.p.drain()

    def rate(self, baud=None):
        """Run the monitor at baud, by default the rate it was synced at, and pace.

        Depositing CNTL30 sets the monitor's bit time without a reset; kimtape
        writes the count closest to the rate, then streams if a frame allows.
        """
        baud = baud or self.home
        if self.count is None:
            got = self.read_back(CNTL30, CNTL30 + 1)
            if CNTL30 not in got or got.get(CNTL30 + 1) != 0:
                self.say("  cannot read CNTL30/CNTH30; writing lock-step")
                return
            self.count = got[CNTL30]
        want = timing.best_count(timing.bit_time(baud))
        if (want, baud) != (self.count, self.p.baud):
            self.open_addr(CNTL30)
            self.p.write(b"%02X." % want)
            self.p.set_rate(baud)
            self.count = want
            self.p.drain()
            if not self.ping():
                raise SystemExit(
                    "kimtape: no answer at %d baud; press RS and retry" % baud
                )
        frame = self.frame or timing.frame_bits(baud, want)
        stream = frame is not None and not self.lock
        self.p.set_rate(baud, frame if stream else 10)
        self.p.pacing.stream = stream
        self.say(
            "monitor at %d baud (CNTL30 $%02X), %s",
            baud,
            want,
            "streaming %d bit frames" % frame if stream else "lock-step",
        )

    def load(self, tape, tries=3):
        """Send a tape with the L command; True once the monitor acknowledges it.

        During L the monitor sends nothing until KIM at the end, or ERR as it
        leaves L, so a differing echo or a missing KIM is a failure; the load
        resumes two records before it, lock-step.
        """
        records = Tape.from_memory(tape.name, tape.mem, self.record).records
        self.say("loading %s", tape)
        first = 0
        for _ in range(tries):
            first = self._pass(records, first)
            if first is None:
                return True
            self.say(
                "\n  failed; resuming at record %d of %d, lock-step",
                first + 1,
                len(records),
            )
            self.p.pacing.stream = False
        return False

    def _pass(self, records, first):
        """Send records from first under one L; None if acknowledged, else a resume."""
        self.p.take()
        began, sent = time.time(), 0
        if self.p.write(b"L") is not None:
            self._recover()
            return first
        for n in range(first, len(records)):
            if self.p.write(records[n]) is not None:
                self._recover()
                return max(first, n - 2)
            sent += int(records[n][1:3], 16)
            self.log.write(
                "\r  record %d/%d, %.0f bytes/s"
                % (n + 1, len(records), sent / max(time.time() - began, 1e-3))
            )
            self.log.flush()
        mark, end = len(self.p.rx), end_record(len(records) - first)
        bad = self.p.write(end)
        self.p.drain()
        reply = bytes(self.p.rx[mark + len(end) :])
        if bad is None and b"KIM" in reply and b"ERR" not in reply:
            self.log.write("\n")
            self.p.take()
            return None
        self._recover()
        return max(first, len(records) - 2) if bad is not None or reply else first

    def _recover(self):
        """Bring the monitor back to its prompt, closing an unfinished L, lock-step."""
        self.p.pacing.stream = False
        for _ in range(2 * timing.RECORD // len(FLUSH) + 2):
            self.p.drain()
            if self.ping():
                return
            self.p.write(FLUSH)
        raise SystemExit("kimtape: the monitor stopped answering; press RS and retry")

    def spot(self, tape):
        """Check the last byte of each record, which a lost record would leave stale."""
        for record in Tape.from_memory(tape.name, tape.mem, self.record).records:
            addr = int(record[3:7], 16) + int(record[1:3], 16) - 1
            if self.read_back(addr, addr).get(addr) != tape.mem[addr]:
                self.say("  $%04X did not load", addr)
                return False
        return True

    def punch(self, lo, hi):
        """Run the monitor's paper tape punch over $lo-$hi; return {addr: byte}."""
        self.deposit(EAL, bytes([(hi + 1) & 0xFF, (hi + 1) >> 8]))
        self.open_addr(lo)
        self.p.take()
        self.p.write(b"Q")
        budget = time.time() + 30 + (hi - lo + 1) * 3 * self.p.char_time
        while time.time() < budget:
            self.p.poll(0.25)
            if re.search(rb";00[0-9A-F]{8}", bytes(self.p.rx)):
                break
        self.p.drain()
        mem = {}
        for match in RECORD.finditer(self.p.take()):
            count, addr = int(match.group(1), 16), int(match.group(2), 16)
            data = bytes.fromhex(match.group(3).decode())
            if count and len(data) == count:
                if checksum(count, addr, data) == int(match.group(4), 16):
                    mem.update(zip(range(addr, addr + count), data))
        return mem

    def read_back(self, lo, hi):
        """Read $lo-$hi by opening the address and stepping with CR.

        Slower than the punch, but it uses only the two commands the monitor is
        proven to answer, so it works where Q does not.
        """
        self.p.take()
        self.open_addr(lo)
        seen = bytearray(self.p.take())
        for _ in range(hi - lo):
            self.p.write(b"\r")
            self.p.drain()
            seen += self.p.take()
        mem = {}
        for match in PROMPT.finditer(bytes(seen)):
            addr = int(match.group(1), 16)
            if lo <= addr <= hi:
                mem[addr] = int(match.group(2), 16)
        return mem

    def verify(self, tape):
        """True if the range matches, False if it differs, None if unreadable."""
        lo, hi = tape.span
        self.say("verifying $%04X-$%04X", lo, hi)
        got = self.punch(lo, hi)
        if not got:
            self.say("  the punch (Q) returned no records; reading back by stepping")
            got = self.read_back(lo, hi)
        if not got:
            self.say(
                "  nothing read back at all - the board is not answering the "
                "read commands, which says nothing about the load itself"
            )
            return None
        bad = [a for a, v in sorted(tape.mem.items()) if got.get(a) != v]
        if not bad:
            self.say("  ok, %d bytes match", len(tape.mem))
            return True
        self.say(
            "  %d of %d bytes wrong (%d never read back), first at $%04X",
            len(bad),
            len(tape.mem),
            sum(1 for a in bad if a not in got),
            bad[0],
        )
        if len(bad) > len(tape.mem) // 2 and lo >= 0x0400:
            self.say(
                "  losing most of a range above $0400 means that RAM is not "
                "decoded: on a PAL-II set SW4 (K1-K4) ON and SW5 pos 1 (8K0) OFF"
            )
        return False

    def go(self, addr):
        """Start execution at addr."""
        self.say("running from $%04X", addr)
        self.open_addr(addr)
        self.p.write(b"G")
        self.p.drain()
