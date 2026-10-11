#!/usr/bin/env python3
"""Measure loading on the simulated KIM-1: stub tolerance, speed, monitor model.

tolerance: host bit rate errors at which the XMODEM stub loads without retry.
throughput: seconds and bytes/s by method.  rom: check kimsim.Listing against
a monitor ROM image ($1800-$1FFF), character by character, cycle for cycle.
"""

import argparse
import random

from kimsim import START, Board, Host, Line, Listing, Uart, dump_cycles, outch_cycles
from tqdm import tqdm

from kimtape import bootstrap, timing
from kimtape.stub import STUBS
from kimtape.tape import CHUNK, Tape, end_record
from kimtape.xmodem import MONITOR, Failed, Sender

RST, GET1, GET6 = 0x1C22, 0x1E60, 0x1E87  # entry points in the monitor listing
POINTL, EAL = 0xFA, 0x17F7


def trial(rate, err=0.0, size=1500, latency=300, stop=None):  # pylint: disable=R0913
    """Load size bytes at $2000; (clean, seconds from the first frame) or None."""
    stub, rng = STUBS[rate], random.Random(size)
    mem = {0x2000 + i: rng.randrange(256) for i in range(size)}
    code, data = bootstrap.layout(stub, mem)
    board = Board(
        1e6 / rate * (1 + err),
        bootstrap.stop_bits(stub) if stop is None else stop,
        ram=((0x0000, 0xFFFF),),
    )
    board.load(code, bootstrap.relocate(stub, code, data))
    sender = Sender(bootstrap.payload(stub, mem, data, MONITOR), echo=True, retries=2)
    host = Host(board, sender, latency=latency)
    board.mpu.pc = code + stub["entry"]
    begin = None
    try:
        while board.run(200, {START}) != START:
            host.poll()
            if begin is None and sender.started:
                begin = board.mpu.processorCycles
    except Failed:
        return None
    if begin is None:
        return None
    board.mpu.processorCycles += 20 * board.uart.period
    host.poll()
    clean = sender.done and not sender.errors
    clean = clean and all(board.mem.data[a] == v for a, v in mem.items())
    return clean, (board.mpu.processorCycles - begin) / 1e6


def tolerance(rate, stop=None):
    """Lowest and highest error in percent with a clean load, and any holes."""
    steps = [x / 4 for x in range(-28, 29)]
    good = [
        pct
        for pct in tqdm(steps, desc="%d baud" % rate, leave=False)
        if (trial(rate, pct / 100, stop=stop) or (False,))[0]
    ]
    inside = [p for p in steps if good and good[0] <= p <= good[-1]]
    return good[0], good[-1], sorted(set(inside) - set(good))


def hex_seconds(mem, baud, way, verify, turnaround=1000):  # pylint: disable=R0913,R0914
    """Seconds to load mem with L at baud and check it, kimtape's old way or a new.

    old: 24 byte records and CRLF, each character after the last one's echo, a
    quiet wait a record.  stream, lock: 255 byte records, in the computed frame
    or after each echo.  Unless verify reads back with Q, new ways check a byte
    a record, as only the stub does.
    """
    bit = timing.bit_time(baud)
    count = timing.best_count(bit)
    frame = timing.frame_bits(baud, count) if way == "stream" else 10
    tape = Tape.from_memory("m", mem, CHUNK if way == "old" else timing.RECORD)
    end = end_record(len(tape.records))
    if way == "old":
        writes = [b"L"] + [r + b"\r\n" for r in tape.records] + [end + b"\r\n"]
    else:
        writes = [b"L"] + tape.records + [end]
    uart, edge = Uart(bit, frame - 9), -turnaround
    echo = 9.5 * bit + turnaround  # from a start bit to the host answering its echo
    quiet = max(4 * frame * bit, 5000) if way == "old" else 0
    for chunk in writes:
        edge += echo + quiet
        if way == "stream":
            uart.send(chunk, edge)
            edge = uart.busy - frame * bit
            continue
        for char in chunk:
            uart.send(bytes([char]), edge)
            edge = max(edge + echo, uart.busy)
    model = Listing(count, uart.line)
    cycles = model.run(0)
    assert model.reply == "KIM" and all(model.mem[a] == v for a, v in mem.items())
    typed = 9.5 * bit + turnaround + 16 * outch_cycles(count)  # a command, a prompt
    if verify:
        cycles += 4 * typed + dump_cycles(count, mem, min(mem), max(mem))
    elif way != "old":
        cycles += 5 * typed * len(tape.records) / 4
    return cycles / timing.CLOCK


def stream(mem, record, lead=b"L"):
    """The characters kimtape sends to load mem in records of record bytes."""
    tape = Tape.from_memory("m", mem, record)
    return lead + b"".join(tape.records) + end_record(len(tape.records))


def rom_trial(image, baud, frame, data):  # pylint: disable=too-many-locals
    """Run the ROM and the Listing on one line; (agree, characters, reply)."""
    board = Board(timing.bit_time(baud), frame - 9)
    board.load(0x1800, image)
    board.mpu.pc = RST
    board.run(2000)
    board.uart.send(b"\r", board.mpu.processorCycles)
    board.run(200 * timing.bit_time(baud))
    count = timing.best_count(timing.bit_time(baud))
    board.mem.data[0x17F2] = count
    mpu, polls, rom = board.mpu, [], []
    while mpu.pc != GET1:
        mpu.step()
    ready = mpu.processorCycles + 3
    board.uart.send(data, ready + 100)
    end = board.uart.busy + 300 * timing.bit_time(baud)
    while mpu.processorCycles < end:
        if mpu.pc == GET1:
            polls.append(mpu.processorCycles + 3)
        elif mpu.pc == GET6 and polls:
            rom.append((polls[-1], mpu.processorCycles + 6))
        mpu.step()
    line = Line()
    line.times, line.levels = board.uart.line.times, board.uart.line.levels
    model = Listing(count, line)
    model.run(ready)
    agree = rom[: len(model.chars)] == [(s, e) for _, s, e, _ in model.chars]
    if model.reply == "KIM":
        heard = bytes(byte for byte, _ in board.received())
        agree = agree and (len(model.chars) < len(data) or b"\x13 KIM" in heard)
        agree = agree and all(board.mem.data[a] == v for a, v in model.mem.items())
    return agree, len(model.chars), model.reply


