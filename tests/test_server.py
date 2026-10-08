"""HTTP tests: a threaded server on port 0, exercised with http.client."""

from __future__ import annotations

import http.client
import json
import time
from collections.abc import Iterator

import pytest

from conftest import T0, make_records
from dump1090_replay.server import BackgroundServer
from dump1090_replay.session import Session


@pytest.fixture
def endpoint() -> Iterator[tuple[str, int]]:
    session = Session(make_records(3))
    server = BackgroundServer(session, port=0)
    port = server.start()
    try:
        yield ("127.0.0.1", port)
    finally:
        server.stop()


def fetch(
    endpoint: tuple[str, int],
    path: str,
    method: str = "GET",
) -> tuple[int, dict[str, str], bytes]:
    host, port = endpoint
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request(method, path)
        response = conn.getresponse()
        body = response.read()
        headers = {name.lower(): value for name, value in response.getheaders()}
        return response.status, headers, body
    finally:
        conn.close()


def test_aircraft_json_is_valid_and_coherent(endpoint: tuple[str, int]) -> None:
    status, headers, body = fetch(endpoint, "/data/aircraft.json")
    assert status == 200
    assert headers["content-type"] == "application/json"
    doc = json.loads(body)
    assert set(doc) == {"now", "messages", "aircraft"}
    assert doc["now"] == pytest.approx(T0, abs=5.0)
    entry = doc["aircraft"][0]
    assert entry["hex"] == "000000"
    assert doc["now"] - entry["seen"] == pytest.approx(T0 - 0.5, abs=5.0)


def test_aircraft_json_now_is_never_wall_time(endpoint: tuple[str, int]) -> None:
    _, _, body = fetch(endpoint, "/data/aircraft.json")
    doc = json.loads(body)
    assert doc["now"] < 1800000000.0  # recording epoch, not the current date


def test_receiver_json_shape(endpoint: tuple[str, int]) -> None:
    status, _, body = fetch(endpoint, "/data/receiver.json")
    assert status == 200
    doc = json.loads(body)
    assert set(doc) == {"version", "refresh", "lat", "lon"}


def test_stats_json_buckets(endpoint: tuple[str, int]) -> None:
    status, _, body = fetch(endpoint, "/data/stats.json")
    assert status == 200
    doc = json.loads(body)
    assert {"latest", "last1min", "last5min", "last15min", "total"} <= set(doc)
    assert "blocks_processed" in doc["latest"]["local"]


def test_bare_alias_paths_work(endpoint: tuple[str, int]) -> None:
    status, _, body = fetch(endpoint, "/aircraft.json")
    assert status == 200
    assert json.loads(body)["aircraft"]


def test_unknown_path_is_404(endpoint: tuple[str, int]) -> None:
    status, _, body = fetch(endpoint, "/data/nope.json")
    assert status == 404
    assert body == b"not found\n"


def test_root_serves_an_index_listing_endpoints(endpoint: tuple[str, int]) -> None:
    status, headers, body = fetch(endpoint, "/")
    assert status == 200
    assert headers["content-type"].startswith("text/plain")
    assert b"/data/aircraft.json" in body
    assert b"/data/stats.json" in body


def test_cors_headers_allow_browser_uis(endpoint: tuple[str, int]) -> None:
    _, headers, _ = fetch(endpoint, "/data/aircraft.json")
    assert headers["access-control-allow-origin"] == "*"


def test_options_preflight_is_accepted(endpoint: tuple[str, int]) -> None:
    status, headers, _ = fetch(endpoint, "/data/aircraft.json", method="OPTIONS")
    assert status == 204
    assert headers["access-control-allow-origin"] == "*"


def test_now_advances_between_requests() -> None:
    session = Session(make_records(4), speed=50.0)
    server = BackgroundServer(session, port=0)
    port = server.start()
    try:
        _, _, first = fetch(("127.0.0.1", port), "/data/aircraft.json")
        first_now = json.loads(first)["now"]
        while session.clock.virtual_now() < first_now + 0.5:
            time.sleep(0.01)
        _, _, second = fetch(("127.0.0.1", port), "/data/aircraft.json")
        second_now = json.loads(second)["now"]
    finally:
        server.stop()
    assert second_now > first_now


def test_stop_is_idempotent_and_port_reports_running_state() -> None:
    session = Session(make_records(1))
    server = BackgroundServer(session, port=0)
    assert server.port == 0
    port = server.start()
    assert port > 0
    assert server.port == port
    server.stop()
    assert server.port == 0
    server.stop()  # second stop is a no-op


def test_background_server_context_manager() -> None:
    session = Session(make_records(1))
    with BackgroundServer(session, port=0) as server:
        port = server.port
        assert port > 0
        status, _, body = fetch(("127.0.0.1", port), "/data/aircraft.json")
        assert status == 200
        assert json.loads(body)["aircraft"]
    assert server.port == 0
