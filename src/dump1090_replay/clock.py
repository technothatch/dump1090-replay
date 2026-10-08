"""A virtual replay clock: wall time scaled by ``--speed``, with pause/resume.

The clock maps wall time onto the recorded timeline::

    virtual_now = first_record_t + (wall_elapsed - paused) * speed

so downstream consumers always see timestamps inside the recording's own
epoch, while ``--speed 10`` advances that epoch ten times faster than reality.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from dump1090_replay.errors import ReplayError

__all__ = ["ReplayClock"]


class ReplayClock:
    """Monotonic wall clock scaled into the recording's timeline."""

    def __init__(
        self,
        start_epoch: float,
        *,
        speed: float = 1.0,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        if speed <= 0:
            raise ReplayError(f"speed must be > 0, got {speed}")
        self._start_epoch = float(start_epoch)
        self._speed = float(speed)
        self._mono = mono
        self._origin = mono()
        self._paused_since: float | None = None
        self._paused_total = 0.0

    @property
    def speed(self) -> float:
        """Replay speed multiplier (10 = ten recorded seconds per wall second)."""
        return self._speed

    @property
    def start_epoch(self) -> float:
        """Timeline epoch the clock started from (the first record's ``t``)."""
        return self._start_epoch

    @property
    def paused(self) -> bool:
        """Whether the clock is currently paused."""
        return self._paused_since is not None

    def elapsed(self) -> float:
        """Recorded seconds consumed so far (paused time excluded)."""
        edge = self._paused_since if self._paused_since is not None else self._mono()
        return max(0.0, (edge - self._origin - self._paused_total) * self._speed)

    def virtual_now(self) -> float:
        """Current time on the recorded timeline, in epoch seconds."""
        return self._start_epoch + self.elapsed()

    def pause(self) -> None:
        """Freeze the timeline. Repeated calls are ignored."""
        if self._paused_since is None:
            self._paused_since = self._mono()

    def resume(self) -> None:
        """Continue the timeline, discarding the paused wall time. Idempotent."""
        if self._paused_since is not None:
            self._paused_total += self._mono() - self._paused_since
            self._paused_since = None

    def remaining(self, target_epoch: float) -> float:
        """Wall seconds to sleep until the timeline reaches ``target_epoch``."""
        return max(0.0, (target_epoch - self.virtual_now()) / self._speed)