def rom_dump(image, baud, lo, hi):
    """Cycles the ROM and dump_cycles give for Q over lo-hi, from Q to the next poll."""
    board = Board(timing.bit_time(baud), 1)
    board.load(0x1800, image)
    count = timing.best_count(timing.bit_time(baud))
    rng = random.Random(lo)
    mem = {a: rng.randrange(256) for a in range(lo, hi + 24)}
    for addr, value in mem.items():
        board.mem.data[addr] = value
    board.load(POINTL, bytes([lo & 0xFF, lo >> 8]))
    board.load(EAL, bytes([(hi + 1) & 0xFF, (hi + 1) >> 8]))
    board.load(0x17F2, bytes([count, 0]))
    mpu, back = board.mpu, None
    mpu.pc = 0x1C6A  # READ
    board.uart.send(b"Q", 100)
    while back is None or mpu.pc != GET1:
        if mpu.pc == GET6 and back is None:
            back = mpu.processorCycles + 6
        mpu.step()
    return mpu.processorCycles + 3 - back, dump_cycles(count, mem, lo, hi)


def rom_check(path):  # pylint: disable=too-many-locals
    """Compare the Listing with the ROM over records at each rate and frame."""
    with open(path, "rb") as handle:
        image = handle.read()
    rng = random.Random(7)
    mem = {0x02F0 + i: rng.randrange(256) for i in range(300)}
    bad = 0
    for baud in tqdm((1200, 2400, 4800, 9600), desc="rates"):
        for frame in timing.FRAMES:
            for data in (stream(mem, 255), stream(mem, 24), b"L;0102FE0A0B0C0"):
                agree, chars, reply = rom_trial(image, baud, frame, data)
                bad += not agree
                print(
                    "%5d baud %2d bit frame: %4d characters, %s, %s"
                    % (baud, frame, chars, reply, "agree" if agree else "DIFFER")
                )
    for baud, lo, hi in ((1200, 0x0200, 0x0230), (4800, 0x02F0, 0x0400)):
        rom, model = rom_dump(image, baud, lo, hi)
        bad += rom != model
        print(
            "%5d baud Q $%04X-$%04X: ROM %d cycles, model %d"
            % (baud, lo, hi, rom, model)
        )
    return bad


def main():
    """Print the measurements as markdown tables."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("what", choices=("tolerance", "throughput", "rom"))
    ap.add_argument("--size", type=int, default=17 * 1024)
    ap.add_argument("--image", help="rom: the monitor ROM, $1800-$1FFF")
    args = ap.parse_args()
    if args.what == "rom":
        raise SystemExit(rom_check(args.image))
    if args.what == "tolerance":
        print("| rate | stop bits | host error that loads cleanly |")
        print("| --- | --- | --- |")
        for rate in sorted(STUBS):
            for stop in sorted({1, bootstrap.stop_bits(STUBS[rate])}):
                lo, hi, holes = tolerance(rate, stop)
                print(
                    "| %d | %d | %+.2f%% to %+.2f%%%s |"
                    % (rate, stop, lo, hi, " except %s" % holes if holes else "")
                )
        return
    print("| method | turnaround | seconds | bytes/s |")
    print("| --- | --- | --- | --- |")
    for rate in tqdm(sorted(STUBS), desc="rates", leave=False):
        for latency in (1000, 16000):
            seconds = trial(rate, size=args.size, latency=latency)[1]
            print(
                "| XMODEM %d | %d ms | %.1f | %.0f |"
                % (rate, latency // 1000, seconds, args.size / seconds)
            )
    hex_table(args.size)


def hex_table(size):
    """Hex records old and new, for a program of size bytes and for the stub."""
    rng = random.Random(size)
    mem = {0x2000 + i: rng.randrange(256) for i in range(size)}
    stub = len(STUBS[19200]["code"])
    code = {0x0200 + i: rng.randrange(256) for i in range(stub)}
    print(
        "\n| rate | method | %dK program | bytes/s | with --verify | stub |"
        % (size // 1024)
    )
    print("| --- | --- | --- | --- | --- | --- |")
    ways = {
        "old": "lock-step, 24 byte records",
        "stream": "streamed, 255 byte records",
        "lock": "lock-step, 255 byte records",
    }
    for baud, way in tqdm(
        [(b, w) for b in (1200, 2400, 4800) for w in ("old", "stream")]
        + [(9600, "lock")],
        desc="hex",
        leave=False,
    ):
        load = hex_seconds(mem, baud, way, way == "old")
        print(
            "| %d | %s | %.0f s | %.0f | %.0f s | %.1f s |"
            % (
                baud,
                ways[way],
                load,
                size / load,
                hex_seconds(mem, baud, way, True),
                hex_seconds(code, baud, way, way == "old"),
            )
        )


if __name__ == "__main__":
    main()
