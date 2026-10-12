"""Command line interface for kimtape."""

import argparse
import os
import random
import re
import select
import sys
import termios
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__
from .bootstrap import Bootstrap
from .monitor import SAL, Monitor
from .port import Pacing, Port
from .stub import STUBS
from .tape import Tape, read_image
from .timing import FRAMES, RECORD
from .xmodem import MONITOR

PROBE_ADDR = 0x1780  # 6532 RAM, present on every KIM-1
PROBE_BYTES = bytes([0x00, 0x00, 0x10, 0x00])
TUNE_RATES = (1200, 2400, 4800, 9600)
MONITOR_BAUD = 4800  # default monitor rate for hex records, if faster than --baud

DESCRIPTION = "Load, dump and run programs on a KIM-1 over a serial line."
EPILOG = """\
examples:
  kimtape load --port /dev/ttyUSB0 zp.ptp game.ptp --go 0200
  kimtape load --port /dev/ttyUSB0 https://example.com/game.ptp --go 0200
  kimtape load --port /dev/ttyUSB0 --hex game.ptp     # no XMODEM stub
  kimtape dump --port /dev/ttyUSB0 --range 0200-0774 -o game.ptp
  kimtape load --port /dev/ttyUSB0 --hex --monitor-baud 0 game.ptp  # stay at --baud
  kimtape probe --port /dev/ttyUSB0
  kimtape tune --port /dev/ttyUSB0
  kimtape term --port /dev/ttyUSB0
  kimtape convert forth.bin --at 2000 -o forth.ptp

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
            echo_lead=args.echo_lead,
        ),
        args.trace,
    )


def open_monitor(args, port):
    """A Monitor on port with the hex record options from parsed arguments."""
    mon = Monitor(port, record=args.record, lock=args.lock_step)
    mon.frame = args.frame
    return mon


def read_source(source):
    """Return (name, bytes) of a file path or an http(s) URL."""
    url = urllib.parse.urlsplit(source)
    if url.scheme not in ("http", "https"):
        with open(source, "rb") as handle:
            return os.path.basename(source), handle.read()
    try:
        with urllib.request.urlopen(source, timeout=60) as response:  # nosec B310
            blob = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit("kimtape: cannot fetch %s: %s" % (source, exc)) from exc
    return os.path.basename(urllib.parse.unquote(url.path)) or url.netloc, blob


def read_tapes(sources):
    """Parse each tape file or URL, failing on a bad checksum."""
    return [Tape(*read_source(source)) for source in sources]


def say(fmt, *args):
    """Write a diagnostic line to stderr."""
    sys.stderr.write((fmt % args if args else fmt) + "\n")
    sys.stderr.flush()


def cmd_info(args):
    """Parse tapes without touching the board."""
    for tape in read_tapes(args.tapes):
        print(tape)
    return 0


def cmd_convert(args):
    """Turn a binary, Intel HEX or S-record image into a paper tape."""
    mem = read_image(read_source(args.image)[1], args.at)
    tape = Tape.from_memory(os.path.basename(args.output or "converted.ptp"), mem)
    say("converted %s", tape)
    if args.output:
        with open(args.output, "wb") as out:
            out.write(tape.text())
    else:
        sys.stdout.buffer.write(tape.text())
        sys.stdout.buffer.flush()
    return 0


def monitor_baud(args):
    """The rate for hex records and read back, or None to stay at --baud."""
    if args.monitor_baud is None:
        return MONITOR_BAUD if args.baud < MONITOR_BAUD else None
    return args.monitor_baud or None


def switched(mon, err):
    """err, with the way out added if it struck while the monitor was switched."""
    if not isinstance(err.code, str) or mon.p.baud == mon.home:
        return err
    return SystemExit(
        "%s\nkimtape: the monitor was at %d baud; press RS and retry with "
        "--monitor-baud 0 to keep it at %d" % (err.code, mon.p.baud, mon.home)
    )


def cmd_load(args):
    """Send tapes, verify each one, and optionally start the program."""
    tapes = read_tapes(args.tapes)
    if not tapes:
        raise SystemExit("kimtape: no tapes given")
    port = open_port(args)
    mon = open_monitor(args, port)
    try:
        mon.sync(args.sync_tries)
        return _load(mon, tapes, args)
    except SystemExit as err:
        raise switched(mon, err) from None
    finally:
        port.close()


def _load(mon, tapes, args):
    go = args.go if args.go is not None and not args.deposit else MONITOR
    if args.hex or not _bootstrap(mon, tapes, go, args):
        mon.rate(monitor_baud(args))
        for tape in tapes:
            _load_one(mon, tape, args)
        mon.rate()
    elif go != MONITOR:
        return 0
    for addr, data in args.deposit or []:
        mon.say("depositing %s at $%04X", data.hex(" ").upper(), addr)
        mon.deposit(addr, data)
    if args.go is not None:
        mon.go(args.go)
    return 0


def _bootstrap(mon, tapes, go, args):
    """Load every tape through the XMODEM stub; False to fall back to hex."""
    mem = {}
    for tape in tapes:
        mon.say("xmodem: %s", tape)
        mem.update(tape.mem)
    rates = sorted((r for r in STUBS if r <= args.baud_fast), reverse=True)
    if Bootstrap(mon, rates, args.stub_at, monitor_baud(args), args.verify).load(
        mem, go
    ):
        return True
    mon.say("falling back to hex records at %d baud", args.baud)
    return False


def _load_one(mon, tape, args):
    for attempt in range(args.retries + 1):
        if not mon.load(tape, args.retries + 1):
            raise SystemExit("kimtape: the monitor did not take %s" % tape.name)
        if not args.verify:
            return
        result = mon.verify(tape)
        if result:
            return
        if result is None:
            raise SystemExit(
                "kimtape: cannot read memory back, so the load is unverifiable."
                "  Run 'kimtape probe --port %s' to find the pacing this board "
                "needs, or load without --verify." % args.port
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
    mon = open_monitor(args, port)
    try:
        mon.sync(args.sync_tries)
        mon.rate(monitor_baud(args))
        mem = mon.punch(lo, hi)
        mon.rate()
    except SystemExit as err:
        raise switched(mon, err) from None
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


def _tune_rate(mon, tape, baud):
    """Load and read back tape at baud in ever safer ways, up to the first that works.

    Each try is (rate, frame, record, lock-step, extra frame bit, ok, bytes/s).
    """
    tried = []
    for record, extra, lock in ((RECORD, 0, False), (24, 0, False), (RECORD, 1, False)):
        mon.lock, mon.record, mon.frame = lock, record, None
        mon.rate(baud)
        if not mon.p.pacing.stream or mon.p.frame + extra > FRAMES[-1]:
            break
        if extra:
            mon.frame = mon.p.frame + extra
            mon.rate(baud)
        tried.append(_tune_try(mon, tape, baud, extra))
        if tried[-1][5]:
            return tried
    mon.lock, mon.record, mon.frame = True, RECORD, None
    mon.rate(baud)
    return tried + [_tune_try(mon, tape, baud, 0)]


def _tune_try(mon, tape, baud, extra):
    began = time.time()
    loaded = mon.load(tape, 1)
    speed = len(tape.mem) / (time.time() - began)
    good = loaded and bool(mon.verify(tape))
    return baud, mon.p.frame, mon.record, mon.lock, extra, good, speed


def cmd_tune(args):
    """Load a pattern at each rate and pacing, verify it, report the fastest."""
    lo, hi = args.range
    mem = {addr: random.randrange(256) for addr in range(lo, hi + 1)}
    tape = Tape.from_memory("pattern $%04X-$%04X" % (lo, hi), mem)
    port = open_port(args)
    mon = open_monitor(args, port)
    tried = []
    try:
        mon.sync(args.sync_tries)
        for baud in args.rates:
            tried += _tune_rate(mon, tape, baud)
    finally:
        mon.lock, mon.frame = False, None
        try:
            mon.rate()
        finally:
            port.close()
    _tune_report(args, tried)
    return 0


def _tune_report(args, tried):
    say("\n  rate  frame  record  pacing     result   bytes/s")
    for baud, frame, record, lock, _, good, speed in tried:
        say(
            "  %4d  %5d  %6d  %-9s  %-7s  %7.0f",
            baud,
            frame,
            record,
            "lock-step" if lock else "stream",
            "ok" if good else "FAILED",
            speed,
        )
    good = [t for t in tried if t[5]]
    if not good:
        raise SystemExit("kimtape: nothing loaded cleanly; check the wiring")
    baud, frame, record, lock, extra, _, _ = max(good, key=lambda t: t[6])
    flags = ["--baud", str(args.baud), "--monitor-baud", str(baud)]
    flags += ["--lock-step"] if lock else []
    flags += ["--frame", str(frame)] if extra else []
    flags += ["--record", str(record)] if record != RECORD else []
    say("\nfastest: kimtape load --port %s %s", args.port, " ".join(flags))


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


def deposit_spec(text):
    """Parse ADDR=BYTES, as in 00F1=DAFDFF, into (address, bytes)."""
    addr, _, data = text.partition("=")
    if not data:
        raise argparse.ArgumentTypeError("expected ADDR=BYTES, e.g. 00F1=DAFDFF")
    try:
        return int(addr, 16), bytes.fromhex(data)
    except ValueError as err:
        raise argparse.ArgumentTypeError(str(err)) from None


def record_length(text):
    """Parse a record length, 1 to the most the monitor's L accepts."""
    value = int(text)
    if not 1 <= value <= RECORD:
        raise argparse.ArgumentTypeError("records hold 1 to %d bytes" % RECORD)
    return value


