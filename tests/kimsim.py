"""A KIM-1 on py65 with its TTY lines modelled bit by bit, timed in CPU cycles.

TTY input is 6530 port A bit 7 and output port B bit 0, both idle high.  The
output is the AND of port B bit 0 and the input, which is the KIM-1's echo.
``Listing`` models the ROM monitor's L command from its published listing.
"""

import bisect
import math

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
        self.mpu.cycletime = list(MPU.cycletime)
        self.mpu.cycletime[0xCE] = 6  # py65 1.2 counts DEC absolute as 3
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


POLL = 9  # GET1: BIT SAD 4, BNE 2, BMI 3
JSR, RTS = 6, 6
CHK = JSR + 2 + 3 + 3 + 3 + 2 + 3 + RTS  # CLC ADC STA LDA ADC STA
GETCH_POLL = 3 + 2 + 2 + 3  # STX, LDX, LDA, then BIT SAD reads on its last cycle
GETBYT_POLL = JSR + JSR + GETCH_POLL  # JSR GETBYT, JSR GETCH


def delay_cycles(count):
    """JSR DELAY with CNTH30 zero: count+1 passes of SEC SBC BCS LDY BPL."""
    passes = count + 1
    return JSR + 12 + 14 * passes + (6 - 1) - 1 + RTS  # the borrow: DEC, BCS falls


def half_cycles(count):
    """JSR DEHALF with CNTH30 zero: LSR A, LSR TIMH, BCC DE2, then DELAY's loop."""
    passes = (count >> 1) + 1
    return JSR + 12 + 2 + 6 + 3 + 14 * passes + (6 - 1) - 1 + RTS


def pack_cycles(char):
    """JSR PACK: CMP/BMI, CMP/BPL, CMP/BMI, then four ROLs into INL and INH."""
    if char < 0x30:
        return JSR + 2 + 3 + RTS
    if char >= 0x47:
        return JSR + 2 + 2 + 2 + 3 + RTS
    test = 2 + 3 if char < 0x40 else 2 + 2 + 2 + 2  # BMI taken, or CLC ADC #9
    return JSR + 2 + 2 + 2 + 2 + test + 8 + 2 + 4 * 17 - 1 + 2 + RTS


def pack_value(char, word):
    """INH:INL after PACK shifts in a hex character; anything else leaves it."""
    if not 0x30 <= char < 0x47:
        return word
    return (word << 4 | ((char + 9 if char >= 0x40 else char) & 0x0F)) & 0xFFFF


class Listing:  # pylint: disable=too-many-instance-attributes
    """The monitor reading the host's line from 'L' on, at the bit level.

    GETCH polls every POLL cycles from when it is called, samples at the times
    DELAY and DEHALF give, and returns; LOAD then works on the character, its
    cycles summed from the routines it runs, and hears nothing meanwhile.
    """

    def __init__(self, count, line):
        self.count, self.line = count, line
        delay, half = delay_cycles(count), half_cycles(count)
        self.step = delay + 22  # LDA SAD, AND, LSR, ORA, STA, DEX, BNE
        self.first = 2 + 2 + delay + half + 3 + 1  # BNE, BMI, to LDA SAD's read
        # after bit 7's read: AND LSR ORA STA, DELAY, DEX BNE, DEHALF, LDX LDA
        # ROL LSR RTS
        self.back = self.first + 7 * self.step + 1 + 13 + delay + 4 + half + 16
        self.mem, self.reply, self.word, self.chars = {}, None, 0, []

    def _detect(self, ready):
        """The first poll from ready that finds the line low, or None if it never is."""
        when = ready
        while self.line.at(when):
            nxt = bisect.bisect_right(self.line.times, when)
            if nxt == len(self.line.times):
                return None
            when += POLL * math.ceil((self.line.times[nxt] - when) / POLL)
        return when

    def run(self, ready):
        """Read until L ends or the line goes quiet; the cycle GETCH last returned."""
        loader = self.loader()
        next(loader)
        end = ready
        while True:
            seen = self._detect(ready)
            if seen is None:
                return end
            bits = [self.line.at(seen + self.first + i * self.step) for i in range(8)]
            char = sum(b << i for i, b in enumerate(bits)) & 0x7F
            end = seen + self.back
            try:
                cost = loader.send(char)
            except StopIteration as stop:
                self.chars.append((char, seen, end, None))
                self.reply = stop.value
                return end
            self.chars.append((char, seen, end, cost))
            ready = end + cost

    def _byte(self, char):
        """GETBYT: the first character's cost yielded, the second taken; INL back."""
        self.word = pack_value(char, self.word)
        second = yield pack_cycles(char) + GETBYT_POLL - JSR
        self.word = pack_value(second, self.word)
        return self.word & 0xFF, pack_cycles(second) + 3 + RTS

    def loader(self):  # pylint: disable=too-many-locals
        """L, from READ: sent each character, yields cycles until GETCH polls again."""
        char = yield
        while char != ord("L"):
            char = yield 2 + 2 + pack_cycles(char) + 3 + 8 * 4 + 3 + JSR + GETCH_POLL
        # READ, PACK, SCAN to CMP #'L', BEQ across a page, JMP LOAD
        char = (
            yield 2 + 2 + pack_cycles(char) + 3 + 7 * 4 + 2 + 4 + 3 + JSR + GETCH_POLL
        )
        while True:
            while char != ord(";"):
                char = yield 2 + 3 + JSR + GETCH_POLL
            char = yield 2 + 2 + 2 + 3 + 3 + GETBYT_POLL
            count, cost = yield from self._byte(char)
            char = yield cost + 2 + CHK + GETBYT_POLL
            high, cost = yield from self._byte(char)
            char = yield cost + 3 + CHK + GETBYT_POLL
            low, cost = yield from self._byte(char)
            total = count + high + low
            char = yield cost + 3 + CHK + 2 + (3 if count == 0 else 2) + GETBYT_POLL
            point = high << 8 | low
            for left in range(count, 0, -1):
                value, cost = yield from self._byte(char)
                self.mem[point] = value
                total += value
                carry = (point & 0xFF) == 0xFF
                point = (point + 1) & 0xFFFF
                incpt = JSR + 5 + (2 + 5 if carry else 3) + RTS
                more = 3 if left > 1 else 2 + 2  # BNE LOAD2, or INX
                char = yield cost + 6 + CHK + incpt + 2 + more + GETBYT_POLL
            value, cost = yield from self._byte(char)
            if value != (total >> 8) & 0xFF:
                char = yield cost + 3 + 3 + GETBYT_POLL  # LOADE1
                yield from self._byte(char)
                return "ERR"
            char = yield cost + 3 + 2 + GETBYT_POLL
            value, cost = yield from self._byte(char)
            if value != total & 0xFF:
                return "ERR"
            if count == 0:
                return "KIM"
            char = yield cost + 3 + 2 + 2 + 3 + 1 + JSR + GETCH_POLL  # BNE LOAD


