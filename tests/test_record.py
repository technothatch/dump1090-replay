"""Tests for recording live snapshots into the shared NDJSON format."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from conftest import aircraft, make_records
from dump1090_replay.errors import ReplayError
from dump1090_replay.ndjson import read_ndjson, write_ndjson
from dump1090_replay.record import (
    DEFAULT_HOST,
    fetch_document,
    record,
    resolve_url,
    snapshot_record,
)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def opener_for(payload: Any) -> Any:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

    def opener(url: str, timeout: float = 0.0) -> FakeResponse:
        return FakeResponse(body)

    return opener


def failing_opener(message: str = "connection refused") -> Any:
    def opener(url: str, timeout: float = 0.0) -> FakeResponse:
        raise urllib.error.URLError(message)

    return opener


def test_resolve_url_appends_aircraft_path() -> None:
    assert resolve_url("http://localhost:8080") == "http://localhost:8080/data/aircraft.json"
    assert resolve_url("http://readsb.lan:8080/") == "http://readsb.lan:8080/data/aircraft.json"


def test_resolve_url_keeps_full_document_urls() -> None:
    full = "http://host:8080/data/aircraft.json"
    assert resolve_url(full) == full
    assert resolve_url("http://host:8080/data/aircraft.json/") == full


def test_fetch_document_returns_full_payload() -> None:
    payload = {"now": 1791028800.0, "messages": 12, "aircraft": [aircraft()]}
    doc = fetch_document(resolve_url(DEFAULT_HOST), opener=opener_for(payload))
    assert doc["messages"] == 12
    assert doc["aircraft"][0]["hex"] == "4ca918"


def test_fetch_document_reports_network_failure() -> None:
    with pytest.raises(ReplayError, match="cannot read http://localhost:8080"):
        fetch_document(resolve_url(DEFAULT_HOST), opener=failing_opener())


def test_fetch_document_rejects_non_json() -> None:
    with pytest.raises(ReplayError, match="did not return JSON"):
        fetch_document(
            "http://x/data/aircraft.json", opener=opener_for(b"<html>proxy error</html>")
        )


def test_fetch_document_rejects_non_object() -> None:
    with pytest.raises(ReplayError, match="expected an object"):
        fetch_document("http://x/data/aircraft.json", opener=opener_for([1, 2, 3]))


def test_fetch_document_rejects_missing_aircraft_array() -> None:
    with pytest.raises(ReplayError, match="no 'aircraft' array"):
        fetch_document("http://x/data/aircraft.json", opener=opener_for({"now": 1.0}))


def test_snapshot_record_prefers_decoder_now() -> None:
    record = snapshot_record({"now": 1791028800.0, "aircraft": [aircraft()]}, wall=99.0)
    assert record.t == 1791028800.0
    assert record.aircraft == [aircraft()]


def test_snapshot_record_falls_back_to_wall_clock() -> None:
    assert snapshot_record({"aircraft": []}, wall=1234.5).t == 1234.5
    assert snapshot_record({"now": True, "aircraft": []}, wall=1234.5).t == 1234.5


def test_record_writes_ndjson_lines(tmp_path: Path, fake_time: Any) -> None:
    out = tmp_path / "flight.ndjson"
    payload = {"now": 1791028800.0, "messages": 10, "aircraft": [aircraft()]}
    written = record(
        "http://decoder:8080",
        out=str(out),
        interval=1.0,
        max_polls=3,
        opener=opener_for(payload),
        sleep=fake_time.sleep,
        now=lambda: 0.0,
    )
    assert written == 3
    records = read_ndjson(out)
    assert len(records) == 3
    assert {
        key for line in out.read_text(encoding="utf-8").splitlines() for key in json.loads(line)
    } == {
        "t",
        "aircraft",
    }
    assert records[0].t == 1791028800.0
    assert fake_time.sleeps == [1.0, 1.0]  # no sleep after the final poll


def test_record_round_trips_through_read_ndjson(tmp_path: Path) -> None:
    source = make_records(4)
    path = tmp_path / "flight.ndjson"
    write_ndjson(path, source)
    assert read_ndjson(path) == source


def test_record_poll_failures_warn_and_continue(
    tmp_path: Path, capsys: Any, fake_time: Any
) -> None:
    out = tmp_path / "flight.ndjson"
    written = record(
        "http://decoder:8080",
        out=str(out),
        interval=0.5,
        max_polls=2,
        opener=failing_opener("host down"),
        sleep=fake_time.sleep,
        now=lambda: 0.0,
    )
    assert written == 0
    err = capsys.readouterr().err
    assert "warning: cannot read" in err
    assert "host down" in err
    assert out.read_text(encoding="utf-8") == ""


def test_record_keeps_going_after_a_transient_failure(tmp_path: Path, capsys: Any) -> None:
    out = tmp_path / "flight.ndjson"
    calls = {"n": 0}
    payload = {"now": 100.0, "aircraft": [aircraft()]}

    def flaky(url: str, timeout: float = 0.0) -> FakeResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("decoder restarting")
        return FakeResponse(json.dumps(payload).encode())

    written = record(
        "http://decoder:8080",
        out=str(out),
        interval=0.0,
        max_polls=2,
        opener=flaky,
        sleep=lambda _s: None,
        now=lambda: 0.0,
    )
    assert written == 1
    assert "warning" in capsys.readouterr().err
    assert read_ndjson(out)[0].t == 100.0


def test_record_rejects_negative_interval(tmp_path: Path) -> None:
    with pytest.raises(ReplayError, match="interval must be >= 0"):
        record(out=str(tmp_path / "x.ndjson"), interval=-1.0, max_polls=1)


def test_record_ctrl_c_returns_partial_count(tmp_path: Path, capsys: Any) -> None:
    out = tmp_path / "flight.ndjson"

    def interrupted(_seconds: float) -> None:
        raise KeyboardInterrupt

    written = record(
        "http://decoder:8080",
        out=str(out),
        interval=1.0,
        opener=opener_for({"now": 100.0, "aircraft": [aircraft()]}),
        sleep=interrupted,
    )
    assert written == 1
    assert "# interrupted" in capsys.readouterr().err
    assert len(read_ndjson(out)) == 1


def test_record_reports_unwritable_output(tmp_path: Path) -> None:
    with pytest.raises(ReplayError, match="cannot write"):
        record(
            "http://decoder:8080",
            out=str(tmp_path),
            interval=0.0,
            max_polls=1,
            opener=opener_for({"aircraft": []}),
        )
