"""In-memory restart gate for a disappeared host supervision ticker thread.

The guard intentionally knows nothing about supervisors, leases, models, runs,
or workers.  It only serializes requests to the host's existing ticker start
path after its thread is conclusively no longer alive.  That path remains the
sole owner of durable lease acquisition and run admission.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time
from typing import Callable, Protocol


_WINDOW_SECONDS = 300.0
_MAX_RESTART_REQUESTS = 3
_INITIAL_BACKOFF_SECONDS = 10.0
_MAX_BACKOFF_SECONDS = 60.0


class _ThreadLike(Protocol):
    def is_alive(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class TickerThreadGuardResult:
    """Fixed, redacted host status for a single guard inspection."""

    status: str
    alert_code: str | None
    retry_after_seconds: int | None
    restart_count: int


class TickerThreadGuard:
    """Request bounded host restarts only after the tracked thread is dead."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._restart_times: list[float] = []
        self._next_retry_at = 0.0
        self._escalated = False

    def inspect(
        self,
        *,
        ticker_thread: _ThreadLike | None,
        request_start: Callable[[], object],
        now: float | None = None,
    ) -> TickerThreadGuardResult:
        """Return liveness or request one bounded restart through the host."""
        if not callable(request_start):
            raise TypeError("ticker thread restart request is invalid")
        checked_at = _epoch(time.time() if now is None else now)
        with self._lock:
            if ticker_thread is not None and ticker_thread.is_alive():
                self._restart_times = self._recent(checked_at)
                self._escalated = False
                return TickerThreadGuardResult("healthy", None, None, len(self._restart_times))

            attempts = self._recent(checked_at)
            if self._escalated:
                return TickerThreadGuardResult(
                    "restart_escalated",
                    "ticker_thread_escalated",
                    None,
                    len(attempts),
                )
            if len(attempts) >= _MAX_RESTART_REQUESTS:
                self._restart_times = attempts
                self._escalated = True
                return TickerThreadGuardResult(
                    "restart_escalated",
                    "ticker_thread_escalated",
                    None,
                    len(attempts),
                )
            if checked_at < self._next_retry_at:
                self._restart_times = attempts
                return TickerThreadGuardResult(
                    "restart_backoff",
                    "ticker_thread_backoff",
                    _retry_after(self._next_retry_at, checked_at),
                    len(attempts),
                )

            # Count before invoking the host callback: an exception cannot
            # turn into an unbounded tight restart loop.
            attempts.append(checked_at)
            self._restart_times = attempts
            self._next_retry_at = checked_at + _backoff_for(len(attempts))
            try:
                requested = bool(request_start())
            except Exception:
                return TickerThreadGuardResult(
                    "restart_failed",
                    "ticker_thread_restart_failed",
                    _retry_after(self._next_retry_at, checked_at),
                    len(attempts),
                )
            if not requested:
                return TickerThreadGuardResult(
                    "restart_failed",
                    "ticker_thread_restart_failed",
                    _retry_after(self._next_retry_at, checked_at),
                    len(attempts),
                )
            return TickerThreadGuardResult(
                "restart_requested",
                "ticker_thread_restart_requested",
                _retry_after(self._next_retry_at, checked_at),
                len(attempts),
            )

    def _recent(self, now: float) -> list[float]:
        return [timestamp for timestamp in self._restart_times if now - timestamp <= _WINDOW_SECONDS]


def _epoch(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("ticker thread guard epoch is invalid")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("ticker thread guard epoch is invalid")
    return result


def _backoff_for(attempt_count: int) -> float:
    exponent = max(0, min(int(attempt_count) - 1, 8))
    return min(_MAX_BACKOFF_SECONDS, _INITIAL_BACKOFF_SECONDS * (2**exponent))


def _retry_after(next_retry_at: float, now: float) -> int:
    return max(0, min(int(math.ceil(next_retry_at - now)), int(_MAX_BACKOFF_SECONDS)))


__all__ = ["TickerThreadGuard", "TickerThreadGuardResult"]
