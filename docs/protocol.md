# Tape format, monitor protocol and the XMODEM loader

`load` puts a small XMODEM-CRC receiver (the stub) into RAM through the
monitor's own paper tape loader, starts it, and sends the program to it at
19200 baud. `--hex` sends the program as paper tape records instead, which is
what the rest of this page up to [the XMODEM loader](#the-xmodem-loader)
describes.

## Paper tape records

Each record is ASCII, uppercase, CRLF terminated:

```
;CCAAAADD...DDXXXX
```

| Field | Meaning |
| --- | --- |
| `;` | record mark |
| `CC` | data byte count, 2 hex digits, up to `$18` (24) per record |
| `AAAA` | load address |
| `DD` | data bytes, `CC` of them |
| `XXXX` | checksum: `CC` plus both address bytes plus every data byte, 16 bit |

A record with a zero count ends the tape. Its address field carries the number of
data records that preceded it, and its checksum equals that number.

`kimtape` validates every checksum on read and refuses a malformed tape rather
than sending it to a board.

## Monitor commands

The ROM monitor accepts uppercase only:

| Command | Effect |
| --- | --- |
| `AAAA<space>` | open an address, show its contents |
| `DD.` | deposit a byte, advance |
| `CR` | advance, show |
| `L` | load a paper tape |
| `Q` | punch from the open address through `$17F7`/`$17F8` |
| `G` | execute from the open address |

`$17F7`/`$17F8` is the end address in 6532 RAM, low byte first. `Q` starts
from whichever address is open, not from `$17F5`/`$17F6`, so `kimtape` deposits
the end address and then opens the start before every `Q`.

## Why writes are paced

The monitor has no receive buffer. It samples the line in software and can hold
exactly one character, so anything sent faster than it can consume is lost.

Writes are therefore clocked by the monitor's own echo, in lock-step: each
character waits for the previous one's echo before going out (`--echo-lead 1`).
Raising the lead lets characters run ahead and the monitor drops them — with a
high lead, single-character commands like `L` and `CR` still work while
multi-character ones like `AAAA<space>` arrive as garbage and draw no reply.

Links that do not echo fall back to a fixed `--char-delay` per character and
`--line-delay` per record.

## Fill characters

The monitor pads every CR/LF with NUL bytes, sized for a real teletype's carriage
to return. A prompt therefore looks like:

```
<CR><LF><00><00><00><00><00><00>1780 00
```

Anything parsing monitor output must tolerate that padding; the address never
follows the newline immediately.

## Verification

`load` reads each range back and compares it byte for byte. It prefers `Q`, which
punches the whole range in one pass. If `Q` returns no records it falls back to
opening the address and stepping with `CR`, which is far slower but uses only the
two commands every monitor answers.

If neither reads back, the load is reported unverifiable rather than retried:
nothing about a failed readback implies the load itself failed.

## The XMODEM loader

### Sequence

1. Sync with the monitor at the monitor's rate, as for hex.
2. Choose where the stub goes, load it as paper tape records and verify it.
3. Open the stub's entry point and send `G`; switch the port to the fast rate.
4. The stub sends `C`; kimtape sends the program as XMODEM-CRC frames.
5. After `EOT` the stub restores what it borrowed and either jumps to the
   `--go` address or returns to the monitor (`$1C4F`, which keeps the bit
   rate the monitor measured). kimtape switches back to the monitor's rate.

If no `C` arrives within 3 s at 19200 (or `--baud-fast`), kimtape waits for the
stub to give up and return to the monitor, then tries again at 9600; if that
fails too it loads with hex records, saying so.

### Payload

Inside the XMODEM stream, padded with SUB (`$1A`) to whole blocks:

```
addr lo, addr hi, len lo, len hi, len bytes of data     one per contiguous run
go lo,   go hi,   0,      0                             end; go $FFFF = monitor
```

Every byte is read back as it is stored. One that does not hold (ROM, missing
RAM, I/O) makes the stub send `CAN`, then the address low byte first, and
return to the monitor; kimtape reports the address and stops, since hex would
fail the same way.

### XMODEM details

Standard XMODEM-CRC after Christensen and Forsberg: 128 byte blocks, `SOH`,
block number and its complement, CRC-16/XMODEM (polynomial `$1021`, initial 0)
high byte first; `ACK`, `NAK`, `EOT`, `CAN`. The stub asks with `C` until the
first block arrives and `NAK` after, every 0.72 s of silence; after six silent
waits (about 4.3 s) it returns to the monitor. Specifics:

| Case | Behaviour |
| --- | --- |
| bad complement or CRC | wait for the line to stay idle 25 ms, then `NAK` |
| previous block again (our `ACK` was lost) | `ACK`, data not stored again |
| anything but `SOH`/`EOT`/`CAN` where a frame should start | idle wait, then `NAK` |
| `EOT` before the end segment | treated as noise |
| `CAN` where a frame should start | return to the monitor |
| no answer to a frame (a byte was lost) | kimtape sends 133 `$FF` fill bytes to complete it, the stub rejects it, and it is sent again |

The KIM-1 echoes the input line on the output in hardware, so kimtape skips as
many received bytes as it sent before looking for an answer, and the stub waits
two bit times before answering so its reply cannot start inside the echo of the
last stop bit.

### Placement

The stub is two pieces: about 416 bytes of code, and 144 bytes of data (11
bytes of saved zero page and a 133 byte frame buffer). Each goes at the
highest address that is free of the program in the first region that has room:

| Piece | Regions, in order | Why |
| --- | --- | --- |
| data | $0100–$01BF, $0200–$03FF, $0400–$13FF | the low stack page is almost never loaded; the stub sets S to $FF |
| code | $0200–$03FF, $0400–$13FF | every KIM-1 has $0200–$03FF; PAL-II and expanded boards have $0400–$13FF |

The 6530 RAM at $1780–$17E6 (103 bytes) is too small for either piece. The
code's timed loops must not straddle a page, since a branch that crosses one
takes an extra cycle; addresses that would are skipped. `--stub-at ADDR` fixes
the code address. When nothing fits, kimtape loads with hex records.

The stub works in eleven zero page bytes, $F4–$FE, the monitor's saved
registers and scratch. It saves them on entry and restores them on the way
out, so the monitor and its open address survive. A program byte bound for one
of them is sent to its slot in the save area instead and lands with the
restore. CNTL30/CNTH30 ($17F2/$17F3) are never touched.

### Bit timing

The stub is assembled once per rate (`stub/build.py`, from `stub/xmodem.s`)
with `P` = 1 000 000 / rate cycles per bit, rounded: 104, 52 and 26, all
0.16% slow. Interrupts are masked throughout. Line reads and writes are
absolute-mode instructions whose I/O cycle is the last of their four.

| Step | Instructions | Cycles |
| --- | --- | --- |
| wait for a frame (with timeout) | `BIT SAD`, `BPL`, `DEY`, `BNE` | a sample every 11; edge seen 0–10 late |
| wait for the next byte in a frame | `BIT SAD`, `BMI` | a sample every 7; edge seen 0–6 late |
| detection to bit 0 sample | fixed delay | 1.5 `P` after the median edge, within ±3 (±5 for the first byte) |
| each data bit | delay `P`−16, `LDA SAD`, `ASL A`, `ROR buf,X`, `BCC` | `P`; a sentinel bit ends the byte |
| bit 7 sample to next start poll | `INX`, compares, wait for the stop bit | 32 |
| each transmitted bit | `LDA`, `ADC #0`, `STA SBD`, delay `P`−21, `SEC`, `ROR`, `DEX`, `BNE` | `P` |

A frame is 10 `P` and the next start bit is due 1.5 `P` after the bit 7 sample.
At 38400 that is 39 cycles against the 32 needed; a host 5% fast takes 13 of
them back, so at 38400 kimtape sends two stop bits (`P` ≥ 32 needs one).

The limit for a host bit rate error is (0.5 − jitter / 2`P`) / 8.5, the half
bit left after detection jitter spread over 8.5 bit times. Within a frame
(jitter 7) that is 5.5%, 5.1% and 4.3% at 9600, 19200 and 38400; the `SOH`
that opens one (jitter 11) is 5.3%, 4.6% and 3.4%, and a lost `SOH` costs only
a retry. Measured on the simulated board (`tests/measure.py tolerance`), in
quarter percent steps, each a 1500 byte load with no retry at all:

| rate | stop bits | host error that loads cleanly |
| --- | --- | --- |
| 9600 | 1 | -5.25% to +5.50% |
| 19200 | 1 | -4.75% to +5.25% |
| 38400 | 1 | -2.50% to +4.50% |
| 38400 | 2 | -3.50% to +4.50% |

The frame CRC and the segment parse run after each frame, about 17 ms per
block at 1 MHz.

### Throughput

A 17K program (`tests/measure.py throughput`), simulated at the board, from the
first request to the final `ACK`; turnaround is the host's delay in answering,
1 ms for most USB adapters and up to 16 ms for an FTDI with its default latency
timer:

| method | turnaround | seconds | bytes/s |
| --- | --- | --- | --- |
| XMODEM 9600 | 1 ms | 22.0 | 790 |
| XMODEM 9600 | 16 ms | 24.1 | 722 |
| XMODEM 19200 | 1 ms | 12.5 | 1397 |
| XMODEM 19200 | 16 ms | 14.5 | 1198 |
| XMODEM 38400 | 1 ms | 8.2 | 2135 |
| XMODEM 38400 | 16 ms | 10.2 | 1703 |
| hex records 1200 | - | 369 | 47 |

Hex records at 1200 baud are 2.5 characters per byte, about 47 bytes/s, and
`load` then reads the range back with `Q` at about the same rate, so the same
program takes over 12 minutes that way. Putting the stub in costs about 20 s
(18 records and their read back at 1200 baud).
