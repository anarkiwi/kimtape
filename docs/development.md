# Development

```sh
pip install -e ".[dev]"
pytest                      # xdist, coverage, no hardware needed
black src tests examples
pylint src/kimtape tests examples
tests/run_example.sh        # examples/load.py against a fake board
```

## The fake board

`tests/fake_kim.py` is a KIM-1 monitor stand-in on a pty. It implements character
echo, `AAAA<space>`, `DD.`, `CR`, `L`, `Q` and `G`, honours the KIM-1 memory map
so that writes outside RAM do not stick, and pads its output with the same NUL
fill characters the ROM emits.

That last detail matters: an early version of the fake omitted the fill, so the
tests passed against it while real hardware failed. Anything the fake models
loosely is a place where the tests will agree with each other and disagree with a
board.

It takes one argument, a record number to drop, which drives the retry tests;
`-1` drops every record.

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
