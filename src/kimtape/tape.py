"""KIM-1 paper tape records.

A tape is a sequence of records, each ``;`` CC AAAA DD... XXXX, terminated by a
record with a zero byte count.  CC is the byte count, AAAA the load address, and
XXXX the sum of the count, both address bytes and every data byte.
"""

import re

RECORD = re.compile(rb";([0-9A-F]{2})([0-9A-F]{4})((?:[0-9A-F]{2})*)([0-9A-F]{4})")
CHUNK = 24  # data bytes per record, as the KIM ROM punches them


def checksum(count, addr, data):
    """The KIM-1 record checksum: count plus address bytes plus data."""
    return (count + (addr >> 8) + (addr & 0xFF) + sum(data)) & 0xFFFF


def contiguous_runs(mem):
    """Yield (start, [bytes]) for each contiguous address run in mem."""
    start, run = None, []
    for addr in sorted(mem):
        if start is not None and addr != start + len(run):
            yield start, run
            start, run = None, []
        if start is None:
            start = addr
        run.append(mem[addr])
    if run:
        yield start, run


class Tape:
    """A parsed paper tape: its records and the memory image they carry."""

    def __init__(self, name, blob=b""):
        self.name, self.records, self.mem = name, [], {}
        for line in blob.split(b"\n"):
            line = line.strip()
            if not line.startswith(b";"):
                continue
            count, addr, data = self._parse(line)
            if count == 0:
                break  # end-of-tape record
            self.records.append(line)
            self.mem.update(zip(range(addr, addr + count), data))
        if blob and not self.mem:
            raise ValueError("%s: no data records" % name)

    def _parse(self, line):
        match = RECORD.fullmatch(line)
        if not match:
            raise ValueError("%s: malformed record %r" % (self.name, line[:24]))
        count, addr = int(match.group(1), 16), int(match.group(2), 16)
        data = bytes.fromhex(match.group(3).decode())
        if len(data) != count:
            raise ValueError(
                "%s: record at $%04X holds %d of %d bytes"
                % (self.name, addr, len(data), count)
            )
        if checksum(count, addr, data) != int(match.group(4), 16):
            raise ValueError("%s: bad checksum in record at $%04X" % (self.name, addr))
        return count, addr, data

    @classmethod
    def from_memory(cls, name, mem):
        """Build a tape from {addr: byte}, cutting contiguous runs into records."""
        tape = cls(name)
        tape.mem = dict(mem)
        for start, run in contiguous_runs(mem):
            for off in range(0, len(run), CHUNK):
                data, addr = bytes(run[off : off + CHUNK]), start + off
                tape.records.append(
                    b";%02X%04X%s%04X"
                    % (
                        len(data),
                        addr,
                        data.hex().upper().encode(),
                        checksum(len(data), addr, data),
                    )
                )
        return tape

    @property
    def span(self):
        """The lowest and highest addresses the tape loads."""
        return min(self.mem), max(self.mem)

    def text(self):
        """The tape as bytes, records CRLF separated, with an end record."""
        count = len(self.records)
        return b"\r\n".join(self.records + [b";00%04X%04X" % (count, count)]) + b"\r\n"

    def __str__(self):
        lo, hi = self.span
        return "%s ($%04X-$%04X, %d bytes, %d records)" % (
            self.name,
            lo,
            hi,
            len(self.mem),
            len(self.records),
        )
