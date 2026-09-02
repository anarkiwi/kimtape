"""The data-driven example loader: catalogue handling, fetching, conversion."""

import importlib.util
import os
import pathlib
import zipfile

import pytest

from conftest import ihex

from kimtape import Tape

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "example_loader", ROOT / "examples/load.py"
)
loader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loader)


def as_url(path):
    """A file:// URL, so fetching can be tested without a network."""
    return "file://" + str(path)


@pytest.fixture(name="tape_file")
def tape_file_fixture(tmp_path):
    """A small valid paper tape on disk."""
    path = tmp_path / "prog.ptp"
    path.write_bytes(
        Tape.from_memory("prog.ptp", {0x0200 + i: i for i in range(20)}).text()
    )
    return path


def test_catalogue_entries_are_complete():
    """Every entry needs the fields the loader and the docs rely on."""
    catalogue = loader.load_catalogue()
    assert catalogue, "the catalogue is empty"
    for name, entry in catalogue.items():
        for field in (
            "title",
            "author",
            "description",
            "io",
            "start",
            "tapes",
            "source",
        ):
            assert field in entry, "%s is missing %s" % (name, field)
        assert entry["io"] in ("tty", "keypad"), name
        assert int(entry["start"], 16) <= 0xFFFF, name
        assert entry["tapes"], name
        for spec in entry["tapes"]:
            assert ("url" in spec) ^ ("archive" in spec), name
            assert "archive" not in spec or "member" in spec, name


def test_describe_mentions_where_it_runs():
    entry = {"title": "T", "io": "tty", "start": "2000", "ram": "8K"}
    assert "terminal" in loader.describe("t", entry)
    entry["io"] = "keypad"
    assert "keypad" in loader.describe("t", entry)


def test_obtain_a_paper_tape(tape_file, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    got = loader.obtain({"url": as_url(tape_file)}, str(cache))
    with open(got, "rb") as handle:
        assert Tape("prog.ptp", handle.read()).span == (0x0200, 0x0213)


def test_obtain_converts_a_raw_binary(tmp_path):
    image = tmp_path / "prog.bin"
    image.write_bytes(bytes(range(32)))
    cache = tmp_path / "cache"
    cache.mkdir()
    got = loader.obtain({"url": as_url(image), "at": "2000"}, str(cache))
    assert got.endswith(".ptp")
    with open(got, "rb") as handle:
        assert Tape("prog.ptp", handle.read()).span == (0x2000, 0x201F)


def test_obtain_converts_intel_hex_from_an_archive(tmp_path):
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("build/prog.hex", ihex([(0x2000, 0, b"\xaa\xbb"), (0, 1, b"")]))
    cache = tmp_path / "cache"
    cache.mkdir()
    got = loader.obtain(
        {"archive": as_url(archive), "member": "build/prog.hex"}, str(cache)
    )
    with open(got, "rb") as handle:
        assert Tape("prog.ptp", handle.read()).mem == {0x2000: 0xAA, 0x2001: 0xBB}


def test_downloads_are_reused(tape_file, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    loader.obtain({"url": as_url(tape_file)}, str(cache))
    tape_file.unlink()  # gone from "the network"; the cached copy must serve
    got = loader.obtain({"url": as_url(tape_file)}, str(cache))
    assert os.path.exists(got)


def test_build_args_carries_deposits_and_start():
    entry = {"start": "2000", "deposits": [{"addr": "00F1", "bytes": "DAFDFF"}]}
    args = loader.main.__globals__["argparse"].Namespace(
        port="/dev/ttyUSB0", baud=1200, no_run=False, rest=["--no-verify"]
    )
    argv = loader.build_args(entry, ["a.ptp"], args)
    assert argv[:2] == ["load", "--port"]
    assert "--deposit" in argv and "00F1=DAFDFF" in argv
    assert argv[argv.index("--go") + 1] == "2000"
    assert argv[-1] == "--no-verify"


def test_build_args_omits_start_when_not_running():
    entry = {"start": "2000"}
    args = loader.main.__globals__["argparse"].Namespace(
        port="/dev/ttyUSB0", baud=1200, no_run=True, rest=[]
    )
    assert "--go" not in loader.build_args(entry, ["a.ptp"], args)


def test_list_prints_the_catalogue(capsys):
    assert loader.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "programs:" in out
    assert "kimventure" in out


def test_unknown_program_is_refused():
    with pytest.raises(SystemExit, match="no such program"):
        loader.main(["nosuchthing", "--port", os.devnull])


def test_fetch_only_does_not_touch_a_port(tape_file, tmp_path, capsys):
    catalogue = tmp_path / "programs.toml"
    catalogue.write_text(
        'title = "T"\nauthor = "A"\ndescription = "D"\nio = "tty"\n'
        'start = "0200"\nsource = "https://example.invalid"\n'
        'tapes = [{ url = "%s" }]\n' % as_url(tape_file),
        encoding="ascii",
    )
    wrapped = tmp_path / "cat.toml"
    wrapped.write_text(
        "[demo]\n" + catalogue.read_text(encoding="ascii"), encoding="ascii"
    )
    code = loader.main(
        [
            "demo",
            "--catalogue",
            str(wrapped),
            "--dir",
            str(tmp_path / "c"),
            "--fetch-only",
            "--port",
            "/dev/nonexistent-tty",
        ]
    )
    assert code == 0
    assert "prog.ptp ($0200-$0213" in capsys.readouterr().out
