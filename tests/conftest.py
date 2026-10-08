"""Shared test fixtures — inline ADS-B data and a fake clock.

No test ever talks to a decoder: every snapshot here is written inline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dump1090_replay.ndjson import Record, write_ndjson

#: 2026-10-03 12:00:00 UTC — same epoch family as examples/demo.ndjson.
T0 = 1791028800.0


def aircraft(**fields: Any) -> dict[str, Any]:
    """One realistic aircraft object (SPEC.md field list subset)."""
    base: dict[str, Any] = {
        "hex": "4ca918",
        "flight": "RYR22GK ",
        "type": "B738",
        "alt_baro": 34000,
        "gs": 458,
        "track": 210.5,
        "squawk": "1200",
        "emergency": "none",
        "lat": 51.4712,
        "lon": -0.4543,
        "seen": 0.5,
        "seen_pos": 0.7,
        "messages": 100,
        "rssi": -4.2,
    }
    base.update(fields)
    return base


def make_records(count: int = 3, *, t0: float = T0, gap: float = 1.0) -> list[Record]:
    """``count`` records ``gap`` seconds apart, one aircraft each."""
    return [
        Record(
            t=t0 + i * gap,
            aircraft=[aircraft(hex=f"{i:06x}", flight=f"TEST{i:03d}  ", messages=100 + i)],
        )
        for i in range(count)
    ]


class FakeTime:
    """A monotonic clock advanced only by its own ``sleep`` (fully deterministic)."""

    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def mono(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += max(0.0, seconds)

    @property
    def slept(self) -> float:
        return sum(self.sleeps)


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def records() -> list[Record]:
    return make_records()


@pytest.fixture
def ndjson_file(tmp_path: Path, records: list[Record]) -> Path:
    path = tmp_path / "flight.ndjson"
    write_ndjson(path, records)
    return path
