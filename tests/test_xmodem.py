"""The XMODEM-CRC sender against a reference receiver, over a lossy channel."""

import random

import pytest

from kimtape.xmodem import (
    ACK,
    CAN,
    CRC,
    EOT,
    FILL,
    MONITOR,
    NAK,
    SOH,
    SUB,
    Failed,
    Rejected,
    Sender,
    crc16,
    frames,
    segments,
)


class Receiver:  # pylint: disable=too-many-instance-attributes
    """Reference XMODEM-CRC receiver, storing the segment stream as the stub does."""

    def __init__(self, rom=()):
        self.frame, self.block, self.stream, self.rom = bytearray(), 1, bytearray(), rom
        self.mem, self.done, self.junk = {}, False, False

    def start(self):
        """The request that opens a transfer."""
        return bytes([CRC])

    def receive(self, byte):  # pylint: disable=too-many-return-statements
        """Take one byte; return the answer, if one is due."""
        if not self.frame and byte == EOT:
            self.done = True
            return bytes([ACK])
        if not self.frame and byte != SOH:
            self.junk = True
            return b""
        self.frame.append(byte)
        if len(self.frame) < 133:
            return b""
        frame, self.frame = bytes(self.frame), bytearray()
        number, data = frame[1], frame[3:131]
        if number ^ frame[2] != 0xFF or crc16(frame[3:133]) != 0:
            return bytes([NAK])
        if number == (self.block - 1) & 0xFF:
            return bytes([ACK])
        if number != self.block & 0xFF:
            return bytes([NAK])
        self.block += 1
        self.stream += data
        return self._store() or bytes([ACK])

    def idle(self):
        """The line has gone quiet: ask again if what came was not a frame."""
        junk, self.junk = self.junk, False
        return bytes([NAK]) if junk else b""

    def _store(self):
        mem, stream, at = {}, self.stream, 0
        while at + 4 <= len(stream):
            addr = int.from_bytes(stream[at : at + 2], "little")
            size = int.from_bytes(stream[at + 2 : at + 4], "little")
            if not size or at + 4 + size > len(stream):
                break
            for i in range(size):
                if addr + i in self.rom:
                    return bytes([CAN]) + (addr + i).to_bytes(2, "little")
                mem[addr + i] = stream[at + 4 + i]
            at += 4 + size
        self.mem = mem
        return b""


def run(sender, receiver, channel=lambda data: data, echo=False):
    """Shuttle bytes until the sender finishes; return how many waits expired."""
    pending, expired = receiver.start(), 0
    while not sender.done:
        reply = b""
        for byte in pending:
            reply += sender.receive(byte)
        if not pending:
            expired += 1
            reply = sender.expire()
        if echo:
            for byte in reply:
                assert not sender.receive(byte)
        pending = b"".join(receiver.receive(b) for b in channel(reply))
        pending += receiver.idle()
    return expired


def image(seed, *spans):
    """Pseudo-random bytes over each (addr, size) span."""
    rng = random.Random(seed)
    return {
        a: rng.randrange(256) for addr, size in spans for a in range(addr, addr + size)
    }


def test_crc_check_value():
    assert crc16(b"123456789") == 0x31C3


def test_frames_are_numbered_padded_and_checked():
    out = frames(bytes(range(200)))
    assert [len(f) for f in out] == [133, 133]
    assert out[1][:3] == bytes([SOH, 2, 0xFD])
    assert out[1][3 + 72 : 131] == bytes([SUB]) * 56
    assert crc16(out[0][3:]) == 0


def test_segments_encode_runs_and_the_go_address():
    stream = segments({0x10: 1, 0x11: 2, 0x300: 3}, go=0x0200)
    assert stream == bytes.fromhex("1000 0200 0102 0003 0100 03 0002 0000")
    assert segments({})[-4:] == MONITOR.to_bytes(2, "little") + b"\0\0"


def test_clean_transfer_with_echo():
    mem = image(1, (0x0000, 0xEF), (0x0200, 600), (0x1000, 5))
    receiver = Receiver()
    sender = Sender(segments(mem), echo=True)
    assert run(sender, receiver, echo=True) == 0
    assert receiver.mem == mem and receiver.done
    assert sender.sent == sender.total


def test_corrupted_frame_is_sent_again():
    mem = image(2, (0x0200, 1000))
    hits = []

    def corrupt(data):
        if len(data) == 133 and len(hits) < 3:
            hits.append(1)
            data = data[:50] + bytes([data[50] ^ 0x40]) + data[51:]
        return data

    sender = Sender(segments(mem))
    assert run(sender, Receiver(), corrupt) == 0
    assert len(hits) == 3


def test_lost_byte_is_filled_out_and_sent_again():
    mem = image(3, (0x0200, 400))
    receiver, lost = Receiver(), []

    def drop(data):
        if len(data) == 133 and not lost:
            lost.append(data[70])
            return data[:70] + data[71:]
        return data

    sender = Sender(segments(mem))
    assert run(sender, receiver, drop) == 1
    assert receiver.mem == mem


def test_lost_ack_gives_a_duplicate_that_is_not_stored_twice():
    mem = image(4, (0x0200, 300))
    receiver, acks = Receiver(), []
    real = receiver.receive

    def forgetful(byte):
        out = real(byte)
        if out == bytes([ACK]) and not acks:
            acks.append(1)
            return b""
        return out

    receiver.receive = forgetful
    sender = Sender(segments(mem))
    assert run(sender, receiver) == 1
    assert receiver.mem == mem


def test_cancel_reports_the_address():
    mem = image(5, (0x13F0, 0x20))
    with pytest.raises(Rejected) as err:
        run(Sender(segments(mem)), Receiver(rom=range(0x1400, 0x2000)))
    assert err.value.addr == 0x1400


def test_cancel_without_an_address_fails():
    sender = Sender(b"x")
    sender.receive(CRC)
    sender.receive(CAN)
    with pytest.raises(Failed, match="cancelled"):
        sender.expire()


def test_too_many_rejections_fail():
    sender = Sender(b"x", retries=2)
    sender.receive(CRC)
    sender.receive(NAK)
    sender.receive(NAK)
    with pytest.raises(Failed, match="rejected, 3 times"):
        sender.receive(NAK)


def test_silence_before_the_start_fails():
    with pytest.raises(Failed, match="no request"):
        Sender(b"x").expire()


def test_unanswered_eot_is_sent_again():
    sender = Sender(b"")
    assert sender.receive(CRC) == bytes([EOT])
    assert sender.expire() == bytes([EOT])
    assert not sender.receive(ord("?"))
    sender.receive(ACK)
    assert sender.done and not sender.receive(NAK)


def test_fill_cannot_open_a_frame():
    sender = Sender(b"x" * 10)
    sender.receive(CRC)
    assert set(sender.expire()) == {FILL}
