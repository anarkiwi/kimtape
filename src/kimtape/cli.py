"""Command line interface for kimtape."""

import argparse
import os
import re
import select
import sys
import termios

from . import __version__
from .monitor import SAL, Monitor
from .port import Pacing, Port
from .tape import Tape

PROBE_ADDR = 0x1780  # 6532 RAM, present on every KIM-1
PROBE_BYTES = bytes([0x00, 0x00, 0x10, 0x00])

DESCRIPTION = "Load, dump and run programs on a KIM-1 over a serial line."
EPILOG = """\
examples:
  kimtape load --port /dev/ttyUSB0 zp.ptp game.ptp --go 0200
  kimtape dump --port /dev/ttyUSB0 --range 0200-0774 -o game.ptp
  kimtape probe --port /dev/ttyUSB0
  kimtape term --port /dev/ttyUSB0

The board is assumed to be on the machine running the tool.  Press RS before
running: the monitor sets its bit rate from the first carriage return it sees.
"""


def open_port(args):
    """Open the serial port described by parsed arguments."""
    return Port(
        args.port,
        args.baud,
        Pacing(
            char_delay=(None if args.char_delay is None else args.char_delay / 1000.0),
            line_delay=args.line_delay / 1000.0,
            echo_lead=args.echo_lead,
        ),
        args.trace,
    )


def read_tapes(paths):
    """Parse each tape file, failing on a bad checksum."""
    tapes = []
    for path in paths:
        with open(path, "rb") as handle:
            tapes.append(Tape(os.path.basename(path), handle.read()))
    return tapes


def say(fmt, *args):
    """Write a diagnostic line to stderr."""
    sys.stderr.write((fmt % args if args else fmt) + "\n")
    sys.stderr.flush()


def cmd_info(args):
    """Parse tapes without touching the board."""
    for tape in read_tapes(args.tapes):
        print(tape)
    return 0


def cmd_load(args):
    """Send tapes, verify each one, and optionally start the program."""
    tapes = read_tapes(args.tapes)
    if not tapes:
        raise SystemExit("kimtape: no tapes given")
    port = open_port(args)
    mon = Monitor(port)
    try:
        mon.sync(args.sync_tries)
        for tape in tapes:
            _load_one(mon, tape, args)
        if args.go is not None:
            mon.go(args.go)
    finally:
        port.close()
    return 0


def _load_one(mon, tape, args):
    for attempt in range(args.retries + 1):
        mon.load(tape)
        if args.no_verify:
            return
        result = mon.verify(tape)
        if result:
            return
        if result is None:
            raise SystemExit(
                "kimtape: cannot read memory back, so the load is unverifiable."
                "  Run 'kimtape probe --port %s' to find the pacing this board "
                "needs, or pass --no-verify to load anyway." % args.port
            )
        if attempt == args.retries:
            raise SystemExit(
                "kimtape: %s failed to verify after %d attempts"
                % (tape.name, attempt + 1)
            )
        mon.say("  retrying %s", tape.name)


def cmd_dump(args):
    """Punch a memory range back out and write it as a tape."""
    lo, hi = args.range
    port = open_port(args)
    mon = Monitor(port)
    try:
        mon.sync(args.sync_tries)
        mem = mon.punch(lo, hi)
    finally:
        port.close()
    holes = (hi - lo + 1) - sum(1 for a in range(lo, hi + 1) if a in mem)
    if holes:
        say("warning: %d of %d addresses did not read back", holes, hi - lo + 1)
    tape = Tape.from_memory(
        os.path.basename(args.output or "dump.ptp"),
        {a: mem[a] for a in range(lo, hi + 1) if a in mem},
    )
    say("dumped %s", tape)
    if args.output:
        with open(args.output, "wb") as out:
            out.write(tape.text())
    else:
        sys.stdout.buffer.write(tape.text())
        sys.stdout.buffer.flush()
    return 0


def cmd_run(args):
    """Execute from an address."""
    port = open_port(args)
    mon = Monitor(port)
    try:
        mon.sync(args.sync_tries)
        mon.go(args.go)
    finally:
        port.close()
    return 0


def _probe_receive(port, mon):
    """Send an address one character at a time and report what comes back."""
    say("== receive path: send '%04X ' one character at a time", PROBE_ADDR)
    port.pace(echo=False, char_delay=5 * port.char_time)
    typed = ("%04X " % PROBE_ADDR).encode()
    echoed = 0
    for char in typed:
        port.take()
        port.write(bytes([char]))
        port.drain()
        back = port.take()
        echoed += bool(back)
        say(
            "   sent %-3s got %s", Port.show(bytes([char])), Port.show(back) or "(none)"
        )
    say("   characters echoed: %d of %d", echoed, len(typed))
    del mon


def _probe_pacing(port, mon):
    """Open a known address under each pacing regime; return what worked."""
    regimes = [
        ("lock-step echo", {"echo": True, "echo_lead": 1}, []),
        ("echo, 2 ahead", {"echo": True, "echo_lead": 2}, ["--echo-lead", "2"]),
    ]
    for chars in (1, 2, 5):
        regimes.append(
            (
                "timed, %d char%s" % (chars, "" if chars == 1 else "s"),
                {"echo": False, "char_delay": chars * port.char_time},
                ["--char-delay", "%.1f" % (chars * port.char_time * 1000)],
            )
        )
    # the monitor pads CR/LF with NUL fill for a real teletype's carriage, so
    # the address does not follow the newline immediately
    prompt = re.compile(rb"[\r\n\x00]%04X [0-9A-F]{2}" % PROBE_ADDR)
    say("== pacing trial: open $%04X under each regime", PROBE_ADDR)
    winner = None
    for name, setup, flags in regimes:
        port.pace(**setup)
        port.take()
        mon.open_addr(PROBE_ADDR)
        answers = bool(prompt.search(port.take()))
        say("   %-16s %s", name, "answers" if answers else "no reply")
        if answers and winner is None:
            winner = (name, setup, flags)
    return winner


