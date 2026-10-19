"""2026-10-19 (round 3): ATS HTTP per-host gate and keep-alive connections.
Offline: a local HTTP/1.1 server exercises reuse, error shapes and redirects."""
import socket
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sponsorscout.scanning.ats import ats_scanner as ascan


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive capable

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.server.seen_ports.append(self.client_address[1])
        if self.path.startswith("/404"):
            body = b'{"error":"nope"}'
            self.send_response(404)
        elif self.path.startswith("/redirect"):
            body = b""
            self.send_response(301)
            self.send_header("Location", "/ok")
        else:
            body = b'{"ok": true}'
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.seen_ports = []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        # Drop this thread's pooled connections so tests stay independent.
        ascan._CONN_LOCAL.pool = {}


def _url(srv, path="/ok"):
    return f"http://127.0.0.1:{srv.server_address[1]}{path}"


def test_connection_is_reused_across_requests(server):
    ascan._CONN_LOCAL.pool = {}
    for _ in range(3):
        status, body = ascan._keepalive_request(
            _url(server), "GET", None, {"Accept": "application/json"}, 5)
        assert status == 200 and b'"ok"' in body
    # Same client socket each time: the TCP connection was reused.
    assert len(set(server.seen_ports)) == 1


def test_404_raises_http_error_with_code(server):
    ascan._CONN_LOCAL.pool = {}
    with pytest.raises(urllib.error.HTTPError) as exc:
        ascan._keepalive_request(_url(server, "/404"), "GET", None, {}, 5)
    assert exc.value.code == 404


def test_redirect_returns_none_for_urlopen_fallback(server):
    ascan._CONN_LOCAL.pool = {}
    assert ascan._keepalive_request(_url(server, "/redirect"), "GET", None, {}, 5) is None


def test_transport_failure_raises_urlerror_like_urlopen():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    ascan._CONN_LOCAL.pool = {}
    with pytest.raises(urllib.error.URLError) as exc:
        ascan._keepalive_request(f"http://127.0.0.1:{port}/x", "GET", None, {}, 2)
    assert str(exc.value).startswith("<urlopen error")


def test_fetch_goes_through_keepalive_and_maps_errors(server):
    scanner = ascan.ATSScanner(skip_preflight=True)
    ascan._CONN_LOCAL.pool = {}
    assert scanner._get_json(_url(server)) == {"ok": True}
    with pytest.raises(urllib.error.HTTPError):
        scanner._get_json(_url(server, "/404"))


def test_redirect_through_fetch_still_follows_urlopen(server):
    scanner = ascan.ATSScanner(skip_preflight=True)
    ascan._CONN_LOCAL.pool = {}
    assert scanner._get_json(_url(server, "/redirect")) == {"ok": True}


def test_host_gate_is_shared_per_host_and_caps_concurrency(monkeypatch):
    monkeypatch.setenv("SPONSORSCOUT_HOST_CONCURRENCY", "2")
    ascan._HOST_GATES.clear()
    assert ascan._host_gate("a.example") is ascan._host_gate("a.example")
    assert ascan._host_gate("a.example") is not ascan._host_gate("b.example")

    gate = ascan._host_gate("cap.example")
    active, peak, lock = [0], [0], threading.Lock()

    def work():
        with gate:
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            threading.Event().wait(0.03)
            with lock:
                active[0] -= 1

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] <= 2


def test_bad_gate_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SPONSORSCOUT_HOST_CONCURRENCY", "lots")
    ascan._HOST_GATES.clear()
    gate = ascan._host_gate("env.example")
    assert gate is not None
