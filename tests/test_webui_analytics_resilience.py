from __future__ import annotations

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
    assert response.json()["degraded_reason"] == "session_db_unavailable"
