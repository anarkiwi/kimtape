# Hardware setup

## Serial line

1200 baud, 8 data bits, no parity, 1 stop bit, no flow control. Press RS before
running any command: the monitor measures the first carriage return it receives
to set its own bit rate, and `kimtape sync` sends it.

Higher rates work on some boards — the monitor derives its divisor from that
first character — but 1200 is what the ROM was written around.

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
| program loads but the keypad misbehaves | the TTY link is still fitted |
