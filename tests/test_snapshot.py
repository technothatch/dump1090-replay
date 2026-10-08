"""Tests for document building: coherent now/seen clocks and dump1090 shapes."""

from __future__ import annotations

import pytest

from conftest import aircraft
from dump1090_replay.ndjson import Record
from dump1090_replay.snapshot import (
    MessageCounter,
    adjust_aircraft,
    build_aircraft_doc,
    build_receiver_doc,
    build_stats_doc,
)

RECORD_TIME = 1791028800.0
STATS_LOCAL_KEYS = {
    "blocks_processed",
    "blocks_dropped",
    "modeac",
    "modes",
    "bad",
    "unknown_icao",
    "accepted",
    "signal",
    "peak_signal",
    "strong_signals",
}


def test_aircraft_doc_shape() -> None:
    doc = build_aircraft_doc([aircraft()], record_time=RECORD_TIME, now=RECORD_TIME, messages=99)
    assert set(doc) == {"now", "messages", "aircraft"}
    assert doc["now"] == pytest.approx(RECORD_TIME)
    assert doc["messages"] == 99
    assert len(doc["aircraft"]) == 1


def test_seen_offsets_preserve_arrival_time() -> None:
    record_time = RECORD_TIME
    now = RECORD_TIME + 12.0
    doc = build_aircraft_doc(
        [aircraft(seen=0.5, seen_pos=0.7)],
        record_time=record_time,
        now=now,
        messages=1,
    )
    entry = doc["aircraft"][0]
    assert entry["seen"] == pytest.approx(12.5)
    assert entry["seen_pos"] == pytest.approx(12.7)
    # now - seen must still be the moment the message actually arrived
    assert doc["now"] - entry["seen"] == pytest.approx(record_time - 0.5)


def test_non_age_fields_pass_through_unchanged() -> None:
    source = aircraft(alt_baro="ground", lat=51.5, lon=-0.4, track=90.0, squawk="7700")
    [entry] = adjust_aircraft([source], offset=30.0)
    assert entry["alt_baro"] == "ground"
    assert entry["lat"] == 51.5
    assert entry["lon"] == -0.4
    assert entry["track"] == 90.0
    assert entry["squawk"] == "7700"
    assert source["seen"] == 0.5  # input never mutated


def test_missing_age_fields_stay_missing() -> None:
    [entry] = adjust_aircraft([{"hex": "abc123"}], offset=5.0)
    assert entry == {"hex": "abc123"}


def test_negative_offsets_clamp_ages_at_zero() -> None:
    [entry] = adjust_aircraft([aircraft(seen=0.2)], offset=-10.0)
    assert entry["seen"] == 0.0


def test_boolean_age_fields_are_not_shifted() -> None:
    [entry] = adjust_aircraft([{"hex": "abc123", "seen": True}], offset=5.0)
    assert entry["seen"] is True


def test_receiver_doc_synthesised_in_dump1090_shape() -> None:
    doc = build_receiver_doc(None, lat=51.5, lon=-0.4, refresh=2.0)
    assert set(doc) == {"version", "refresh", "lat", "lon"}
    assert doc["version"].startswith("dump1090-replay ")
    assert doc["refresh"] == 2.0
    assert doc["lat"] == 51.5
    assert doc["lon"] == -0.4


def test_receiver_doc_prefers_recorded_document() -> None:
    recorded = {"version": "readsb 3.14.1", "refresh": 1, "lat": 10.0, "lon": 20.0}
    doc = build_receiver_doc(recorded, lat=0.0, lon=0.0, refresh=1.0)
    assert doc == recorded
    assert doc is not recorded


def test_stats_doc_has_all_five_buckets() -> None:
    doc = build_stats_doc(
        None,
        now=RECORD_TIME + 600.0,
        started=RECORD_TIME,
        messages=7200,
        aircraft_count=6,
        offset=600.0,
    )
    assert {"latest", "last1min", "last5min", "last15min", "total"} <= set(doc)
    for name in ("latest", "last1min", "last5min", "last15min", "total"):
        bucket = doc[name]
        assert set(bucket) == {"start", "end", "local", "remote"}
        assert set(bucket["local"]) >= STATS_LOCAL_KEYS
        assert bucket["start"] <= bucket["end"]


def test_stats_doc_top_level_blocks() -> None:
    doc = build_stats_doc(
        None,
        now=RECORD_TIME + 10.0,
        started=RECORD_TIME,
        messages=120,
        aircraft_count=4,
        offset=10.0,
    )
    assert set(doc["cpu"]) == {"demod", "reader", "background", "total"}
    assert "cpr" in doc and "received" in doc["cpr"]
    assert doc["tracks"] == {"all": 4, "single_message": 0}
    assert doc["messages"] == {"total": 120}


def test_stats_doc_uses_recorded_document_when_present() -> None:
    recorded = {
        "latest": {"start": 100.0, "end": 101.0, "local": {"modes": 5}},
        "total": {"start": 0.0, "end": 101.0, "local": {"modes": 50}},
        "cpu": {"total": 3.0},
    }
    doc = build_stats_doc(
        recorded,
        now=RECORD_TIME,
        started=RECORD_TIME,
        messages=1,
        aircraft_count=1,
        offset=10.0,
    )
    assert doc["latest"]["start"] == pytest.approx(110.0)
    assert doc["latest"]["end"] == pytest.approx(111.0)
    assert doc["total"]["start"] == pytest.approx(10.0)
    assert doc["cpu"] == {"total": 3.0}  # non-bucket blocks untouched


def test_message_counter_is_monotonic() -> None:
    counter = MessageCounter()
    empty = Record(t=0.0, aircraft=[])
    assert counter.update(empty) == 0
    assert counter.update(Record(t=1.0, aircraft=[aircraft(messages=50)])) == 50
    # a later record with fewer messages must not run the counter backwards
    assert counter.update(Record(t=2.0, aircraft=[aircraft(messages=20)])) == 50
    assert counter.update(Record(t=3.0, aircraft=[], messages=90)) == 90
    assert counter.update(Record(t=4.0, aircraft=[], messages=10)) == 90
    assert counter.total == 90


def test_message_counter_ignores_boolean_and_bogus_values() -> None:
    counter = MessageCounter()
    record = Record(t=0.0, aircraft=[{"hex": "abc123", "messages": True}, {"hex": "def456"}])
    assert counter.update(record) == 0
