"""A local HTTP server for the download tests: it serves one body and misbehaves in the ways a real source can.

The integrity of the source depends on the pinned SHA-256 and on the host list, not on the transport (decision D02), so the tests use plain HTTP on
127.0.0.1 through the ``insecure_hosts`` parameter of the code under test, which only a test passes. The server sends no ETag and no Last-Modified, like
Mendeley's first response. It also listens on 127.0.0.2, the same machine under another address, so that a redirect can lead to a host that the host list
can refuse (or accept) without leaving the machine.

Routes (every request is recorded in ``requests`` as ``(method, path, headers)``):

- ``/file.csv``: 200 with the body and its ``Content-Length``;
- ``/no-length``: 200 with the body, no ``Content-Length`` and the connection closed at the end;
- ``/truncated``: announces the whole length and closes after half of the body;
- ``/hang``: waits ``delay_s`` seconds before answering like ``/file.csv``;
- ``/status/<code>``: that status with an empty body;
- ``/redirect?to=<location>&code=<302>``: a redirect to any ``Location``, absolute or relative;
- ``/chain/<n>``: ``n`` redirects (each to ``/chain/<n-1>``, relative) and then the body;
- ``/loop``: redirects to itself; ``/no-location``: a 302 without ``Location``;
- ``/huge``: announces a ``Content-Length`` of a terabyte and sends nothing; ``/endless``: no ``Content-Length`` and a body that never ends;
- ``/drip?step=<bytes>&pause=<seconds>``: the whole body with its ``Content-Length``, ``step`` bytes at a time with ``pause`` seconds between them: a source
  that is slow but never silent for long, which only an overall deadline stops.
- ``/gated?id=<name>``: the whole body with its ``Content-Length``, but only its first half until the test opens the gate of that name
  (``server.gate(name).release.set()``); ``server.gate(name).started`` is set when the first half has been sent. It orders overlapping downloads by events and
  not by time (no sleeps), so a test can say which of two finishes first whatever the load of the machine.
"""

from __future__ import annotations

import socketserver
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

POLL_INTERVAL_S = 0.01
ALT_HOST = "127.0.0.2"
ENDLESS_LIMIT = 64 * 1024 * 1024
GATE_TIMEOUT_S = 30.0       # a gate that nobody opens frees its connection after this long, so that a failed test does not hang the server


