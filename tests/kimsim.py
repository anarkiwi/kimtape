"""A KIM-1 on py65 with its TTY lines modelled bit by bit, timed in CPU cycles.

TTY input is 6530 port A bit 7 and output port B bit 0, both idle high.  The
output is the AND of port B bit 0 and the input, which is the KIM-1's echo.
"""

import bisect

from py65.devices.mpu6502 import MPU

SAD, SBD, START = 0x1740, 0x1742, 0x1C4F
ACCESS = 3  # absolute mode I/O lands on the last of the instruction's 4 cycles


class Line:
    """A serial line as its level transitions in cycles; idle high."""

    def __init__(self):
        self.times, self.levels = [float("-inf")], [1]

    def set(self, when, level):
        """Drive the line to level from when on; times never go backwards."""
        if level != self.levels[-1]:
            self.times.append(when)
            self.levels.append(level)

    def at(self, when):
        """Level at a time."""
        return self.levels[bisect.bisect_right(self.times, when) - 1]


class Uart:  # pylint: disable=too-few-public-methods
    """The host's transmitter, ``period`` cycles a bit; ``gap()`` idles between."""

    def __init__(self, period, stop=1, gap=lambda: 0):
        self.period, self.stop, self.gap = period, stop, gap
        self.line, self.busy = Line(), 0.0

    def send(self, data, now):
        """Queue bytes back to back from now or the end of the last."""
        when = max(now, self.busy)
        for byte in data:
            bits = [0] + [(byte >> i) & 1 for i in range(8)] + [1] * self.stop
            for i, bit in enumerate(bits):
                self.line.set(when + i * self.period, bit)
            when += len(bits) * self.period + self.gap()
        self.busy = when


class Memory:
    """64K with the 6530 TTY port bits mapped and writes outside RAM dropped."""

    def __init__(self, board, ram):
        self.board, self.data = board, bytearray(0x10000)
        self.ram, self.written = bytearray(0x10000), set()
        for lo, hi in ram:
            self.ram[lo : hi + 1] = b"\1" * (hi + 1 - lo)

    def __getitem__(self, addr):
        if addr == SAD:
            return self.board.uart.line.at(self.board.now()) << 7
        return self.data[addr]

    def __setitem__(self, addr, value):
        if addr == SBD:
            self.board.pb0.set(self.board.now(), value & 1)
            self.data[addr] = value
        elif self.ram[addr]:
            self.data[addr] = value
            self.written.add(addr)


class Board:
    """A KIM-1 whose host side sends and decodes at ``period`` cycles a bit."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        period,
        stop=1,
        echo=True,
        ram=((0x0000, 0x13FF), (0x1780, 0x17FF)),
        gap=lambda: 0,
    ):
        self.uart, self.echo = Uart(period, stop, gap), echo
        self.pb0 = Line()
        self.mem = Memory(self, ram)
        self.mem.data[SBD] = 0x07  # as the monitor's INIT1 leaves it
        self.mpu = MPU(memory=self.mem)
        self.cursor = 0.0

    def now(self):
        """Cycle of the I/O access in the instruction being executed."""
        return self.mpu.processorCycles + ACCESS

    def load(self, addr, data):
        """Write memory directly."""
        self.mem.data[addr : addr + len(data)] = data

    def out(self, when):
        """Level of the TTY output line."""
        return self.pb0.at(when) & (self.uart.line.at(when) if self.echo else 1)

    def _fall(self, until):
        """First falling edge of the output after the cursor, or None."""
        level = self.out(self.cursor)
        lines = (self.pb0.times, self.uart.line.times)
        edges = sorted(
            t
            for times in lines
            for t in times[bisect.bisect_right(times, self.cursor) :]
            if t <= until
        )
        for when in edges:
            new = self.out(when)
            if level and not new:
                return when
            level = new
        return None

    def received(self):
        """Bytes the host has decoded from the output, with their end times."""
        period, until = self.uart.period, self.mpu.processorCycles
        got = []
        while True:
            edge = self._fall(until - 10 * period)
            if edge is None:
                return got
            bits = [self.out(edge + (i + 1.5) * period) for i in range(8)]
            self.cursor = edge + 9.5 * period
            got.append((sum(b << i for i, b in enumerate(bits)), self.cursor))

    def run(self, cycles, stops=()):
        """Execute for about this many cycles or until the PC is in stops."""
        mpu = self.mpu
        end = mpu.processorCycles + cycles
        while mpu.processorCycles < end and mpu.pc not in stops:
            mpu.step()
        return mpu.pc


class Host:  # pylint: disable=too-few-public-methods
    """Answers the board with an XMODEM sender, ``latency`` cycles late."""

    def __init__(self, board, sender, latency=200, timeout=1_000_000):
        self.board, self.sender = board, sender
        self.latency, self.timeout, self.deadline = latency, timeout, None
        self.heard = bytearray()

    def _send(self, data, when):
        if data:
            self.board.uart.send(data, max(when, self.board.mpu.processorCycles))
        if self.sender.started:
            self.deadline = self.board.uart.busy + self.timeout

    def poll(self):
        """Deliver what the board has sent and act on an expired wait."""
        for byte, end in self.board.received():
            self.heard.append(byte)
            self._send(self.sender.receive(byte), end + self.latency)
        now = self.board.mpu.processorCycles
        if self.deadline is not None and now > self.deadline:
            self._send(self.sender.expire(), now)

    def converse(self, stops, limit=60_000_000, step=100):
        """Run until the PC reaches one of stops; return it."""
        board = self.board
        end = board.mpu.processorCycles + limit
        while board.mpu.processorCycles < end:
            pc = board.run(step, stops)
            self.poll()
            if pc in stops:
                return pc
        raise TimeoutError("board still running at $%04X" % board.mpu.pc)
