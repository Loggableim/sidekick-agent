"""Compat shim — provides ``now()`` for code importing from ``sidekick_time``.

The cron module (``runtime/cron/jobs.py``, ``runtime/cron/scheduler.py``)
imports ``from sidekick_time import now as _hermes_now``.  This shim satisfies
that contract with a simple ``time.time()`` wrapper.
"""

from __future__ import annotations

import time


def now() -> float:
    """Return the current Unix timestamp (seconds since epoch).

    Mirrors ``time.time()``.  Used by the cron scheduler and job runner.
    """
    return time.time()


__all__ = ["now"]