def outch_cycles(count):
    """JSR OUTCH: DELAY before the start bit, then start, 8 data and stop bits."""
    delay = delay_cycles(count)
    return JSR + 3 + 3 + delay + 10 + delay + 2 + 8 * (22 + delay) - 1 + 10 + delay + 9


def prtbyt_cycles(count, value):
    """JSR PRTBYT: two JSR HEXTA, each jumping to OUTCH; a letter costs 1 more."""
    hexta = [
        JSR + 2 + 2 + 2 + (4 if n > 9 else 3) + 2 + 3 + outch_cycles(count) - JSR
        for n in (value >> 4, value & 0x0F)
    ]
    return JSR + 3 + 8 + hexta[0] + 3 + hexta[1] + 3 + RTS


def prtpnt_cycles(count, word):
    """JSR PRTPNT: POINTH and POINTL printed and checksummed."""
    high, low = prtbyt_cycles(count, word >> 8), prtbyt_cycles(count, word & 0xFF)
    return JSR + 3 + high + CHK + 3 + low + CHK + RTS


def dump_cycles(count, mem, lo, hi):
    """GETCH's return of Q to its next poll, punching lo to EAL = hi + 1.

    SCAN reaches DUMP across a page; DUMP prints whole 24 byte records.
    """
    crlf = JSR + 2 + 8 * (4 + outch_cycles(count) + 2 + 3) - 1 + RTS
    head = 2 + 3 + 3 + crlf + 2 + outch_cycles(count) + 3 + 4 + 3 + 4
    total = 2 + 2 + pack_cycles(ord("Q")) + 3 + 6 * 4 + 2 + 4 + 3 + 8
    total += 8 + JSR + GETCH_POLL  # CLEAR, READ
    point, records = lo, 0
    while point <= hi:
        chk = 24 + (point >> 8) + (point & 0xFF)
        total += head + 3 + 2 + 2 + prtbyt_cycles(count, 24) + CHK - 1
        total += prtpnt_cycles(count, point)
        for _ in range(24):
            value = mem.get(point, 0)
            chk += value
            incpt = JSR + 5 + (2 + 5 if (point & 0xFF) == 0xFF else 3) + RTS
            total += 2 + 5 + prtbyt_cycles(count, value) + CHK + incpt + 2 + 3
            point += 1
        records += 1
        total += 3 + prtbyt_cycles(count, (chk >> 8) & 0xFF) + 3
        total += prtbyt_cycles(count, chk & 0xFF) + 5 + (3 if records & 0xFF else 7) + 3
    chk = (records >> 8) + (records & 0xFF)
    total += head + 2 + 2 + prtbyt_cycles(count, 0) + 24 + prtpnt_cycles(count, records)
    return (
        total + 3 + prtbyt_cycles(count, chk >> 8) + 3 + prtbyt_cycles(count, chk) + 3
    )
