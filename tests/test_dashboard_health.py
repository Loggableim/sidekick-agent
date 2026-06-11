import pytest
from pathlib import Path
from starlette.requests import Request

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
    monkeypatch.setattr(web_server, "_is_old_frontend", lambda: False)

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


def test_proxy_response_keeps_safe_stdlib_headers(monkeypatch):
    from cli import web_server

    captured = {}

    def fake_proxy(method, path, headers, body):
        captured["path"] = path
        return (
            200,
            b"{}",
            {
                "Content-Type": "application/json; charset=utf-8",
                "Set-Cookie": "profile=default; Path=/; SameSite=Lax",
                "Content-Disposition": 'attachment; filename="session.json"',
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "Connection": "close",
            },
            "application/json; charset=utf-8",
        )

    monkeypatch.setattr(web_server, "_proxy_sync", fake_proxy)

    client = TestClient(web_server.app)
    response = client.get(
        "/api/not-native-route",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    assert captured["path"] == "/api/not-native-route"
    assert response.headers["set-cookie"] == "profile=default; Path=/; SameSite=Lax"
    assert response.headers["content-disposition"] == 'attachment; filename="session.json"'
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert "connection" not in {key.lower() for key in response.headers}


def test_proxy_forwards_original_host_for_legacy_csrf():
    from cli import web_server

    forwarded = web_server._forward_request_headers(
        {
            "host": "127.0.0.1:9119",
            "origin": "http://127.0.0.1:9119",
            "content-length": "2",
        }
    )

    assert "host" not in {key.lower() for key in forwarded}
    assert forwarded["origin"] == "http://127.0.0.1:9119"
    assert forwarded["X-Forwarded-Host"] == "127.0.0.1:9119"
    assert forwarded["X-Real-Host"] == "127.0.0.1:9119"
    assert "content-length" not in {key.lower() for key in forwarded}


def test_query_token_only_authenticates_event_streams():
    from cli import web_server

    good_stream_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/chat/stream",
            "headers": [],
            "query_string": f"stream_id=s1&token={web_server._SESSION_TOKEN}".encode(),
            "scheme": "http",
            "server": ("127.0.0.1", 9119),
            "client": ("127.0.0.1", 50000),
        }
    )
    normal_api_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/workspaces",
            "headers": [],
            "query_string": f"token={web_server._SESSION_TOKEN}".encode(),
            "scheme": "http",
            "server": ("127.0.0.1", 9119),
            "client": ("127.0.0.1", 50000),
        }
    )

    assert web_server._has_valid_session_token(good_stream_request)
    assert not web_server._has_valid_session_token(normal_api_request)


def test_proxy_stream_yields_sse_lines_without_buffering(monkeypatch):
    from cli import web_server

    class FakeResponse:
        def __init__(self):
            self.lines = iter([b"event: heartbeat\n", b"data: {}\n", b"\n", b""])

        def readline(self):
            return next(self.lines)

    monkeypatch.setattr(web_server, "_ensure_stdlib_backend", lambda: 9123)
    monkeypatch.setattr(web_server.urllib.request, "urlopen", lambda req, timeout: FakeResponse())

    chunks = list(
        web_server._proxy_stream(
            "GET",
            "/api/chat/stream?stream_id=s1",
            {"host": "127.0.0.1:9119"},
            None,
        )
    )

    assert chunks == [b"event: heartbeat\n", b"data: {}\n", b"\n"]
