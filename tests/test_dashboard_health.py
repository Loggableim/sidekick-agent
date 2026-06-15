import json
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
    monkeypatch.setattr(web_server, "_is_old_frontend", lambda: False)

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
    monkeypatch.setattr(web_server, "_is_old_frontend", lambda: False)

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


def test_sessions_endpoint_uses_space_index_when_workspace_is_active(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server
    monkeypatch.setattr(web_server, "_is_old_frontend", lambda: False)

    space_sessions = tmp_path / "home" / "spaces" / "color" / "sessions"
    space_sessions.mkdir(parents=True)
    index = [
        {
            "session_id": "color-live",
            "title": "Color chat",
            "workspace": r"C:\projekte\color",
            "workspace_slug": "color",
            "message_count": 2,
            "created_at": 20.0,
            "updated_at": 21.0,
            "last_message_at": 21.0,
        },
        {
            "session_id": "foreign-only-index",
            "title": "Foreign chat",
            "workspace": r"C:\sidekick\home\spaces\nova",
            "workspace_slug": "nova",
            "message_count": 2,
            "created_at": 30.0,
            "updated_at": 31.0,
            "last_message_at": 31.0,
        },
    ]
    (space_sessions / "_index.json").write_text(json.dumps(index), encoding="utf-8")
    (space_sessions / "color-live.json").write_text(
        json.dumps({"session_id": "color-live", "messages": []}),
        encoding="utf-8",
    )

    class _FakeSpace:
        slug = "color"
        sessions_dir = space_sessions

    monkeypatch.setattr("web.api.space_engine.get_workspace", lambda slug: _FakeSpace() if slug == "color" else None)

    client = TestClient(web_server.app)
    response = client.get(
        "/api/sessions?workspace=color",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["session_id"] for item in payload["sessions"]] == ["color-live"]
    assert payload["sessions"][0]["workspace_slug"] == "color"


def test_sessions_search_is_space_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server
    monkeypatch.setattr(web_server, "_is_old_frontend", lambda: False)

    monkeypatch.setattr(
        web_server,
        "_load_space_sessions",
        lambda slug: [
            {"session_id": "color-live", "title": "Color palette", "workspace_slug": slug, "model": "m"},
            {"session_id": "color-other", "title": "Unrelated", "workspace_slug": slug, "model": "m"},
        ],
    )

    client = TestClient(web_server.app)
    response = client.get(
        "/api/sessions/search?workspace=color&q=palette",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["results"] == payload["sessions"]
    assert payload["results"][0]["session_id"] == "color-live"
    assert payload["results"][0]["snippet"] == "Color palette"
    assert payload["results"][0]["title"] == "Color palette"
    assert payload["results"][0]["match_type"] == "title"


def test_session_space_routes_do_not_proxy_for_old_static_ui(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server

    monkeypatch.setattr(web_server, "_is_old_frontend", lambda: True)
    monkeypatch.setattr(
        web_server,
        "_load_space_sessions",
        lambda slug: [
            {
                "session_id": "color-live",
                "title": "Color palette",
                "workspace_slug": slug,
                "updated_at": 42.0,
            }
        ],
    )

    async def _fail_proxy(request):
        raise AssertionError("space-scoped session routes must stay on FastAPI")

    monkeypatch.setattr(web_server, "_proxy_request_to_stdlib", _fail_proxy)

    client = TestClient(web_server.app)
    headers = {"X-Hermes-Session-Token": web_server._SESSION_TOKEN}

    sessions = client.get("/api/sessions?workspace=color", headers=headers)
    assert sessions.status_code == 200
    assert sessions.json()["sessions"][0]["session_id"] == "color-live"

    search = client.get("/api/sessions/search?workspace=color&q=palette", headers=headers)
    assert search.status_code == 200
    assert search.json()["results"][0]["session_id"] == "color-live"


def test_space_session_detail_repairs_stale_stream_state(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server

    sessions_dir = tmp_path / "home" / "spaces" / "color" / "sessions"
    sessions_dir.mkdir(parents=True)
    session_path = sessions_dir / "color-live.json"
    session_path.write_text(
        json.dumps(
            {
                "session_id": "color-live",
                "title": "Color deploy",
                "workspace": r"C:\projekte\color",
                "workspace_slug": "color",
                "active_stream_id": "dead-stream",
                "pending_user_message": "finish this",
                "pending_attachments": [],
                "pending_started_at": 1234,
                "messages": [{"role": "assistant", "content": "ready", "timestamp": 1}],
                "context_messages": [{"role": "system", "content": "large hidden context"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (sessions_dir / "_index.json").write_text(
        json.dumps(
            [
                {
                    "session_id": "color-live",
                    "title": "Color deploy",
                    "workspace_slug": "color",
                    "active_stream_id": "dead-stream",
                    "pending_user_message": "finish this",
                    "has_pending_user_message": True,
                    "message_count": 1,
                    "is_streaming": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    class _FakeWorkspace:
        def __init__(self, sessions_dir):
            self.sessions_dir = sessions_dir

    monkeypatch.setattr("web.api.space_engine.get_workspace", lambda slug: _FakeWorkspace(sessions_dir))
    monkeypatch.setattr(web_server, "_stream_is_active_for_space", lambda stream_id, slug: False)

    response = TestClient(web_server.app).get(
        "/api/session?session_id=color-live&workspace=color&messages=0&resolve_model=0",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    payload = response.json()["session"]
    assert payload["session_id"] == "color-live"
    assert payload["messages"] == []
    assert "context_messages" not in payload
    assert payload["active_stream_id"] is None
    assert payload["pending_user_message"] is None
    assert payload["has_pending_user_message"] is False
    assert payload["message_count"] == 2

    stored = json.loads(session_path.read_text(encoding="utf-8"))
    assert stored["messages"][-1]["role"] == "user"
    assert stored["messages"][-1]["content"] == "finish this"
    assert stored["messages"][-1]["_recovered"] is True
    assert stored["active_stream_id"] is None
    assert stored["pending_user_message"] is None

    index = json.loads((sessions_dir / "_index.json").read_text(encoding="utf-8"))
    assert index[0]["session_id"] == "color-live"
    assert index[0]["active_stream_id"] is None
    assert index[0]["pending_user_message"] is None
    assert index[0]["has_pending_user_message"] is False
    assert index[0]["message_count"] == 2
    assert index[0]["is_streaming"] is False


def test_space_sessions_listing_clears_old_stale_stream_markers(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server

    sessions_dir = tmp_path / "home" / "spaces" / "color" / "sessions"
    sessions_dir.mkdir(parents=True)
    session_path = sessions_dir / "stale.json"
    old_ts = 1000.0
    session_path.write_text(
        json.dumps(
            {
                "session_id": "stale",
                "title": "Stale stream",
                "workspace_slug": "color",
                "active_stream_id": "old-stream",
                "pending_user_message": "do not lose me",
                "pending_started_at": old_ts,
                "messages": [{"role": "assistant", "content": "ready", "timestamp": 1}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (sessions_dir / "_index.json").write_text(
        json.dumps(
            [
                {
                    "session_id": "stale",
                    "title": "Stale stream",
                    "workspace_slug": "color",
                    "active_stream_id": "old-stream",
                    "pending_user_message": "do not lose me",
                    "pending_started_at": old_ts,
                    "is_streaming": False,
                    "message_count": 1,
                    "updated_at": old_ts,
                }
            ]
        ),
        encoding="utf-8",
    )

    class _FakeSpace:
        def __init__(self, sessions_dir):
            self.slug = "color"
            self.sessions_dir = sessions_dir

    monkeypatch.setattr("web.api.space_engine.get_workspace", lambda slug: _FakeSpace(sessions_dir) if slug == "color" else None)
    monkeypatch.setattr(web_server.time, "time", lambda: old_ts + 1000)
    monkeypatch.setattr(web_server, "_stream_is_active_for_space", lambda stream_id, slug: False)

    response = TestClient(web_server.app).get(
        "/api/sessions?workspace=color",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    row = response.json()["sessions"][0]
    assert row["active_stream_id"] is None
    assert row["pending_user_message"] is None
    assert row["is_streaming"] is False
    assert row["message_count"] == 2

    stored = json.loads(session_path.read_text(encoding="utf-8"))
    assert stored["messages"][-1]["role"] == "user"
    assert stored["messages"][-1]["content"] == "do not lose me"
    assert stored["messages"][-1]["_recovered"] is True
    assert stored["active_stream_id"] is None
    assert stored["pending_user_message"] is None


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


def test_mobile_settings_has_main_section_switcher():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")

    assert 'id="settingsSectionDropdown"' in index_html
    assert 'onchange="switchSettingsSection(this.value)"' in index_html
    assert ".settings-section-switcher{display:none" in style_css
    assert ".settings-section-switcher{display:block" in style_css
    assert "const dd=$('settingsSectionDropdown')" in panels_js


def test_mobile_sidebar_is_forced_out_of_flex_flow():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert "@media(max-width:640px)" in style_css
    assert ".sidebar{" in style_css
    assert "position:fixed!important" in style_css
    assert "main.main{width:100%!important" in style_css


def test_dashboard_self_link_is_hidden_for_current_origin():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")

    assert "new URL(url,window.location.href).origin===window.location.origin" in ui_js
    assert "const running=probedRunning&&!sameOrigin" in ui_js


def test_cast_status_uses_user_safe_error_summary():
    routes_py = Path("web/api/routes.py").read_text(encoding="utf-8")

    assert '"error": "Hub nicht erreichbar"' in routes_py
    assert '"detail": _sanitize_error(exc)' in routes_py


def test_background_stream_requests_keep_owner_workspace():
    messages_js = Path("web/static/messages.js").read_text(encoding="utf-8")
    sessions_js = Path("web/static/sessions.js").read_text(encoding="utf-8")

    assert "workspace_slug:ownerWorkspaceSlug" in messages_js
    assert "function _ownerScopedApiPath(path)" in messages_js
    assert "_ownerScopedApiPath(`api/chat/stream?stream_id=" in messages_js
    assert "_ownerScopedApiPath(`/api/chat/stream/status?stream_id=" in messages_js
    assert "_ownerScopedApiPath(`/api/session?session_id=" in messages_js
    assert "workspace_slug:stored.workspace_slug||stored.space_slug||stored.space||''" in sessions_js
    assert "function _sessionBelongsToActiveWorkspace(s)" in sessions_js
    assert "if(!_sessionBelongsToActiveWorkspace(s)) return false" in sessions_js
    assert "ageMs < 10*60*1000" in sessions_js


def test_space_deeplink_initializes_active_workspace():
    spaces_js = Path("web/static/spaces.js").read_text(encoding="utf-8")
    sw_js = Path("web/static/sw.js").read_text(encoding="utf-8")

    assert "function _spaceSlugFromLocation()" in spaces_js
    assert "new URLSearchParams(window.location.search || '').get('workspace')" in spaces_js
    assert "let _activeSpace = _urlActiveSpace || localStorage.getItem('sidekick-active-workspace')" in spaces_js
    assert "localStorage.setItem('sidekick-active-workspace', _urlActiveSpace)" in spaces_js
    assert "'./static/spaces.js' + VQ" in sw_js


def test_launcher_stops_orphan_stdlib_backends():
    launcher = Path("Sidekick-Launcher.ps1").read_text(encoding="utf-8")

    assert "function Stop-OrphanStdlibBackends" in launcher
    assert "\\-m\\s+web\\.server" in launcher
    assert 'Stop-OrphanStdlibBackends "launcher stop"' in launcher
    assert 'Stop-OrphanStdlibBackends "pre-start cleanup"' in launcher


def test_goal_continuation_auto_starts_after_delivery():
    messages_js = Path("web/static/messages.js").read_text(encoding="utf-8")
    goals_py = Path("cli/goals.py").read_text(encoding="utf-8")

    assert "function _startGoalContinuation(goalNext, attempt=0)" in messages_js
    assert "api(_ownerScopedApiPath('/api/chat/start')" in messages_js
    assert "setTimeout(()=>_startGoalContinuation(_goalNext),250)" in messages_js
    assert "already has an active stream" in messages_js
    assert "merely reports progress" in goals_py
    assert "If any required work remains" in goals_py


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
