"""Compat shim — provides ``now()`` for code importing from ``sidekick_time``.

Returns a timezone-aware ``datetime`` (UTC) matching the original
``sidekick_time.now()`` contract.  Consumers in cron and run_agent.py
call ``.isoformat()``, ``.strftime()``, ``.tzinfo``, ``timedelta`` etc.
"""

from __future__ import annotations

from datetime import datetime, timezone


def now() -> datetime:
    """Return the current UTC datetime.

    All original consumers (cron jobs, run_agent.py) expect a ``datetime``
    with ``.isoformat()``, ``.strftime()``, ``.tzinfo``, and arithmetic.
    """
    return datetime.now(timezone.utc)


__all__ = ["now"]
