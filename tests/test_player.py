"""Tests for the replay loop: pacing, looping, stop conditions, ticks."""

from __future__ import annotations

import pytest

from conftest import FakeTime, make_records
from dump1090_replay.errors import ReplayError
from dump1090_replay.ndjson import Record
from dump1090_replay.player import MAX_NAP, play
from dump1090_replay.session import Session


def test_play_serves_every_record_once(records: list[Record], fake_time: FakeTime) -> None:
    session = Session(records, mono=fake_time.mono)
    result = play(session, sleep=fake_time.sleep, mono=fake_time.mono)
    assert result.records_served == 3
    assert result.passes == 1
    assert session.index == 2


def test_pacing_divides_gaps_by_speed(records: list[Record], fake_time: FakeTime) -> None:
    session = Session(records, speed=10.0, mono=fake_time.mono)
    result = play(session, sleep=fake_time.sleep, mono=fake_time.mono)
    # 2 gaps of 1 recorded second at 10x -> 0.2 wall seconds
    assert fake_time.slept == pytest.approx(0.2)
    assert result.elapsed == pytest.approx(0.2)


def test_realtime_pacing_matches_span(records: list[Record], fake_time: FakeTime) -> None:
    session = Session(records, mono=fake_time.mono)
    play(session, sleep=fake_time.sleep, mono=fake_time.mono)
    assert fake_time.slept == pytest.approx(2.0)


def test_records_are_served_in_order(records: list[Record], fake_time: FakeTime) -> None:
    session = Session(records, mono=fake_time.mono)
    seen: list[float] = []
    play(
        session,
        on_record=lambda rec: seen.append(rec.t),
        sleep=fake_time.sleep,
        mono=fake_time.mono,
    )
    assert seen == [rec.t for rec in records]


def test_loop_repeats_until_stop(records: list[Record], fake_time: FakeTime) -> None:
    session = Session(records, mono=fake_time.mono)
    served = 0

    def stop() -> bool:
        return served >= 5

    def count(_record: Record) -> None:
        nonlocal served
        served += 1

    result = play(
        session, loop=True, on_record=count, stop=stop, sleep=fake_time.sleep, mono=fake_time.mono
    )
    assert result.records_served == 5
    assert result.passes == 1  # one full pass plus two records into the second


def test_loop_keeps_clock_monotonic_across_passes(fake_time: FakeTime) -> None:
    records = make_records(3)
    session = Session(records, mono=fake_time.mono)
    span = session.span
    during_second_pass: list[float] = []

    def note(_record: Record) -> None:
        if session.pass_number == 1 and session.index == 1:
            during_second_pass.append(session.aircraft_doc()["now"])

    def stop() -> bool:
        return bool(during_second_pass)

    play(
        session,
        loop=True,
        on_record=note,
        stop=stop,
        sleep=fake_time.sleep,
        mono=fake_time.mono,
    )
    assert during_second_pass, "second pass was never reached"
    # virtual time moved beyond the first pass span: ages can only grow
    assert during_second_pass[0] >= records[0].t + span


def test_duration_stops_the_run_early(fake_time: FakeTime) -> None:
    records = make_records(6, gap=1.0)
    session = Session(records, mono=fake_time.mono)
    result = play(session, duration=0.5, sleep=fake_time.sleep, mono=fake_time.mono)
    assert result.records_served < 6
    assert result.elapsed == pytest.approx(0.5)
    assert fake_time.slept == pytest.approx(MAX_NAP)


def test_on_tick_fires_during_long_gaps(fake_time: FakeTime) -> None:
    records = make_records(3, gap=4.0)
    session = Session(records, mono=fake_time.mono)
    ticks: list[float] = []
    play(
        session,
        tick_interval=0.5,
        on_tick=lambda: ticks.append(fake_time.value),
        sleep=fake_time.sleep,
        mono=fake_time.mono,
    )
    assert len(ticks) >= 3  # initial tick plus ticks during the 4 s gaps
    assert ticks[0] == 0.0


def test_initial_tick_fires_before_first_record(records: list[Record], fake_time: FakeTime) -> None:
    session = Session(records, mono=fake_time.mono)
    order: list[str] = []
    play(
        session,
        on_record=lambda _r: order.append("record"),
        on_tick=lambda: order.append("tick"),
        sleep=fake_time.sleep,
        mono=fake_time.mono,
    )
    assert order[0] == "tick"
    assert order.count("tick") >= 1
    assert order.count("record") == 3


def test_keyboard_interrupt_propagates_to_the_caller(
    records: list[Record],
    fake_time: FakeTime,
) -> None:
    session = Session(records, mono=fake_time.mono)

    def interrupted(_seconds: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        play(session, sleep=interrupted, mono=fake_time.mono)
    assert session.index == 0  # first record has no wait, second wait interrupted


def test_paused_clock_holds_playback_until_stopped(
    records: list[Record],
    fake_time: FakeTime,
) -> None:
    session = Session(records, mono=fake_time.mono)

    def pause_after_first(_record: Record) -> None:
        session.pause()

    def stop() -> bool:
        return fake_time.value >= 0.1  # two paused naps of IDLE_NAP

    result = play(
        session,
        on_record=pause_after_first,
        stop=stop,
        sleep=fake_time.sleep,
        mono=fake_time.mono,
    )
    assert session.clock.paused is True
    assert result.records_served == 1  # held on the second record


def test_negative_duration_is_rejected(records: list[Record], fake_time: FakeTime) -> None:
    session = Session(records, mono=fake_time.mono)
    with pytest.raises(ReplayError, match="duration must be >= 0"):
        play(session, duration=-1.0, sleep=fake_time.sleep, mono=fake_time.mono)


def test_non_positive_tick_interval_is_rejected(records: list[Record], fake_time: FakeTime) -> None:
    session = Session(records, mono=fake_time.mono)
    with pytest.raises(ReplayError, match="tick interval must be > 0"):
        play(session, tick_interval=0.0, sleep=fake_time.sleep, mono=fake_time.mono)


def test_zero_speed_session_cannot_be_constructed() -> None:
    with pytest.raises(ReplayError, match="speed must be > 0"):
        Session(make_records(1), speed=0.0)
