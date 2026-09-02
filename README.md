# kimtape

Paper tape loader for the MOS Technology KIM-1 and its replicas. Loads, dumps and
verifies programs over a serial line using the stock ROM monitor — no extra
hardware, no pyserial, standard library only.

Works with the KIM-1, PAL-1, PAL-II, Micro-KIM, KIM Clone and simulators exposed
on a pty.

## Install

```sh
pip install git+https://github.com/anarkiwi/kimtape
```

## Use

```sh
kimtape load  --port /dev/ttyUSB0 zp.ptp game.ptp --go 0200
kimtape dump  --port /dev/ttyUSB0 --range 0200-0774 -o game.ptp
kimtape run   --port /dev/ttyUSB0 --go 0200
kimtape probe --port /dev/ttyUSB0     # what does this board answer, and how?
kimtape term  --port /dev/ttyUSB0     # raw terminal, ^] to quit
kimtape info  game.ptp                # parse a tape without a board
```

Programs distributed as raw binaries, Intel HEX or S-records — fig-FORTH,
xKIM, MICRO-ADE among them — convert first:

```sh
kimtape convert forth.bin --at 2000 -o forth.ptp
kimtape convert xKIM.hex -o xkim.ptp          # format read from the content
```

Press RS before running: the monitor sets its bit rate from the first carriage
return it receives. Defaults are 1200 baud, 8N1, no flow control.

`load` verifies every byte by reading the range back, and retries a tape that
does not match. `probe` reports which pacing a board needs when something is
wrong; start there.

As a library:

```python
from kimtape import Monitor, Port, Tape

port = Port("/dev/ttyUSB0", 1200)
mon = Monitor(port)
mon.sync()
mon.load(Tape("game.ptp", open("game.ptp", "rb").read()))
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

- [docs/protocol.md](docs/protocol.md) — tape format, monitor commands, why writes are paced
- [docs/hardware.md](docs/hardware.md) — serial wiring, TTY jumper, PAL-II switch settings
- [docs/kimventure.md](docs/kimventure.md) — the worked example, start to finish
- [docs/kim-software.md](docs/kim-software.md) — other programs worth loading
- [docs/development.md](docs/development.md) — tests, the fake board, releasing

## Licence

Apache 2.0. Program images are fetched at run time and are not redistributed
here; they remain the property of their authors.
