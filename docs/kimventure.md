# Worked example: KIM-Venture

KIM-Venture is Bob Leedom's 1979 adventure game for the KIM-1, played on the six
seven-segment digits and the keypad. It is the example this repository is built
around because it exercises everything: two tapes at different addresses, zero
page data, expansion RAM, and a program that takes over the hardware the monitor
was using.

`examples/kimventure.sh` fetches it and loads it:

```sh
cd /some/scratch/directory
/path/to/examples/kimventure.sh --port /dev/ttyUSB0
```

Press RS first. The run takes about two minutes at 1200 baud, most of it in the
two verification passes.

| Option | Effect |
| --- | --- |
| `-p, --port DEVICE` | serial port, default `$KIM_PORT` or `/dev/ttyUSB0` |
| `-b, --baud RATE` | line rate, default 1200 |
| `-n, --no-run` | load and verify, do not start |
| `-f, --fetch` | re-download even if the tapes are present |
| `-- ...` | everything after `--` goes to `kimtape` |

## What gets loaded

The full build needs expansion RAM. The three-part original fits an unexpanded
KIM-1 but starts at $0100 and uses the stack page.

| Range | Contents |
| --- | --- |
| $0000–$00EE | zero page data and the LIGHT routine, from `Venture-ZeroPage.ptp` |
| $00EF–$00FF | monitor scratch, deliberately untouched |
| $0200–$0774 | game, support routines and scoring, from `Venture-Full.ptp` |

Run address is $0200. On a PAL-II, `SW4` position 1 (K1, $0400–$07FF) must be on;
see [hardware.md](hardware.md).

## Playing

The game scans the keypad and drives the display itself, calling no monitor
routines. **Remove the TTY link (application connector V to 21) once loading has
finished** — while it is fitted, that shorted matrix position reads as a stuck
key. The board does not need resetting; if you do press RS, `AD 0200 GO`
restarts the program, which survives in RAM.

Scoring lives at $0600: press `ST`, then `AD 0600 GO`. `ST` again and
`AD 0200 GO` rejoins the game in progress.

## Saving a game

Save zero page and the location byte at $04BD:

```sh
kimtape dump --port /dev/ttyUSB0 --range 0000-00EE -o savegame.ptp
kimtape dump --port /dev/ttyUSB0 --range 04BD-04BD -o location.ptp
```

Restore by loading `savegame.ptp` in place of `Venture-ZeroPage.ptp`, then
`location.ptp`, then `Venture-Full.ptp`.

## Provenance

The game is © Robert Leedom and is not redistributed here. The script fetches it
from Mark Bush's [KIM-Venture repository](https://github.com/markbush/KIM-Venture),
falling back to the copy in [Hans Otten's archive](http://retro.hansotten.nl/6502-sbc/kim-1-manuals-and-software/kim-1-software/kim-venture/),
which also hosts the manual, the game instructions and a map.

Note that the full build is marked a work in progress by its author. If it
misbehaves, the original three-part version — `Venture-ZeroPage`, `Venture-Game`
and `Venture-Extra`, started at $0100 — is the conservative choice, and
`kimtape` loads it the same way.
