#!/usr/bin/env python3
"""A KIM-1 ROM monitor stand-in on a pty, for exercising kimtape.py without hardware.

Implements the TTY command set the loader uses: character echo, AAAA<space> to
open, DD. to deposit, L to load paper tape, Q to punch, G to execute.  G at a
kimtape XMODEM stub runs it on a simulated 6502, bit by bit in real time.
Prints the slave device path on stdout, then serves until stdin closes.
"""

import fcntl
import os
import pty
import re
import select
import struct
import sys
import termios
import threading
import time

from kimsim import START, Board, Line, Listing

from kimtape import bootstrap, timing
from kimtape.stub import DATA_ORG, STUBS

RECORD = re.compile(rb";([0-9A-F]{2})([0-9A-F]{4})((?:[0-9A-F]{2})*)([0-9A-F]{4})")
EAL, EAH = 0x17F7, 0x17F8
CHUNK = 24
CHAR_TIME = 0.001  # the monitor's output takes line time
RAM = ((0x0000, 0x13FF), (0x17E7, 0x17FF))
CNTL30 = 0x17F2
RATES = {
    getattr(termios, name): int(name[1:])
    for name in dir(termios)
    if name[1:].isdigit() and name[0] == "B"
}
NOSTUB_LIFE = 1.0  # how long a stub that is never understood takes to give up


class Outside:  # pylint: disable=too-few-public-methods
    """Every address but those in a range."""

    def __init__(self, inside):
        self.inside = inside

    def __contains__(self, addr):
        return addr not in self.inside


def cks(count, addr, data):
    """The KIM-1 record checksum."""
    return (count + (addr >> 8) + (addr & 0xFF) + sum(data)) & 0xFFFF


