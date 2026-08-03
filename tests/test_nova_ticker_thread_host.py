from __future__ import annotations


class _StopEvent:
    def __init__(self) -> None:
        self.set_calls = 0

    def set(self) -> None:
        self.set_calls += 1


class _Supervisor:
    def __init__(self) -> None:
        self.releases: list[tuple[str, str, str]] = []

    def release_ticker_lease(self, lease_id: str, owner_id: str, *, reason: str) -> bool:
        self.releases.append((lease_id, owner_id, reason))
        return True


def test_ticker_thread_exit_stops_its_heartbeat_and_releases_its_own_lease() -> None:
    from cli import web_server

    stop = _StopEvent()
    stop_requested = _StopEvent()
    supervisor = _Supervisor()
    lifecycle = {
        "stop_requested": stop_requested,
        "lease_stop": stop,
        "supervisor": supervisor,
        "ticker_lease_id": "lease-1",
        "ticker_owner": "dashboard:1:token",
        "runtime": None,
    }

    web_server._cleanup_nova_supervision_ticker_lifecycle(lifecycle)

    assert stop_requested.set_calls == 1
    assert stop.set_calls == 1
    assert supervisor.releases == [
        ("lease-1", "dashboard:1:token", "ticker_thread_exited")
    ]
    assert lifecycle["ticker_lease_id"] is None
    assert lifecycle["runtime"] is None



def test_dashboard_owner_check_does_not_conflate_listener_or_unknown_owner_with_death(monkeypatch) -> None:
    """Lease ownership is independent of HTTP readiness and fail-closed.

    A malformed/foreign owner must remain untouched because the dashboard
    cannot prove that process exited; only a definitive missing PID may be
    reclaimed.
    """
    from cli import web_server

    monkeypatch.setattr(web_server.os, "getpid", lambda: 4242)
    assert web_server._dashboard_execution_owner_alive("dashboard:4242:nonce") is True
    assert web_server._dashboard_execution_owner_alive("dashboard:not-a-pid:nonce") is True
    assert web_server._dashboard_execution_owner_alive("worker:opaque") is True


def test_dashboard_owner_check_reports_definitively_missing_process(monkeypatch) -> None:
    from cli import web_server

    monkeypatch.setattr(web_server.os, "getpid", lambda: 4242)
    monkeypatch.setattr(web_server.os, "name", "posix")
    monkeypatch.setattr(web_server.os, "kill", lambda pid, signal: (_ for _ in ()).throw(ProcessLookupError()))
    assert web_server._dashboard_execution_owner_alive("dashboard:987654:nonce") is False

def test_ticker_bootstrap_state_is_published_before_daemon_start(monkeypatch) -> None:
    """A first immediate pulse must not be erased by startup telemetry reset."""
    from cli import web_server

    starts: list[tuple[str, bool]] = []

    class _Thread:
        def __init__(self, *, target, daemon, name):
            self.target = target
            self.daemon = daemon
            self.name = name

        def start(self) -> None:
            starts.append((self.name, bool(web_server._NOVA_SUPERVISION_TICKER_STATE.get("running"))))

    monkeypatch.setattr(web_server, "_NOVA_SUPERVISION_TICKER_STARTED", False)
    monkeypatch.delenv("SIDEKICK_DISABLE_NOVA_SUPERVISION", raising=False)
    monkeypatch.setattr(web_server.threading, "Thread", _Thread)

    web_server._start_nova_space_supervision_ticker()

    assert starts == [
        ("nova-supervision-ticker", True),
        ("nova-supervision-ticker-guard", True),
    ]
    assert web_server.app.state.nova_supervision_ticker["running"] is True


def test_dashboard_owner_check_keeps_access_denied_process_alive(monkeypatch) -> None:
    import ctypes
    from types import SimpleNamespace
    from cli import web_server

    class _Kernel:
        def OpenProcess(self, *_args):
            return 0
        def GetLastError(self):
            return 5  # ERROR_ACCESS_DENIED: process may still be alive

    monkeypatch.setattr(web_server.os, "name", "nt")
    monkeypatch.setattr(web_server.os, "getpid", lambda: 4242)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=_Kernel()), raising=False)
    assert web_server._dashboard_execution_owner_alive("dashboard:987654:nonce") is True


def test_dashboard_owner_check_reclaims_invalid_process(monkeypatch) -> None:
    import ctypes
    from types import SimpleNamespace
    from cli import web_server

    class _Kernel:
        def OpenProcess(self, *_args):
            return 0
        def GetLastError(self):
            return 87  # ERROR_INVALID_PARAMETER: PID is definitively gone

    monkeypatch.setattr(web_server.os, "name", "nt")
    monkeypatch.setattr(web_server.os, "getpid", lambda: 4242)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=_Kernel()), raising=False)
    assert web_server._dashboard_execution_owner_alive("dashboard:987654:nonce") is False