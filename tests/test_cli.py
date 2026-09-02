"""Command line behaviour: argument handling, probe, terminal, error paths."""

import os
import pty
import subprocess
import sys

from conftest import BAUD, Board, sample_tape

from kimtape import Tape


def test_info_reports_extents(cli, tmp_path):
    prog = str(tmp_path / "info.ptp")
    sample_tape(prog, 0x0200, 30, seed=11)
    code, out, _ = cli("info", prog)
    assert code == 0
    assert "info.ptp ($0200-$021D, 30 bytes" in out


def test_module_entry_point():
    """python -m kimtape works, which is what the example falls back to."""
    res = subprocess.run(
        [sys.executable, "-m", "kimtape", "--version"],
        stdout=subprocess.PIPE,
        check=True,
    )
    assert res.stdout.strip()


def test_version(cli):
    code, out, _ = cli("--version")
    assert code == 0
    assert out.strip()


def test_range_must_have_two_ends(cli):
    code, _, err = cli("dump", "--port", os.devnull, "--range", "0200")
    assert code != 0
    assert "expected LO-HI" in err


def test_missing_device_reports_clearly(cli):
    code, _, err = cli("run", "--port", "/dev/nonexistent-tty", "--go", "0200")
    assert code != 0
    assert "cannot open" in err


def test_non_serial_device_reports_clearly(cli):
    code, _, err = cli("run", "--port", os.devnull, "--go", "0200")
    assert code != 0
    assert "not a serial port" in err


def test_unsupported_baud_reports_clearly(cli, board):
    code, _, err = cli("run", "--port", board.dev, "--baud", "1234", "--go", "0200")
    assert code != 0
    assert "unsupported baud rate" in err


def test_silent_board_reports_clearly(cli):
    master, slave = pty.openpty()  # a port with nobody answering
    try:
        code, _, err = cli(
            "run",
            "--port",
            os.ttyname(slave),
            "--baud",
            BAUD,
            "--sync-tries",
            "1",
            "--go",
            "0200",
        )
    finally:
        os.close(master)
        os.close(slave)
    assert code != 0
    assert "no response" in err


def test_load_without_tapes(cli):
    code, _, err = cli("load", "--port", os.devnull)
    assert code != 0
    assert "no tapes given" in err


def test_probe_reports_each_command(cli, board):
    code, _, err = cli("probe", "--port", board.dev, "--baud", BAUD)
    assert code == 0, err
    assert "characters echoed: 5 of 5" in err
    assert "lock-step echo   answers" in err
    assert "deposit works     : True" in err
    assert "punch returned    : 16 bytes" in err


def test_probe_gives_up_when_nothing_answers(cli):
    """A board that answers the reset and then goes deaf."""
    board = Board(mute=True)
    try:
        code, _, err = cli("probe", "--port", board.dev, "--baud", BAUD)
    finally:
        board.kill()
    assert code != 0
    assert "never answered an address-open" in err


def test_trace_shows_bytes(cli, board):
    code, _, err = cli(
        "run", "--port", board.dev, "--baud", BAUD, "--go", "0200", "--trace"
    )
    assert code == 0
    assert "--> " in err and "<-- " in err


def test_term_passes_bytes_through(cli, board, monkeypatch):
    """Drive the terminal from a pty.  CR goes to the board, ^] quits."""

    class Stdin:  # pylint: disable=too-few-public-methods
        """Just enough of sys.stdin for tty.setraw and select."""

        def __init__(self, fd):
            self._fd = fd

        def fileno(self):
            """The descriptor tty.setraw and select operate on."""
            return self._fd

    master, slave = pty.openpty()
    try:
        os.write(master, b"\r\x1d")  # queued before the terminal starts reading
        monkeypatch.setattr(sys, "stdin", Stdin(slave))
        code, _, err = cli("term", "--port", board.dev, "--baud", BAUD)
    finally:
        os.close(master)
        os.close(slave)
    assert code == 0
    assert "^] to quit" in err


def test_dump_of_undecoded_range_reads_as_erased(cli, board):
    """The monitor punches whatever the bus returns; nothing is skipped."""
    code, out, err = cli(
        "dump", "--port", board.dev, "--baud", BAUD, "--range", "2000-2010"
    )
    assert code == 0, err
    tape = Tape("out.ptp", out.encode())
    assert tape.span == (0x2000, 0x2010)
    assert set(tape.mem.values()) == {0xFF}
