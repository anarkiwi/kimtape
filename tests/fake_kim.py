#!/usr/bin/env python3
"""A KIM-1 ROM monitor stand-in on a pty, for exercising kimtape.py without hardware.

Implements the TTY command set the loader uses: character echo, AAAA<space> to
open, DD. to deposit, L to load paper tape, Q to punch, G to execute.  Prints the
slave device path on stdout, then serves until stdin closes.
"""

import os
import pty
import re
import sys
import termios
import threading

RECORD = re.compile(rb";([0-9A-F]{2})([0-9A-F]{4})((?:[0-9A-F]{2})*)([0-9A-F]{4})")
SAL, SAH, EAL, EAH = 0x17F5, 0x17F6, 0x17F7, 0x17F8
CHUNK = 24


def cks(count, addr, data):
    """The KIM-1 record checksum."""
    return (count + (addr >> 8) + (addr & 0xFF) + sum(data)) & 0xFFFF


class FakeKIM:  # pylint: disable=too-many-instance-attributes
    """A KIM-1 ROM monitor stand-in: echo, open, deposit, load, punch, execute."""

    def __init__(self, master, drop=0, mute=False):
        self.fd, self.mem, self.addr, self.digits = master, {}, 0, b""
        self.drop, self.seen, self.gone, self.woke = drop, 0, None, False
        self.mute, self.deaf = mute, False

    def out(self, data):
        """Write to the line unless this board has gone deaf."""
        if self.deaf:
            return
        os.write(self.fd, data)

    PAD = b"\x00" * 6  # teletype carriage fill, as the ROM sends

    def prompt(self):
        """The monitor's address and contents display."""
        self.out(
            b"\r\n%s%04X %02X " % (self.PAD, self.addr, self.mem.get(self.addr, 0))
        )

    def getc(self):
        """Read one character, echoing it as the monitor does."""
        try:
            ch = os.read(self.fd, 1)
        except OSError:  # the other end closed the pty
            return b""
        if ch:
            self.out(ch)  # the monitor echoes everything
        return ch

    def serve(self):
        """The monitor command loop."""
        while True:
            ch = self.getc()
            if not ch:
                return
            if ch in b"0123456789ABCDEF":
                self.digits += ch
            elif ch == b" ":
                self.addr = int(self.digits[-4:] or b"0", 16)
                self.digits = b""
                self.prompt()
            elif ch == b".":
                self.store(self.addr, int(self.digits[-2:] or b"0", 16))
                self.addr = (self.addr + 1) & 0xFFFF
                self.digits = b""
                self.prompt()
            elif ch in b"\r\n":
                self.digits = b""
                if not self.woke:
                    self.woke = True  # first CR sets the bit rate
                    self.out(b"\r\n" + self.PAD + b"KIM")
                else:
                    self.addr = (self.addr + 1) & 0xFFFF
                self.prompt()
                self.deaf = self.mute  # answered the reset, now goes deaf
            elif ch == b"L":
                self.load()
            elif ch == b"Q":
                self.punch()
            elif ch == b"G":
                self.gone = self.addr
                self.out(b"\r\n")
                return

    def store(self, addr, value):
        """Honour the KIM memory map: only RAM sticks."""
        if addr < 0x1400 or 0x17E7 <= addr <= 0x17FF:
            self.mem[addr] = value

    def load(self):
        """LOADT: gather ';' records until the count is zero."""
        line = b""
        while True:
            ch = self.getc()
            if not ch:
                return
            if ch in b"\r\n":
                m = RECORD.fullmatch(line.strip())
                line = b""
                if not m:
                    continue
                count, addr = int(m.group(1), 16), int(m.group(2), 16)
                data = bytes.fromhex(m.group(3).decode())
                if count == 0:
                    self.out(b"\r\n" + self.PAD + b"KIM")
                    self.prompt()
                    return
                if cks(count, addr, data) != int(m.group(4), 16):
                    continue  # a real KIM counts the error and skips
                self.seen += 1
                if self.drop in (-1, self.seen):  # injected fault
                    continue
                for i, b in enumerate(data):
                    self.store(addr + i, b)
            else:
                line += ch

    def punch(self):
        """DUMPT: write SAL/SAH through EAL/EAH as tape records."""
        lo = self.mem.get(SAL, 0) | (self.mem.get(SAH, 0) << 8)
        hi = self.mem.get(EAL, 0) | (self.mem.get(EAH, 0) << 8)
        self.out(b"\r\n" + self.PAD)
        n = 0
        for base in range(lo, hi, CHUNK):
            data = bytes(
                self.mem.get(a, 0xFF) for a in range(base, min(base + CHUNK, hi))
            )
            self.out(
                b";%02X%04X%s%04X\r\n%s"
                % (
                    len(data),
                    base,
                    data.hex().upper().encode(),
                    cks(len(data), base, data),
                    self.PAD,
                )
            )
            n += 1
        self.out(b";00%04X%04X\r\n%s" % (n, n, self.PAD))
        self.prompt()


def main():
    """Announce the pty, serve until stdin closes, then report."""
    drop = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    mute = len(sys.argv) > 2 and sys.argv[2] == "mute"
    master, slave = pty.openpty()
    for fd in (master, slave):
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    print(os.ttyname(slave), flush=True)
    kim = FakeKIM(master, drop, mute)
    thread = threading.Thread(target=kim.serve, daemon=True)
    thread.start()
    sys.stdin.read()  # parent closes stdin to stop us
    print("GO=%s" % ("%04X" % kim.gone if kim.gone is not None else "none"))
    print("BYTES=%d" % len(kim.mem))
    for addr in sorted(kim.mem):
        print("%04X %02X" % (addr, kim.mem[addr]))


if __name__ == "__main__":
    main()
