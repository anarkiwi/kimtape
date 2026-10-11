"""The KIM-1 monitor's software UART, timed in 1 MHz cycles from its listing.

GETCH hears nothing while the monitor works on the character it returned, so
the host's frame must cover that work.  docs/protocol.md derives each number.
"""

import math

CLOCK = 1_000_000
POLL = 9  # GET1: BIT SAD, BNE, BMI
RECORD = 255  # LOAD counts the data bytes of a record down in X
FRAMES = (10, 11, 12)  # 8N1, 8N2, 8 data + mark parity + 2 stop
TOLERANCE = 0.01  # host bit rate error allowed for on top of the model
RECEIVER = (0.5 - 1 / 16) / 9.5  # bit time mismatch a 16x oversampler decodes

AFTER = {  # cycles from GETCH's return to its next poll, hex letter, INCPT carry
    "command": 165,  # a hex digit at the command prompt: READ, PACK, SCAN
    "wait": 21,  # anything but ';' between records: LOAD
    "mark": 34,  # ';'
    "digit": 123,  # the first digit of any byte: GETBYT, PACK
    "count": 168,  # TAX, CHK
    "addrh": 169,  # STA POINTH, CHK
    "addrl": 174,  # STA POINTL, CHK, TXA, BEQ LOAD3
    "data": 202,  # STA (POINTL),Y, CHK, INCPT, DEX, BNE, INX
    "sumh": 144,  # CMP CHKHI, then LOADE1 on a mismatch
    "suml": 143,  # CMP CHKSUM, TXA, BNE LOAD across a page
}


def delay(count):
    """Cycles in JSR DELAY with CNTH30 zero: 14 a pass, one pass more than CNTL30."""
    return 14 * count + 42


def half(count):
    """Cycles in JSR DEHALF with CNTH30 zero: DELAY's loop for half the count."""
    return 14 * (count >> 1) + 53


def period(count):
    """GETCH's and OUTCH's bit time: DELAY plus 22 cycles of sampling."""
    return delay(count) + 22


def back(count):
    """Cycles from detecting a start bit to GETCH's return: about ten bits."""
    return 9 * delay(count) + 2 * half(count) + 196


def window(bit, count):
    """Range of start bit detection lateness over which all 8 samples land.

    The first sample follows detection by DELAY, DEHALF and 8 cycles, the rest
    come a period apart; data bit i occupies [(i+1)*bit, (i+2)*bit).
    """
    first, step = delay(count) + half(count) + 8, period(count)
    return (
        max((i + 1) * bit - first - i * step for i in range(8)),
        min((i + 2) * bit - first - i * step for i in range(8)),
    )


def autobaud(bit):
    """The CNTL30 values RST can measure from a CR at this host bit time.

    DET3 counts the start bit in 14 cycle passes, 5 more for the one carrying
    into CNTH30, the first 17 cycles after a poll 0-8 late; CNTL30 is passes-4.
    """
    return sorted({math.ceil((bit - late - 22) / 14) - 3 for late in (0, POLL - 1e-6)})


def best_count(bit):
    """The CNTL30 whose bit time is closest to the host's."""
    return max(0, min(255, round((bit - period(0)) / 14)))


def record_classes(count):
    """What L reads, by class, for 'L' and one data record of count bytes."""
    head = ["command", "mark", "digit", "count", "digit", "addrh", "digit", "addrl"]
    return head + ["digit", "data"] * count + ["digit", "sumh", "digit", "suml"]


def lateness(bit, count, frame, classes):
    """Worst start bit detection lateness over a stream of characters.

    Each character keeps the monitor busy for back() plus AFTER after its start
    bit was seen; whatever the frame does not cover carries into the next.
    """
    busy, late, worst = back(count), POLL, POLL
    for kind in classes:
        late = max(POLL, late + busy + AFTER[kind] - frame * bit)
        worst = max(worst, late)
    return worst


def bit_time(baud):
    """Host bit time in cycles."""
    return CLOCK / baud


def _extremes(baud):
    bit = bit_time(baud)
    return bit * (1 - TOLERANCE), bit * (1 + TOLERANCE)


def receives(baud, count):
    """True if the monitor and host can read each other a character at a time."""
    return abs(period(count) * baud / CLOCK - 1) <= RECEIVER - TOLERANCE and all(
        POLL < window(bit, count)[1] for bit in _extremes(baud)
    )


def frame_bits(baud, count, records=RECORD):
    """Fewest bits a character for streamed records, or None if no frame will do."""
    if not receives(baud, count):
        return None
    stream = record_classes(records) * 2
    for frame in FRAMES:
        if all(
            lateness(bit, count, frame, stream) < window(bit, count)[1]
            for bit in _extremes(baud)
        ):
            return frame
    return None
