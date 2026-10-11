#!/usr/bin/env python3
"""Measure the XMODEM stub on the simulated KIM-1: bit rate tolerance and speed.

tolerance: the host bit rate errors, in quarter percent steps, at which a load
completes without a single retry.  throughput: bytes per second for a payload,
from the first request to the final ACK, with a given host turnaround.
"""

import argparse
import random

from kimsim import START, Board, Host
from tqdm import tqdm

from kimtape import bootstrap
from kimtape.stub import STUBS
from kimtape.tape import CHUNK
from kimtape.xmodem import MONITOR, Failed, Sender

HEX_BAUD = 1200


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


def hex_seconds(size):
    """Seconds to send size bytes as 24 byte records at HEX_BAUD, 8N1."""
    records = -(-size // CHUNK)
    chars = size * 2 + records * (1 + 2 + 4 + 4 + 2)
    return chars * 10 / HEX_BAUD


def main():
    """Print the measurements as markdown tables."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("what", choices=("tolerance", "throughput"))
    ap.add_argument("--size", type=int, default=17 * 1024)
    args = ap.parse_args()
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
    seconds = hex_seconds(args.size)
    print(
        "| hex records %d | - | %.0f | %.0f |"
        % (HEX_BAUD, seconds, args.size / seconds)
    )


if __name__ == "__main__":
    main()