def hexrange(text):
    """Parse a hex range argument such as 0200-0774."""
    lo, _, hi = text.partition("-")
    if not hi:
        raise argparse.ArgumentTypeError("expected LO-HI, e.g. 0200-0774")
    return int(lo, 16), int(hi, 16)


def _hex_options():
    """Options for commands that send hex records or read memory back."""
    hexopts = argparse.ArgumentParser(add_help=False)
    hexopts.add_argument(
        "--monitor-baud",
        type=int,
        metavar="RATE",
        help="run the monitor at this rate for hex records and read back, "
        "switching it from --baud by writing CNTL30 and back when done; "
        "load and dump default to %d when --baud is slower, and 0 stays at "
        "--baud" % MONITOR_BAUD,
    )
    hexopts.add_argument(
        "--frame",
        type=int,
        choices=FRAMES,
        help="bits a character when streaming: 11 is 8N2, 12 adds mark parity; "
        "by default the fewest the monitor's timing allows",
    )
    hexopts.add_argument(
        "--record",
        type=record_length,
        default=RECORD,
        metavar="N",
        help="data bytes per hex record, up to %d" % RECORD,
    )
    hexopts.add_argument(
        "--lock-step",
        action="store_true",
        help="wait for each character's echo instead of streaming",
    )
    return hexopts


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
    common.add_argument("--line-delay", type=float, help=argparse.SUPPRESS)
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
    hexopts = _hex_options()
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "load", parents=[common, hexopts], help="send tapes, optionally run"
    )
    p.add_argument("tapes", nargs="*", help="tape files or http(s) URLs")
    p.add_argument("--go", type=hexaddr, metavar="ADDR", help="run from this address")
    p.add_argument("--retries", type=int, default=2)
    p.add_argument(
        "--verify",
        action="store_true",
        help="read hex records back with Q; the monitor's checksums are "
        "trusted otherwise",
    )
    p.add_argument("--no-verify", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--hex",
        action="store_true",
        help="send hex records through the monitor, as every KIM-1 accepts, "
        "rather than bootstrapping the faster XMODEM loader",
    )
    p.add_argument(
        "--baud-fast",
        type=int,
        default=38400,
        choices=sorted(STUBS),
        metavar="RATE",
        help="fastest XMODEM bit rate, one of %(choices)s (default %(default)s); "
        "each slower one is tried next, then hex",
    )
    p.add_argument(
        "--stub-at",
        type=hexaddr,
        metavar="ADDR",
        help="put the XMODEM stub here rather than in free RAM it finds",
    )
    p.add_argument(
        "--deposit",
        type=deposit_spec,
        action="append",
        metavar="ADDR=BYTES",
        help="write bytes after loading, before running; repeatable.  KB-9 BASIC "
        "needs 00F1=DAFDFF, for instance",
    )
    p.set_defaults(func=cmd_load)

    p = sub.add_parser(
        "dump", parents=[common, hexopts], help="punch a memory range to a tape"
    )
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
        "tune",
        parents=[common, hexopts],
        help="find the fastest hex record settings on this board",
    )
    p.add_argument(
        "--range",
        type=hexrange,
        default=(0x0200, 0x03FF),
        metavar="LO-HI",
        help="scratch RAM to load test patterns into (default 0200-03FF)",
    )
    p.add_argument(
        "--rates",
        type=lambda text: [int(r) for r in text.split(",")],
        default=list(TUNE_RATES),
        metavar="R,R,...",
        help="monitor rates to try (default %s)" % ",".join(map(str, TUNE_RATES)),
    )
    p.set_defaults(func=cmd_tune)

    p = sub.add_parser(
        "term", parents=[common], help="raw terminal; drive the monitor by hand"
    )
    p.set_defaults(func=cmd_term)

    p = sub.add_parser(
        "convert", help="make a paper tape from a binary, Intel HEX or S-record"
    )
    p.add_argument("image", help="file or http(s) URL")
    p.add_argument(
        "--at", type=hexaddr, metavar="ADDR", help="load address, for a raw binary"
    )
    p.add_argument("-o", "--output", help="write here instead of stdout")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("info", help="parse tapes and report their extents")
    p.add_argument("tapes", nargs="+", help="tape files or http(s) URLs")
    p.set_defaults(func=cmd_info)
    return ap


def main(argv=None):
    """Parse arguments and dispatch; returns a process exit code."""
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as err:
        # a malformed tape or an unreadable file is a user error, not a crash
        raise SystemExit("kimtape: %s" % err) from None
