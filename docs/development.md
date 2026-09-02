# Development

```sh
pip install -e ".[dev]"
pytest                      # xdist, coverage, no hardware needed
black src tests
pylint src/kimtape tests
tests/run_example.sh        # examples/kimventure.sh against a fake board
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

## Test fixtures

Tests generate synthetic tapes rather than shipping real program images. No
third-party program is committed to this repository or downloaded during CI.

## CI

`lint` runs black and pylint. `test` runs the suite on every supported Python.
`example` shellchecks and runs the worked example against the fake board.
Coverage below 85% fails the build.
