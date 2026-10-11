"""Loading through the XMODEM stub: placing it, starting it, and feeding it.

The stub goes in through the monitor's paper tape loader, then receives the
program over XMODEM-CRC at a fixed fast rate and returns to the monitor or
runs the program.  docs/protocol.md has the details.
"""

import time

from . import stub as images
from .tape import Tape
from .xmodem import BLOCK, CAN, MONITOR, Failed, Rejected, Sender, segments

# all KIM-1s have $0000-$03FF, expanded ones $0400-$13FF; $01C0-$01FF is stack
CODE_REGIONS = ((0x0200, 0x0400), (0x0400, 0x1400))
DATA_REGIONS = ((0x0100, 0x01C0),) + CODE_REGIONS
TURNAROUND = 32  # cycles from a byte's last data sample to the next start poll
STUB_LIFE = 6.0  # seconds an unanswered stub waits before returning
START_WAIT = 3.0  # seconds for the stub to ask for the first block


def stop_bits(stub):
    """Stop bits the host sends so the stub polls before the next start bit.

    The slack after the turnaround must cover a host fast by 5% over a frame.
    """
    return 1 if 1.5 * stub["cycles"] - TURNAROUND >= 0.5 * stub["cycles"] else 2


def relocate(stub, code, data):
    """The stub's code assembled for these code and data addresses."""
    image = bytearray(stub["code"])
    for relocs, base, org in (
        (stub["code_relocs"], code, images.CODE_ORG),
        (stub["data_relocs"], data, images.DATA_ORG),
    ):
        for hi in relocs:
            word = (image[hi - 1] | image[hi] << 8) - org + base
            image[hi - 1 : hi + 1] = (word & 0xFFFF).to_bytes(2, "little")
    return bytes(image)


def critical_fits(stub, code):
    """True if the stub's timed loops sit on one page at this address."""
    lo, hi = stub["critical"]
    return (code + lo) >> 8 == (code + hi - 1) >> 8


def place(size, regions, used, fits=lambda base: True):
    """Highest free base for size bytes in the first region that has one."""
    for lo, hi in regions:
        for base in range(hi - size, lo - 1, -1):
            if used.find(1, base, base + size) < 0 and fits(base):
                return base
    return None


def layout(stub, mem, code=None):
    """(code, data) addresses clear of mem and of each other, or None."""
    used = bytearray(0x10000)
    for addr in mem:
        used[addr] = 1
    size = len(stub["code"])
    if code is None:
        code = place(size, CODE_REGIONS, used, lambda b: critical_fits(stub, b))
        if code is None:
            return None
    used[code : code + size] = b"\1" * size
    data = place(stub["data_size"], DATA_REGIONS, used)
    return None if data is None else (code, data)


def payload(stub, mem, data, go):
    """The segment stream, bytes for the stub's zero page sent to its save area.

    They land when the stub restores the zero page on its way out.
    """
    zp, count = stub["zero_page"]
    return segments(
        {
            (data + addr - zp if zp <= addr < zp + count else addr): value
            for addr, value in mem.items()
        },
        go,
    )


def transfer(port, sender, log, wait=1.0, start=None):
    """Run sender over the port until the receiver acknowledges EOT.

    Allows ``start`` seconds for the receiver to ask and ``wait`` seconds past
    each transmission for an answer; raises xmodem.Failed.
    """
    deadline = time.time() + (START_WAIT if start is None else start)
    began, shown = None, -1
    while not sender.done:
        if not port.poll(max(0.0, deadline - time.time())):
            if time.time() >= deadline:
                port.send(sender.expire())
                deadline = time.time() + (BLOCK + 5) * port.char_time + wait
            continue
        for byte in port.take():
            out = sender.receive(byte)
            if out:
                port.send(out)
                deadline = time.time() + len(out) * port.char_time + wait
                began = began or time.time()
        if sender.sent != shown:
            shown = sender.sent
            log.write(
                "\r  block %d/%d, %.0f bytes/s"
                % (shown, sender.total, shown * BLOCK / max(time.time() - began, 1e-3))
            )
            log.flush()
    log.write("\n")


class Bootstrap:  # pylint: disable=too-few-public-methods
    """Loads a memory image through the stub, trying each rate in turn."""

    def __init__(self, mon, rates, code=None):
        self.mon, self.rates, self.code = mon, rates, code

    def load(self, mem, go=MONITOR):
        """True once mem is loaded; False if no rate worked and hex is left.

        Raises SystemExit if memory refuses a write, which hex would not cure.
        """
        for rate in self.rates:
            stub = images.STUBS[rate]
            where = layout(stub, mem, self.code)
            if where is None:
                self.mon.say("no free RAM for the %d byte stub", len(stub["code"]))
                return False
            if self._attempt(stub, rate, where, mem, go):
                return True
        return False

    def _attempt(self, stub, rate, where, mem, go):  # pylint: disable=R0913,R0917
        mon, port = self.mon, self.mon.p
        code, data = where
        image = relocate(stub, code, data)
        tape = Tape.from_memory("xmodem stub", dict(zip(range(code, 0x10000), image)))
        mon.load(tape)
        if not mon.verify(tape):
            mon.say("the stub did not load")
            return False
        mon.go(code + stub["entry"])
        monitor_rate = port.baud
        port.set_rate(rate, stop_bits(stub))
        mon.say("xmodem at %d baud", rate)
        try:
            transfer(port, Sender(payload(stub, mem, data, go), port.echo), mon.log)
        except Rejected as err:
            port.set_rate(monitor_rate)
            mon.await_monitor(STUB_LIFE)
            raise SystemExit(
                "kimtape: %s.  Is there RAM there?  On a PAL-II, SW4 (K1-K4) "
                "enables $0400-$13FF and SW5 positions 2-8 $2000 up." % err
            ) from None
        except Failed as err:
            mon.say("\n  %s at %d baud", err, rate)
            port.send(bytes([CAN, CAN]))
            port.set_rate(monitor_rate)
            if not mon.await_monitor(STUB_LIFE):
                raise SystemExit(
                    "kimtape: the monitor did not come back; press RS and retry"
                ) from None
            return False
        port.set_rate(monitor_rate)
        if go == MONITOR:
            mon.await_monitor(STUB_LIFE)
        return True
