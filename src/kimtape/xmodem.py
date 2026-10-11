"""XMODEM-CRC, after Christensen and Forsberg's XMODEM/YMODEM reference.

Frames are SOH, block number, its complement, 128 data bytes padded with SUB
and their CRC-16/XMODEM, high byte first.  The payload for the kimtape stub is
a run of segments (address, length, data) ended by a zero length segment.
"""

import binascii

SOH, EOT, ACK, NAK, CAN, SUB = 0x01, 0x04, 0x06, 0x15, 0x18, 0x1A
CRC = ord("C")
BLOCK = 128
FILL = 0xFF  # a start bit then all ones: cannot be misframed
MONITOR = 0xFFFF


def crc16(data):
    """CRC-16/XMODEM: polynomial $1021, initial 0, no reflection."""
    return binascii.crc_hqx(data, 0)


def frames(payload):
    """Cut a payload into XMODEM-CRC frames, block numbers from 1."""
    out = []
    for n, off in enumerate(range(0, len(payload), BLOCK), 1):
        data = payload[off : off + BLOCK].ljust(BLOCK, bytes([SUB]))
        head = bytes([SOH, n & 0xFF, ~n & 0xFF])
        out.append(head + data + crc16(data).to_bytes(2, "big"))
    return out


def segments(mem, go=MONITOR):
    """Encode {addr: byte} as the stub's segment stream, continuing at go.

    Each contiguous run is its address and length, low byte first, then its
    bytes; a zero length segment carries go, ``MONITOR`` for the monitor.
    """
    out = bytearray()
    start, run = None, bytearray()
    for addr in sorted(mem) + [None]:
        if addr is not None and start is not None and addr == start + len(run):
            run.append(mem[addr])
            continue
        if run:
            out += start.to_bytes(2, "little") + len(run).to_bytes(2, "little") + run
        if addr is not None:
            start, run = addr, bytearray([mem[addr]])
    return bytes(out) + go.to_bytes(2, "little") + b"\0\0"


class Failed(Exception):
    """The transfer was abandoned; the message says why."""


class Rejected(Failed):
    """The receiver cancelled because memory at ``addr`` did not hold a write."""

    def __init__(self, addr):
        super().__init__("memory at $%04X did not hold what was written" % addr)
        self.addr = addr


class Sender:  # pylint: disable=too-many-instance-attributes
    """XMODEM-CRC sender as a state machine that never touches a port.

    ``receive`` and ``expire`` return what to send next.  With ``echo`` the line
    returns everything sent, as a KIM-1 does, and that much is skipped.
    """

    def __init__(self, payload, echo=False, retries=10):
        self.frames = frames(payload)
        self.echo, self.retries = echo, retries
        self.index, self.errors, self.skip = 0, 0, 0
        self.started = self.done = False
        self.cancel = None

    @property
    def total(self):
        """Frames to send."""
        return len(self.frames)

    @property
    def sent(self):
        """Frames acknowledged so far."""
        return self.index

    def _out(self, data):
        if self.echo:
            self.skip += len(data)
        return data

    def _current(self):
        return self._out(
            self.frames[self.index] if self.index < len(self.frames) else bytes([EOT])
        )

    def _error(self, why):
        self.errors += 1
        if self.errors > self.retries:
            raise Failed("%s, %d times" % (why, self.errors))

    def receive(self, byte):
        """React to one received byte; return the bytes to send."""
        if self.skip:
            self.skip -= 1
        elif self.cancel is not None:
            self.cancel.append(byte)
            if len(self.cancel) == 2:
                raise Rejected(int.from_bytes(self.cancel, "little"))
        elif self.done:
            pass
        elif byte == CAN and self.started:
            self.cancel = bytearray()
        elif byte in (CRC, NAK):
            if self.started:
                self._error("block %d rejected" % (self.index + 1))
            self.started = True
            return self._current()
        elif byte == ACK and self.started:
            self.errors = 0
            self.done = self.index == len(self.frames)
            if not self.done:
                self.index += 1
                return self._current()
        return b""

    def expire(self):
        """A wait for an answer ran out; return the bytes to send.

        The frame in flight may be partly lost, so FILL bytes complete it for
        the receiver to reject, and it is sent again on the NAK that follows.
        """
        if self.cancel is not None:
            raise Failed("receiver cancelled")
        if not self.started:
            raise Failed("no request to start")
        self._error("no answer to block %d" % (self.index + 1))
        if self.index == len(self.frames):
            return self._current()
        return self._out(bytes([FILL]) * (BLOCK + 5))
