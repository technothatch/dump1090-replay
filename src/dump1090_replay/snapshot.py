"""Build dump1090 / readsb JSON documents from a replayed record.

Faithfulness rules for ``aircraft.json``:

- ``now`` is the *virtual* time of the replay, never wall time.
- ``seen`` / ``seen_pos`` are ages in seconds relative to ``now``; they grow
  with the replay clock so ``now - seen`` (the moment the message arrived)
  stays exactly where it was in the recording.
- Everything else — ``lat``, ``lon``, ``track``, ``alt_baro`` (including the
  ``"ground"`` string), squawks, rssi, … — is passed through byte for byte.

``receiver.json`` and ``stats.json`` are synthesised in dump1090's shape
(the shared NDJSON convention only carries ``t`` + ``aircraft``) unless the
record itself carries those documents.
"""

from __future__ import annotations

import threading
from typing import Any

from dump1090_replay import __version__
from dump1090_replay.ndjson import Record

__all__ = [
    "MessageCounter",
    "adjust_aircraft",
    "build_aircraft_doc",
    "build_receiver_doc",
    "build_stats_doc",
]

#: Aircraft fields that are ages in seconds relative to ``now``.
TIME_FIELDS = ("seen", "seen_pos")

#: stats.json bucket name -> window in seconds (dump1090 / readsb layout).
STATS_BUCKETS = (
    ("latest", 1.0),
    ("last1min", 60.0),
    ("last5min", 300.0),
    ("last15min", 900.0),
)


def adjust_aircraft(aircraft: list[dict[str, Any]], *, offset: float) -> list[dict[str, Any]]:
    """Return ``aircraft`` with every age field shifted by ``offset`` seconds."""
    adjusted: list[dict[str, Any]] = []
    for entry in aircraft:
        copy = dict(entry)
        for key in TIME_FIELDS:
            value = copy.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                copy[key] = round(max(0.0, float(value) + offset), 1)
        adjusted.append(copy)
    return adjusted


def build_aircraft_doc(
    aircraft: list[dict[str, Any]],
    *,
    record_time: float,
    now: float,
    messages: int,
) -> dict[str, Any]:
    """Assemble a dump1090-style ``aircraft.json`` document for one snapshot.

    Args:
        aircraft: The record's aircraft array (never mutated).
        record_time: The record's ``t`` — the moment it was captured.
        now: Virtual replay time being served.
        messages: Cumulative decoder message counter to report.
    """
    offset = now - record_time
    return {
        "now": round(now, 3),
        "messages": int(messages),
        "aircraft": adjust_aircraft(aircraft, offset=offset),
    }


def build_receiver_doc(
    receiver: dict[str, Any] | None,
    *,
    lat: float,
    lon: float,
    refresh: float,
) -> dict[str, Any]:
    """Serve the record's ``receiver.json`` if present, else a synthesised one."""
    if receiver is not None:
        return dict(receiver)
    return {
        "version": f"dump1090-replay {__version__}",
        "refresh": refresh,
        "lat": lat,
        "lon": lon,
    }


def build_stats_doc(
    stats: dict[str, Any] | None,
    *,
    now: float,
    started: float,
    messages: int,
    aircraft_count: int,
    offset: float,
) -> dict[str, Any]:
    """Serve the record's ``stats.json`` if present, else a synthesised one.

    The synthesised document carries the five dump1090 buckets
    (``latest``, ``last1min``, ``last5min``, ``last15min``, ``total``) each with
    the local subkeys dump1090 publishes, plus the top-level ``cpu``, ``cpr``,
    ``tracks`` and ``messages`` blocks.
    """
    if stats is not None:
        return _shift_bucket_times(stats, offset=offset)

    elapsed = max(now - started, 1.0)
    rate = messages / elapsed
    doc: dict[str, Any] = {}
    for name, window in (*STATS_BUCKETS, ("total", elapsed)):
        count = max(round(rate * window), 0)
        doc[name] = {
            "start": round(max(now - window, started), 3),
            "end": round(now, 3),
            "local": {
                "blocks_processed": count + 8,
                "blocks_dropped": 0,
                "modeac": count // 10,
                "modes": count,
                "bad": count // 50,
                "unknown_icao": 0,
                "accepted": [count, 0, 0, 0, 0, 0, 0, 0],
                "signal": round(-33.0 + min(aircraft_count, 20) * 0.4, 1),
                "peak_signal": -4.0,
                "strong_signals": max(aircraft_count // 2, 1),
            },
            "remote": {},
        }
    doc["cpu"] = {"demod": 7.5, "reader": 2.0, "background": 1.0, "total": 10.5}
    doc["cpr"] = {
        "received": 0,
        "invalid": 0,
        "filtered": 0,
        "new_star": 0,
        "hit": 0,
        "miss": 0,
        "dos": 0,
    }
    doc["tracks"] = {"all": aircraft_count, "single_message": 0}
    doc["messages"] = {"total": messages}
    return doc


def _shift_bucket_times(stats: dict[str, Any], *, offset: float) -> dict[str, Any]:
    """Shift a provided stats document's bucket ``start``/``end`` with the clock."""
    shifted: dict[str, Any] = dict(stats)
    for name, _window in (*STATS_BUCKETS, ("total", 0.0)):
        bucket = shifted.get(name)
        if not isinstance(bucket, dict):
            continue
        copy = dict(bucket)
        for key in ("start", "end"):
            value = copy.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                copy[key] = round(float(value) + offset, 3)
        shifted[name] = copy
    return shifted


class MessageCounter:
    """Track dump1090's cumulative ``messages`` counter across a replay.

    The shared NDJSON format does not store the counter, so it is derived from
    the largest per-aircraft ``messages`` value seen and held monotonic —
    including across ``--loop`` restarts, where the recorded data repeats but
    a decoder's counter would keep climbing.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0

    @property
    def total(self) -> int:
        """Highest counter value observed so far."""
        return self._total

    def update(self, record: Record) -> int:
        """Fold one record into the counter and return the new total."""
        with self._lock:
            candidates = [self._total]
            if record.messages is not None:
                candidates.append(record.messages)
            for entry in record.aircraft:
                value = entry.get("messages")
                if isinstance(value, int) and not isinstance(value, bool):
                    candidates.append(value)
            self._total = max(candidates)
            return self._total
