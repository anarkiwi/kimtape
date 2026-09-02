"""Tape parsing and generation, no hardware involved."""

import pytest
from conftest import ihex, srec

import kimtape
from kimtape import Tape, checksum, contiguous_runs


def test_roundtrip():
    mem = {0x0200 + i: i & 0xFF for i in range(100)}
    text = Tape.from_memory("t.ptp", mem).text()
    assert Tape("t.ptp", text).mem == mem


def test_records_split_at_chunk_size():
    tape = Tape.from_memory("t.ptp", {0x0200 + i: 0 for i in range(50)})
    assert [int(r[3:7], 16) for r in tape.records] == [0x0200, 0x0218, 0x0230]


def test_separate_runs_get_separate_records():
    mem = dict.fromkeys(list(range(0x0000, 0x0010)) + list(range(0x0200, 0x0210)), 0)
    tape = Tape.from_memory("t.ptp", mem)
    assert [int(r[3:7], 16) for r in tape.records] == [0x0000, 0x0200]


def test_contiguous_runs():
    runs = list(contiguous_runs({0: 1, 1: 2, 5: 3}))
    assert runs == [(0, [1, 2]), (5, [3])]


def test_checksum_is_count_plus_address_plus_data():
    assert checksum(2, 0x0102, b"\x03\x04") == 2 + 1 + 2 + 3 + 4


def test_bad_checksum_rejected():
    text = Tape.from_memory("t.ptp", {0x0200: 0x42}).text()
    with pytest.raises(ValueError, match="bad checksum"):
        Tape("t.ptp", text.replace(b";010200", b";010201"))


def test_malformed_record_rejected():
    with pytest.raises(ValueError, match="malformed record"):
        Tape("t.ptp", b";nonsense\r\n")


def test_short_record_rejected():
    with pytest.raises(ValueError, match="holds 1 of 2 bytes"):
        Tape("t.ptp", b";020200420046\r\n")


def test_no_data_records_rejected():
    with pytest.raises(ValueError, match="no data records"):
        Tape("t.ptp", b";00000A000A\r\n")


def test_stops_at_end_record():
    head = Tape.from_memory("t.ptp", {0x0200: 0x42}).text()
    tail = Tape.from_memory("t.ptp", {0x0300: 0x44}).text()
    assert Tape("t.ptp", head + tail).mem == {0x0200: 0x42}


def test_ignores_lines_that_are_not_records():
    text = b"a comment\r\n" + Tape.from_memory("t.ptp", {0x0200: 0x42}).text()
    assert Tape("t.ptp", text).mem == {0x0200: 0x42}


def test_span_and_str():
    tape = Tape.from_memory("game.ptp", {0x0200: 1, 0x0201: 2})
    assert tape.span == (0x0200, 0x0201)
    assert str(tape) == "game.ptp ($0200-$0201, 2 bytes, 1 records)"


def test_read_binary_needs_an_address():
    assert kimtape.read_binary(b"\x01\x02", 0x2000) == {0x2000: 1, 0x2001: 2}
    with pytest.raises(ValueError, match="no address of its own"):
        kimtape.read_image(b"\x01\x02")


def test_read_intel_hex():
    blob = ihex([(0x2000, 0, b"\xaa\xbb"), (0x0000, 1, b"")])
    assert kimtape.read_image(blob) == {0x2000: 0xAA, 0x2001: 0xBB}


@pytest.mark.parametrize("record", [(0x2000, 0, b"\xaa"), (0x0000, 1, b"")])
def test_intel_hex_checksum_is_checked(record):
    """Every record is checked, the end record included."""
    blob = bytearray(ihex([record]))
    blob[-1:] = b"0" if blob[-1:] != b"0" else b"1"
    with pytest.raises(ValueError, match="checksum error"):
        kimtape.read_image(bytes(blob))


def test_intel_hex_short_record_rejected():
    with pytest.raises(ValueError, match="is short"):
        kimtape.read_image(b":0420000012\n")


def test_intel_hex_rejects_unsupported_record_type():
    with pytest.raises(ValueError, match="type 04 is not supported"):
        kimtape.read_image(ihex([(0x0000, 4, b"\x00\x01")]))


def test_read_srec():
    assert kimtape.read_image(srec([(0x2000, b"\xaa\xbb")])) == {
        0x2000: 0xAA,
        0x2001: 0xBB,
    }


def test_read_image_recognises_a_paper_tape():
    text = Tape.from_memory("t.ptp", {0x0200: 0x42}).text()
    assert kimtape.read_image(text) == {0x0200: 0x42}


def test_empty_hex_is_rejected():
    with pytest.raises(ValueError, match="no Intel HEX data records"):
        kimtape.read_intel_hex(b":00000001FF\n")


def test_truncated_end_record_is_accepted():
    """Some punches emit a bare ';00' with no address or checksum."""
    text = Tape.from_memory("t.ptp", {0x0200: 0x42}).text()
    body = text.split(b";00", maxsplit=1)[0]
    assert Tape("t.ptp", body + b";00\r\n").mem == {0x0200: 0x42}
