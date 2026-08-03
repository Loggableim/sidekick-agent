"""Read-only liveness guard for the host-owned Nova supervision ticker.

This module deliberately never acquires a lease, pulses a runtime, starts a
model, or writes an alert.  It only projects the existing durable lease into a
small fixed status so the host can fail closed when its ticker is absent or
stale.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import sqlite3
import time

from nova.space_supervisor import ManagedSpaceSupervisor


_STALE_AFTER_SECONDS = 180.0
_MAX_AGE_SECONDS = 86_400


@dataclass(frozen=True, slots=True)
class TickerWatchdogResult:
    """Bounded liveness projection with no owner, root, or lease identity."""

    status: str
    alert_code: str | None
    last_pulse_at: float | None
    age_seconds: int | None

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"


def inspect_ticker_liveness(
    *,
    supervisor: ManagedSpaceSupervisor,
    now: float | None = None,
    stale_after_seconds: float = _STALE_AFTER_SECONDS,
) -> TickerWatchdogResult:
    """Read the current ticker lease and fail closed for stale/missing state."""
    if not isinstance(supervisor, ManagedSpaceSupervisor):
        raise TypeError("ticker watchdog supervisor is invalid")
    checked_at = _finite_epoch(time.time() if now is None else now)
    stale_after = _positive_seconds(stale_after_seconds)
    ledger_path = Path(supervisor._ledger_path)
    if not ledger_path.is_file():
        return TickerWatchdogResult("missing", "ticker_missing", None, None)
    try:
        uri = ledger_path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            row = connection.execute(
                """SELECT expires_at, updated_at
                   FROM supervisor_ticker_leases
                   WHERE state = 'active'
                   ORDER BY updated_at DESC LIMIT 1"""
            ).fetchone()
    except (OSError, sqlite3.Error, ValueError):
        return TickerWatchdogResult("unavailable", "ticker_unavailable", None, None)
    if row is None:
        return TickerWatchdogResult("missing", "ticker_missing", None, None)
    try:
        expires_at = _finite_epoch(row[0])
        updated_at = _finite_epoch(row[1])
    except (TypeError, ValueError):
        return TickerWatchdogResult("unavailable", "ticker_unavailable", None, None)
    age_seconds = _bounded_age(checked_at, updated_at)
    if expires_at <= checked_at or checked_at - updated_at > stale_after:
        return TickerWatchdogResult("stale", "ticker_stale", updated_at, age_seconds)
    return TickerWatchdogResult("healthy", None, updated_at, age_seconds)


def _finite_epoch(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("ticker watchdog epoch is invalid")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("ticker watchdog epoch is invalid")
    return result


def _positive_seconds(value: object) -> float:
    result = _finite_epoch(value)
    if result <= 0:
        raise ValueError("ticker watchdog stale window is invalid")
    return result


def _bounded_age(now: float, updated_at: float) -> int:
    return min(_MAX_AGE_SECONDS, max(0, int(now - updated_at)))


__all__ = ["TickerWatchdogResult", "inspect_ticker_liveness"]
