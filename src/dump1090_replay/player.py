"""The replay loop: pace records against the clock and drive tick callbacks.

``play()`` waits until the virtual clock reaches each record's timeline time
(dividing every wait by ``--speed``), marks the record current on the session
(so the HTTP server serves it), and fires callbacks for per-record work
(history files) and periodic work (history ticks while a long gap elapses).

    Stop conditions, in the order they are checked:

1. ``stop()`` returns True (polled before every record and during every wait),
2. ``duration`` wall seconds have elapsed (0 disables the limit),
3. the recording ran out and ``loop`` is off.

Ctrl+C policy lives in the CLI: ``KeyboardInterrupt`` propagates out of
``play()`` so the caller can print a summary and shut the server down.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from dump1090_replay.errors import ReplayError
from dump1090_replay.ndjson import Record
from dump1090_replay.session import Session

__all__ = ["IDLE_NAP", "MAX_NAP", "PlayResult", "play"]

#: Nap while paused, so pause/stop stay responsive.
IDLE_NAP = 0.05

#: Longest single sleep; keeps ``stop()`` and ``duration`` checks responsive
#: even when the next record is far away.
MAX_NAP = 0.5


@dataclass(frozen=True, slots=True)
class PlayResult:
    """What one ``play()`` run did."""

    records_served: int
    passes: int
    elapsed: float


def play(
    session: Session,
    *,
    loop: bool = False,
    duration: float = 0.0,
    tick_interval: float = 1.0,
    on_record: Callable[[Record], None] | None = None,
    on_tick: Callable[[], None] | None = None,
    stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    mono: Callable[[], float] = time.monotonic,
) -> PlayResult:
    """Replay ``session``'s recording, optionally forever.

    Args:
        session: Shared state; ``start()`` is called here (fresh clock).
        loop: Restart from the top instead of stopping at the last record.
        duration: Wall-clock cap in seconds; 0 means no cap.
        tick_interval: Wall seconds between ``on_tick`` calls.
        on_record: Called once per served record (after it becomes current).
        on_tick: Called roughly every ``tick_interval`` wall seconds, and once
            at the very start — this is how history files stay fresh during
            long gaps between records.
        stop: Cooperative stop predicate, polled continuously.
        sleep: Injectable sleep for tests.
        mono: Injectable monotonic clock for tests (must pair with ``sleep``).

    Returns:
        How many records were served, how many full passes completed, and how
        many wall seconds the run took.
    """
    if duration < 0:
        raise ReplayError(f"duration must be >= 0, got {duration}")
    if tick_interval <= 0:
        raise ReplayError(f"tick interval must be > 0, got {tick_interval}")

    session.start()
    wall_start = mono()
    next_tick = wall_start + tick_interval
    served = 0
    passes = 0
    pass_number = 0

    def result() -> PlayResult:
        return PlayResult(served, passes, mono() - wall_start)

    def tick_if_due() -> None:
        nonlocal next_tick
        if on_tick is None:
            return
        now = mono()
        if now >= next_tick:
            on_tick()
            while next_tick <= now:
                next_tick += tick_interval

    def out_of_time() -> bool:
        if stop is not None and stop():
            return True
        return duration > 0 and mono() - wall_start >= duration

    def wait_until(target: float) -> None:
        clock = session.clock
        while True:
            if out_of_time():
                return
            remaining = clock.remaining(target)
            if remaining <= 0:
                return
            if clock.paused:
                sleep(min(IDLE_NAP, remaining))
                tick_if_due()
                continue
            nap = min(remaining, MAX_NAP)
            if on_tick is not None:
                nap = min(nap, max(next_tick - mono(), 0.0))
            if nap > 0:
                sleep(nap)
            tick_if_due()

    if on_tick is not None:
        on_tick()

    while True:
        session.start_pass(pass_number)
        for index in range(len(session.records)):
            if out_of_time():
                return result()
            wait_until(session.target_time(index, pass_number))
            if out_of_time():
                return result()
            session.set_index(index)
            served += 1
            if on_record is not None:
                on_record(session.records[index])
            if out_of_time():
                return result()
            tick_if_due()
        passes += 1
        if not loop:
            break
        pass_number += 1
    return result()
