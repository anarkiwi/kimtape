#!/usr/bin/env python3
"""Fetch a KIM-1 program from its archive and load it with kimtape.

The catalogue lives in programs.toml next to this script.  Each entry says
where the tapes come from, where the program loads, whether it talks to the
terminal or to the keypad and LED display, and anything that has to be poked
into memory before it will run.

    ./load.py --list
    ./load.py microchess --port /dev/ttyUSB0
    ./load.py kb9 --port /dev/ttyUSB0
    ./load.py qchess --fetch-only

Programs are downloaded once into --dir and reused.  Nothing here is
redistributed: every image is fetched from its own archive at run time.
"""

import argparse
import io
import os
import sys
import tomllib
import urllib.request
import zipfile

# so this script runs from a checkout that has not been pip installed
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
)

# pylint: disable=wrong-import-position
from kimtape import Tape, read_image
from kimtape.cli import main as kimtape_main

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOGUE = os.path.join(HERE, "programs.toml")


def load_catalogue(path=CATALOGUE):
    """Read the program catalogue."""
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def describe(name, entry):
    """One line for --list."""
    where = "terminal" if entry.get("io") == "tty" else "keypad and LEDs"
    return "  %-14s %-34s %s, $%s, %s" % (
        name,
        entry["title"],
        where,
        entry["start"],
        entry.get("ram", "unexpanded"),
    )


def fetch(url, cache):
    """Download url once into cache, returning its bytes."""
    name = os.path.join(cache, os.path.basename(url.split("?")[0]))
    if os.path.exists(name):
        with open(name, "rb") as handle:
            return handle.read()
    print("fetching %s" % url, file=sys.stderr)
    with urllib.request.urlopen(url, timeout=60) as response:  # nosec B310
        blob = response.read()
    with open(name, "wb") as out:
        out.write(blob)
    return blob


def obtain(spec, cache):
    """Produce one tape file on disk from a catalogue tape entry."""
    if "archive" in spec:
        with zipfile.ZipFile(io.BytesIO(fetch(spec["archive"], cache))) as archive:
            blob = archive.read(spec["member"])
        name = os.path.basename(spec["member"])
    else:
        blob, name = fetch(spec["url"], cache), os.path.basename(spec["url"])
    path = os.path.join(cache, name)
    if not blob.lstrip().startswith(b";"):  # not paper tape, so convert it
        at = int(spec["at"], 16) if "at" in spec else None
        tape = Tape.from_memory(name, read_image(blob, at))
        path = os.path.splitext(path)[0] + ".ptp"
        blob = tape.text()
    with open(path, "wb") as out:
        out.write(blob)
    return path


def build_args(entry, paths, args):
    """The kimtape load command line for this program."""
    argv = ["load", "--port", args.port, "--baud", str(args.baud)] + paths
    for deposit in entry.get("deposits", []):
        argv += ["--deposit", "%s=%s" % (deposit["addr"], deposit["bytes"])]
    if not args.no_run:
        argv += ["--go", entry["start"]]
    return argv + args.rest


def main(argv=None):
    """Fetch and load one catalogued program."""
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n", maxsplit=1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("program", nargs="?")
    ap.add_argument("--list", action="store_true", help="show the catalogue")
    ap.add_argument("--port", default=os.environ.get("KIM_PORT", "/dev/ttyUSB0"))
    ap.add_argument("--baud", type=int, default=int(os.environ.get("KIM_BAUD", "1200")))
    ap.add_argument("--dir", default=".", help="where to keep downloads")
    ap.add_argument("--no-run", action="store_true", help="load but do not start")
    ap.add_argument("--fetch-only", action="store_true", help="download, do not load")
    ap.add_argument("--catalogue", default=CATALOGUE)
    ap.add_argument("rest", nargs="*", help="further kimtape options after --")
    args = ap.parse_args(argv)

    catalogue = load_catalogue(args.catalogue)
    if args.list or not args.program:
        print("programs:")
        for name in sorted(catalogue):
            print(describe(name, catalogue[name]))
        print("\nload one with: %s <name> --port /dev/ttyUSB0" % sys.argv[0])
        return 0 if args.list else 2

    if args.program not in catalogue:
        raise SystemExit("%s: no such program; --list shows them all" % args.program)
    entry = catalogue[args.program]
    os.makedirs(args.dir, exist_ok=True)
    paths = [obtain(spec, args.dir) for spec in entry["tapes"]]
    for path in paths:
        with open(path, "rb") as handle:
            print(Tape(os.path.basename(path), handle.read()))
    if args.fetch_only:
        return 0

    code = kimtape_main(build_args(entry, paths, args))
    if entry.get("notes") and not args.no_run:
        print("\n%s" % entry["notes"])
    return code


if __name__ == "__main__":
    sys.exit(main())
