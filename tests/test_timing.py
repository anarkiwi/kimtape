"""The pacing rule against kimsim.Listing, the monitor's L at the bit level."""

import random

import pytest
from kimsim import Listing, Uart
from measure import stream

from kimtape import timing
from kimtape.tape import Tape, checksum

RATES = (1200, 2400, 4800)


def sample(size=600, at=0x02F0, seed=3):
    """Random bytes at an address whose records cross a page."""
    rng = random.Random(seed)
    return {at + i: rng.randrange(256) for i in range(size)}


def listen(baud, frame, data, error=0.0, count=None):
    """The Listing after hearing data sent back to back in frames of frame bits."""
    bit = timing.bit_time(baud)
    uart = Uart(bit * (1 + error), frame - 9)
    uart.send(data, 1000)
    model = Listing(timing.best_count(bit) if count is None else count, uart.line)
    model.run(0)
    return model


def loaded(model, mem):
    """True if the model acknowledged the load and holds mem."""
    return model.reply == "KIM" and all(model.mem.get(a) == v for a, v in mem.items())


@pytest.mark.parametrize(
    "baud,counts", [(1200, [55]), (2400, [25, 26]), (4800, [10, 11]), (9600, [3])]
)
def test_autobaud_matches_what_rst_measures(baud, counts):
    assert timing.autobaud(timing.bit_time(baud)) == counts


@pytest.mark.parametrize(
    "baud,frame", [(1200, 11), (2400, 11), (4800, 11), (9600, None)]
)
def test_frames_for_standard_rates(baud, frame):
    assert timing.frame_bits(baud, timing.best_count(timing.bit_time(baud))) == frame


def test_limits_of_the_monitor_uart():
    assert timing.receives(9600, 3)
    assert not timing.receives(19200, 0)
    assert not timing.receives(4800, 11)  # RST's other count is 4.6% slow
    assert timing.frame_bits(4800, 11) is None
    assert timing.frame_bits(19200, 0) is None


@pytest.mark.parametrize("baud", RATES)
@pytest.mark.parametrize("error", [-timing.TOLERANCE, 0.0, timing.TOLERANCE])
def test_streaming_at_the_computed_frame_never_drops(baud, error):
    mem = sample()
    frame = timing.frame_bits(baud, timing.best_count(timing.bit_time(baud)))
    model = listen(baud, frame, stream(mem, timing.RECORD), error)
    assert loaded(model, mem)


@pytest.mark.parametrize("baud", RATES)
def test_one_bit_less_drops(baud):
    mem = sample()
    frame = timing.frame_bits(baud, timing.best_count(timing.bit_time(baud)))
    model = listen(baud, frame - 1, stream(mem, timing.RECORD))
    assert not loaded(model, mem)


def test_the_slower_count_needs_another_bit():
    mem = sample()
    assert not loaded(listen(4800, 11, stream(mem, timing.RECORD), count=11), mem)
    assert loaded(listen(4800, 12, stream(mem, timing.RECORD), count=11), mem)


COMBOS = [
    (baud, count, frame)
    for baud in (1200, 1800, 2400, 4800, 9600)
    for count in timing.autobaud(timing.bit_time(baud))
    for frame in timing.FRAMES
]


@pytest.mark.parametrize("baud,count,frame", COMBOS)
def test_lateness_rule_agrees_with_the_listing(baud, count, frame):
    """The analytic rule passes exactly the frames the bit level model loads."""
    bit, mem = timing.bit_time(baud), sample()
    late = timing.lateness(bit, count, frame, timing.record_classes(255) * 2)
    rule = late < timing.window(bit, count)[1]
    assert rule == loaded(listen(baud, frame, stream(mem, 255), count=count), mem)


def test_longest_records_load():
    mem = sample(255 * 3, 0x0200)
    assert len(Tape.from_memory("m", mem, timing.RECORD).records) == 3
    assert loaded(listen(2400, 11, stream(mem, timing.RECORD)), mem)


def test_checksum_error_is_reported():
    data = b"L;010300AA%04X" % (checksum(1, 0x0300, b"\xaa") ^ 1)
    assert listen(1200, 11, data).reply == "ERR"
    data = b"L;010300AA%04X" % (checksum(1, 0x0300, b"\xaa") ^ 0x100)
    assert listen(1200, 11, data + b"00").reply == "ERR"


def test_listing_costs_match_the_table():
    """Every character costs at most its class's AFTER, and each worst case occurs."""
    data = bytes([0xB1] + [0xAA] * 8 + [0x1F])  # ends on $0AFF, checksum $072A
    record = b";0A0AF6%s%04X" % (
        data.hex().upper().encode(),
        checksum(10, 0x0AF6, data),
    )
    head = ["command", "command", "mark", "digit", "count", "digit", "addrh"]
    body = head + ["digit", "addrl"] + ["digit", "data"] * 10
    body += ["digit", "sumh", "digit", "suml", "wait"]
    classes = body + head[2:] + ["digit", "addrl", "digit", "sumh", "digit", "suml"]
    model = listen(1200, 12, b"AL" + record + b"\r;00000A000A")
    assert model.reply == "KIM" and len(model.chars) == len(classes)
    worst = {}
    for kind, (_, _, _, cost) in zip(classes, model.chars):
        worst[kind] = max(worst.get(kind, 0), cost or 0)
    bad = b"L;010300AA%04X00" % (checksum(1, 0x0300, b"\xaa") ^ 0xA00)
    worst["sumh"] = listen(1200, 12, bad).chars[11][3]
    assert worst == timing.AFTER


def test_lock_step_gaps_load_where_no_frame_will():
    mem = sample(300)
    bit = timing.bit_time(9600)
    uart = Uart(bit, 1, gap=lambda: 1000)
    uart.send(stream(mem, timing.RECORD), 1000)
    model = Listing(timing.best_count(bit), uart.line)
    model.run(0)
    assert loaded(model, mem)
