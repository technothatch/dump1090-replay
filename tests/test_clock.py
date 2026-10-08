"""Tests for the replay clock: speed math, pause/resume offsets, targets."""

from __future__ import annotations

import pytest

from conftest import FakeTime
from dump1090_replay.clock import ReplayClock
from dump1090_replay.errors import ReplayError

START = 1791028800.0


def test_speed_scales_wall_time() -> None:
    ft = FakeTime()
    clock = ReplayClock(START, speed=10.0, mono=ft.mono)
    ft.value = 1.0
    assert clock.elapsed() == pytest.approx(10.0)
    assert clock.virtual_now() == pytest.approx(START + 10.0)


def test_speed_one_tracks_wall_time() -> None:
    ft = FakeTime()
    clock = ReplayClock(START, mono=ft.mono)
    ft.value = 7.5
    assert clock.elapsed() == pytest.approx(7.5)
    assert clock.speed == 1.0
    assert clock.start_epoch == START


def test_pause_freezes_the_timeline() -> None:
    ft = FakeTime()
    clock = ReplayClock(START, mono=ft.mono)
    ft.value = 2.0
    clock.pause()
    assert clock.paused is True
    ft.value = 100.0
    assert clock.elapsed() == pytest.approx(2.0)
    assert clock.virtual_now() == pytest.approx(START + 2.0)


def test_pause_is_idempotent() -> None:
    ft = FakeTime()
    clock = ReplayClock(START, mono=ft.mono)
    ft.value = 1.0
    clock.pause()
    ft.value = 50.0
    clock.pause()  # second pause must not re-anchor the pause start
    ft.value = 60.0
    clock.resume()
    assert clock.elapsed() == pytest.approx(1.0)
    assert clock.paused is False


def test_resume_after_pause_keeps_correct_offset() -> None:
    ft = FakeTime()
    clock = ReplayClock(START, speed=2.0, mono=ft.mono)
    ft.value = 3.0  # 3 wall s -> 6 recorded s
    clock.pause()
    ft.value = 30.0  # paused for 27 wall s, must not count
    clock.resume()
    ft.value = 31.0  # one more wall s -> +2 recorded s
    assert clock.elapsed() == pytest.approx(8.0)
    assert clock.virtual_now() == pytest.approx(START + 8.0)


def test_resume_without_pause_is_a_noop() -> None:
    ft = FakeTime()
    clock = ReplayClock(START, mono=ft.mono)
    ft.value = 4.0
    clock.resume()
    assert clock.elapsed() == pytest.approx(4.0)


def test_non_positive_speed_is_rejected() -> None:
    with pytest.raises(ReplayError, match="speed must be > 0"):
        ReplayClock(START, speed=0.0)
    with pytest.raises(ReplayError, match="speed must be > 0"):
        ReplayClock(START, speed=-3.0)


def test_remaining_scales_with_speed() -> None:
    ft = FakeTime()
    clock = ReplayClock(START, speed=10.0, mono=ft.mono)
    assert clock.remaining(START + 5.0) == pytest.approx(0.5)


def test_remaining_clamps_at_zero_for_past_targets() -> None:
    ft = FakeTime()
    clock = ReplayClock(START, mono=ft.mono)
    ft.value = 10.0
    assert clock.remaining(START) == 0.0
    assert clock.remaining(START - 100.0) == 0.0


def test_remaining_is_zero_while_paused_if_already_reached() -> None:
    ft = FakeTime()
    clock = ReplayClock(START, mono=ft.mono)
    ft.value = 5.0
    clock.pause()
    assert clock.remaining(START + 4.0) == 0.0
    assert clock.remaining(START + 6.0) == pytest.approx(1.0)
