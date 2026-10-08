"""Shared replay state: which record is current, and what time it is.

The play loop mutates the session while HTTP handler threads read it, so every
public accessor takes a lock. Reads are always coherent: the served ``now``
comes from the same clock that decides which record is current, which is what
keeps ``now`` / ``seen`` offsets believable to downstream tools.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from dump1090_replay.clock import ReplayClock
from dump1090_replay.errors import ReplayError
from dump1090_replay.ndjson import Record
from dump1090_replay.snapshot import (
    MessageCounter,
    build_aircraft_doc,
    build_receiver_doc,
    build_stats_doc,
)

__all__ = ["Session"]


class Session:
    """The current position in a recording plus the replay clock."""

    def __init__(
        self,
        records: Sequence[Record],
        *,
        speed: float = 1.0,
        lat: float = 0.0,
        lon: float = 0.0,
        refresh: float = 1.0,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        if not records:
            raise ReplayError("no records to replay")
        self._records = list(records)
        self._lock = threading.Lock()
        self._index = 0
        self._pass_number = 0
        # Loop passes replay the timeline again on top of the previous span, so
        # virtual time (and therefore ages) stay monotonic forever. A recording
        # with zero span still needs to make progress, hence the 1 s floor.
        self._span = max(self._records[-1].t - self._records[0].t, 1.0)
        self._clock = ReplayClock(self._records[0].t, speed=speed, mono=mono)
        self._counter = MessageCounter()
        self._mono = mono
        self._lat = lat
        self._lon = lon
        self._refresh = refresh

    @property
    def records(self) -> list[Record]:
        """The full recording (read-only use: the play loop paces over it)."""
        return self._records

    @property
    def clock(self) -> ReplayClock:
        """The replay clock driving ``now``."""
        return self._clock

    @property
    def span(self) -> float:
        """Timeline duration of one pass over the recording, in seconds."""
        return self._span

    @property
    def index(self) -> int:
        """Index of the record currently being served."""
        with self._lock:
            return self._index

    def start(self) -> None:
        """(Re)start the clock at the first record's timestamp.

        Called by the play loop so time spent loading files or binding sockets
        never leaks into the replayed timeline.
        """
        with self._lock:
            self._clock = ReplayClock(self._records[0].t, speed=self._clock.speed, mono=self._mono)
            self._index = 0
            self._pass_number = 0
            self._counter = MessageCounter()

    def start_pass(self, pass_number: int) -> None:
        """Switch to pass ``pass_number`` of the recording (0 = first)."""
        with self._lock:
            self._pass_number = pass_number

    @property
    def pass_number(self) -> int:
        """Current pass over the recording (grows when ``--loop`` wraps)."""
        with self._lock:
            return self._pass_number

    def target_time(self, index: int, pass_number: int) -> float:
        """Timeline time at which record ``index`` of pass ``pass_number`` is due."""
        return self._records[index].t + pass_number * self._span

    def set_index(self, index: int) -> None:
        """Make record ``index`` of the current pass the one being served."""
        if not 0 <= index < len(self._records):
            raise ReplayError(f"record index {index} out of range")
        with self._lock:
            self._index = index
            self._counter.update(self._records[index])

    def current(self) -> tuple[Record, float]:
        """The record in play plus its timeline time, under one lock."""
        with self._lock:
            record = self._records[self._index]
            return record, record.t + self._pass_number * self._span

    def aircraft_doc(self) -> dict[str, Any]:
        """The ``aircraft.json`` document for the current record and clock."""
        with self._lock:
            record = self._records[self._index]
            record_time = record.t + self._pass_number * self._span
            now = self._clock.virtual_now()
            messages = self._counter.update(record)
        return build_aircraft_doc(
            record.aircraft, record_time=record_time, now=now, messages=messages
        )

    def receiver_doc(self) -> dict[str, Any]:
        """The ``receiver.json`` document for this replay."""
        record, _ = self.current()
        return build_receiver_doc(
            record.receiver, lat=self._lat, lon=self._lon, refresh=self._refresh
        )

    def stats_doc(self) -> dict[str, Any]:
        """The ``stats.json`` document for the current record and clock."""
        record, record_time = self.current()
        now = self._clock.virtual_now()
        started = self._records[0].t
        return build_stats_doc(
            record.stats,
            now=now,
            started=started,
            messages=self._counter.update(record),
            aircraft_count=len(record.aircraft),
            offset=now - record_time,
        )

    def pause(self) -> None:
        """Freeze the replay clock (both HTTP output and pacing)."""
        self._clock.pause()

    def resume(self) -> None:
        """Unfreeze the replay clock."""
        self._clock.resume()