class Gate:
    """The two events of a ``/gated`` route: the server sets ``started`` after the first half of the body, and sends the rest when the test sets ``release``."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()


class _Server(ThreadingHTTPServer):
    def server_bind(self) -> None:
        """Bind without the reverse DNS lookup of ``HTTPServer.server_bind``, which takes seconds for some loopback addresses on Windows."""
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class FileServer:
    def __init__(self, body: bytes = b"", delay_s: float = 3.0) -> None:
        self.body = body
        self.delay_s = delay_s
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self._gates: dict[str, Gate] = {}
        self._gates_lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # keep the test output clean
                pass

            def _empty(self, code: int, **headers: str) -> None:
                self.send_response(code)
                for name, value in headers.items():
                    self.send_header(name.replace("_", "-"), value)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _redirect(self, code: int, location: str | None) -> None:
                self.send_response(code)
                if location is not None:
                    self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _body(self, send_length: bool = True, upto: int | None = None) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                if send_length:
                    self.send_header("Content-Length", str(len(outer.body)))
                else:
                    self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(outer.body if upto is None else outer.body[:upto])
                self.wfile.flush()
                if not send_length or upto is not None:
                    self.close_connection = True

            def _respond(self, method: str) -> None:
                outer.requests.append((method, self.path, dict(self.headers.items())))
                parts = urllib.parse.urlsplit(self.path)
                path, query = parts.path, urllib.parse.parse_qs(parts.query)
                if path == "/file.csv":
                    self._body()
                elif path == "/no-length":
                    self._body(send_length=False)
                elif path == "/truncated":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(outer.body)))
                    self.end_headers()
                    self.wfile.write(outer.body[: len(outer.body) // 2])
                    self.wfile.flush()
                    self.close_connection = True
                elif path == "/hang":
                    time.sleep(outer.delay_s)
                    self._body()
                elif path.startswith("/status/"):
                    self._empty(int(path.rsplit("/", 1)[1]))
                elif path == "/redirect":
                    self._redirect(int(query.get("code", ["302"])[0]), query["to"][0])
                elif path.startswith("/chain/"):
                    remaining = int(path.rsplit("/", 1)[1])
                    if remaining > 0:
                        self._redirect(302, f"/chain/{remaining - 1}")
                    else:
                        self._body()
                elif path == "/loop":
                    self._redirect(302, "/loop")
                elif path == "/no-location":
                    self._redirect(302, None)
                elif path == "/huge":
                    self.send_response(200)
                    self.send_header("Content-Length", str(10**12))
                    self.end_headers()
                    self.close_connection = True
                elif path == "/drip":
                    step, pause = int(query.get("step", ["100"])[0]), float(query.get("pause", ["0.05"])[0])
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(outer.body)))
                    self.end_headers()
                    try:
                        for start in range(0, len(outer.body), step):
                            self.wfile.write(outer.body[start:start + step])
                            self.wfile.flush()
                            time.sleep(pause)
                    except OSError:
                        pass
                    self.close_connection = True
                elif path == "/gated":
                    gate = outer.gate(query["id"][0])
                    half = len(outer.body) // 2
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(outer.body)))
                    self.end_headers()
                    try:
                        self.wfile.write(outer.body[:half])
                        self.wfile.flush()
                        gate.started.set()
                        if gate.release.wait(timeout=GATE_TIMEOUT_S):
                            self.wfile.write(outer.body[half:])
                            self.wfile.flush()
                    except OSError:
                        pass
                    self.close_connection = True
                elif path == "/endless":
                    self.send_response(200)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    sent = 0
                    block = b"x" * 65536
                    try:
                        while sent < ENDLESS_LIMIT:
                            self.wfile.write(block)
                            sent += len(block)
                    except OSError:
                        pass
                    self.close_connection = True
                else:
                    self._empty(404)

            def do_GET(self) -> None:
                self._respond("GET")

            def do_HEAD(self) -> None:
                self._respond("HEAD")

        self._server = _Server(("127.0.0.1", 0), Handler)
        self._alt = _Server((ALT_HOST, self._server.server_address[1]), Handler)
        # the default poll interval is 0.5 s and ``shutdown()`` waits for one: half a second lost by every test that starts a server
        self._threads = [
            threading.Thread(target=lambda s=server: s.serve_forever(poll_interval=POLL_INTERVAL_S), daemon=True) for server in (self._server, self._alt)
        ]

    def gate(self, name: str) -> Gate:
        """The gate of the ``/gated?id=<name>`` route, created on first use."""
        with self._gates_lock:
            return self._gates.setdefault(name, Gate())

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def url(self, path: str = "/file.csv", host: str = "127.0.0.1") -> str:
        """The address of `path` on this server, as seen from ``host`` (127.0.0.1 or ``ALT_HOST``)."""
        return f"http://{host}:{self.port}{path}"

    def paths(self) -> list[str]:
        return [path for _method, path, _headers in self.requests]

    def hosts(self) -> list[str]:
        """The ``Host`` header of every request: ``127.0.0.1:port`` or ``127.0.0.2:port``."""
        return [headers.get("Host", "") for _method, _path, headers in self.requests]

    def __enter__(self) -> FileServer:
        for thread in self._threads:
            thread.start()
        return self

    def __exit__(self, *exc) -> None:
        for server in (self._server, self._alt):
            server.shutdown()
            server.server_close()
        for thread in self._threads:
            thread.join(timeout=5)
