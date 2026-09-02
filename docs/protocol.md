# Tape format and monitor protocol

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
| `Q` | punch `$17F5`/`$17F6` through `$17F7`/`$17F8` |
| `G` | execute from the open address |

`$17F5`–`$17F8` are the start and end address vectors in 6532 RAM: low byte
first, then high. `kimtape` deposits them before every `Q`.

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
