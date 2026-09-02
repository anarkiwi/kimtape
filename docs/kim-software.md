# Other programs worth loading

Programs that load with `kimtape` and where to get them. Addresses were taken
from the tape records themselves, not from prose, so they can be checked against
what `kimtape info` reports.

Anything marked "needs expansion" will not run on a stock 1K KIM-1. On a PAL-II
that means the `SW4` and `SW5` settings in [hardware.md](hardware.md); the
$2000 programs need `SW5` position 2 (8K1) on.

## A note on file extensions

`.ptp`, `.pap` and sometimes plain `.txt` all hold the same MOS paper tape
format. `kimtape` looks for the `;` record mark and ignores everything else, so
the extension does not matter — `kimtape info <file>` will tell you whether a
file is a tape and what it contains.

Several notable programs are distributed only as `.bin`, Intel HEX or S19.
Hans Otten's [PC utilities page](http://retro.hansotten.nl/6502-sbc/kim-1-manuals-and-software/pc-utilities/)
hosts Convert8bithexformat, which converts those to MOS paper tape.

## Languages

| Program | Author | Extent | Start | Tape | Source |
| --- | --- | --- | --- | --- | --- |
| Microsoft KB-9 BASIC V1.2 | Microsoft / MOS, 1977 | $2000–$41BC | $3FB1 | `kb9v2.pap` | [kb9v12.zip](http://retro.hansotten.nl/uploads/files/kb9v12.zip) |
| Microsoft KB-9 BASIC V2 | as above, reworked | $2000–$41AE | $2000 | `kb9V2.ptp` | [kb9v2.zip](http://retro.hansotten.nl/uploads/files/kb9v2.zip) |
| Microsoft KB-6 BASIC V2 | 6-digit build | $2000–$3F70 | $2000 | `kb6V2.ptp` | [kb6v2.zip](http://retro.hansotten.nl/uploads/files/kb6v2.zip) |
| FOCAL-65 (KIM-1 port) | Wayne Wall / Denver 6502 Group | $0000–$0007, $0020–$00D1, $2000–$35F3 | $2000 | `kfocal.ptp` | [focal-65 for KIM-1.zip](http://retro.hansotten.nl/uploads/focal/focal-65%20for%20KIM-1.zip) |
| Tiny BASIC, low build | Tom Pittman, 1976 | $0200–$0ADE | $0200 | `TinyBasic KIM 0200.pap` | [Otten's patched builds](http://retro.hansotten.nl/6502-sbc/kim-1-manuals-and-software/kim-1-software/tiny-basic/) |
| Tiny BASIC, high build | as above | $2000–$28DE | $2000 | `TinyBasic KIM 2000.pap` | as above |
| Tiny BASIC (Tranter build) | Tom Pittman / Jeff Tranter | $0100–$0114, $0200–$0AD5 | $0200 | `TinyBasic.ptp` | [jefftranter/6502](https://github.com/jefftranter/6502/tree/master/asm/KIM-1/TinyBasic) |

KB-9 needs 16K from $2000 and three bytes set before it will run: `$00F1=DA`,
`$00F2=FD`, `$00F3=FF`. Otten's patched Tiny BASIC builds add a prompt and
backspace handling, which is what you want on a modern terminal; program space
starts just past the interpreter.

Two more, neither available as paper tape: **fig-FORTH 6502** ships as a
[raw binary](http://retro.hansotten.nl/uploads/files/Forth%20origineel%202000.BIN)
loading at $2000, and **Bob's Tiny BASIC** (Corsham Technologies,
[source](https://github.com/CorshamTech/6502-Tiny-BASIC)) as `.bin` and Intel
HEX, occupying $0200–$13FF. Convert them first. **VTL-02** exists only as
source with no KIM-1 build.

## Games on an unexpanded KIM-1

| Program | Author | Extent | Start | Source |
| --- | --- | --- | --- | --- |
| Microchess | Peter Jennings, 1976 | $0000–$004E, $0070–$00B0, $00C0–$00DC, $0100–$01AF, $0200–$03FA, $1780–$17E5 | $0000 | [microchesssrc.zip](http://retro.hansotten.nl/uploads/files/microchesssrc.zip), [jefftranter/6502](https://github.com/jefftranter/6502/tree/master/asm/KIM-1/Microchess) |
| KIM-Venture | Bob Leedom, 1979 | see [kimventure.md](kimventure.md) | $0100 | [markbush/KIM-Venture](https://github.com/markbush/KIM-Venture) |
| Hexpawn | Bob Leedom, 1979 | $0100–$01EB or $0200–$03FF | $0100 / $0200 | [hexpawn.zip](http://retro.hansotten.nl/uploads/hexpawn/hexpawn.zip), [netzherpes/HexPawn_KIM1](https://github.com/netzherpes/HexPawn_KIM1) |
| Baseball | Bob Leedom | $0000–$00EF, $0100–$03FF, $1780–$17EF | $0100 | [Baseball.zip](http://retro.hansotten.nl/uploads/baseball/Baseball.zip) |
| MatchThis | Gino F. Silvestri | $0200–$036D | $0200 | [netzherpes/MatchThis-for-KIM-1](https://github.com/netzherpes/MatchThis-for-KIM-1) |
| Tunesmith | Anthony T. Scarpelli, 1979 | two tapes | $0200 | [netzherpes/tunesmith-for-kim-1](https://github.com/netzherpes/tunesmith-for-kim-1) |
| Pocket Calculator | Siep de Vries, 1977 | three tapes | $0200 | [netzherpes/Pocket-Calculator-for-KIM-1](https://github.com/netzherpes/Pocket-Calculator-for-KIM-1) |
| Pong with sound | Butterfield, sound by G. W. Hawkins | one tape | $0200 | [netzherpes/Kim-Pong-with-Sound](https://github.com/netzherpes/Kim-Pong-with-Sound) |

Microchess is the one to try first: chess in 1K on the keypad and LEDs, and the
first commercially sold home computer game. Hexpawn learns from losing.

## The First Book of KIM

Every program in Butterfield, Ockers and Rehnke's 1977 book fits an unexpanded
KIM-1 — all of them live in $0000–$03FF and the 6532 RAM at $1780–$17FF. Two
complete paper tape collections:

- [jefftranter/6502](https://github.com/jefftranter/6502/tree/master/asm/KIM-1/TheFirstBookOfKIM) — sources and tapes
- [Dave Will's collection](http://retro.hansotten.nl/uploads/files/first%20book%20of%20KIM%20wave%20binary%20papertape.zip) — binaries, WAVs and tapes

Blackjack ($0200–$03EB), Lunar Lander ($0200–$02D9), Wumpus (start $0200),
Kim-Tac-Toe, Horserace, Music Box and about twenty more, plus utilities:
Hypertape, Memory Test, Mini Dis, Branch, Relocate, Sort.

## Games needing expansion

| Program | Author | Extent | Start | Source |
| --- | --- | --- | --- | --- |
| Q-Chess 1.0 | TTY adaptation by Fer Weber, 1981 | $2000–$427F | $2380 cold, $2500 warm | [QCHESSBINARY.zip](http://retro.hansotten.nl/uploads/qchess/QCHESSBINARY.zip) |
| Usurpator II | H. G. Muller, 1983 | $2000–$2FBC, $32C0–$33FF | $2000 | [usurpatorII.zip](http://retro.hansotten.nl/uploads/files/usurpatorII.zip) |
| HUEY | Don Rindsberg, 1977 | at $2000 | $2000 | [netzherpes/HUEY-for-KIM-1](https://github.com/netzherpes/HUEY-for-KIM-1) |
| Game of Life | adapted by Theodore E. Bridge, 1980 | at $2000 | $2000 | [netzherpes/KIM-1-Game-of-Life](https://github.com/netzherpes/KIM-1-Game-of-Life) |
| Banner | Jim Zuber | at $2000, two tapes | $2000 | [netzherpes/Banner-for-KIM-1](https://github.com/netzherpes/Banner-for-KIM-1) |
| 50 Years KIM-1 Demo | Michael Krämer, 2026 | crunched build at $2C00 | — | [netzherpes/KIM1-Demo](https://github.com/netzherpes/KIM1-Demo) |

Usurpator II takes moves as `E2E4` over the terminal, which makes it the easiest
chess program to play on a board with no display fitted.

## Development tools

| Program | Author | Extent | Start | Tape | Source |
| --- | --- | --- | --- | --- | --- |
| 2K Symbolic Assembler | Robert Ford Denison, ~1980 | $0200–$09FF or $2000–$27FF | $0200 / $2000 | yes | [2ksa source binary.zip](http://retro.hansotten.nl/uploads/files/2ksa%20source%20binary.zip) |
| Printing Disassembler V3 | Otten 1982, Deas 2023 | $B000–$B4B0, vars at $0200 | $B000 | `PRDISV3.pap` | [PRDISV3.zip](http://retro.hansotten.nl/uploads/prdis/PRDISV3.zip) |
| LEDIP line editor | Kiumi Akingbehin | at $2000 | $2000, warm $203C | `ledip_c.ptp` | [netzherpes/LEDIP](https://github.com/netzherpes/LEDIP) |
| PLEASE | Robert M. Tripp, 1977 | $00B0–$00EF, $0100–$011F, $0200–$03FF, $1780–$17E4 | see manual | `please.ptp` plus five modules | [jefftranter/6502](https://github.com/jefftranter/6502/tree/master/asm/KIM-1/PLEASE) |
| MICRO-ADE | Peter Jennings, 1977 | $2000–$2FFF | $2000 | no, convert | [microade.zip](http://retro.hansotten.nl/uploads/microade/microade.zip) |
| ASSM/TED | C. W. Moser | at $2000 | $2000 | no, convert | [kimassmted.zip](http://retro.hansotten.nl/uploads/files/kimassmted.zip) |
| Instant Assembler | Alan Cashin, ~1979 | ~350 bytes at $0200 | $0200 | no, convert | [instantassembler.zip](http://retro.hansotten.nl/uploads/files/instantassembler.zip) |

## Monitors and operating systems

**xKIM**, Corsham Technologies' extended monitor, adds hex loading, memory
dump/edit/test/fill and SD card commands on top of the ROM monitor. It occupies
$E000–$FFFF with scratch at $DF80–$DFF9, so it needs high expansion, and it is
distributed only as `.bin`, Intel HEX and S19 —
[source](https://github.com/CorshamTech/xKIM),
[prebuilt](http://retro.hansotten.nl/uploads/corsham/xKIM-master%20older%20version.zip),
[manual](http://retro.hansotten.nl/uploads/corsham/xKIM%20Manual%201.6.pdf).
There is a [CA65 port with IEC support](https://github.com/netzherpes/xKIM_IEC_PAL-1)
aimed at the PAL-1. Note that the file named `kim clone xkim.hex` in that
archive is not xKIM at all: its records cover $2000–$4310, which is KB-9 BASIC
patched for xKIM's SD routines.

**DOS/65**, Richard A. Leary's CP/M-architecture 6502 disk operating system, was
offered in a KIM-1 configuration, but no KIM-1 SIM is downloadable, it needs a
floppy controller, and its resident footprint alone exceeds 5K. Sources and
manuals are at [z80.eu](http://www.z80.eu/dos65.html) and in
[Otten's archive](http://retro.hansotten.nl/6502-sbc/dos-65/). Its assembler
emits `.KIM` files, which are MOS KIM hex object format rather than anything
KIM-1 specific.

Also worth knowing about, though paper tape availability is unconfirmed:
[KGN COMAL](http://retro.hansotten.nl/6502-sbc/kgn-comal/) and
[Pascal-M](http://pascal.hansotten.com/niklaus-wirth/px-descendants/pascal-m/),
a full Pascal P2 compiler for the KIM-1.

## Archives

- [Hans Otten's KIM-1 software index](http://retro.hansotten.nl/6502-sbc/kim-1-manuals-and-software/kim-1-software/) — the main archive
- [Programs collected by Nils](http://retro.hansotten.nl/6502-sbc/kim-1-manuals-and-software/kim-1-software/kim-1-programs-by-nils/)
- [jefftranter/6502](https://github.com/jefftranter/6502/tree/master/asm/KIM-1) — sources and tapes, reassembled
- [netzherpes](https://github.com/netzherpes) — many KIM-1 programs restored with paper tapes

corshamtech.com is offline; use the mirrors above.

Every program here belongs to its author. Fetch them from the archives rather
than redistributing them.
