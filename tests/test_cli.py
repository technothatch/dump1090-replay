"""CLI tests: record/play/info exit codes, argument errors, real server runs."""

from __future__ import annotations

import json
import socket
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from conftest import FakeTime, aircraft, make_records
from dump1090_replay.cli import main
from dump1090_replay.ndjson import Record, read_ndjson, write_ndjson


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def decoder_opener(payload: dict[str, Any]) -> Any:
    def opener(url: str, timeout: float = 0.0) -> FakeResponse:
        return FakeResponse(json.dumps(payload).encode())

    return opener


def test_version_prints_and_exits(capsys: Any) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "dump1090-replay 0.1.0" in capsys.readouterr().out


def test_missing_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_play_rejects_non_numeric_speed(ndjson_file: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["play", str(ndjson_file), "--speed", "warp", "--http", "0"])
    assert excinfo.value.code == 2


def test_play_reports_bind_failure(ndjson_file: Path, capsys: Any) -> None:
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = int(blocker.getsockname()[1])
    try:
        code = main(["play", str(ndjson_file), "--http", str(port)])
    finally:
        blocker.close()
    assert code == 1
    assert "cannot listen" in capsys.readouterr().err


def test_play_ctrl_c_stops_gracefully(ndjson_file: Path, capsys: Any) -> None:
    def interrupted(_seconds: float) -> None:
        raise KeyboardInterrupt

    code = main(["play", str(ndjson_file), "--http", "0"], sleep=interrupted)
    assert code == 0
    assert "# stopped" in capsys.readouterr().err


def test_play_missing_file_returns_error(tmp_path: Path, capsys: Any) -> None:
    code = main(["play", str(tmp_path / "nope.ndjson"), "--http", "0"])
    assert code == 1
    err = capsys.readouterr().err
    assert "error: " in err
    assert "file not found" in err


def test_play_rejects_non_positive_speed(ndjson_file: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["play", str(ndjson_file), "--speed", "0", "--http", "0"])
    assert excinfo.value.code == 2


def test_play_rejects_non_positive_interval(ndjson_file: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["play", str(ndjson_file), "--interval", "-1", "--http", "0"])
    assert excinfo.value.code == 2


def test_play_runs_to_completion_without_http(
    ndjson_file: Path,
    fake_time: FakeTime,
    capsys: Any,
) -> None:
    code = main(
        ["play", str(ndjson_file), "--speed", "10", "--http", "0"],
        sleep=fake_time.sleep,
        mono=fake_time.mono,
    )
    assert code == 0
    err = capsys.readouterr().err
    assert "3 record(s)" in err
    assert "served 3 record(s), 1 pass(es)" in err
    assert fake_time.slept == pytest.approx(0.2)


def test_play_with_realtime_file_writes_history(
    ndjson_file: Path,
    tmp_path: Path,
    fake_time: FakeTime,
    capsys: Any,
) -> None:
    live = tmp_path / "live"
    code = main(
        [
            "play",
            str(ndjson_file),
            "--http",
            "0",
            "--realtime-file",
            str(live),
            "--speed",
            "5",
        ],
        sleep=fake_time.sleep,
        mono=fake_time.mono,
    )
    assert code == 0
    assert (live / "aircraft.json").is_file()
    history = sorted(live.glob("history_*.json"))
    assert history, "no history_NNN.json files were written"
    payload = json.loads(history[0].read_text(encoding="utf-8"))
    assert set(payload) == {"now", "messages", "aircraft"}
    assert "history -> " in capsys.readouterr().err


def test_play_serves_http_and_can_be_looped(
    ndjson_file: Path,
    capsys: Any,
) -> None:
    # --http 0 means "disabled", so probe the OS for a free port first.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()

    code = main(
        [
            "play",
            str(ndjson_file),
            "--loop",
            "--http",
            str(port),
            "--duration",
            "0.3",
            "--speed",
            "50",
        ],
    )
    assert code == 0
    err = capsys.readouterr().err
    assert f"http://127.0.0.1:{port}" in err
    assert "loop" in err
    assert "served " in err


