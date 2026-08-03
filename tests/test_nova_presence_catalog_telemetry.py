from __future__ import annotations

from fastapi.testclient import TestClient


def test_presence_card_projects_redacted_catalog_wait_telemetry(monkeypatch, tmp_path):
    """A presence read exposes a bounded recovery state but no run identity."""
    from cli import web_server

    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.setitem(
        web_server._NOVA_SUPERVISION_TICKER_STATE,
        "last_catalog_refresh_attempts",
        3,
    )
    monkeypatch.setitem(
        web_server._NOVA_SUPERVISION_TICKER_STATE,
        "last_outcomes",
        [
            {
                "space": "aquarium-zentrum",
                "status": "waiting_for_catalog",
                "run_id": "1f5f55a4-42d3-43ce-aa7a-e0fdb2c937b6",
                "error": "C:/private/provider secret",
            }
        ],
    )

    response = TestClient(web_server.app).get(
        "/api/nova/presence-card",
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    supervision = response.json()["supervision"]
    assert supervision["last_catalog_refresh_attempts"] == 3
    assert supervision["last_outcomes"] == []
    rendered = response.text
    assert "1f5f55a4" not in rendered
    assert "private/provider" not in rendered


def test_presence_telemetry_filters_unmanaged_spaces_and_raw_errors():
    from cli import web_server

    raw = [
        {"space": "aquarium-zentrum", "status": "waiting_for_catalog", "error": "provider token C:/private"},
        {"space": "finanzjunkie", "status": "completed"},
        {"space": "nova", "status": "started"},
    ]
    assert web_server._public_nova_supervision_outcomes(
        raw, managed_spaces={"aquarium-zentrum"}
    ) == [{"space": "aquarium-zentrum", "status": "waiting_for_catalog"}]
    assert web_server._public_nova_supervision_error("provider token C:/private") is None
    assert web_server._public_nova_supervision_error("ticker_watchdog_stale") == "ticker_watchdog_stale"



def test_presence_endpoint_drops_all_outcomes_when_no_spaces_are_managed(monkeypatch):
    from cli import web_server

    from web.api import nova_presence
    monkeypatch.setattr(
        nova_presence,
        "build_presence_card",
        lambda: {"state": "available", "managed_spaces": []},
    )
    monkeypatch.setitem(
        web_server._NOVA_SUPERVISION_TICKER_STATE,
        "last_outcomes",
        [
            {"space": "nova", "status": "started"},
            {"space": "finanzjunkie", "status": "completed"},
            {"space": "aquarium-zentrum", "status": "waiting_for_catalog"},
        ],
    )
    payload = __import__("asyncio").run(web_server.nova_presence_card_endpoint())
    assert payload["supervision"]["last_outcomes"] == []


def test_presence_endpoint_fails_closed_on_corrupt_ticker_telemetry(monkeypatch):
    """Malformed process-local ticker values must not break a read-only GET."""
    from cli import web_server
    from web.api import nova_presence

    monkeypatch.setattr(
        nova_presence,
        "build_presence_card",
        lambda: {"state": "available", "managed_spaces": [], "supervision": {}},
    )
    corrupt = {
        "interval_seconds": "not-a-number",
        "consumer_interval_seconds": None,
        "last_catalog_refresh_attempts": "-999",
        "error_count": object(),
    }
    for key, value in corrupt.items():
        monkeypatch.setitem(web_server._NOVA_SUPERVISION_TICKER_STATE, key, value)

    payload = __import__("asyncio").run(web_server.nova_presence_card_endpoint())
    supervision = payload["supervision"]
    assert supervision["interval_seconds"] == 60
    assert supervision["consumer_interval_seconds"] >= 1
    assert supervision["last_catalog_refresh_attempts"] == 0
    assert supervision["error_count"] == 0
