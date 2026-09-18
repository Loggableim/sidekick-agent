from __future__ import annotations

import asyncio
import sqlite3

from fastapi.testclient import TestClient


def test_usage_analytics_returns_bounded_degraded_payload_on_session_db_io(monkeypatch, tmp_path):
    """A state-db I/O fault must not surface as a WebUI 500 storm."""

    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server

    class _BrokenSessionDB:
        def __init__(self, *args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error: C:/private/state.db")

    monkeypatch.setattr("runtime._compat.shim_state.SessionDB", _BrokenSessionDB)

    client = TestClient(web_server.app)
    response = client.get(
        "/api/analytics/usage?days=1",
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["degraded"] is True
    assert payload["degraded_reason"] == "session_db_unavailable"
    assert payload["period_days"] == 1
    assert payload["totals"]["total_sessions"] == 0
    assert "C:/private/state.db" not in response.text



def test_usage_analytics_short_circuits_oversized_state_db(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    from cli import web_server

    state_path = tmp_path / "home" / "state.db"
    state_path.parent.mkdir(parents=True)
    with state_path.open("wb") as handle:
        handle.truncate(1_073_741_825)

    class _UnexpectedSessionDB:
        def __init__(self, *args, **kwargs):
            raise AssertionError("oversized state DB must short-circuit before SQLite")

    monkeypatch.setattr("runtime._compat.shim_state.SessionDB", _UnexpectedSessionDB)
    client = TestClient(web_server.app)
    response = client.get(
        "/api/analytics/usage?days=1",
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["degraded_reason"] == "state_db_too_large"


def test_usage_analytics_offloads_blocking_scan_off_the_event_loop(monkeypatch):
    """Regression guard for the 2026-09-19 dashboard freeze.

    The usage-analytics scan (InsightsEngine over a multi-hundred-MB state.db)
    used to run inline inside the async route, blocking the event loop; while
    it ran, the dashboard accepted TCP connections but served nothing, which
    broke every smoke run and every open tab's 60s poll. The route must
    delegate to _get_usage_analytics_sync via asyncio.to_thread so the scan
    executes in a worker thread (no running loop there).
    """
    from cli import web_server

    probe = {"on_loop": None}

    def _probe(days):
        try:
            asyncio.get_running_loop()
            probe["on_loop"] = True
        except RuntimeError:
            probe["on_loop"] = False
        return {"daily": [], "by_model": [], "totals": {}, "period_days": days}

    monkeypatch.setattr(web_server, "_get_usage_analytics_sync", _probe)
    client = TestClient(web_server.app)
    response = client.get(
        "/api/analytics/usage?days=1",
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    assert probe["on_loop"] is False, "analytics scan ran on the event loop"
