# Development

```sh
sudo apt-get install cc65   # ca65/ld65, for the XMODEM stub
pip install -e ".[dev]"
pytest                      # xdist, coverage, no hardware needed
black src tests examples stub
pylint src/kimtape tests examples stub
tests/run_example.sh        # examples/load.py against a fake board
```

## The XMODEM stub

`stub/xmodem.s` is the 6502 receiver. `stub/build.py` assembles it for each
rate at two code and two data origins, finds the relocations from what moved,
and writes `src/kimtape/stub.py`; `stub/build.py --check` (run in CI) fails if
the committed bytes are not what the source assembles to. Run the build after
any change to the source.

`tests/kimsim.py` runs it on py65 with the TTY lines modelled bit by bit in
CPU cycles: a host UART at any rate and error drives port A bit 7, and port B
bit 0, ANDed with the input as the KIM-1 echo does, is decoded back. The stub
tests use it directly; `tests/measure.py tolerance` and
`tests/measure.py throughput` produce the tables in
[protocol.md](protocol.md).

## The fake board

`tests/fake_kim.py` is a KIM-1 monitor stand-in on a pty. It implements character
echo, `AAAA<space>`, `DD.`, `CR`, `L`, `Q` and `G`, honours the KIM-1 memory map
so that writes outside RAM do not stick, and pads its output with the same NUL
fill characters the ROM emits.

That last detail matters: an early version of the fake omitted the fill, so the
tests passed against it while real hardware failed. Anything the fake models
loosely is a place where the tests will agree with each other and disagree with a
board.

Its `L` is the monitor model's LOAD (below), fed character by character, with
the ROM's `KIM` and `ERR` replies. It takes one argument, a record number to
lose whole, as a damaged `;` would, which drives the retry tests; `-1` loses
every record. A second changes the fault or the board: `err` spoils a digit of
that record so the monitor reports `ERR`; `nostream` garbles characters that
arrive while another is waiting, as a monitor slower than its listing would;
`mute` stops answering after the reset; `nostub` makes a started XMODEM stub
never answer. Its CNTL30 is what RST would measure at the pty's rate.

`G` at an address holding a kimtape stub runs the stub on `tests/kimsim.py`,
bridged to the pty in real time, so `load` is tested end to end through the
real 6502 code; when the stub returns to `$1C4F` the fake carries on as the
monitor.

## The monitor model

`kimsim.Listing` is the ROM monitor's `L` at the bit level: GETCH's polls and
samples at the cycles DELAY and DEHALF give for a CNTL30, and LOAD's work on
each character summed from the routines it runs, deaf meanwhile. `kimsim`
also times `Q`. `tests/test_timing.py` drives it with host lines to check the
pacing rule in `src/kimtape/timing.py`.

`tests/measure.py rom --image kim.bin` runs the same lines through a monitor
ROM image ($1800–$1FFF) on py65 and checks the model agrees character by
character, cycle for cycle, including on lines it garbles, and that `Q` takes
the cycles the model says. No ROM is in this repository; assemble one from the
published listing (e.g. github.com/jefftranter/6502, `asm/KIM-1/ROMs/kim.s`):

```sh
ca65 --feature labels_without_colons kim.s && ld65 -t none -o kim.bin kim.o
```

py65 1.2 counts `DEC` absolute as 3 cycles rather than 6; `kimsim.Board`
corrects it, which DELAY's loop depends on.

## The catalogue

`examples/programs.toml` lists programs, where their tapes come from, where
they load, and whether they are used from the terminal or the keypad. Adding a
program means adding an entry, not writing a script. A tape entry is either a
`url`, or an `archive` plus the `member` to take from it; add `at` when the
file is a raw binary that carries no address of its own, and the loader
converts it.

`test_catalogue_entries_are_complete` checks the shape of every entry. It does
not fetch anything, so a URL that rots will not be caught by CI — check with
`examples/load.py <name> --fetch-only`.

## Test fixtures

Tests generate synthetic tapes rather than shipping real program images. No
third-party program is committed to this repository or downloaded during CI.

## CI

`lint` runs black and pylint. `test` runs the suite on every supported Python.
`example` shellchecks and runs the worked example against the fake board.
Coverage below 85% fails the build.

## Releases

`.github/workflows/release.yml` publishes to PyPI by trusted publishing when a
GitHub release is published. One-time setup:

1. On PyPI, add a pending trusted publisher (https://pypi.org/manage/account/publishing/):
   project `kimtape`, owner `anarkiwi`, repository `kimtape`, workflow
   `release.yml`, environment `pypi`.
2. The repository has an environment named `pypi`.

To release, bump `version` in `pyproject.toml` and `__version__` in
`src/kimtape/__init__.py`, merge, then publish a release tagged `v<version>`
(e.g. `v0.1.1`); the build fails if the tag and the version differ.
Run the workflow by hand to build and check without publishing.
