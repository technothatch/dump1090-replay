"""Rewrite replayed snapshots as dump1090-style ``history_NNN.json`` files.

File-based consumers (tar1090, dashboards reading a ``--write-json`` directory)
expect a circular buffer of one-second snapshots named ``history_000.json`` …
``history_NNN.json`` next to an ``aircraft.json``. ``HistoryWriter`` produces
exactly that layout: every write rotates through the ring, updates
``aircraft.json``, and lands atomically (temp file + ``os.replace``) so a
consumer never reads a half-written document.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dump1090_replay.errors import ReplayError

__all__ = ["HistoryWriter"]


class HistoryWriter:
    """Circular ``history_NNN.json`` writer with an ``aircraft.json`` mirror."""

    def __init__(self, directory: str | Path, *, size: int = 600) -> None:
        if size < 1:
            raise ReplayError(f"history size must be >= 1, got {size}")
        self._directory = Path(directory)
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ReplayError(f"cannot create {self._directory}: {exc}") from exc
        self._size = size
        self._next = 0

    @property
    def directory(self) -> Path:
        """Directory receiving the history files."""
        return self._directory

    def write(self, doc: dict[str, Any]) -> Path:
        """Rotate one snapshot into the ring and refresh ``aircraft.json``.

        Returns:
            The ``history_NNN.json`` path that was written.
        """
        name = f"history_{self._next:03d}.json"
        target = self._directory / name
        self._atomic_write(target, doc)
        self._atomic_write(self._directory / "aircraft.json", doc)
        self._next = (self._next + 1) % self._size
        return target

    @staticmethod
    def _atomic_write(target: Path, doc: dict[str, Any]) -> None:
        body = json.dumps(doc, ensure_ascii=False, separators=(", ", ": "))
        scratch = target.with_name(target.name + ".tmp")
        try:
            with scratch.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(body + "\n")
            os.replace(scratch, target)
        except OSError as exc:
            raise ReplayError(f"cannot write {target}: {exc}") from exc
