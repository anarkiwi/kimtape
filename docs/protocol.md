# Tape format, monitor protocol and the XMODEM loader

`load` puts a small XMODEM-CRC receiver (the stub) into RAM through the
monitor's own paper tape loader, starts it, and sends the program to it at
38400 baud. `--hex` sends the program as paper tape records instead, which is
what the rest of this page up to [the XMODEM loader](#the-xmodem-loader)
describes.

## Paper tape records

Each record is ASCII, uppercase:

```
;CCAAAADD...DDXXXX
```

| Field | Meaning |
| --- | --- |
| `;` | record mark |
| `CC` | data byte count, 2 hex digits; the ROM punches `$18` (24), `L` takes up to `$FF` |
| `AAAA` | load address |
| `DD` | data bytes, `CC` of them |
| `XXXX` | checksum: `CC` plus both address bytes plus every data byte, 16 bit |

Tape files separate records with CRLF; `L` ignores anything between records,
so kimtape sends them back to back. A record with a zero count ends the tape.
Its address field carries the number of data records that preceded it, and its
checksum is the sum of that field's two bytes, as `L` checks it and `Q` punches
it; kimtape also reads tapes that repeat the count there.

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
the end address and then opens the start before every `Q`. `Q` (DUMP, `$1D42`)
checks the end only between records, so it punches whole 24 byte records, up to
23 bytes past the end.

## The monitor's serial line

The monitor has no UART. Everything below is cycle counted from the published
listing of the 6530-002 ROM (KIM-1 User Manual, appendix; MOS rev E, 1975) at
1 MHz, with CNTH30 zero, as it is from 300 baud up. `src/kimtape/timing.py`
holds the numbers; `tests/kimsim.py` models the routines at the bit level, and
`tests/measure.py rom` checks that model against a ROM image, cycle for cycle.

### Bit timing and the auto-baud

RST (`$1C22`) waits for a start bit (DET1, `$1C31`), then counts it in DET3/DET2
(`$1C3A`/`$1C42`) passes of 14 cycles, 5 more for the pass that carries into
CNTH30, starting from `$FFFC`; it stores the count less 4 in CNTL30/CNTH30
(`$17F2`/`$17F3`). A carriage return's start bit is followed by a one, so the
count measures one bit. DELAY (`$1ED4`) then waits 14 × CNTL30 + 42 cycles
including its JSR, DEHALF (`$1EEB`) about half that, and both GETCH and OUTCH
spend 22 cycles a bit around DELAY, so the monitor's bit time is
14 × CNTL30 + 64 cycles.

That leaves 14 cycle steps, and the first poll of the start bit is 0–8 cycles
late, so at some rates RST lands on either of two counts:

| rate | host bit | CNTL30 RST measures | monitor bit | error |
| --- | --- | --- | --- | --- |
| 1200 | 833.3 | 55 | 834 | +0.08% |
| 2400 | 416.7 | 25 or 26 | 414 or 428 | −0.64% or +2.72% |
| 4800 | 208.3 | 10 or 11 | 204 or 218 | −2.08% or +4.64% |
| 9600 | 104.2 | 3 | 106 | +1.76% |
| 19200 | 52.1 | 0 | 64 | +22.9%, unusable |

The host must decode the monitor's output too: a 16× oversampling receiver
takes a mismatch of up to (½ − 1/16) / 9.5 = 4.6%. So the monitor's line tops
out at 9600 baud at 1 MHz. kimtape reads CNTL30 after the first CR and, if
another count is closer to the host's rate, deposits it; RAM is all that
changes. The same deposit moves the monitor to another rate without a reset:
`load` and `dump` sync at `--baud`, write CNTL30 for 4800 (or `--monitor-baud`)
when `--baud` is slower, switch the port as the `.` goes out, and write the
original rate back before anything runs or kimtape exits; `--monitor-baud 0`
leaves the monitor at `--baud`. Should kimtape stop half way, press RS and the
monitor measures `--baud` again; a failure at the switched rate says so and
suggests `--monitor-baud 0`.

### Receiving a character

GETCH (`$1E5A`) polls the line every 9 cycles (GET1: `BIT SAD`, `BNE`, `BMI`).
On a low it calls DELAY then DEHALF, so its first sample (GET2, `LDA SAD`)
falls DELAY + DEHALF + 8 cycles after the poll that saw the start bit, about
1.5 bits; the others follow a monitor bit apart. After bit 7 it calls DELAY
and DEHALF again, strips bit 7 (ROL, LSR) and returns 9 × DELAY + 2 × DEHALF
+ 196 cycles after that poll: within a few cycles of the end of an 8N1 stop
bit (33 after it at 1200, 3 before it at 4800). It hears nothing until it is
called again.

What the monitor does with each character before polling again, from GETCH's
return to the next poll's read, at the slower of a hex digit or letter:

| after | path | cycles |
| --- | --- | --- |
| a hex digit at the prompt | READ, PACK, SCAN, READ | 165 |
| anything but `;` in `L` | LOAD | 21 |
| `;` | LOAD, GETBYT | 34 |
| the first digit of any byte | PACK, GETBYT | 123 |
| the count | PACK, TAX, CHK | 168 |
| the address high byte | PACK, `STA POINTH`, CHK | 169 |
| the address low byte | PACK, `STA POINTL`, CHK, TXA, BEQ | 174 |
| a data byte | PACK, `STA (POINTL),Y`, CHK, INCPT carrying, DEX, BNE/INX | 202 |
| the checksum high byte | PACK, CMP, then LOADE1 on a mismatch | 144 |
| the checksum low byte | PACK, CMP, TXA, BNE LOAD across a page | 143 |

`L` (LOAD, `$1CE7`) stores each byte as it arrives and checks the checksum at
the end of the record. It counts data bytes down in X, so a record holds up to
255; INCPT carries into POINTH, so a record may cross pages. It transmits
nothing while it loads: the echo is the hardware's. At a zero count with a good
checksum it prints `<DC3> KIM` and goes to START; on a bad checksum it prints
`<DC3> ERR<DC3> KIM` and goes to START just the same, leaving `L` with the rest
of the tape unread. A record whose `;` is lost to noise is skipped without a
word: LOAD waits for the next `;`.

### Pacing

A character's start bit is seen some cycles after it falls; call that its
lateness. All eight samples land in their bits while the lateness stays below
a limit set by the bit times (`timing.window`, 411 cycles at 1200, 104 at 4800
with CNTL30 10). Each character keeps the monitor from the line for
9 × DELAY + 2 × DEHALF + 196 cycles plus the table above after it is seen; the
part of that a frame of F bits does not cover carries over:

    lateness' = max(9, lateness + busy − F × bit)

kimtape takes the fewest frame bits for which this stays inside the limit over
255 byte records, with the host's bit time 1% off either way:

| rate | CNTL30 | 8N1 (10 bits) | 8N2 (11 bits) | 8 data, mark parity, 2 stop (12 bits) |
| --- | --- | --- | --- | --- |
| 1200 | 55 | drops | **streams** | streams |
| 2400 | 25 | drops | **streams** | streams |
| 4800 | 10 | drops | **streams** | streams |
| 4800 | 11 | drops | drops | streams, output 4.6% slow |
| 9600 | 3 | drops | drops | drops: lock-step |

8N1 never works: GETCH alone takes the whole frame. 8N2 leaves
a whole bit for the work, which at 4800 is 9 cycles more than the slowest
character needs. No frame suffices at 9600, where kimtape waits for each echo
instead (lock-step), and the gap the host's turnaround leaves covers the work.
The rule passes exactly the frames that the bit level model loads, and one
bit less than it picks drops characters (`tests/test_timing.py`). Nothing
needs a pause of its own; the record length is bounded only by `L`'s 255.

Streaming lets 16 characters run ahead of their echo. During `L` the echo must
match what was sent exactly, since the monitor is silent; anything else is its
`ERR`. kimtape then stops, brings the monitor back to its prompt (sending end
records, lock-step, until a CR is answered, in case `L` lost its place), and
resumes two records before the one where the echo went wrong, lock-step from
then on. No `KIM` after the end record means the same, from the first record.
`--lock-step` waits for every echo from the start; links that do not echo fall
back to a fixed `--char-delay` per character.

## Fill characters

The monitor pads every CR/LF it prints with NUL bytes, sized for a real
teletype's carriage to return. A prompt therefore looks like:

```
<CR><LF><00><00><00><00><00><00>1780 00
```

Anything parsing monitor output must tolerate that padding; the address never
follows the newline immediately. `L` prints nothing until it ends.

## Verification

A hex load counts as done when the echo matched what was sent and the end
record drew `KIM` without `ERR`: every record's checksum held. That leaves a
record lost whole to a damaged `;`, which the monitor cannot see. The XMODEM
stub is checked for that, one byte a record (the last, which a lost record
leaves stale); `--verify` reads every hex load back.

The read back prefers `Q`, which punches the whole range in one pass. If `Q`
returns no records it falls back to opening the address and stepping with `CR`,
which is far slower but uses only the two commands every monitor answers.

If neither reads back, the load is reported unverifiable rather than retried:
nothing about a failed readback implies the load itself failed.

### Hex throughput

`tests/measure.py throughput` on the bit level model, with a host that answers
an echo 1 ms after its stop bit. The old way is kimtape 0.1: 24 byte records,
lock-step, a quiet wait a record, and always a `Q` read back. The stub column
is the bootstrap's hex stage, 416 bytes, with its check.

| rate | method | 17K program | bytes/s | with `--verify` | stub |
| --- | --- | --- | --- | --- | --- |
| 1200 | lock-step, 24 byte records | 874 s | 20 | 874 s | 22.3 s |
| 1200 | streamed, 255 byte records | 340 s | 51 | 775 s | 8.3 s |
| 2400 | lock-step, 24 byte records | 458 s | 38 | 458 s | 11.7 s |
| 2400 | streamed, 255 byte records | 170 s | 103 | 386 s | 4.2 s |
| 4800 | lock-step, 24 byte records | 251 s | 69 | 251 s | 6.4 s |
| 4800 | streamed, 255 byte records | 85 s | 205 | 192 s | 2.1 s |
| 9600 | lock-step, 255 byte records | 73 s | 239 | 129 s | 1.8 s |

The default `--monitor-baud` of 4800 makes the 1200 baud user's bootstrap
stage four times faster. 9600 lock-step depends on the adapter's turnaround: at 1 ms it beats
4800 streamed, at an FTDI's default 16 ms it is far slower. `kimtape tune`
measures it on the board.

## The XMODEM loader

### Sequence

1. Sync with the monitor at the monitor's rate, as for hex.
2. Choose where the stub goes, load it as paper tape records and verify it.
3. Open the stub's entry point and send `G`; switch the port to the fast rate.
4. The stub sends `C`; kimtape sends the program as XMODEM-CRC frames.
5. After `EOT` the stub restores what it borrowed and either jumps to the
   `--go` address or returns to the monitor (`$1C4F`, which keeps the bit
   rate the monitor measured). kimtape switches back to the monitor's rate.

If no `C` arrives within 3 s at 38400 (or `--baud-fast`), kimtape waits for the
stub to give up and return to the monitor, then tries again at each slower rate
in turn, 19200 then 9600; if those fail too it loads with hex records, saying
so.

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

Hex records stream at 2.05 characters a byte in 11 bit frames: 51 bytes/s at
1200 baud, 205 at 4800 (see [hex throughput](#hex-throughput)). Putting the
stub in costs about 8 s at 1200 baud, 2 s at the default `--monitor-baud` of
4800.
