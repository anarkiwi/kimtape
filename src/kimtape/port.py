"""Serial port with pacing suited to a monitor that bit-bangs its UART.

The KIM-1 monitor has no receive buffer: it samples the line in software and
works on each character before it listens again.  Writes are either streamed
in frames long enough to cover that work, or clocked by the monitor's echo.
"""

import os
import select
import sys
import termios
import time
from dataclasses import dataclass

CMSPAR = getattr(termios, "CMSPAR", 0o10000000000)  # Linux: mark/space parity
FRAME_FLAGS = {
    10: 0,
    11: termios.CSTOPB,
    12: termios.CSTOPB | termios.PARENB | termios.PARODD | CMSPAR,
}


def _speed(baud):
    try:
        return getattr(termios, "B%d" % baud)
    except AttributeError:
        raise SystemExit("kimtape: unsupported baud rate %d" % baud) from None


@dataclass
class Pacing:
    """How fast characters may be pushed at the monitor.

    ``stream`` lets up to ``window`` characters run ahead of their echo, the
    frame doing the pacing; otherwise ``echo_lead`` (1 is lock-step) applies.
    ``char_delay`` paces a link that does not echo, when not streaming.
    """

    char_delay: float = None
    echo_lead: int = 1
    echo: bool = True
    stream: bool = False
    window: int = 16


class Port:  # pylint: disable=too-many-instance-attributes
    """A raw 8N1 serial port.  Standard library only, no pyserial."""

    def __init__(self, dev, baud, pacing=None, trace=False):
        speed = _speed(baud)
        try:
            self.fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as err:
            raise SystemExit(
                "kimtape: cannot open %s: %s" % (dev, err.strerror)
            ) from None
        try:
            self._configure(speed)
        except termios.error as err:
            os.close(self.fd)
            raise SystemExit(
                "kimtape: %s is not a serial port (%s)" % (dev, err.args[-1])
            ) from None
        self.baud, self.frame = baud, 10
        self._timing(baud)
        self.pacing = pacing or Pacing()
        if self.pacing.char_delay is None:
            self.pacing.char_delay = self.char_time
        self.trace = trace
        self.rx = bytearray()

    @property
    def echo(self):
        """Whether writes are currently clocked against the monitor's echo."""
        return self.pacing.echo

    def _timing(self, baud, frame=10):
        self.char_time = frame / float(baud)
        # quiet detection cannot be tighter than the scheduler can wake us: at
        # 1200 baud four character times dominate, but on a fast link it would
        # otherwise call the line idle before the far end had begun replying
        self.quiet_time = max(4 * self.char_time, 0.005)
        self.echo_timeout = max(0.5, 40 * self.char_time)

    def _configure(self, speed, frame=10, when=termios.TCSANOW, flush=None):
        cc = list(termios.tcgetattr(self.fd)[6])
        cc[termios.VMIN], cc[termios.VTIME] = 0, 0
        cflag = termios.CS8 | termios.CREAD | termios.CLOCAL | FRAME_FLAGS[frame]
        termios.tcsetattr(self.fd, when, [0, 0, cflag, 0, speed, speed, cc])
        termios.tcflush(self.fd, termios.TCIOFLUSH if flush is None else flush)

    def set_rate(self, baud, frame=10):
        """Change bit rate and frame once what is queued has gone, dropping input."""
        self._configure(_speed(baud), frame, termios.TCSADRAIN, termios.TCIFLUSH)
        self.baud, self.frame = baud, frame
        self._timing(baud, frame)
        self.rx = bytearray()

    @staticmethod
    def show(data):
        """Readable form of a byte string, for traces and probe output."""
        out = []
        for byte in data:
            if byte == 0x0D:
                out.append("<CR>")
            elif byte == 0x0A:
                out.append("<LF>\n     ")
            elif 0x20 <= byte < 0x7F:
                out.append(chr(byte))
            else:
                out.append("<%02X>" % byte)
        return "".join(out)

    def _trace(self, arrow, data):
        if self.trace and data:
            sys.stderr.write("%s %s\n" % (arrow, self.show(data)))
            sys.stderr.flush()

    def pace(self, echo, char_delay=None, echo_lead=1):
        """Switch pacing regime; used by the probe to find what the board needs."""
        self.pacing.echo, self.pacing.echo_lead = echo, echo_lead
        self.pacing.stream = False
        if char_delay is not None:
            self.pacing.char_delay = char_delay

    def poll(self, timeout):
        """Absorb whatever has arrived within timeout; return bytes read."""
        if not select.select([self.fd], [], [], timeout)[0]:
            return 0
        try:
            chunk = os.read(self.fd, 4096)
        except (BlockingIOError, OSError):
            return 0
        self.rx += chunk
        self._trace("<--", chunk)
        return len(chunk)

    def drain(self, limit=3.0):
        """Read until the line has stayed quiet for a few character times."""
        deadline = time.time() + limit
        while time.time() < deadline and self.poll(self.quiet_time):
            pass

    def _await(self, count):
        """Read until count bytes are in; on silence, conclude the link has no echo."""
        deadline = time.time() + self.echo_timeout
        while len(self.rx) < count and time.time() < deadline:
            self.poll(self.char_time)
        if len(self.rx) < count:
            self.pacing.echo = False

    def _put(self, data):
        """Write what the device will take now; return how much that was."""
        try:
            done = os.write(self.fd, data)
        except BlockingIOError:
            select.select([], [self.fd], [], 1.0)
            return 0
        self._trace("-->", data[:done])
        return done

    def write(self, data):
        """Send bytes paced by the frame or the echo; the index of a bad echo, or None.

        The KIM-1 echoes in hardware, so the echo is exactly what was sent unless
        the monitor itself transmitted meanwhile.
        """
        base, sent, checked = len(self.rx), 0, 0
        lead = self.pacing.window if self.pacing.stream else self.pacing.echo_lead
        while sent < len(data):
            if self.pacing.echo:
                self._await(base + sent + 1 - lead)
                got = self.rx[base + checked : base + sent]
                if got != data[checked : checked + len(got)]:
                    break
                checked += len(got)
                room = len(self.rx) - base + lead - sent
            elif self.pacing.stream:
                room = len(data)
            else:
                time.sleep(self.pacing.char_delay)
                room = 1
            sent += self._put(data[sent : sent + max(room, 0)])
            self.poll(0)
        if not self.pacing.echo:
            return None
        self._await(base + sent)
        got = bytes(self.rx[base : base + sent])
        return next((i for i, (a, b) in enumerate(zip(got, data)) if a != b), None)

    def send(self, data):
        """Send bytes as fast as the line takes them, unpaced."""
        view = memoryview(data)
        while view:
            view = view[self._put(view) :]

    def take(self):
        """Return everything received so far and clear the buffer."""
        out, self.rx = bytes(self.rx), bytearray()
        return out

    def close(self):
        """Release the device."""
        os.close(self.fd)
