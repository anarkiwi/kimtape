"""Tapes and images given as http(s) URLs, served from a local directory."""

import functools
import http.server
import threading

import pytest
from conftest import BAUD, sample_tape

from kimtape import Tape


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the directory; /latest/<name> redirects as GitHub releases do."""

    def do_GET(self):  # pylint: disable=invalid-name
        if self.path.startswith("/latest/"):
            self.send_response(302)
            self.send_header("Location", "/" + self.path.rsplit("/", 1)[1])
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, *args):  # pylint: disable=arguments-differ
        pass


@pytest.fixture(name="server")
def server_fixture(tmp_path):
    """Base URL of an HTTP server over tmp_path."""
    handler = functools.partial(_Handler, directory=str(tmp_path))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_info_follows_redirect(cli, server, tmp_path):
    sample_tape(str(tmp_path / "web.ptp"), 0x0200, 30, seed=12)
    code, out, err = cli("info", server + "/latest/web.ptp")
    assert code == 0, err
    assert "web.ptp ($0200-$021D, 30 bytes" in out


def test_load_from_url(cli, board, server, tmp_path):
    want = sample_tape(str(tmp_path / "net%20prog.ptp"), 0x0300, 200, seed=13)
    code, _, err = cli(
        "load", "--port", board.dev, "--baud", BAUD, server + "/net%2520prog.ptp"
    )
    board.finish()
    assert code == 0, err
    assert "net%20prog.ptp" in err
    assert {a: board.mem.get(a) for a in want} == want


def test_convert_from_url(cli, server, tmp_path):
    (tmp_path / "prog.bin").write_bytes(bytes(range(16)))
    out = str(tmp_path / "prog.ptp")
    code, _, err = cli("convert", server + "/prog.bin", "--at", "2000", "-o", out)
    assert code == 0, err
    with open(out, "rb") as tape:
        assert Tape("prog.ptp", tape.read()).mem == {0x2000 + i: i for i in range(16)}


def test_missing_url_is_an_error(cli, server):
    code, _, err = cli("info", server + "/nothing.ptp")
    assert code != 0
    assert "cannot fetch %s/nothing.ptp: HTTP Error 404" % server in err
