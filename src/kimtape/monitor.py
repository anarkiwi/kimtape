"""The KIM-1 ROM monitor as seen down a TTY line.

Commands, uppercase only:

====================  ==================================================
``AAAA<space>``       open an address and show its contents
``DD.``               deposit a byte and advance
``CR``                advance and show
``L``                 load a paper tape
``Q``                 punch a paper tape over SAL/SAH to EAL/EAH
``G``                 execute from the open address
====================  ==================================================

The monitor pads its CR/LF with NUL fill characters, sized for a real
teletype's carriage, so output never follows a newline immediately.
"""

import re
import sys
import time

from .tape import RECORD, checksum

SAL = 0x17F5  # monitor scratch: start lo/hi then end lo/hi
PROMPT = re.compile(rb"([0-9A-F]{4}) ([0-9A-F]{2})")


class Monitor:
    """Drives one Port through the monitor's command set."""

    def __init__(self, port, log=None):
        # resolved here rather than as a default argument, so that a caller who
        # redirects sys.stderr after import still gets its output captured
        self.p, self.log = port, sys.stderr if log is None else log

    def say(self, fmt, *args):
        """Write a diagnostic line to the log stream."""
        self.log.write((fmt % args if args else fmt) + "\n")
        self.log.flush()

    def sync(self, tries=3):
        """Wake the monitor.  The first CR after RS is what sets its bit rate."""
        for attempt in range(1, tries + 1):
            self.p.take()
            self.p.write(b"\r")
            self.p.drain()
            reply = self.p.take()
            if b"KIM" in reply or PROMPT.search(reply):
                self.say("monitor: %s", reply.strip().decode("ascii", "replace"))
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
        """Write bytes from addr, advancing as it goes."""
        self.open_addr(addr)
        for byte in data:
            self.p.write(("%02X." % byte).encode())
        self.p.drain()

    def load(self, tape):
        """Send a tape with the L command, one record at a time."""
        self.say("loading %s", tape)
        self.p.take()
        self.p.write(b"L")
        self.p.drain()
        for n, record in enumerate(tape.records, 1):
            self.p.write(record + b"\r\n")
            self.p.drain()
            if not self.p.echo:
                time.sleep(self.p.line_delay)
            self.log.write("\r  record %d/%d" % (n, len(tape.records)))
            self.log.flush()
        count = len(tape.records)
        self.p.write(b";00%04X%04X\r\n" % (count, count))
        self.p.drain()
        self.log.write("\r  %d records sent%s\n" % (count, " " * 16))
        self.p.take()

    def punch(self, lo, hi):
        """Run the monitor's paper tape punch over $lo-$hi; return {addr: byte}."""
        self.deposit(SAL, bytes([lo & 0xFF, lo >> 8, (hi + 1) & 0xFF, (hi + 1) >> 8]))
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