class FakeKIM:  # pylint: disable=too-many-instance-attributes
    """A KIM-1 ROM monitor stand-in: echo, open, deposit, load, punch, execute."""

    def __init__(self, master, drop=0, mode=""):
        self.fd, self.mem, self.addr, self.digits = master, {}, 0, b""
        self.drop, self.seen, self.gone, self.woke = drop, 0, None, False
        self.mute, self.deaf = mode == "mute", False
        self.stubs, self.mode = mode != "nostub", mode

    def out(self, data):
        """Write to the line unless this board has gone deaf."""
        if self.deaf:
            return
        os.write(self.fd, data)

    PAD = b"\x00" * 6  # teletype carriage fill, as the ROM sends

    def say(self, data):
        """Monitor output.  Its UART is in software and cannot receive while it
        sends, so characters arriving meanwhile are lost, though the hardware
        still echoes them."""
        for i in range(len(data)):
            self.out(data[i : i + 1])
            time.sleep(CHAR_TIME)
        while select.select([self.fd], [], [], 0)[0]:
            try:
                ch = os.read(self.fd, 1)
            except OSError:
                return
            self.out(ch)

    def prompt(self):
        """The monitor's address and contents display."""
        self.say(
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

    def serve(self):  # pylint: disable=too-many-branches
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
                    self.autobaud()
                    self.say(b"\r\n" + self.PAD + b"KIM")
                else:
                    self.addr = (self.addr + 1) & 0xFFFF
                self.prompt()
                self.deaf = self.mute  # answered the reset, now goes deaf
            elif ch == b"L":
                self.load()
            elif ch == b"Q":
                self.punch()
            elif ch == b"G":
                stub = self.stub_at(self.addr)
                if stub is None:
                    self.gone = self.addr
                    self.say(b"\r\n")
                    return
                if not self.stubs:
                    self.ignore(NOSTUB_LIFE)
                elif not self.run_stub(*stub):
                    return
                self.say(b"\r\n" + self.PAD + b"KIM")
                self.prompt()

    def store(self, addr, value):
        """Honour the KIM memory map: only RAM sticks."""
        if addr < 0x1400 or 0x17E7 <= addr <= 0x17FF:
            self.mem[addr] = value

    def ignore(self, seconds):
        """Hear nothing for a while, as a stub that cannot be understood."""
        time.sleep(seconds)
        while select.select([self.fd], [], [], 0)[0]:
            os.read(self.fd, 4096)

    def stub_at(self, addr):
        """(rate, stub, data address) if a kimtape stub starts at addr."""
        for rate, stub in STUBS.items():
            code, size = addr - stub["entry"], len(stub["code"])
            at = stub["data_relocs"][0] - 1
            word = self.mem.get(code + at, 0) | self.mem.get(code + at + 1, 0) << 8
            data = word - (stub["code"][at] | stub["code"][at + 1] << 8) + DATA_ORG
            image = bootstrap.relocate(stub, code, data & 0xFFFF)
            if all(self.mem.get(code + i) == image[i] for i in range(size)):
                return rate, stub
        return None

    def run_stub(self, rate, stub):
        """Run the stub against the pty in real time; False if it ran a program."""
        board = Board(1e6 / rate, bootstrap.stop_bits(stub), True, RAM)
        for addr, value in self.mem.items():
            board.mem.data[addr] = value
        board.mpu.pc, began = self.addr, time.monotonic()
        code = self.addr - stub["entry"]
        outside = Outside(range(code, code + len(stub["code"])))
        while board.mpu.pc not in outside:
            ahead = board.mpu.processorCycles / 1e6 - (time.monotonic() - began)
            if select.select([self.fd], [], [], max(ahead, 0))[0]:
                try:
                    board.uart.send(os.read(self.fd, 4096), board.mpu.processorCycles)
                except OSError:
                    return False
            board.run(2000, outside)
            out = bytes(byte for byte, _ in board.received())
            if out:
                os.write(self.fd, out)
        for addr in board.mem.written:
            self.mem[addr] = board.mem.data[addr]
        if board.mpu.pc == START:
            return True
        self.gone = board.mpu.pc
        return False

    def autobaud(self):
        """RST measures the CR's start bit into CNTL30 at the line's rate."""
        baud = RATES.get(termios.tcgetattr(self.fd)[5], 1200)
        self.mem[CNTL30] = max(0, timing.autobaud(timing.bit_time(baud))[-1])
        self.mem[CNTL30 + 1] = 0

    def pending(self):
        """Characters waiting on the line."""
        return struct.unpack("i", fcntl.ioctl(self.fd, termios.FIONREAD, b"\0" * 4))[0]

    def load(self):
        """LOAD, character by character, as the monitor's listing has it.

        Faults: drop N skips record N whole, as a lost ';' does; mode err spoils
        a data digit so its checksum fails; nostream garbles what comes in a rush.
        """
        model = Listing(0, Line())
        model.mem = Stores(self)
        loader = model.loader()
        next(loader)
        loader.send(ord("L"))
        offset, queue = 0, b""
        while True:
            ch = queue[:1] or self.getc()
            queue = queue[1:]
            if not ch:
                return
            offset += 1
            if self.mode == "nostream" and self.pending() > 1:
                ch = bytes([ch[0] ^ 1])
            if ch == b";" and not queue:
                queue = self.getc() + self.getc()
                self.seen, offset = self.seen + (queue != b"00"), 0
                spoil = queue != b"00" and self.drop in (-1, self.seen)
                ch = b"?" if spoil and self.mode != "err" else ch
            if offset == 8 and self.mode == "err" and self.drop in (-1, self.seen):
                ch = bytes([ch[0] ^ 1])
            try:
                loader.send(ch[0])
            except StopIteration as stop:
                self.say(b"\x13 ERR" if stop.value == "ERR" else b"")
                self.say(b"\x13 KIM\r\n" + self.PAD + b"\r\n" + self.PAD + b"KIM")
                self.prompt()
                return

    def punch(self):
        """DUMPT: write the open address through EAL/EAH as tape records."""
        lo = self.addr
        hi = self.mem.get(EAL, 0) | (self.mem.get(EAH, 0) << 8)
        self.say(b"\r\n" + self.PAD)
        n = 0
        for base in range(lo, hi, CHUNK):
            data = bytes(
                self.mem.get(a, 0xFF) for a in range(base, min(base + CHUNK, hi))
            )
            self.say(
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
        self.say(b";00%04X%04X\r\n%s" % (n, n, self.PAD))
        self.prompt()


class Stores:  # pylint: disable=too-few-public-methods
    """Memory as the model writes it, through the board's map."""

    def __init__(self, kim):
        self.kim = kim

    def __setitem__(self, addr, value):
        self.kim.store(addr, value)


def main():
    """Announce the pty, serve until stdin closes, then report."""
    drop = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    mode = sys.argv[2] if len(sys.argv) > 2 else ""
    master, slave = pty.openpty()
    for fd in (master, slave):
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    print(os.ttyname(slave), flush=True)
    kim = FakeKIM(master, drop, mode)
    thread = threading.Thread(target=kim.serve, daemon=True)
    thread.start()
    sys.stdin.read()  # parent closes stdin to stop us
    print("GO=%s" % ("%04X" % kim.gone if kim.gone is not None else "none"))
    print("BYTES=%d" % len(kim.mem))
    for addr in sorted(kim.mem):
        print("%04X %02X" % (addr, kim.mem[addr]))


if __name__ == "__main__":
    main()
