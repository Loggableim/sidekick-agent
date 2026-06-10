import pytest
from pathlib import Path

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


def test_models_endpoint_returns_catalog_json_not_spa(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server

    monkeypatch.setattr(
        "web.api.config.get_available_models",
        lambda: {
            "active_provider": "opencode-zen",
            "default_model": "deepseek-v4-flash",
            "configured_model_badges": {},
            "groups": [
                {
                    "provider": "OpenCode Zen",
                    "provider_id": "opencode-zen",
                    "models": [{"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"}],
                }
            ],
        },
    )

    client = TestClient(web_server.app)
    response = client.get(
        "/api/models",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["active_provider"] == "opencode-zen"
    assert payload["groups"][0]["models"][0]["id"] == "deepseek-v4-flash"


def test_live_models_endpoint_returns_json_for_matching_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server

    monkeypatch.setattr(
        "web.api.config.get_available_models",
        lambda: {
            "active_provider": "opencode-zen",
            "default_model": "deepseek-v4-flash",
            "groups": [
                {
                    "provider": "OpenCode Zen",
                    "provider_id": "opencode-zen",
                    "models": [{"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"}],
                    "extra_models": [{"id": "glm-5", "label": "GLM 5"}],
                }
            ],
        },
    )

    client = TestClient(web_server.app)
    response = client.get(
        "/api/models/live?provider=opencode-zen",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["provider"] == "opencode-zen"
    assert payload["count"] == 2
    assert [model["id"] for model in payload["models"]] == ["deepseek-v4-flash", "glm-5"]


def test_sessions_endpoint_default_limit_surfaces_legacy_history(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server

    class _FakeSessionDB:
        def __init__(self, *args, **kwargs):
            self._conn = self

        def list_sessions(self, limit=0, offset=0):
            count = max(0, min(96 - offset, limit))
            return [
                {
                    "session_id": f"sess-{offset + i}",
                    "title": f"Session {offset + i}",
                    "started_at": 1000 + offset + i,
                    "last_active": 1000 + offset + i,
                    "ended_at": 1000 + offset + i,
                }
                for i in range(count)
            ]

        def execute(self, query):
            class _Cursor:
                def fetchone(self_inner):
                    return (96,)

            return _Cursor()

        def close(self):
            return None

    monkeypatch.setattr("runtime._compat.shim_state.SessionDB", _FakeSessionDB)

    client = TestClient(web_server.app)
    response = client.get(
        "/api/sessions",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["limit"] == 200
    assert len(payload["sessions"]) == 96
    assert payload["total"] == 96


def test_workspace_api_wrapper_sends_dashboard_session_token():
    api_auth_js = Path("web/static/api-auth.js").read_text(encoding="utf-8")
    workspace_js = Path("web/static/workspace.js").read_text(encoding="utf-8")

    assert "__HERMES_SESSION_TOKEN__" in api_auth_js
    assert "X-Hermes-Session-Token" in api_auth_js
    assert "__SIDEKICK_FETCH_AUTH_INSTALLED__" in api_auth_js
    assert "{ defaultJson: false }" in api_auth_js
    assert "__HERMES_SESSION_TOKEN__" in workspace_js
    assert "X-Hermes-Session-Token" in workspace_js
    assert "hasDashboardToken" in workspace_js
    assert "onLoginPage" in workspace_js


def test_api_auth_script_loads_before_app_fetches():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    sw_js = Path("web/static/sw.js").read_text(encoding="utf-8")

    assert index_html.index("static/api-auth.js") < index_html.index("static/ui.js")
    assert index_html.index("static/api-auth.js") < index_html.index("static/boot.js")
    assert "'./static/api-auth.js' + VQ" in sw_js
