# Hardware setup

## Serial line

1200 baud, 8 data bits, no parity, 1 stop bit, no flow control. Press RS before
running any command: the monitor measures the first carriage return it receives
to set its own bit rate, and every kimtape command starts by sending one.

The monitor can run up to 9600 baud at 1 MHz, and kimtape can move it there and
back without a reset by writing CNTL30 (`--monitor-baud`; see
[protocol.md](protocol.md#the-monitors-serial-line)). Hex records stream in 8N2
frames up to 4800 baud and go lock-step at 9600. `kimtape tune` loads a test
pattern into $0200–$03FF at 1200, 2400, 4800 and 9600 baud, reads it back, and
prints the fastest `load` options that worked:

```sh
kimtape tune --port /dev/ttyUSB0        # press RS first; overwrites $0200-$03FF
```

USB adapters make 2 stop bits and mark parity (FTDI, CP210x, CH340, PL2303);
ones that cannot are caught by the echo check and fall back to lock-step.

`load` talks to the monitor at that rate only long enough to put its XMODEM
loader in; the program itself goes at 19200 baud, timed by the 6502's own
clock rather than the monitor's measurement. Every common USB to TTL adapter
(FTDI, CP210x, CH340, PL2303) makes 9600, 19200 and 38400 within a fraction of
a percent, and the loader tolerates about 4% either way (see
[protocol.md](protocol.md#bit-timing)), so a board on a 1 MHz crystal needs
nothing set. If the load stops at `xmodem at 19200 baud`:

| Try | When |
| --- | --- |
| nothing: kimtape retries at 9600, then falls back to hex | the usual case is handled |
| `--baud-fast 9600` | 19200 fails every time; skips the wasted attempt |
| `--hex` | the board is not at 1 MHz, or anything else between it and the host is |
| `--stub-at ADDR` | the loader's automatic placement picks RAM that is not there |

An FTDI adapter holds short replies for its latency timer, 16 ms by default,
before passing them on; setting it to 1 ms makes each block's turnaround faster:
`echo 1 | sudo tee /sys/bus/usb-serial/devices/ttyUSB0/latency_timer`.

## TTY mode

The monitor only speaks over the serial line when it is in TTY mode, which is
selected by shorting a position in the keypad matrix.

| Board | How |
| --- | --- |
| KIM-1, PAL-1, PAL-II | link application connector pin **V** to pin **21** |
| Micro-KIM | jumper K3 |
| KIM Clone | see its own documentation |

A program that scans the keypad itself sees that shorted position as a stuck
key, so remove the link once loading is done.

## PAL-II wiring

The PAL-II uses TTL levels rather than the KIM-1's 20 mA current loop, so a
USB-to-TTL adapter connects directly:

| Application connector | Adapter |
| --- | --- |
| pin **T** (TTL in, KYBD) | TXD |
| pin **U** (TTL out, PTR) | RXD |
| pin **1** | GND |
| pin **A** | +5V, if the board is adapter powered |

Reversing pins 1 and A will destroy the board.

## PAL-II switch settings

The factory default configuration is correct for almost everything. For
reference:

| Control | Default | Effect |
| --- | --- | --- |
| `SW4`, 4-way | all **ON** | enables K1–K4 RAM, $0400–$13FF, 1K per switch |
| `SW5` position 1 (8K0) | **OFF** | leaves $0000–$1FFF to the onboard KIM-1 logic |
| `SW5` positions 2–8 (8K1–8K7) | **ON** | enables $2000–$FFFF in 8K blocks |
| Vector select | **DOWN** | reset/NMI/IRQ vectors from the onboard monitor ROM |
| 8K7 select | **RIGHT** | RAM rather than external ROM in the top 8K |
| SST | **RIGHT**, off | single step; on, the CPU halts after each instruction |

`SW5` position 1 must be OFF for the board to behave as a KIM-1 — it is what
hands the bottom 8K to the KIM-1 decoding rather than the flat 64K decoder.

Which of `SW4`'s switches matter depends on where a program loads:

| Switch | Range |
| --- | --- |
| K1 | $0400–$07FF |
| K2 | $0800–$0BFF |
| K3 | $0C00–$0FFF |
| K4 | $1000–$13FF |

Zero page, the stack and $0200–$03FF are always present regardless, so a program
that fits the unexpanded KIM-1 needs none of them.

## Symptoms

| Symptom | Cause |
| --- | --- |
| no reply to anything | not in TTY mode, TX/RX swapped, or RS not pressed |
| `KIM` appears but commands are ignored | characters arriving faster than the monitor can take them; see `kimtape probe` |
| a range above $0400 reads back wrong or erased | that RAM is not decoded; check `SW4` and `SW5` position 1 |
| `memory at $XXXX did not hold what was written` | the same, found by the XMODEM loader as it stores |
| no reply after `xmodem at 19200 baud` | see [Serial line](#serial-line) above |
| program loads but the keypad misbehaves | the TTY link is still fitted |
