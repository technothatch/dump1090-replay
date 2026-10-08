"""Tests for history_NNN.json output: shape, rotation, atomic writes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dump1090_replay.errors import ReplayError
from dump1090_replay.history import HistoryWriter


def doc(now: float = 1791028800.0, *, aircraft_count: int = 1) -> dict[str, Any]:
    return {
        "now": now,
        "messages": 42,
        "aircraft": [{"hex": f"{i:06x}", "seen": 0.5} for i in range(aircraft_count)],
    }


def test_writes_history_000_with_aircraft_json_shape(tmp_path: Path) -> None:
    writer = HistoryWriter(tmp_path / "live")
    path = writer.write(doc())
    assert path.name == "history_000.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"now", "messages", "aircraft"}
    assert payload["aircraft"][0]["hex"] == "000000"


def test_rotation_increments_then_wraps(tmp_path: Path) -> None:
    writer = HistoryWriter(tmp_path / "live", size=3)
    names = [writer.write(doc(now=i)).name for i in range(5)]
    assert names == [
        "history_000.json",
        "history_001.json",
        "history_002.json",
        "history_000.json",
        "history_001.json",
    ]


def test_aircraft_json_is_refreshed_on_every_write(tmp_path: Path) -> None:
    writer = HistoryWriter(tmp_path / "live")
    writer.write(doc(now=100.0))
    writer.write(doc(now=200.0))
    payload = json.loads((tmp_path / "live" / "aircraft.json").read_text(encoding="utf-8"))
    assert payload["now"] == 200.0


def test_directory_is_created_recursively(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "live"
    writer = HistoryWriter(target)
    assert target.is_dir()
    assert writer.directory == target
    writer.write(doc())
    assert (target / "history_000.json").is_file()


def test_no_scratch_files_are_left_behind(tmp_path: Path) -> None:
    writer = HistoryWriter(tmp_path / "live", size=2)
    for i in range(4):
        writer.write(doc(now=float(i)))
    leftovers = [p.name for p in (tmp_path / "live").iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    assert len(list((tmp_path / "live").glob("history_*.json"))) == 2


def test_invalid_ring_size_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ReplayError, match="history size must be >= 1"):
        HistoryWriter(tmp_path / "live", size=0)


def test_unwritable_directory_is_a_clear_error(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ReplayError, match="cannot create"):
        HistoryWriter(blocker / "live")


def test_replace_failure_is_wrapped(tmp_path: Path) -> None:
    writer = HistoryWriter(tmp_path / "live")
    (tmp_path / "live" / "history_000.json").mkdir()  # target occupied by a directory
    with pytest.raises(ReplayError, match="cannot write"):
        writer.write(doc())