def cmd_probe(args):
    """Find the pacing this board needs, then check each monitor command."""
    port = open_port(args)
    mon = Monitor(port)
    try:
        say("== sync")
        mon.sync(args.sync_tries)
        _probe_receive(port, mon)
        winner = _probe_pacing(port, mon)
        if winner is None:
            raise SystemExit(
                "kimtape: the monitor never answered an address-open under any "
                "pacing.  It replies to a bare CR, so the link works; check that "
                "the board is in TTY mode (application connector V to 21)."
            )
        port.pace(**winner[1])
        say("== deposit %s at $%04X", PROBE_BYTES.hex(" ").upper(), SAL)
        mon.deposit(SAL, PROBE_BYTES)
        stepped = mon.read_back(SAL, SAL + len(PROBE_BYTES) - 1)
        say("== punch $0000-$000F")
        punched = mon.punch(0x0000, 0x000F)
    finally:
        port.close()
    _probe_report(winner, stepped, punched)
    return 0


def _probe_report(winner, stepped, punched):
    deposits = [stepped.get(SAL + i) for i in range(len(PROBE_BYTES))] == list(
        PROBE_BYTES
    )
    say("\nresults")
    say("  pacing that works : %s", winner[0])
    say(
        "  deposit read back : %s",
        " ".join("%02X" % stepped[a] for a in sorted(stepped)) or "(nothing)",
    )
    say("  deposit works     : %s", deposits)
    say("  punch returned    : %d bytes", len(punched))
    say(
        "  verification      : %s",
        (
            "punch (fast)"
            if punched
            else "stepping (slow but working)" if deposits else "unavailable"
        ),
    )
    if winner[2]:
        say("  flags to use      : %s", " ".join(winner[2]))


def cmd_term(args):
    """Raw terminal on the port, so the monitor can be driven by hand."""
    import tty  # pylint: disable=import-outside-toplevel

    port = open_port(args)
    stdin = sys.stdin.fileno()
    saved = termios.tcgetattr(stdin)
    say("connected to %s at %d baud; ^] to quit\r", args.port, args.baud)
    try:
        # TCSANOW rather than tty.setraw's default TCSAFLUSH, which would throw
        # away anything already queued for us
        tty.setraw(stdin, termios.TCSANOW)
        _term_loop(stdin, port)
    finally:
        termios.tcsetattr(stdin, termios.TCSADRAIN, saved)
        port.close()
        sys.stderr.write("\r\n")
    return 0


def _term_loop(stdin, port):
    while True:
        ready = select.select([stdin, port.fd], [], [])[0]
        if stdin in ready:
            data = os.read(stdin, 64)
            if not data or b"\x1d" in data:
                return
            os.write(port.fd, data)
        if port.fd in ready:
            try:
                data = os.read(port.fd, 256)
            except (BlockingIOError, OSError):
                data = b""
            if data:
                os.write(1, data)


def hexaddr(text):
    """Parse a hex address argument such as 0200."""
    return int(text, 16)


def hexrange(text):
    """Parse a hex range argument such as 0200-0774."""
    lo, _, hi = text.partition("-")
    if not hi:
        raise argparse.ArgumentTypeError("expected LO-HI, e.g. 0200-0774")
    return int(lo, 16), int(hi, 16)


def build_parser():
    """Build the argument parser for every subcommand."""
    ap = argparse.ArgumentParser(
        prog="kimtape",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=__version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--port",
        required=True,
        metavar="DEVICE",
        help="serial device, e.g. /dev/ttyUSB0",
    )
    common.add_argument("--baud", type=int, default=1200)
    common.add_argument("--sync-tries", type=int, default=3)
    common.add_argument(
        "--char-delay",
        type=float,
        metavar="MS",
        help="per-character delay used when the link has no echo",
    )
    common.add_argument(
        "--line-delay",
        type=float,
        default=250.0,
        metavar="MS",
        help="per-record delay used when the link has no echo",
    )
    common.add_argument(
        "--echo-lead",
        type=int,
        default=1,
        metavar="N",
        help="characters the echo may lag behind; 1 is lock-step, which is "
        "what a bit-banged monitor UART needs",
    )
    common.add_argument(
        "--trace", action="store_true", help="log every byte sent and received"
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "load", parents=[common], help="send tapes, verify, optionally run"
    )
    p.add_argument("tapes", nargs="*")
    p.add_argument("--go", type=hexaddr, metavar="ADDR", help="run from this address")
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--no-verify", action="store_true")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("dump", parents=[common], help="punch a memory range to a tape")
    p.add_argument("--range", type=hexrange, required=True, metavar="LO-HI")
    p.add_argument("-o", "--output", help="write here instead of stdout")
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("run", parents=[common], help="execute from an address")
    p.add_argument("--go", type=hexaddr, required=True, metavar="ADDR")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser(
        "probe", parents=[common], help="check what this board answers and how"
    )
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser(
        "term", parents=[common], help="raw terminal; drive the monitor by hand"
    )
    p.set_defaults(func=cmd_term)

    p = sub.add_parser("info", help="parse tapes and report their extents")
    p.add_argument("tapes", nargs="+")
    p.set_defaults(func=cmd_info)
    return ap


def main(argv=None):
    """Parse arguments and dispatch; returns a process exit code."""
    args = build_parser().parse_args(argv)
    return args.func(args)
