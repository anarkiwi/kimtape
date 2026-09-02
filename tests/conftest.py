"""Shared helpers: a fake KIM-1 on a pty, synthetic tapes, and a CLI runner."""

import os
import random
import subprocess
import sys

import pytest

from kimtape import Tape
from kimtape.cli import main

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "fake_kim.py")
BAUD = "115200"  # a pty ignores the rate; this only keeps the pacing delays small


class Board:
    """A fake KIM-1 monitor on a pty, and the memory it ends up holding."""

    def __init__(self, drop=0, mute=False):
        self.proc = subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, FAKE, str(drop)] + (["mute"] if mute else []),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=True,
        )
        self.dev = self.proc.stdout.readline().strip()
        self.mem, self.go, self.done = {}, None, False

    def finish(self):
        """Stop the board and collect what it recorded."""
        if self.done:
            return self
        self.done = True
        for line in self.proc.communicate()[0].splitlines():
            if line.startswith("GO="):
                self.go = None if line[3:] == "none" else int(line[3:], 16)
            elif " " in line and not line.startswith("BYTES"):
                addr, value = line.split()
                self.mem[int(addr, 16)] = int(value, 16)
        return self

    def kill(self):
        """Stop the board without collecting anything."""
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


@pytest.fixture(name="board")
def board_fixture():
    """A fake board, torn down after the test."""
    board = Board()
    yield board
    board.kill()


@pytest.fixture(name="cli")
def cli_fixture(capfd):
    """Run the CLI in process so that coverage sees it."""

    def run(*args):
        code = 0
        try:
            code = main(list(args))
        except SystemExit as exc:
            if isinstance(exc.code, str):  # SystemExit("message") is an error
                sys.stderr.write(exc.code + "\n")
                code = 1
            else:
                code = exc.code or 0
        sys.stderr.flush()
        sys.stdout.flush()
        captured = capfd.readouterr()
        return code, captured.out, captured.err

    return run


def sample_tape(path, addr, size, seed=1):
    """Write a tape of pseudo-random bytes and return the memory it carries."""
    rng = random.Random(seed)
    mem = {addr + i: rng.randrange(256) for i in range(size)}
    with open(path, "wb") as out:
        out.write(Tape.from_memory(os.path.basename(path), mem).text())
    return mem


def ihex(records):
    """Assemble Intel HEX text from (addr, kind, data) triples."""
    out = []
    for addr, kind, data in records:
        body = bytes([len(data), addr >> 8, addr & 0xFF, kind]) + data
        out.append(b":" + (body + bytes([(-sum(body)) & 0xFF])).hex().upper().encode())
    return b"\n".join(out)


def srec(records):
    """Assemble S1 records from (addr, data) pairs, with an S9 terminator."""
    out = []
    for addr, data in records:
        body = bytes([len(data) + 3, addr >> 8, addr & 0xFF]) + data
        out.append(b"S1" + (body + bytes([~sum(body) & 0xFF])).hex().upper().encode())
    return b"\n".join(out + [b"S9030000FC"])
