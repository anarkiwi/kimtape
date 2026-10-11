"""The assembled XMODEM stub, run on a simulated KIM-1 with bit-level TTY lines."""

import random

import pytest
from kimsim import START, Board, Host

from kimtape import bootstrap
from kimtape.stub import STUBS
from kimtape.xmodem import ACK, CAN, CRC, EOT, MONITOR, NAK, Failed, Rejected, Sender

ZP, NZP = STUBS[19200]["zero_page"]
CNTL30 = 0x17F2
LONG_WAIT = 256 * 256 * 11


def image(seed, *spans):
    """Pseudo-random bytes over each (addr, size) span."""
    rng = random.Random(seed)
    return {
        a: rng.randrange(256) for addr, size in spans for a in range(addr, addr + size)
    }


class Rig:  # pylint: disable=too-few-public-methods
    """A board with the stub placed for mem, and a host to feed it."""

    def __init__(self, rate, mem, err=0.0, gaps=0.0, go=MONITOR, **sender):
        self.stub = STUBS[rate]
        self.code, self.data = bootstrap.layout(self.stub, mem)
        rng = random.Random(rate)
        period = 1e6 / rate * (1 + err)
        self.board = Board(
            period,
            bootstrap.stop_bits(self.stub),
            gap=lambda: rng.random() * gaps * period,
        )
        self.board.load(self.code, bootstrap.relocate(self.stub, self.code, self.data))
        self.board.load(0, bytes(rng.randrange(256) for _ in range(0x100)))
        self.board.load(CNTL30, b"\x35\x00")
        self.zero_page = bytes(self.board.mem.data[:0x100])
        self.sender = Sender(
            bootstrap.payload(self.stub, mem, self.data, go), echo=True, **sender
        )
        self.host = Host(self.board, self.sender, latency=300)
        self.board.mpu.pc = self.code + self.stub["entry"]

    def run(self, *stops):
        """Run to START or another stop, then let the host hear the rest."""
        pc = self.host.converse({START, *stops})
        self.board.mpu.processorCycles += 20 * self.board.uart.period
        self.host.poll()
        return pc

    def loaded(self, mem):
        """True if memory holds mem."""
        return all(self.board.mem.data[a] == v for a, v in mem.items())

    def untouched(self, *spare):
        """True if the zero page and CNTL30/CNTH30 are as they were."""
        zp = self.board.mem.data[:0x100]
        return self.board.mem.data[CNTL30 : CNTL30 + 2] == b"\x35\x00" and all(
            zp[a] == self.zero_page[a] for a in range(0x100) if a not in spare
        )


@pytest.mark.parametrize("rate", sorted(STUBS))
@pytest.mark.parametrize("err", [-0.03, 0.0, 0.03])
def test_loads_at_each_rate_within_three_percent(rate, err):
    mem = image(rate, (0x0000, 0x40), (0x0200, 700), (0x0700, 300))
    rig = Rig(rate, mem, err=err, gaps=2.0)
    assert rig.run() == START
    assert rig.sender.done and rig.sender.errors == 0
    assert rig.loaded(mem)
    assert rig.untouched(*range(0x40))


def test_runs_the_program_with_the_zero_page_restored():
    mem = image(1, (0x0400, 500), (ZP, NZP))
    rig = Rig(19200, mem, go=0x0400)
    assert rig.run(0x0400) == 0x0400
    assert rig.loaded(mem)
    assert rig.untouched(*range(ZP, ZP + NZP))
    assert rig.board.mpu.sp == 0xFF


def test_one_stop_bit_is_too_few_at_38400_from_a_fast_host():
    mem = image(2, (0x0400, 300))
    rig = Rig(38400, mem, err=-0.03)
    rig.board.uart.stop = 1
    rig.sender.retries = 2
    with pytest.raises(Failed):
        rig.run()


def test_corrupted_frame_is_rejected_and_sent_again():
    mem = image(3, (0x0200, 600))
    rig = Rig(19200, mem)
    receive, spoiled = rig.sender.receive, []

    def spoil(byte):
        out = receive(byte)
        if len(out) == 133 and rig.sender.index == 2 and not spoiled:
            spoiled.append(out)
            out = out[:40] + bytes([out[40] ^ 1]) + out[41:]
        return out

    rig.sender.receive = spoil
    assert rig.run() == START
    assert spoiled and rig.loaded(mem)
    assert NAK in rig.host.heard


def test_lost_byte_is_recovered_by_filling():
    mem = image(4, (0x0200, 400))
    rig = Rig(19200, mem)
    rig.host.timeout = 200_000
    receive, cut = rig.sender.receive, []

    def lose(byte):
        out = receive(byte)
        if len(out) == 133 and not cut:
            cut.append(out[90])
            out = out[:90] + out[91:]
            rig.sender.skip -= 1
        return out

    rig.sender.receive = lose
    assert rig.run() == START
    assert cut and rig.loaded(mem)


def test_lost_ack_is_not_stored_twice():
    mem = image(5, (0x0200, 300), (0x0400, 2))
    rig = Rig(19200, mem)
    rig.host.timeout = 200_000
    receive, lost = rig.sender.receive, []

    def deaf(byte):
        if byte == ACK and rig.sender.skip == 0 and rig.sender.index == 1 and not lost:
            lost.append(byte)
            return b""
        return receive(byte)

    rig.sender.receive = deaf
    assert rig.run() == START
    assert lost and rig.loaded(mem)


def test_memory_that_does_not_hold_is_reported():
    mem = image(6, (0x13F0, 0x20))
    rig = Rig(19200, mem)
    with pytest.raises(Rejected) as err:
        rig.run()
    assert err.value.addr == 0x1400
    assert rig.board.run(10_000, {START}) == START
    assert rig.untouched()


def test_cancel_returns_to_the_monitor():
    rig = Rig(19200, image(7, (0x0200, 10)))
    rig.sender.receive = lambda byte: bytes([CAN, CAN]) if byte == CRC else b""
    assert rig.run() == START
    assert rig.untouched()


def test_unanswered_stub_gives_up():
    rig = Rig(9600, image(8, (0x0200, 10)))
    rig.sender.receive = lambda byte: b""
    assert rig.run() == START
    assert rig.host.heard == bytes([CRC]) * 6
    assert rig.board.mpu.processorCycles > 5 * LONG_WAIT
    assert rig.untouched()


def test_junk_is_waited_out_before_asking_again():
    rig = Rig(19200, image(9, (0x0200, 200)))
    receive, noisy = rig.sender.receive, []

    def noise(byte):
        if not noisy:
            noisy.append(byte)
            return bytes([0x55, 0x01, 0xAA])
        return receive(byte)

    rig.sender.receive = noise
    rig.sender.echo = False
    rig.board.echo = False
    assert rig.run() == START
    assert rig.sender.done


def test_eot_before_the_run_ends_is_not_believed():
    mem = image(10, (0x0200, 50))
    rig = Rig(19200, mem)
    receive, early = rig.sender.receive, []

    def hasty(byte):
        if byte == CRC and not early:
            early.append(byte)
            rig.sender.skip += 1
            return bytes([EOT])
        return receive(byte)

    rig.sender.receive = hasty
    assert rig.run() == START
    assert early and rig.sender.done and rig.loaded(mem)
