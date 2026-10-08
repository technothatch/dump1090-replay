"""Record live ``aircraft.json`` snapshots into the shared NDJSON format.

``dump1090-replay record`` polls a decoder and appends one line per poll::

    {"t": <epoch>, "aircraft": [ ... ]}

``t`` is the decoder's own ``now`` (wall clock as a fallback), which is what
lets :mod:`dump1090_replay.player` reconstruct coherent ``now`` / ``seen``
clocks later. The output is directly consumable by dump1090-recorder and
dump1090-influx, which share the same convention.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

from dump1090_replay.errors import ReplayError
from dump1090_replay.ndjson import Record, format_record

__all__ = [
    "AIRCRAFT_PATH",
    "DEFAULT_HOST",
    "fetch_document",
    "record",
    "resolve_url",
    "snapshot_record",
]

DEFAULT_HOST = "http://localhost:8080"
AIRCRAFT_PATH = "/data/aircraft.json"


def resolve_url(url: str) -> str:
    """Accept a bare host (``http://host:8080``) or a full document URL.

    Anything that does not already point at a ``*.json`` document is treated
    as the decoder root and gets ``/data/aircraft.json`` appended, which is
    where both dump1090-fa and readsb publish their aircraft state.
    """
    cleaned = url.rstrip("/")
    if cleaned.endswith(".json"):
        return cleaned
    return cleaned + AIRCRAFT_PATH


def fetch_document(
    url: str,
    *,
    timeout: float = 5.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Fetch one full ``aircraft.json`` document.

    Raises:
        ReplayError: Network failure, non-JSON body, or an unexpected shape.
    """
    try:
        with opener(url, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise ReplayError(f"cannot read {url}: {exc}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ReplayError(f"{url} did not return JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReplayError(f"{url} returned a JSON {type(payload).__name__}, expected an object")
    aircraft = payload.get("aircraft")
    if not isinstance(aircraft, list):
        raise ReplayError(f"{url} returned no 'aircraft' array")
    return payload


def snapshot_record(doc: dict[str, Any], *, wall: float) -> Record:
    """Turn a fetched document into one NDJSON record.

    ``t`` prefers the decoder's own ``now`` (so replayed ``seen`` ages line up
    with the served ``now``); a document without a numeric ``now`` falls back
    to the wall clock at poll time.
    """
    decoder_now = doc.get("now")
    if isinstance(decoder_now, int | float) and not isinstance(decoder_now, bool):
        t = float(decoder_now)
    else:
        t = wall
    aircraft = doc.get("aircraft")
    entries: Sequence[Any] = aircraft if isinstance(aircraft, list) else []
    return Record(t=t, aircraft=[entry for entry in entries if isinstance(entry, dict)])


def record(
    url: str = DEFAULT_HOST,
    *,
    out: str,
    interval: float = 1.0,
    max_polls: int = 0,
    timeout: float = 5.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> int:
    """Poll a decoder and append every snapshot to ``out``.

    Args:
        url: Decoder base URL or full ``aircraft.json`` URL.
        out: Destination NDJSON path (truncated).
        interval: Seconds between polls.
        max_polls: Stop after this many polls (0 = run until interrupted).
        timeout: HTTP timeout per poll.
        opener: Injectable HTTP opener for tests.
        sleep: Injectable sleep for tests.
        now: Injectable wall clock, used when the document has no ``now``.

    Returns:
        Number of snapshots written. Failed polls are warned about on stderr
        and do not abort the recording (a decoder rebooting mid-recording is
        normal); they still count towards ``max_polls``.

    Raises:
        ReplayError: The output file cannot be written, or interval is invalid.
    """
    if interval < 0:
        raise ReplayError(f"interval must be >= 0, got {interval}")
    endpoint = resolve_url(url)
    target = Path(out)
    try:
        handle: TextIO = target.open("w", encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ReplayError(f"cannot write {target}: {exc}") from exc

    written = 0
    polls = 0
    with handle:
        try:
            while True:
                polls += 1
                try:
                    doc = fetch_document(endpoint, timeout=timeout, opener=opener)
                except ReplayError as exc:
                    print(f"warning: {exc}", file=sys.stderr)
                else:
                    line = format_record(snapshot_record(doc, wall=now()))
                    try:
                        handle.write(line + "\n")
                    except OSError as exc:
                        raise ReplayError(f"cannot write {target}: {exc}") from exc
                    written += 1
                if max_polls and polls >= max_polls:
                    break
                sleep(interval)
        except KeyboardInterrupt:  # Ctrl+C ends the recording, not crashes it
            print("\n# interrupted", file=sys.stderr)
    return written
