"""Serial port with pacing suited to a monitor that bit-bangs its UART.

The KIM-1 monitor has no receive buffer: it samples the line in software and can
hold exactly one character.  Writes are therefore clocked by the monitor's own
echo, in lock-step by default.  Where the link does not echo, a fixed delay per
character stands in.
"""

import os
import select
import sys
import termios
import time
from dataclasses import dataclass

BITS = 10  # 8N1: start + 8 data + stop


@dataclass
class Pacing:
    """How fast characters may be pushed at the monitor.

    ``echo`` selects the regime: clock writes against the monitor's echo, or
    fall back to a fixed delay.  ``echo_lead`` is how many characters the echo
    may lag behind; 1 is lock-step, which is what the monitor's software UART
    needs.  The delays apply only when there is no echo to clock against.
    """

    char_delay: float = None
    line_delay: float = 0.25
    echo_lead: int = 1
    echo: bool = True


class Port:
    """A raw 8N1 serial port.  Standard library only, no pyserial."""

    def __init__(self, dev, baud, pacing=None, trace=False):
        try:
            speed = getattr(termios, "B%d" % baud)
        except AttributeError:
            raise SystemExit("kimtape: unsupported baud rate %d" % baud) from None
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
        self.char_time = BITS / float(baud)
        self.pacing = pacing or Pacing()
        if self.pacing.char_delay is None:
            self.pacing.char_delay = self.char_time
        # quiet detection cannot be tighter than the scheduler can wake us: at
        # 1200 baud four character times dominate, but on a fast link it would
        # otherwise call the line idle before the far end had begun replying
        self.quiet_time = max(4 * self.char_time, 0.005)
        self.echo_timeout = max(0.5, 40 * self.char_time)
        self.trace = trace
        self.rx = bytearray()

    @property
    def echo(self):
        """Whether writes are currently clocked against the monitor's echo."""
        return self.pacing.echo

    @property
    def line_delay(self):
        """Pause after each tape record when there is no echo to clock against."""
        return self.pacing.line_delay

    def _configure(self, speed):
        cc = list(termios.tcgetattr(self.fd)[6])
        cc[termios.VMIN], cc[termios.VTIME] = 0, 0
        termios.tcsetattr(
            self.fd,
            termios.TCSANOW,
            [0, 0, termios.CS8 | termios.CREAD | termios.CLOCAL, 0, speed, speed, cc],
        )
        termios.tcflush(self.fd, termios.TCIOFLUSH)

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

    def write(self, data):
        """Send bytes, paced by the monitor's echo or else by the clock."""
        base = len(self.rx)
        for i in range(len(data)):
            if self.pacing.echo:
                target = base + i + 1 - self.pacing.echo_lead
                deadline = time.time() + self.echo_timeout
                while len(self.rx) < target and time.time() < deadline:
                    self.poll(self.char_time)
                if len(self.rx) < target:
                    # no echo on this link: pace on the clock instead
                    self.pacing.echo = False
            if not self.pacing.echo:
                time.sleep(self.pacing.char_delay)
            os.write(self.fd, data[i : i + 1])
            self._trace("-->", data[i : i + 1])
            self.poll(0)

    def take(self):
        """Return everything received so far and clear the buffer."""
        out, self.rx = bytes(self.rx), bytearray()
        return out

    def close(self):
        """Release the device."""
        os.close(self.fd)
