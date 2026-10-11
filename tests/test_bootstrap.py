"""Placing and relocating the XMODEM stub, and the payload it is sent."""

import os
import random
import shutil
import subprocess
import sys

import pytest

from kimtape import bootstrap
from kimtape.stub import STUBS
from kimtape.xmodem import MONITOR, segments

BUILD = os.path.join(os.path.dirname(__file__), os.pardir, "stub", "build.py")
needs_ca65 = pytest.mark.skipif(not shutil.which("ca65"), reason="needs cc65")
sys.path.insert(0, os.path.dirname(BUILD))


@needs_ca65
def test_generated_stubs_match_the_source():
    subprocess.run([sys.executable, BUILD, "--check"], check=True)


@needs_ca65
@pytest.mark.parametrize("rate", sorted(STUBS))
def test_relocation_matches_assembling_there(rate, tmp_path):
    import build  # pylint: disable=import-outside-toplevel

    rng = random.Random(rate)
    stub = STUBS[rate]
    for _ in range(3):
        code, data = rng.randrange(0x0200, 0xE000), rng.randrange(0x0100, 0xE000)
        image, _ = build.assemble(stub["cycles"], code, data, str(tmp_path))
        assert bootstrap.relocate(stub, code, data) == image


def test_stop_bits_follow_the_turnaround():
    assert [bootstrap.stop_bits(STUBS[r]) for r in (9600, 19200, 38400)] == [1, 1, 2]


@pytest.mark.parametrize("rate", sorted(STUBS))
def test_stub_prefers_the_top_of_page_two_and_the_low_stack(rate):
    stub = STUBS[rate]
    code, data = bootstrap.layout(stub, {a: 0 for a in range(0x2000, 0x6400)})
    assert code + len(stub["code"]) <= 0x0400 and code >= 0x0200
    assert bootstrap.critical_fits(stub, code)
    assert data == 0x01C0 - stub["data_size"]


def test_stub_moves_up_past_a_program_in_page_two():
    stub = STUBS[19200]
    mem = {a: 0 for a in list(range(0x0200, 0x0300)) + list(range(0x0100, 0x0110))}
    code, data = bootstrap.layout(stub, mem)
    assert 0x0400 <= code and code + len(stub["code"]) <= 0x1400
    assert data == 0x01C0 - stub["data_size"]
    mem.update({a: 0 for a in range(0x0130, 0x01C0)})
    code, data = bootstrap.layout(stub, mem)
    assert data + stub["data_size"] <= code or data >= code + len(stub["code"])
    assert all(a not in mem for a in range(data, data + stub["data_size"]))


def test_no_room_and_a_fixed_address():
    stub = STUBS[19200]
    assert bootstrap.layout(stub, {a: 0 for a in range(0x0100, 0x1400)}) is None
    full = {a: 0 for a in range(0x0000, 0x1400) if not 0x0800 <= a < 0x0A00}
    assert bootstrap.layout(stub, full, code=0x0800) is None
    code, data = bootstrap.layout(stub, {}, code=0x0E00)
    assert code == 0x0E00 and data == 0x01C0 - stub["data_size"]


def test_critical_loops_never_straddle_a_page():
    stub = STUBS[19200]
    lo, hi = stub["critical"]
    fits = [c for c in range(0x0200, 0x0300) if bootstrap.critical_fits(stub, c)]
    assert all((c + lo) >> 8 == (c + hi - 1) >> 8 for c in fits)
    assert len(fits) == 256 - (hi - lo - 1)


def test_payload_sends_zero_page_bytes_through_the_save_area():
    stub = STUBS[19200]
    zp, count = stub["zero_page"]
    mem = {0x0010: 1, zp: 2, zp + count - 1: 3, 0x0200: 4}
    data = 0x0130
    assert bootstrap.payload(stub, mem, data, 0x0200) == segments(
        {0x0010: 1, data: 2, data + count - 1: 3, 0x0200: 4}, 0x0200
    )
    assert bootstrap.payload(stub, {}, data, MONITOR) == segments({}, MONITOR)
