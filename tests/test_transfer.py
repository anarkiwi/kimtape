"""Transfers driven against the fake KIM-1 monitor on a pty."""

import os

import pytest
from conftest import BAUD, Board, sample_tape

from kimtape import Monitor, Port, Tape, bootstrap


def test_load_verify_and_go(cli, board, tmp_path):
    zp, prog = str(tmp_path / "zp.ptp"), str(tmp_path / "prog.ptp")
    want = sample_tape(zp, 0x0000, 0xEF, seed=2)
    want.update(sample_tape(prog, 0x0200, 600, seed=3))
    code, _, err = cli(
        "load", "--port", board.dev, "--baud", BAUD, "--hex", zp, prog, "--go", "0200"
    )
    board.finish()
    assert code == 0, err
    assert board.go == 0x0200
    assert {a: board.mem.get(a) for a in want} == want
    assert "ok, " in err


def test_dropped_record_is_retried(cli, tmp_path):
    prog = str(tmp_path / "drop.ptp")
    want = sample_tape(prog, 0x0300, 200, seed=4)
    board = Board(drop=3)
    try:
        code, _, err = cli("load", "--port", board.dev, "--baud", BAUD, "--hex", prog)
        board.finish()
    finally:
        board.kill()
    assert code == 0, err
    assert "retrying" in err
    assert {a: board.mem.get(a) for a in want} == want


def test_no_verify_skips_readback(cli, board, tmp_path):
    prog = str(tmp_path / "quick.ptp")
    want = sample_tape(prog, 0x0300, 60, seed=9)
    code, _, err = cli(
        "load", "--port", board.dev, "--baud", BAUD, "--hex", prog, "--no-verify"
    )
    board.finish()
    assert code == 0, err
    assert "verifying" not in err
    assert {a: board.mem.get(a) for a in want} == want


def test_unrecoverable_load_fails(cli, tmp_path):
    prog = str(tmp_path / "bad.ptp")
    sample_tape(prog, 0x0300, 100, seed=7)
    board = Board(drop=-1)  # drop every record
    try:
        code, _, err = cli(
            "load", "--port", board.dev, "--baud", BAUD, "--hex", prog, "--retries", "1"
        )
    finally:
        board.kill()
    assert code != 0
    assert "failed to verify after 2 attempts" in err


def test_dump_matches_what_was_loaded(cli, board, tmp_path):
    prog, out = str(tmp_path / "src.ptp"), str(tmp_path / "back.ptp")
    want = sample_tape(prog, 0x0400, 300, seed=5)
    assert cli("load", "--port", board.dev, "--baud", BAUD, prog)[0] == 0
    code, _, err = cli(
        "dump", "--port", board.dev, "--baud", BAUD, "--range", "0400-052B", "-o", out
    )
    assert code == 0, err
    with open(out, "rb") as back:
        assert Tape("back.ptp", back.read()).mem == want


def test_dump_to_stdout(cli, board):
    code, out, err = cli(
        "dump", "--port", board.dev, "--baud", BAUD, "--range", "0000-000F"
    )
    assert code == 0, err
    assert Tape("out.ptp", out.encode()).span == (0x0000, 0x000F)


def test_readback_by_stepping(cli, board, tmp_path):
    prog = str(tmp_path / "step.ptp")
    want = sample_tape(prog, 0x0500, 40, seed=6)
    assert cli("load", "--port", board.dev, "--baud", BAUD, prog)[0] == 0
    port = Port(board.dev, 115200)
    with open(os.devnull, "w", encoding="ascii") as quiet:
        mon = Monitor(port, log=quiet)
        mon.sync()
        got = mon.read_back(0x0500, 0x0527)
    port.close()
    assert got == want


def test_run_only(cli, board):
    code, _, err = cli("run", "--port", board.dev, "--baud", BAUD, "--go", "0200")
    board.finish()
    assert code == 0, err
    assert board.go == 0x0200


def test_xmodem_loads_segments_and_runs_the_program(cli, board, tmp_path):
    zp, prog, extra = (str(tmp_path / n) for n in ("zp.ptp", "prog.ptp", "x.ptp"))
    want = sample_tape(zp, 0x0000, 0xEF, seed=21)
    want.update(sample_tape(prog, 0x0200, 900, seed=22))
    want.update(sample_tape(extra, 0x0A00, 64, seed=23))
    code, _, err = cli(
        "load", "--port", board.dev, "--baud", BAUD, zp, prog, extra, "--go", "0200"
    )
    board.finish()
    assert code == 0, err
    assert "xmodem at 19200 baud" in err and "block 10/10" in err
    assert board.go == 0x0200
    assert {a: board.mem.get(a) for a in want} == want


@pytest.mark.parametrize("rate", ["9600", "38400"])
def test_xmodem_returns_to_the_monitor_for_deposits(cli, board, tmp_path, rate):
    prog = str(tmp_path / "prog.ptp")
    want = sample_tape(prog, 0x0400, 300, seed=24)
    code, _, err = cli(
        "load",
        "--port",
        board.dev,
        "--baud",
        BAUD,
        "--baud-fast",
        rate,
        "--stub-at",
        "0800",
        prog,
        "--deposit",
        "00F1=DAFDFF",
        "--go",
        "0400",
    )
    board.finish()
    assert code == 0, err
    assert "loading xmodem stub ($0800-" in err
    assert board.go == 0x0400
    want.update({0xF1: 0xDA, 0xF2: 0xFD, 0xF3: 0xFF})
    assert {a: board.mem.get(a) for a in want} == want


def test_silent_stub_falls_back_to_hex(cli, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "START_WAIT", 0.5)
    prog = str(tmp_path / "prog.ptp")
    want = sample_tape(prog, 0x0300, 100, seed=25)
    board = Board(mode="nostub")
    try:
        code, _, err = cli("load", "--port", board.dev, "--baud", BAUD, prog)
        board.finish()
    finally:
        board.kill()
    assert code == 0, err
    assert "at 19200 baud" in err and "at 9600 baud" in err
    assert "falling back to hex" in err
    assert {a: board.mem.get(a) for a in want} == want


def test_no_room_for_the_stub_falls_back_to_hex(cli, board, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "CODE_REGIONS", ((0x0300, 0x0400),))
    prog = str(tmp_path / "prog.ptp")
    want = sample_tape(prog, 0x0300, 100, seed=26)
    code, _, err = cli("load", "--port", board.dev, "--baud", BAUD, prog)
    board.finish()
    assert code == 0, err
    assert "no free RAM" in err and "falling back to hex" in err
    assert {a: board.mem.get(a) for a in want} == want


def test_memory_that_does_not_hold_stops_the_load(cli, board, tmp_path):
    prog = str(tmp_path / "rom.ptp")
    sample_tape(prog, 0x13C0, 0x80, seed=27)
    code, _, err = cli("load", "--port", board.dev, "--baud", BAUD, prog)
    assert code != 0
    assert "memory at $1400 did not hold" in err
