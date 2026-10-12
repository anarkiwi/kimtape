# kimtape

Paper tape loader for the MOS Technology KIM-1 and its replicas. Loads, dumps and
verifies programs over a serial line using the stock ROM monitor — no extra
hardware, no pyserial, standard library only. `load` bootstraps a small XMODEM
receiver through the monitor and sends the program at 38400 baud, about 40
times faster than paper tape records at 1200.

Works with the KIM-1, PAL-1, PAL-II, Micro-KIM, KIM Clone and simulators exposed
on a pty.

## Install

```sh
pip install kimtape
```

## Use

```sh
kimtape load  --port /dev/ttyUSB0 zp.ptp game.ptp --go 0200
kimtape load  --port /dev/ttyUSB0 --hex game.ptp   # records only, no loader
kimtape dump  --port /dev/ttyUSB0 --range 0200-0774 -o game.ptp
kimtape run   --port /dev/ttyUSB0 --go 0200
kimtape tune  --port /dev/ttyUSB0     # fastest hex record settings for this board
kimtape probe --port /dev/ttyUSB0     # what does this board answer, and how?
kimtape term  --port /dev/ttyUSB0     # raw terminal, ^] to quit
kimtape info  game.ptp                # parse a tape without a board
```

Tapes and images may be http(s) URLs, fetched on the fly:

```sh
kimtape load --port /dev/ttyUSB0 \
  https://github.com/anarkiwi/kimformer/releases/latest/download/kimdos-0400.ptp --go 0400
```

Programs distributed as raw binaries, Intel HEX or S-records — fig-FORTH,
xKIM, MICRO-ADE among them — convert first:

```sh
kimtape convert forth.bin --at 2000 -o forth.ptp
kimtape convert xKIM.hex -o xkim.ptp          # format read from the content
```

Press RS before running: the monitor sets its bit rate from the first carriage
return it receives. Defaults are 1200 baud, 8N1, no flow control.

`load` falls back to 19200, then 9600 baud, then hex records by itself if
38400 does not work; `--baud-fast` caps the first rate and `--hex` skips the
loader. Hex records, the loader's included, stream in frames sized from the
monitor's own timing, 255 bytes a record, and the monitor's checksums are
trusted; `--verify` reads them back. `load` and `dump` move the monitor to 4800
baud for hex records and read back without a reset, and back afterwards, when
`--baud` is slower; `--monitor-baud RATE` picks another rate, `--monitor-baud 0`
stays at `--baud`, and `tune` finds the fastest setting a board takes.
The XMODEM loader reads each byte back as it stores it. `probe` reports what a
board answers when something is wrong; start there.

As a library:

```python
from kimtape import Monitor, Port, Tape
from kimtape.bootstrap import Bootstrap

port = Port("/dev/ttyUSB0", 1200)
mon = Monitor(port)
mon.sync()
tape = Tape("game.ptp", open("game.ptp", "rb").read())
if not Bootstrap(mon, [38400, 19200, 9600], baud=4800).load(tape.mem, go=0x0200):
    mon.load(tape)   # hex records
    mon.go(0x0200)
```

## Examples

`examples/load.py` fetches any catalogued program from its own archive and
loads it. The catalogue is [examples/programs.toml](examples/programs.toml);
nothing is redistributed here.

```sh
examples/load.py --list
examples/load.py microchess --port /dev/ttyUSB0    # chess in 1K, on the keypad
examples/load.py kb9 --port /dev/ttyUSB0           # Microsoft BASIC, over this terminal
examples/load.py kimventure --port /dev/ttyUSB0
```

Most catalogued programs talk over the same serial line you loaded them
through: BASIC, FOCAL, Tiny BASIC, two chess programs, an RPN calculator, a
text editor and a disassembler. See
[docs/kim-software.md](docs/kim-software.md) for what else is out there, and
[docs/kimventure.md](docs/kimventure.md) for the worked example in full.

## Documentation

- [docs/protocol.md](docs/protocol.md) — tape format, the monitor's serial timing and pacing, the XMODEM loader
- [docs/hardware.md](docs/hardware.md) — serial wiring, TTY jumper, PAL-II switch settings
- [docs/kimventure.md](docs/kimventure.md) — the worked example, start to finish
- [docs/kim-software.md](docs/kim-software.md) — other programs worth loading
- [docs/development.md](docs/development.md) — tests, the fake board, releasing

## Licence

Apache 2.0. Program images are fetched at run time and are not redistributed
here; they remain the property of their authors.
