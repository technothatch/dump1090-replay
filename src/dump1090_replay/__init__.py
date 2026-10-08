"""Package exports for dump1090-replay."""

from __future__ import annotations

from dump1090_replay.clock import ReplayClock
from dump1090_replay.errors import ReplayError
from dump1090_replay.ndjson import Record, read_ndjson

__all__ = [
    "Record",
    "ReplayClock",
    "ReplayError",
    "__version__",
    "read_ndjson",
]

__version__ = "0.1.0"
