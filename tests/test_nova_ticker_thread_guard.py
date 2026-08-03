from __future__ import annotations

from nova.ticker_thread_guard import TickerThreadGuard


class _Thread:
    def __init__(self, alive: bool) -> None:
        self.alive = alive

    def is_alive(self) -> bool:
        return self.alive


def test_alive_ticker_never_requests_a_second_host_start() -> None:
    guard = TickerThreadGuard()
    calls: list[str] = []

    result = guard.inspect(
        ticker_thread=_Thread(True),
        request_start=lambda: calls.append("start") or True,
        now=100.0,
    )

    assert result.status == "healthy"
    assert calls == []


def test_dead_ticker_restarts_once_then_uses_bounded_backoff() -> None:
    guard = TickerThreadGuard()
    calls: list[str] = []

    first = guard.inspect(
        ticker_thread=_Thread(False),
        request_start=lambda: calls.append("start") or True,
        now=100.0,
    )
    second = guard.inspect(
        ticker_thread=_Thread(False),
        request_start=lambda: calls.append("start") or True,
        now=101.0,
    )

    assert first.status == "restart_requested"
    assert second.status == "restart_backoff"
    assert second.retry_after_seconds == 9
    assert calls == ["start"]


def test_repeated_dead_ticker_escalates_without_a_fourth_start() -> None:
    guard = TickerThreadGuard()
    calls: list[str] = []
    start = lambda: calls.append("start") or True

    assert guard.inspect(ticker_thread=None, request_start=start, now=0.0).status == "restart_requested"
    assert guard.inspect(ticker_thread=None, request_start=start, now=10.0).status == "restart_requested"
    assert guard.inspect(ticker_thread=None, request_start=start, now=30.0).status == "restart_requested"
    escalated = guard.inspect(ticker_thread=None, request_start=start, now=70.0)

    assert escalated.status == "restart_escalated"
    assert escalated.alert_code == "ticker_thread_escalated"
    assert calls == ["start", "start", "start"]


def test_escalation_latches_past_restart_window_until_live_thread_returns() -> None:
    guard = TickerThreadGuard()
    calls: list[str] = []
    start = lambda: calls.append("start") or True

    for moment in (0.0, 10.0, 30.0):
        assert guard.inspect(
            ticker_thread=None,
            request_start=start,
            now=moment,
        ).status == "restart_requested"

    assert guard.inspect(ticker_thread=None, request_start=start, now=70.0).status == "restart_escalated"
    still_escalated = guard.inspect(ticker_thread=None, request_start=start, now=10_000.0)
    assert still_escalated.status == "restart_escalated"
    assert still_escalated.alert_code == "ticker_thread_escalated"
    assert calls == ["start", "start", "start"]

    recovered = guard.inspect(ticker_thread=_Thread(True), request_start=start, now=10_001.0)
    assert recovered.status == "healthy"
    assert guard.inspect(ticker_thread=None, request_start=start, now=10_002.0).status == "restart_requested"
    assert len(calls) == 4

