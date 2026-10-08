"""Errors shared across dump1090-replay."""

from __future__ import annotations

__all__ = ["ReplayError"]


class ReplayError(Exception):
    """Bad input, invalid options, or an unreadable data source."""

    # Intentionally plain: every caller turns this into a user-facing message.
