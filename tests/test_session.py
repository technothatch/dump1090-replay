"""Tests for the shared session state: pass timing, docs, guards."""

from __future__ import annotations

import pytest

from conftest import T0, FakeTime, aircraft, make_records
from dump1090_replay.errors import ReplayError
from dump1090_replay.session import Session


def test_empty_recording_is_rejected() -> None:
    with pytest.raises(ReplayError, match="no records to replay"):
        Session([])


def test_span_covers_one_pass(records: list) -> None:
    session = Session(records)
    assert session.span == pytest.approx(2.0)  # 3 records, 1 s gaps
    assert len(session.records) == 3


def test_single_record_span_floors_at_one_second() -> None:
    session = Session(make_records(1))
    assert session.span == pytest.approx(1.0)


def test_target_time_shifts_each_pass_by_the_span() -> None:
    session = Session(make_records(3))
    assert session.target_time(0, 0) == pytest.approx(T0)
    assert session.target_time(2, 0) == pytest.approx(T0 + 2.0)
    assert session.target_time(0, 1) == pytest.approx(T0 + 2.0)
    assert session.target_time(1, 3) == pytest.approx(T0 + 7.0)


def test_set_index_out_of_range_is_rejected() -> None:
    session = Session(make_records(2))
    with pytest.raises(ReplayError, match="out of range"):
        session.set_index(2)
    with pytest.raises(ReplayError, match="out of range"):
        session.set_index(-1)


def test_aircraft_doc_before_start_serves_first_record() -> None:
    session = Session(make_records(3))
    doc = session.aircraft_doc()
    assert doc["now"] == pytest.approx(T0)
    assert doc["aircraft"][0]["hex"] == "000000"


def test_set_index_switches_record_and_counts_messages() -> None:
    session = Session(make_records(3))
    session.set_index(2)
    doc = session.aircraft_doc()
    assert doc["aircraft"][0]["hex"] == "000002"
    assert doc["messages"] == 102  # per-aircraft messages of record 2


def test_start_resets_clock_index_and_counter() -> None:
    ft = FakeTime()
    session = Session(make_records(3), mono=ft.mono)
    session.set_index(2)
    ft.value = 50.0
    session.start()
    assert session.index == 0
    assert session.pass_number == 0
    assert session.clock.virtual_now() == pytest.approx(T0)


def test_start_pass_tracks_current_pass() -> None:
    session = Session(make_records(3))
    session.start_pass(4)
    assert session.pass_number == 4


def test_receiver_doc_honours_configured_position() -> None:
    session = Session(make_records(1), lat=51.5, lon=-0.4, refresh=2.5)
    doc = session.receiver_doc()
    assert doc["lat"] == 51.5
    assert doc["lon"] == -0.4
    assert doc["refresh"] == 2.5


def test_receiver_doc_uses_recorded_document() -> None:
    recorded = {"version": "readsb", "refresh": 1, "lat": 1.0, "lon": 2.0}
    from dump1090_replay.ndjson import Record

    session = Session([Record(t=T0, aircraft=[aircraft()], receiver=recorded)])
    assert session.receiver_doc() == recorded


def test_stats_doc_reflects_current_record() -> None:
    session = Session(make_records(3))
    session.set_index(1)
    doc = session.stats_doc()
    assert doc["tracks"]["all"] == 1
    assert doc["messages"]["total"] == 101


def test_pause_and_resume_proxy_the_clock() -> None:
    ft = FakeTime()
    session = Session(make_records(2), mono=ft.mono)
    session.pause()
    assert session.clock.paused is True
    session.resume()
    assert session.clock.paused is False
