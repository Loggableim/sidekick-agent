import pytest

TestClient = pytest.importorskip("fastapi.testclient").TestClient


def test_dashboard_health_endpoint_returns_readiness(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli.web_server import app

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["service"] == "sidekick-dashboard"
    assert "version" in payload
    assert "web_dist_ready" in payload


def test_workspaces_endpoint_merges_space_engine_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server

    class _FakeSpace:
        slug = "ltth"
        name = "LTTH"

        def get_project_dir(self):
            return r"E:\LTTH DEV BRANCH"

    monkeypatch.setattr(
        web_server,
        "load_workspaces",
        lambda: [{"path": r"C:\Users\logga\workspace", "name": "Home"}],
    )
    monkeypatch.setattr(web_server, "get_last_workspace", lambda: r"C:\Users\logga\workspace")
    monkeypatch.setattr(
        "web.api.space_engine.get_all_workspaces",
        lambda: [_FakeSpace()],
    )

    client = TestClient(web_server.app)
    response = client.get(
        "/api/workspaces",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["last"] == r"C:\Users\logga\workspace"
    assert len(payload["workspaces"]) == 2
    assert any(item.get("slug") == "ltth" and item.get("is_space") for item in payload["workspaces"])


def test_workspaces_endpoint_requires_session_token(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server

    monkeypatch.setattr(web_server, "load_workspaces", lambda: [])
    monkeypatch.setattr(web_server, "get_last_workspace", lambda: "")

    client = TestClient(web_server.app)

    unauthorized = client.get("/api/workspaces")
    assert unauthorized.status_code == 401

    authorized = client.get(
        "/api/workspaces",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )
    assert authorized.status_code == 200
