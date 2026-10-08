"""An HTTP server that answers like a dump1090 / readsb decoder.

Serves the three documents a web UI or tooling normally polls —
``/data/aircraft.json``, ``/data/receiver.json`` and ``/data/stats.json`` —
from a live :class:`~dump1090_replay.session.Session`, so every response is
internally coherent (``now`` advances, ages grow with it).

CORS headers are always sent: the point of this tool is developing browser UIs
against a replayed feed, usually from a different port or file:// origin.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import urlparse

from dump1090_replay.session import Session

__all__ = ["BackgroundServer", "ReplayRequestHandler", "ReplayServer"]

log = logging.getLogger(__name__)

#: Paths accepted for each document (dump1090's /data/ layout plus the bare
#: aliases some tools use when the replay server is the only thing running).
AIRCRAFT_PATHS = frozenset({"/data/aircraft.json", "/aircraft.json"})
RECEIVER_PATHS = frozenset({"/data/receiver.json", "/receiver.json"})
STATS_PATHS = frozenset({"/data/stats.json", "/stats.json"})

INDEX_BODY = (
    b"dump1090-replay\n"
    b"\n"
    b"GET /data/aircraft.json   replayed snapshot (now / seen coherent)\n"
    b"GET /data/receiver.json   receiver identity\n"
    b"GET /data/stats.json      decoder statistics buckets\n"
    b"\n"
    b"Point a dump1090/readsb-compatible client at this server.\n"
)


class ReplayRequestHandler(BaseHTTPRequestHandler):
    """GET handler backed by the session attached to the server."""

    server_version = "dump1090-replay"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        session = self._session()
        if path in ("/", "/index.html"):
            self._send(200, INDEX_BODY, "text/plain; charset=utf-8")
        elif path in AIRCRAFT_PATHS:
            self._send_json(session.aircraft_doc())
        elif path in RECEIVER_PATHS:
            self._send_json(session.receiver_doc())
        elif path in STATS_PATHS:
            self._send_json(session.stats_doc())
        else:
            self._send(404, b"not found\n", "text/plain; charset=utf-8")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        log.debug("%s - %s", self.address_string(), format % args)

    def _session(self) -> Session:
        return cast("ReplayServer", self.server).session

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors()
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, doc: dict[str, Any]) -> None:
        body = json.dumps(doc, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8")
        self._send(200, body, "application/json")

    def _send_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")


class ReplayServer(ThreadingHTTPServer):
    """Threaded HTTP server exposing one replay session."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], session: Session) -> None:
        super().__init__(address, ReplayRequestHandler)
        self.session = session


class BackgroundServer:
    """Run a :class:`ReplayServer` on a daemon thread, with clean shutdown."""

    def __init__(self, session: Session, *, bind: str = "127.0.0.1", port: int = 8090) -> None:
        self._session = session
        self._bind = bind
        self._port = port
        self._server: ReplayServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        """Bind, start serving, and return the actual port (0 -> ephemeral)."""
        server = ReplayServer((self._bind, self._port), self._session)
        thread = threading.Thread(
            target=server.serve_forever,
            name="dump1090-replay-http",
            daemon=True,
        )
        self._server = server
        self._thread = thread
        thread.start()
        assert server.server_address is not None
        return int(server.server_address[1])

    def stop(self) -> None:
        """Shut the server down and join its thread. Safe to call twice."""
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    @property
    def port(self) -> int:
        """Bound port, or 0 when not running."""
        if self._server is None or self._server.server_address is None:
            return 0
        return int(self._server.server_address[1])

    def __enter__(self) -> BackgroundServer:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()