def test_play_bad_history_size_is_an_error(ndjson_file: Path, tmp_path: Path, capsys: Any) -> None:
    code = main(
        [
            "play",
            str(ndjson_file),
            "--http",
            "0",
            "--realtime-file",
            str(tmp_path / "live"),
            "--history-size",
            "0",
        ]
    )
    assert code == 1
    assert "history size" in capsys.readouterr().err


def test_record_via_cli(tmp_path: Path, capsys: Any) -> None:
    out = tmp_path / "flight.ndjson"
    payload = {"now": 1791028800.0, "messages": 7, "aircraft": [aircraft()]}
    code = main(
        [
            "record",
            "--url",
            "http://decoder:8080",
            "--out",
            str(out),
            "--interval",
            "0",
            "--max-polls",
            "2",
        ],
        opener=decoder_opener(payload),
        sleep=lambda _s: None,
    )
    assert code == 0
    records = read_ndjson(out)
    assert len(records) == 2
    assert records[0].t == 1791028800.0
    assert "wrote 2 snapshot(s)" in capsys.readouterr().err


def test_record_env_url_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    monkeypatch.setenv("DUMP1090_URL", "http://env-decoder:8080/data/aircraft.json")
    seen: list[str] = []

    def opener(url: str, timeout: float = 0.0) -> FakeResponse:
        seen.append(url)
        return FakeResponse(json.dumps({"now": 5.0, "aircraft": []}).encode())

    code = main(
        ["record", "--out", str(tmp_path / "f.ndjson"), "--max-polls", "1"],
        opener=opener,
        sleep=lambda _s: None,
    )
    assert code == 0
    assert seen == ["http://env-decoder:8080/data/aircraft.json"]
    capsys.readouterr()


def test_record_decoder_down_exits_one(tmp_path: Path, capsys: Any) -> None:
    def opener(url: str, timeout: float = 0.0) -> FakeResponse:
        raise urllib.error.URLError("connection refused")

    code = main(
        ["record", "--out", str(tmp_path / "f.ndjson"), "--max-polls", "1"],
        opener=opener,
        sleep=lambda _s: None,
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "warning: " in err
    assert "wrote 0 snapshot(s)" in err


def test_record_unwritable_output_errors(tmp_path: Path, capsys: Any) -> None:
    code = main(
        ["record", "--out", str(tmp_path), "--max-polls", "1"],
        opener=decoder_opener({"aircraft": []}),
        sleep=lambda _s: None,
    )
    assert code == 1
    assert "cannot write" in capsys.readouterr().err


def test_info_summarises_recording(ndjson_file: Path, capsys: Any) -> None:
    code = main(["info", str(ndjson_file)])
    assert code == 0
    out = capsys.readouterr().out
    assert "3 record(s) spanning 2.0s" in out
    assert "3 aircraft" in out
    assert "2026-10-03 12:00:00 UTC" in out
    assert "000000" in out


def test_info_reports_empty_recording(tmp_path: Path, capsys: Any) -> None:
    path = tmp_path / "empty.ndjson"
    path.write_text("", encoding="utf-8")
    code = main(["info", str(path)])
    assert code == 1
    assert "no records found" in capsys.readouterr().err


def test_info_counts_unique_aircraft(tmp_path: Path, capsys: Any) -> None:
    records = make_records(3)
    path = tmp_path / "flight.ndjson"
    write_ndjson(path, records)
    code = main(["info", str(path)])
    assert code == 0
    assert "3 aircraft" in capsys.readouterr().out


def test_info_tolerates_odd_aircraft_fields(tmp_path: Path, capsys: Any) -> None:
    path = tmp_path / "odd.ndjson"
    write_ndjson(
        path,
        [Record(t=1791028800.0, aircraft=[{"hex": 123, "flight": "   "}, {"hex": "abc123"}])],
    )
    code = main(["info", str(path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "1 aircraft" in out
    assert "0 callsign(s)" in out
    assert "abc123" in out


def test_info_with_no_aircraft_at_all(tmp_path: Path, capsys: Any) -> None:
    path = tmp_path / "quiet.ndjson"
    write_ndjson(path, [Record(t=1791028800.0, aircraft=[])])
    code = main(["info", str(path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "0 aircraft, 0 callsign(s)" in out
    assert "hex:" not in out
