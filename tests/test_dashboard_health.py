import asyncio
import json
import re
import warnings
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


def test_agent_health_exposes_sanitized_gateway_startup_reason():
    from web.api.agent_health import _runtime_detail_subset

    details = _runtime_detail_subset(
        {
            "gateway_state": "startup_failed",
            "updated_at": "2026-06-19T18:46:00+00:00",
            "exit_reason": "telegram: The token `123456789:SECRET_TOKEN_VALUE` was rejected by the server.",
            "platforms": {
                "telegram": {
                    "state": "fatal",
                    "error_code": "InvalidToken",
                    "error_message": "The token `123456789:SECRET_TOKEN_VALUE` was rejected.",
                }
            },
        }
    )

    assert details["gateway_state"] == "startup_failed"
    assert details["exit_reason"] == "telegram: The token `<redacted>` was rejected by the server."
    assert "SECRET_TOKEN_VALUE" not in json.dumps(details)
    assert details["platform_states"] == {"fatal": 1}


def test_agent_health_banner_renders_gateway_exit_reason():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")

    assert "function _agentHealthDetailSentence(label,value)" in ui_js
    assert "payload&&payload.details&&payload.details.exit_reason" in ui_js
    assert "_agentHealthDetailSentence('Reason',reason)" in ui_js
    assert "Gateway heartbeat failed.${state}${reasonText} Messages" in ui_js


def test_openapi_schema_excludes_legacy_proxy_without_duplicate_operation_warning(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server

    web_server.app.openapi_schema = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        response = TestClient(web_server.app).get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert "/api/{path}" not in schema["paths"]
    assert not any("Duplicate Operation ID" in str(item.message) for item in caught)


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
        {
            "session_id": "stale-slug",
            "title": "Stale slug copied into color",
            "workspace": r"C:\projekte\color",
            "workspace_slug": "default",
            "message_count": 1,
            "created_at": 40.0,
            "updated_at": 41.0,
            "last_message_at": 41.0,
        },
    ]
    (space_sessions / "_index.json").write_text(json.dumps(index), encoding="utf-8")
    (space_sessions / "color-live.json").write_text(
        json.dumps({"session_id": "color-live", "messages": []}),
        encoding="utf-8",
    )
    (space_sessions / "stale-slug.json").write_text(
        json.dumps(
            {
                "session_id": "stale-slug",
                "title": "Stale slug copied into color",
                "workspace": r"C:\projekte\color",
                "workspace_slug": "default",
                "messages": [{"role": "user", "content": "hi"}],
            }
        ),
        encoding="utf-8",
    )

    class _FakeSpace:
        slug = "color"
        sessions_dir = space_sessions

    monkeypatch.setattr("web.api.space_engine.get_workspace", lambda slug: _FakeSpace() if slug == "color" else None)
    monkeypatch.setattr(
        "web.api.routes.get_cli_sessions",
        lambda: (_ for _ in ()).throw(AssertionError("non-default spaces must not scan global state.db")),
    )

    client = TestClient(web_server.app)
    response = client.get(
        "/api/sessions?workspace=color",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert [item["session_id"] for item in payload["sessions"]] == ["stale-slug", "color-live"]
    assert [item["workspace_slug"] for item in payload["sessions"]] == ["color", "color"]


def test_default_workspace_query_uses_default_space_index(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server
    monkeypatch.setattr(web_server, "_is_old_frontend", lambda: False)

    space_sessions = tmp_path / "home" / "spaces" / "default" / "sessions"
    space_sessions.mkdir(parents=True)
    (space_sessions / "_index.json").write_text(
        json.dumps(
            [
                {
                    "session_id": "default-live",
                    "title": "Default chat",
                    "workspace": r"C:\sidekick\home\spaces\default",
                    "workspace_slug": "default",
                    "message_count": 1,
                    "created_at": 10.0,
                    "updated_at": 11.0,
                    "last_message_at": 11.0,
                }
            ]
        ),
        encoding="utf-8",
    )
    (space_sessions / "default-live.json").write_text(
        json.dumps(
            {
                "session_id": "default-live",
                "title": "Default chat",
                "workspace": r"C:\sidekick\home\spaces\default",
                "workspace_slug": "default",
                "messages": [{"role": "user", "content": "hi"}],
            }
        ),
        encoding="utf-8",
    )

    class _FakeSpace:
        slug = "default"
        sessions_dir = space_sessions

    monkeypatch.setattr("web.api.space_engine.get_workspace", lambda slug: _FakeSpace() if slug == "default" else None)

    client = TestClient(web_server.app)
    headers = {"X-Hermes-Session-Token": web_server._SESSION_TOKEN}

    response = client.get("/api/sessions?workspace=default", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["sessions"][0]["session_id"] == "default-live"
    assert payload["sessions"][0]["workspace_slug"] == "default"

    detail = client.get(
        "/api/session?session_id=default-live&workspace=default&messages=0&resolve_model=0",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["session"]["session_id"] == "default-live"
    assert detail.json()["session"]["workspace_slug"] == "default"


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


def test_expected_api_failures_do_not_pollute_webui_error_log():
    workspace_js = Path("web/static/workspace.js").read_text(encoding="utf-8")
    sessions_js = Path("web/static/sessions.js").read_text(encoding="utf-8")

    assert "const logApiError=fetchOpts.logError!==false" in workspace_js
    assert "delete fetchOpts.logError" in workspace_js
    assert "isExpectedGameModeBlock=res.status===409&&data&&data.error&&data.error.code==='game_mode_enabled'" in workspace_js
    assert "if(logApiError&&!isExpectedGameModeBlock&&!path.startsWith('api/errors/')" in workspace_js
    assert sessions_js.count("logError: false") >= 3


def test_game_mode_chat_start_rejection_keeps_chat_history_clean():
    messages_js = Path("web/static/messages.js").read_text(encoding="utf-8")

    assert "function _gameModeWouldBlockClientModel(model, provider)" in messages_js
    assert "localProviders.has(p)" in messages_js
    assert "p.startsWith('custom:')&&localProviders.has(p.slice(7))" in messages_js
    assert "m.startsWith('@ollama:')" in messages_js
    assert "selectedProvider=S.session&&S.session.model_provider||null" in messages_js
    assert "if(_gameModeWouldBlockClientModel(selectedModel,selectedProvider))" in messages_js
    assert "ollama-cloud" not in messages_js[messages_js.index("function _gameModeWouldBlockClientModel"):messages_js.index("async function send")]

    catch_start = messages_js.index("const gameModeBlocked=!!(e&&e.data&&e.data.error&&e.data.error.code==='game_mode_enabled')")
    generic_error_start = messages_js.index("S.messages.push({role:'assistant',content:`**Error:** ${errMsg}`})")
    game_mode_block = messages_js[catch_start:generic_error_start]

    assert "if(gameModeBlocked)" in game_mode_block
    assert "if(S.messages[S.messages.length-1]===userMsg) S.messages.pop();" in game_mode_block
    assert "msgBox.value=text;" in game_mode_block
    assert "showToast(errMsg,5000,'warning')" in game_mode_block
    assert "return;" in game_mode_block


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
    assert "overflow:hidden!important" in style_css
    assert "pointer-events:none!important" in style_css
    assert "transform:translate3d(calc(-100% - 20px),0,0)!important;" in style_css
    assert "transition:transform .25s ease!important;" in style_css
    assert ".sidebar.mobile-open{" in style_css
    assert "transform:translate3d(0,0,0)!important;" in style_css
    assert "pointer-events:auto!important" in style_css
    assert ".rightpanel{" in style_css
    assert "right:calc(-1 * var(--mobile-rightpanel-width))!important" in style_css
    assert re.search(
        r"\.rightpanel\{\s*"
        r"--mobile-rightpanel-width:min\(300px,100vw\);\s*"
        r"display:flex!important;\s*"
        r"position:fixed!important;\s*"
        r"right:calc\(-1 \* var\(--mobile-rightpanel-width\)\)!important;\s*"
        r"top:calc\(38px \+ var\(--app-titlebar-safe-top,0px\)\)!important;",
        style_css,
        re.S,
    )
    assert "border-left-color:transparent!important" in style_css
    assert ".rightpanel.mobile-open{" in style_css
    assert "main.main{width:100%!important" in style_css


def test_mobile_nav_click_closes_sidebar_and_keeps_hamburger_clickable():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert "opts.fromRailClick && typeof closeMobileSidebar === 'function'" in panels_js
    assert "typeof _isDesktopWidth === 'function' && !_isDesktopWidth()" in panels_js
    assert "closeMobileSidebar();" in panels_js
    assert ".app-titlebar{position:relative;z-index:220!important;}" in style_css
    assert ".app-titlebar-hamburger{position:relative;z-index:221!important;}" in style_css
    assert "top:calc(38px + var(--app-titlebar-safe-top,0px))!important;" in style_css


def test_mobile_titlebar_center_stays_in_flex_flow():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert "@media(max-width:640px)" in style_css
    assert ".app-titlebar-center{position:static;" in style_css
    assert "transform:none;" in style_css
    assert ".compact-toggle-btn{display:none!important;}" in style_css
    assert ".titlebar-space-spacer{display:none;}" in style_css
    assert ".titlebar-space{flex:0 1 auto;min-width:0;max-width:112px;margin-right:0;}" in style_css
    assert ".titlebar-space-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;}" in style_css


def test_mobile_titlebar_keeps_language_and_game_mode_visible():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")
    mobile_guard = style_css[style_css.rfind("@media(max-width:640px)") :]

    assert ".titlebar-actions{display:inline-flex!important;flex:0 0 auto;align-items:center;gap:2px;margin-right:0;}" in mobile_guard
    assert ".titlebar-actions #btnCastToggle," in mobile_guard
    assert ".titlebar-actions #btnRebootSidekick," in mobile_guard
    assert ".titlebar-actions #btnShutdownSidekick{display:none!important;}" in mobile_guard


def test_agents_dashboard_chat_docks_existing_chat_view_in_main_area():
    agents_js = Path("web/static/agents.js").read_text(encoding="utf-8")
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    dashboard_css = Path("web/static/agents-dashboard.css").read_text(encoding="utf-8")

    assert "function dockAgentChatInMain()" in agents_js
    assert "main.classList.add('agents-main-chat-open');" in agents_js
    assert "main.appendChild(view);" in agents_js
    assert "const chatDockedInMain = dockAgentChatInMain();" in agents_js
    assert (
        agents_js.index("const chatDockedInMain = dockAgentChatInMain();")
        < agents_js.index("document.getElementById('agentsChatView').classList.remove('hidden');")
    )
    assert "function restoreAgentChatHome()" in agents_js
    assert "restoreAgentChatHome();" in agents_js
    assert "window.restoreAgentChatHome = restoreAgentChatHome;" in agents_js
    assert "if (typeof restoreAgentChatHome === 'function') restoreAgentChatHome();" in panels_js
    assert "#mainAgents.agents-main-chat-open > #agentsChatView" in dashboard_css
    assert "#mainAgents.agents-main-chat-open > #agentsDashboardGrid" in dashboard_css
    assert "#mainAgents.agents-main-chat-open .agents-workspace-layout" in dashboard_css


def test_gmail_setup_dialog_uses_current_main_and_sidebar_contract():
    gmail_js = Path("web/static/gmail.js").read_text(encoding="utf-8")

    setup_block = re.search(
        r"function showGmailSetupDialog\(\) \{(?P<body>.*?)\n\}\n\nasync function saveGmailSetup",
        gmail_js,
        re.S,
    )
    assert setup_block, "showGmailSetupDialog should be present"
    setup_body = setup_block.group("body")

    save_skip_block = gmail_js[
        gmail_js.index("async function saveGmailSetup()") : gmail_js.index("function showGmailSplash()")
    ]

    assert "function _gmailMainView()" in gmail_js
    assert "return document.getElementById('mainGmail');" in gmail_js
    assert "document.querySelector('#panelGmail .gmail-sidebar')" in gmail_js
    assert "function _gmailSetupHost()" in gmail_js
    assert "_gmailSetupHost().appendChild(container);" in setup_body
    assert "position:absolute;inset:0;z-index:120" in setup_body
    assert "_setGmailSetupVisible(true);" in setup_body
    assert "_setGmailSetupVisible(false);" in save_skip_block
    assert "main.innerHTML" not in save_skip_block
    assert "document.getElementById('gmailPanel')" not in setup_body
    assert "document.getElementById('gmailMain')" not in save_skip_block
    assert "document.getElementById('gmailSidebar')" not in save_skip_block


def test_gmail_ai_model_selector_matches_existing_js_contract():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    gmail_js = Path("web/static/gmail.js").read_text(encoding="utf-8")
    gmail_css = Path("web/static/gmail-panel.css").read_text(encoding="utf-8")

    assert 'id="gmailAIModelSelect"' in index_html
    assert 'class="gmail-ai-model-select"' in index_html
    assert 'onchange="gmailAISetModel(this.value)"' in index_html
    assert 'value="llama3.2:latest"' in index_html
    assert "const modelSel = document.getElementById('gmailAIModelSelect');" in gmail_js
    assert "function gmailAISetModel(model)" in gmail_js
    assert ".gmail-ai-model-bar" in gmail_css
    assert ".gmail-ai-model-select" in gmail_css


def test_gmail_compose_overlay_closes_on_escape_without_blocking_panel():
    gmail_js = Path("web/static/gmail.js").read_text(encoding="utf-8")

    open_start = gmail_js.index("function gmailOpenCompose()")
    close_start = gmail_js.index("function gmailCloseCompose()")
    split_start = gmail_js.index("let _gmailSplitDragging", close_start)
    open_body = gmail_js[open_start:close_start]
    close_body = gmail_js[close_start:split_start]

    assert "function _gmailComposeKeydown(e)" in gmail_js
    assert "if (e.key !== 'Escape')" in gmail_js
    assert "gmailCloseCompose();" in gmail_js
    assert "document.addEventListener('keydown', _gmailComposeKeydown);" in open_body
    assert "document.removeEventListener('keydown', _gmailComposeKeydown);" in close_body


def test_gmail_empty_search_restores_current_message_list():
    gmail_js = Path("web/static/gmail.js").read_text(encoding="utf-8")

    search_start = gmail_js.index("async function gmailDoSearch()")
    compose_start = gmail_js.index("function _gmailComposeKeydown", search_start)
    search_body = gmail_js[search_start:compose_start]

    empty_query = search_body.index("if (!query)")
    first_render = search_body.index("const mainList = document.getElementById('gmailMainList')")

    assert empty_query < first_render
    assert "gmailRefresh();" in search_body[empty_query:first_render]
    assert "let _gmailSearchSeq = 0;" in gmail_js
    assert "const searchSeq = ++_gmailSearchSeq;" in search_body
    assert "if (searchSeq !== _gmailSearchSeq) return;" in search_body


def test_websearch_history_chips_use_current_suggestion_container():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")

    assert 'id="websearchSuggestionChips"' in index_html
    assert 'id="websearchChips"' not in index_html
    assert "function _websearchChipContainer()" in browser_js
    assert "return document.getElementById('websearchSuggestionChips');" in browser_js

    render_start = browser_js.index("function _websearchRenderChips()")
    render_end = browser_js.index("function _websearchRenderResultsSummary", render_start)
    search_start = browser_js.index("async function websearchQuickSearch")
    search_end = browser_js.index("const meta = document.getElementById('websearchQuickMeta');", search_start)
    render_body = browser_js[render_start:render_end]
    search_intro = browser_js[search_start:search_end]

    assert "var chips = _websearchChipContainer();" in render_body
    assert "const chips = _websearchChipContainer();" in search_intro
    assert "document.getElementById('websearchChips')" not in browser_js


def test_mobile_sidebar_nav_mirrors_desktop_panel_rail():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")

    sidebar_nav = re.search(
        r'<div class="sidebar-nav">(.*?)<!-- Sidebar Space Selector -->',
        index_html,
        re.S,
    )
    assert sidebar_nav, "mobile sidebar nav block should be present"
    sidebar_nav_html = sidebar_nav.group(1)
    rail_panels = set(
        re.findall(r'<button class="rail-btn nav-tab[^>]+data-panel="([^"]+)"', index_html)
    )
    sidebar_panels = set(re.findall(r'data-panel="([^"]+)"', sidebar_nav_html))

    assert rail_panels <= sidebar_panels
    for panel in ("gmail", "discord"):
        assert f"data-panel=\"{panel}\"" in sidebar_nav_html
        assert f"switchPanel('{panel}',{{fromRailClick:true}})" in sidebar_nav_html


def test_mobile_sidebar_nav_uses_scrollable_touch_targets():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    mobile_guard = style_css[style_css.rfind("@media(max-width:640px)") :]
    assert ".sidebar-nav{overflow-x:auto!important;overflow-y:hidden!important;" in mobile_guard
    assert "scrollbar-width:none;-webkit-overflow-scrolling:touch;" in mobile_guard
    assert ".sidebar-nav::-webkit-scrollbar{display:none;}" in mobile_guard
    assert (
        ".sidebar-nav .nav-tab:not(.nav-tab-space){flex:0 0 44px!important;"
        "width:44px!important;min-width:44px!important;min-height:44px!important;"
        "padding:0!important;}"
    ) in mobile_guard
    assert (
        ".sidebar-nav .nav-tab-space{flex:0 0 64px!important;"
        "min-width:64px!important;min-height:44px!important;}"
    ) in mobile_guard


def test_desktop_rail_owns_vertical_overflow():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert ".rail{display:none;width:48px;" in style_css
    assert "min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior-y:contain;scrollbar-width:none;" in style_css
    assert ".rail::-webkit-scrollbar{display:none;}" in style_css


def test_mobile_open_sidebar_layers_above_rightpanel():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    mobile_guard = style_css[style_css.rfind("@media(max-width:640px)") :]
    sidebar_open = re.search(r"\.sidebar\.mobile-open\{(?P<body>[^}]+)\}", mobile_guard)
    rightpanel = re.search(r"\.rightpanel\{(?P<body>[^}]+)\}", mobile_guard)

    assert sidebar_open, "mobile sidebar open rule should be present"
    assert rightpanel, "mobile rightpanel rule should be present"
    assert "z-index:230!important;" in sidebar_open.group("body")
    assert "z-index:200!important;" in rightpanel.group("body")


def test_space_selector_buttons_bind_before_async_space_load():
    spaces_js = Path("web/static/spaces.js").read_text(encoding="utf-8")

    init_match = re.search(
        r"async function _initSpaceSelector\(\) \{(?P<body>.*?)\n\}",
        spaces_js,
        re.S,
    )
    assert init_match, "_initSpaceSelector should be present"
    init_body = init_match.group("body")

    assert init_body.index("_bindTitlebarSpaceButton();") < init_body.index("await loadSpaces();")
    assert init_body.index("_bindSidebarSpaceButton();") < init_body.index("await loadSpaces();")
    assert "setTimeout(_initSpaceSelector, 500)" not in spaces_js
    assert "spaceSelectorContainer" not in init_body
    assert "document.getElementById('sidebarSpaceName')" in spaces_js
    assert "document.getElementById('sidebarSpaceBtn')" in spaces_js


def test_space_dropdown_items_are_real_buttons():
    spaces_js = Path("web/static/spaces.js").read_text(encoding="utf-8")

    render_match = re.search(
        r"function _renderSpaceDropdownItems\(dd, spaces\) \{(?P<body>.*?)\n\}",
        spaces_js,
        re.S,
    )
    assert render_match, "_renderSpaceDropdownItems should be present"
    render_body = render_match.group("body")

    assert "document.createElement('button')" in render_body
    assert "item.type = 'button'" in render_body
    assert "newItem.type = 'button'" in render_body
    assert "item.dataset.spaceSlug = ws.slug" in render_body
    assert "newItem.dataset.action = 'new-space'" in render_body


def test_mobile_composer_dropdowns_clamp_to_viewport():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")

    helper_start = ui_js.index("function _positionComposerDropdownWithinViewport")
    model_start = ui_js.index("function _positionModelDropdown")
    helper_body = ui_js[helper_start:model_start]

    assert "window.innerHeight" in helper_body
    assert "dd.style.bottom='auto';" in helper_body
    assert "dd.style.top=`${top}px`;" in helper_body
    assert "top=Math.max(viewportMargin,Math.min(top,maxTop));" in helper_body

    reasoning_start = ui_js.index("function _positionReasoningDropdown")
    model_body = ui_js[model_start:ui_js.index("function renderModelDropdown", model_start)]
    reasoning_body = ui_js[reasoning_start:ui_js.index("function closeReasoningDropdown", reasoning_start)]

    assert "_positionComposerDropdownWithinViewport(dd,anchor,footer);" in model_body
    assert "_positionComposerDropdownWithinViewport(dd,anchor,footer);" in reasoning_body


def test_mobile_composer_config_button_has_scroll_row_priority():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert "@media(max-width:640px)" in style_css
    assert (
        ".icon-btn.composer-mobile-config-btn{box-sizing:border-box;position:relative;"
        "display:inline-flex!important;width:44px;height:44px;min-width:44px;"
        "min-height:44px;flex-shrink:0;order:-10;}"
    ) in style_css


def test_mobile_composer_config_layers_above_rightpanel():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    mobile_guard = style_css[style_css.rfind("@media(max-width:640px)") :]
    assert ".rightpanel{" in mobile_guard
    assert "z-index:200!important;" in mobile_guard
    assert (
        ".composer-wrap:has(.composer-mobile-config-panel.open){z-index:240!important;}"
        in mobile_guard
    )


def test_workspace_files_toggle_uses_current_rightpanel_contract():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    boot_js = Path("web/static/boot.js").read_text(encoding="utf-8")

    assert 'onclick="toggleFileTreePanel()"' in index_html
    assert 'class="rightpanel"' in index_html
    assert 'id="chatFileTreePanel"' not in index_html
    assert "function toggleWorkspacePanel(force)" in boot_js
    assert "function toggleMobileFiles(){\n  toggleWorkspacePanel();\n}" in boot_js
    assert "else if(_workspacePanelMode==='browse') _setWorkspacePanelMode('browse');" in boot_js
    assert "const isOpen = fileTreePanel ? !fileTreeMinimized : _workspacePanelMode!=='closed';" in boot_js
    assert "toggleBtn.disabled=!isOpen&&!canBrowse;" in boot_js
    assert "openWorkspacePanel(nextMode,{force:true});" in boot_js
    assert "window.toggleFileTreePanel=function(force){return toggleWorkspacePanel(force);};" in boot_js


def test_open_files_bar_has_current_chat_markup_contract():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    messages_js = Path("web/static/messages.js").read_text(encoding="utf-8")
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert 'class="open-files-bar" id="openFilesBar"' in index_html
    assert 'role="tablist" aria-label="Open referenced files"' in index_html
    assert index_html.index('id="openFilesBar"') < index_html.index('class="messages" id="messages"')
    assert "function _renderOpenFilesBar()" in messages_js
    assert "document.getElementById('openFilesBar')" in messages_js
    assert ".open-files-bar{" in style_css
    assert ".ofb-tab{" in style_css


def test_context_info_button_has_panel_markup_contract():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")

    assert 'id="btnContextInfo"' in index_html
    assert 'onclick="toggleContextInfoPanel()"' in index_html
    assert 'id="contextPanel"' in index_html
    assert 'id="contextPanelLoading"' in index_html
    assert 'id="contextPanelBody"' in index_html
    assert "function toggleContextInfoPanel()" in ui_js
    assert "$('contextPanel')" in ui_js
    assert "$('contextPanelBody')" in ui_js


def test_session_context_info_returns_segment_payload(monkeypatch):
    from types import SimpleNamespace

    from web.api import session_ops

    session = SimpleNamespace(
        session_id="ctx-test",
        model="test-model",
        workspace=r"C:\workspaces\demo",
        messages=[
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
            {"role": "user", "content": [{"type": "file", "text": "file body"}]},
            {"role": "system", "content": "[memory] prefer concise answers"},
        ],
        context_messages=[],
        context_length=1000,
        threshold_tokens=500,
        last_prompt_tokens=250,
    )
    monkeypatch.setattr(session_ops, "get_session", lambda sid: session)

    payload = session_ops.session_context_info("ctx-test")

    assert payload["total_tokens"] == 250
    assert payload["context_length"] == 1000
    assert payload["pct_used"] == 25
    assert payload["metadata"]["message_count"] == 5
    assert payload["metadata"]["workspace"] == r"C:\workspaces\demo"
    assert {segment["id"] for segment in payload["segments"]} >= {
        "chat_history",
        "system_prompt",
        "files",
        "memory",
    }
    assert all(segment["tokens"] >= 0 for segment in payload["segments"])


def test_session_usage_returns_stored_usage_payload(monkeypatch):
    from types import SimpleNamespace

    from web.api import session_ops

    session = SimpleNamespace(
        input_tokens=123,
        output_tokens=45,
        estimated_cost=0.0123,
        context_length=2048,
        threshold_tokens=1024,
        last_prompt_tokens=300,
    )
    monkeypatch.setattr(session_ops, "get_session", lambda sid: session)

    payload = session_ops.session_usage("usage-test")

    assert payload == {
        "input_tokens": 123,
        "output_tokens": 45,
        "total_tokens": 168,
        "estimated_cost": 0.0123,
        "context_length": 2048,
        "threshold_tokens": 1024,
        "last_prompt_tokens": 300,
    }


def test_appstore_panel_switch_does_not_block_main_view_class_update():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")

    appstore_branch = re.search(
        r"// ── Appstore full view lifecycle:.*?if \(mainEl\) \{",
        panels_js,
        re.S,
    )
    assert appstore_branch, "switchPanel appstore branch should precede main class toggles"
    branch = appstore_branch.group(0)

    assert "await loadAppstorePanel();" not in branch
    assert "loadAppstorePanel().catch" in branch


def test_main_view_css_keeps_full_view_panels_exclusive():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    hidden_block = re.search(
        r"main\.main > #mainChat,.*?main\.main > #mainAppstore\{display:none;\}",
        style_css,
        re.S,
    )
    assert hidden_block, "main-view hidden-by-default block should cover every full view"
    hidden_css = hidden_block.group(0)
    for selector in ("#mainBrowser", "#mainDiscord", "#mainAgents", "#mainAppstore"):
        assert selector in hidden_css

    fallback_rule = re.search(
        r"main\.main(?::not\(\.showing-[^)]+\))+ > #mainChat\{display:flex;\}",
        style_css,
    )
    assert fallback_rule, "chat fallback rule should explicitly exclude full-view panels"
    fallback_css = fallback_rule.group(0)
    for panel in ("browser", "discord", "agents", "appstore"):
        assert f":not(.showing-{panel})" in fallback_css

    assert "main.main.showing-discord > #mainDiscord{display:flex;overflow:hidden;}" in style_css
    assert "main.main.showing-appstore > #mainAppstore{display:flex;}" in style_css


def test_discord_panel_activation_loads_sidebar_and_full_view():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")

    assert 'id="discordContent"' in index_html
    assert 'id="mainDiscord"' in index_html
    assert "if (nextPanel === 'discord') setTimeout(function() {" in panels_js
    assert "if (typeof discordChatInit === 'function') discordChatInit();" in panels_js
    assert "if (typeof loadDiscordPanel === 'function') loadDiscordPanel();" in panels_js


def test_discord_tabs_do_not_double_bind_inline_handlers():
    discord_js = Path("web/static/discord.js").read_text(encoding="utf-8")
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")

    assert 'onclick="discordSwitchTab(' in index_html
    assert "tab.getAttribute('onclick')" in discord_js
    assert "tab.dataset.discordBound === '1'" in discord_js
    assert "tab.dataset.discordBound = '1'" in discord_js


def test_discord_full_view_overview_tabs_fit_narrow_column():
    discord_chat_css = Path("web/static/discord-chat.css").read_text(encoding="utf-8")
    discord_chat_js = Path("web/static/discord-chat.js").read_text(encoding="utf-8")

    assert ".discord-col-overview .discord-tabs" in discord_chat_css
    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in discord_chat_css
    assert "overflow: hidden;" in discord_chat_css
    assert "min-width: 0;" in discord_chat_css
    assert "text-overflow: ellipsis;" in discord_chat_css
    assert ">📊 Dash</button>" in discord_chat_js
    assert 'title="Dashboard" aria-label="Dashboard"' in discord_chat_js
    assert "📊 Dashboard</button>" not in discord_chat_js


def test_discord_full_view_stacks_columns_on_mobile():
    discord_chat_css = Path("web/static/discord-chat.css").read_text(encoding="utf-8")

    assert "@media (max-width: 640px)" in discord_chat_css
    assert ".discord-full-area {\n    flex-direction: column;" in discord_chat_css
    assert "overflow-x: hidden;" in discord_chat_css
    assert ".discord-col-overview,\n  .discord-col-nav,\n  .discord-col-main" in discord_chat_css
    assert "width: 100% !important;" in discord_chat_css
    assert "min-width: 0 !important;" in discord_chat_css
    assert "flex: 0 0 auto !important;" in discord_chat_css
    assert ".discord-col-handle {\n    display: none !important;" in discord_chat_css


def test_websearch_mobile_history_overlays_search_content():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")

    assert "@media(max-width:640px)" in style_css
    assert ".websearch-body{position:relative;}" in style_css
    assert ".websearch-history{\n    position:absolute;" in style_css
    assert "width:min(320px,calc(100% - 44px));" in style_css
    assert "transform:translateX(calc(-100% - 8px));" in style_css
    assert ".websearch-content{width:100%;min-width:0;}" in style_css
    assert ".websearch-quick-input-row{flex-direction:column;}" in style_css
    assert ".websearch-go-btn{width:100%;justify-content:center;}" in style_css
    assert "function websearchSetHistoryOpen(open)" in browser_js
    assert "btn.setAttribute('aria-expanded', String(_websearchHistoryOpen));" in browser_js
    assert "websearchSetHistoryOpen(!websearchIsMobileWidth());" in browser_js


def test_appstore_mobile_stacks_content_and_detail_panel():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert ".appstore-topbar{\n    height:auto;" in style_css
    assert ".appstore-body{\n    flex-direction:column;" in style_css
    assert ".appstore-content{\n    flex:0 0 auto;" in style_css
    assert "border-right:0;" in style_css
    assert ".appstore-right{\n    width:100%;" in style_css
    assert "min-width:0;" in style_css
    assert "max-width:none;" in style_css
    assert ".appstore-topbar-center{\n    order:3;" in style_css
    assert ".appstore-hero-content{max-width:100%;}" in style_css


def test_appstore_home_renders_empty_catalog_state():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    home_start = panels_js.index("function _renderAppstoreHome(container)")
    category_start = panels_js.index("function _renderAppstoreCategory(container, catKey)", home_start)
    home_body = panels_js[home_start:category_start]

    assert "if (_appstoreAppsCache.length === 0)" in home_body
    assert "appstore-empty-state" in home_body
    assert "appstore_empty_catalog_title" in home_body
    assert ".appstore-empty-state{" in style_css
    assert ".appstore-empty-state-icon{" in style_css


def test_insights_panel_bounds_wide_content_responsively():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert ".insights-shell{display:grid;grid-template-columns:200px minmax(0,1fr) 220px;gap:16px;align-items:start;min-height:100%;max-width:100%;overflow:hidden;}" in style_css
    assert ".insights-main-column{display:flex;flex-direction:column;gap:14px;max-width:100%;overflow:hidden;}" in style_css
    assert ".insights-kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:8px;min-width:0;}" in style_css
    assert ".insights-card{background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:14px;min-width:0;max-width:100%;overflow:hidden;}" in style_css
    assert ".insights-table{width:100%;font-size:12px;display:block;max-width:100%;overflow-x:auto;}" in style_css
    assert "min-width:420px;" in style_css
    assert ".insights-daily-token-chart{height:200px;display:grid;grid-auto-flow:column;grid-auto-columns:minmax(28px,1fr);gap:4px;align-items:end;padding:6px 0 2px;border-bottom:1px solid var(--border);overflow-x:auto;}" in style_css
    assert ".insights-inspector .system-health-metrics{grid-template-columns:1fr;gap:8px;}" in style_css
    assert "@media(max-width:640px)" in style_css
    assert ".insights-kpi-row{grid-template-columns:repeat(2,minmax(0,1fr));}" in style_css
    assert ".insights-inspector{grid-template-columns:1fr;}" in style_css


def test_logs_lines_belong_to_horizontal_scroll_surface():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert ".logs-output{min-height:320px;max-height:calc(100vh - 170px);overflow:auto;" in style_css
    assert ".log-line{display:block;min-width:100%;width:max-content;box-sizing:border-box;" in style_css
    assert ".logs-output.wrap .log-line{width:100%;max-width:100%;white-space:pre-wrap;overflow-wrap:anywhere;}" in style_css


def test_root_layout_blocks_window_horizontal_scroll():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert "html{height:100%;overflow:hidden;}" in style_css
    assert "body{background:var(--bg);color:var(--text);height:100vh;height:100dvh;overflow:hidden" in style_css


def test_app_shell_resets_root_scroll_drift():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")

    assert "function resetAppShellScroll" in ui_js
    assert "document.body.scrollTop" in ui_js
    assert "document.documentElement.scrollTop" in ui_js
    assert "window.addEventListener('scroll', _queueAppShellScrollReset" in ui_js
    assert "document.addEventListener('scroll', _queueAppShellScrollReset, true)" in ui_js
    assert "window.addEventListener('resize', _queueAppShellScrollReset" in ui_js
    assert "document.addEventListener('focusin', _queueAppShellScrollReset" in ui_js
    assert "window.resetAppShellScroll=resetAppShellScroll;" in ui_js
    assert "if (typeof resetAppShellScroll === 'function') resetAppShellScroll();" in panels_js


def test_agents_wizard_steps_hidden_class_is_effective():
    agents_css = Path("web/static/agents.css").read_text(encoding="utf-8")
    agents_js = Path("web/static/agents.js").read_text(encoding="utf-8")
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")

    assert 'class="agents-wizard-step hidden" id="wizardStep2"' in index_html
    assert "el.classList.add('hidden')" in agents_js
    assert "if (grid) grid.classList.add('hidden')" in agents_js
    assert 'class="agents-workspace-chat hidden" id="agentsChatPane"' in index_html
    assert 'class="agents-workspace-view hidden" id="agentsWorkspaceView"' in index_html
    assert ".agents-wizard-step.hidden" in agents_css
    assert ".agents-grid-view.hidden" in agents_css
    assert ".agents-workspace-chat.hidden" in agents_css
    assert ".agents-workspace-view.hidden" in agents_css
    assert "display: none !important;" in agents_css


def test_agents_chat_sidebar_markup_matches_loader_contract():
    agents_css = Path("web/static/agents.css").read_text(encoding="utf-8")
    agents_js = Path("web/static/agents.js").read_text(encoding="utf-8")
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")

    assert 'class="agents-workspace-info" id="agentsChatSidebar"' in index_html
    assert 'id="agentsProfileInfo"' in index_html
    assert 'id="agentsSessionList"' in index_html
    assert index_html.index('id="agentsWorkspaceView"') < index_html.index('id="agentsChatSidebar"')
    assert "document.getElementById('agentsProfileInfo')" in agents_js
    assert "document.getElementById('agentsSessionList')" in agents_js
    assert "document.getElementById('agentsChatSidebar')" in agents_js
    assert ".agents-workspace-info" in agents_css
    assert ".agents-session-item" in agents_css


def test_dashboard_self_link_is_hidden_for_current_origin():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")

    assert "new URL(url,window.location.href).origin===window.location.origin" in ui_js
    assert "const running=probedRunning&&!sameOrigin" in ui_js


def test_cast_status_uses_user_safe_error_summary():
    routes_py = Path("web/api/routes.py").read_text(encoding="utf-8")

    assert '"error": "Hub nicht erreichbar"' in routes_py
    assert '"detail": _sanitize_error(exc)' in routes_py
    assert 'os.getenv("HERMES_CAST_API_HOST", "").strip()' in routes_py


def test_cast_status_without_config_does_not_probe_network(monkeypatch):
    from web.api import routes

    captured = {}

    def fake_j(handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("cast status should not probe network without configured host")

    monkeypatch.delenv("SIDEKICK_CAST_API_HOST", raising=False)
    monkeypatch.delenv("HERMES_CAST_API_HOST", raising=False)
    monkeypatch.setattr(routes, "j", fake_j)
    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)

    routes._handle_cast_proxy(object(), "/api/cast/status", "GET")

    assert captured["status"] == 200
    assert captured["payload"]["available"] is False
    assert captured["payload"]["active"] is False
    assert captured["payload"]["configured"] is False
    assert captured["payload"]["host"] == ""
    assert "not configured" in captured["payload"]["detail"]


def test_cast_status_configured_host_keeps_safe_unavailable_error(monkeypatch):
    from web.api import routes

    captured = {}

    def fake_j(handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status

    def fail_urlopen(*args, **kwargs):
        raise TimeoutError(r"timed out at C:\secret\hub")

    monkeypatch.setenv("SIDEKICK_CAST_API_HOST", "http://127.0.0.1:9/")
    monkeypatch.setattr(routes, "j", fake_j)
    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)

    routes._handle_cast_proxy(object(), "/api/cast/status", "GET")

    assert captured["status"] == 200
    assert captured["payload"]["available"] is False
    assert captured["payload"]["configured"] is True
    assert captured["payload"]["error"] == "Hub nicht erreichbar"
    assert captured["payload"]["host"] == "http://127.0.0.1:9"
    assert "C:\\secret" not in captured["payload"]["detail"]


def test_cast_toggle_configured_host_keeps_error_status(monkeypatch):
    from web.api import routes

    captured = {}

    def fake_j(handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status

    monkeypatch.setenv("SIDEKICK_CAST_API_HOST", "http://127.0.0.1:9/")
    monkeypatch.setattr(routes, "j", fake_j)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("timed out")),
    )

    routes._handle_cast_proxy(object(), "/api/cast/toggle", "POST")

    assert captured["status"] == 502
    assert captured["payload"]["available"] is False
    assert captured["payload"]["configured"] is True


def test_boot_uses_realistic_metadata_timeouts():
    boot_js = Path("web/static/boot.js").read_text(encoding="utf-8")
    spaces_js = Path("web/static/spaces.js").read_text(encoding="utf-8")

    assert "_bootTimeout(api('/api/profile/active'),5000,'active profile')" in boot_js
    assert "_bootTimeout(loadWorkspaceList(),10000,'workspace list')" in boot_js
    assert "_bootTimeout(_loadActiveSpaceConfig(),8000,'space config')" in boot_js
    assert "_bootTimeout(loadOnboardingWizard(),8000,'onboarding')" in boot_js
    assert "_withSpaceTimeout(api('/api/spaces'), 10000, 'load spaces')" in spaces_js


def test_visible_static_ui_text_is_not_mojibake():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")
    spaces_js = Path("web/static/spaces.js").read_text(encoding="utf-8")
    i18n_js = Path("web/static/i18n.js").read_text(encoding="utf-8")
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert ">▶</button>" in index_html
    assert "← Zurück" in index_html
    assert "🧠 Memory" in index_html
    assert "🤖 AI Enrich" in index_html
    assert "🛡️ Watchdog" in index_html
    assert "ws.emoji || '📁'" in spaces_js
    assert "' · ' + ws.model.provider" in spaces_js
    assert "Running deep research…" in browser_js
    assert "Loading research session…" in browser_js
    assert "Wähle eine Aufgabenkarte" in i18n_js
    assert "content:'✓ '" in style_css

    assert "â–¶</button>" not in index_html
    assert "â† Zurück" not in index_html
    assert "ðŸ" not in index_html
    assert "Â·" not in spaces_js
    assert "ðŸ" not in spaces_js
    assert "FÃ¼hre" not in browser_js
    assert "researchâ€¦" not in browser_js
    assert "WÃ¤hle eine Aufgabenkarte" not in i18n_js
    assert "content:'âœ“ '" not in style_css


def test_static_i18n_references_have_english_fallbacks():
    import re

    static_dir = Path("web/static")
    i18n_js = (static_dir / "i18n.js").read_text(encoding="utf-8")

    en_start = i18n_js.index("  en: {")
    brace_start = i18n_js.index("{", en_start)
    depth = 0
    quote = ""
    escape = False
    en_end = None
    for idx in range(brace_start, len(i18n_js)):
        ch = i18n_js[idx]
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = ""
            continue
        if ch in {"'", '"', "`"}:
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                en_end = idx
                break
    assert en_end is not None

    en_block = i18n_js[brace_start:en_end]
    en_keys = set(re.findall(r"^\s*([A-Za-z0-9_]+)\s*:", en_block, flags=re.MULTILINE))

    refs: dict[str, list[str]] = {}

    def add_ref(key: str, path: Path, line: int) -> None:
        if not key or key.endswith("_"):
            return
        refs.setdefault(key, []).append(f"{path.as_posix()}:{line}")

    for path in sorted(static_dir.glob("*")):
        if path.suffix not in {".html", ".js"} or path.name == "i18n.js":
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".html":
            for match in re.finditer(r'data-i18n(?:-[a-z-]+)?\s*=\s*"([^"]+)"', text):
                add_ref(match.group(1), path, text[: match.start()].count("\n") + 1)
        for match in re.finditer(r"\bt\(\s*['\"]([^'\"]+)['\"]", text):
            add_ref(match.group(1), path, text[: match.start()].count("\n") + 1)

    missing = {key: locations[:3] for key, locations in refs.items() if key not in en_keys}
    assert missing == {}


def test_workspace_load_dir_ignores_abort_noise():
    workspace_js = Path("web/static/workspace.js").read_text(encoding="utf-8")

    assert "e && e.name === 'AbortError'" in workspace_js
    assert "console.warn('loadDir',e)" in workspace_js


def test_unconfigured_cast_status_keeps_button_hidden():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")

    assert "let _castConfigured=true;" in ui_js
    assert "s.configured!==false" in ui_js
    assert "if(!_castConfigured)" in ui_js
    assert "if(!_castConfigured)_cleanupCastTimers()" in ui_js
    assert "btn.style.display='none'" in ui_js


def test_empty_session_model_resolution_skips_catalog(monkeypatch):
    from web.api import routes

    class Session:
        model = ""
        model_provider = None

    def fail_catalog():
        raise AssertionError("empty session model should not build model catalog")

    monkeypatch.setattr(routes, "get_available_models", fail_catalog)
    monkeypatch.setattr(routes, "get_effective_default_model", lambda: "default-fast")

    assert routes._resolve_effective_session_model_for_display(Session()) == "default-fast"
    assert routes._resolve_effective_session_model_provider_for_display(Session()) is None


def test_matching_session_model_provider_resolution_skips_catalog(monkeypatch):
    from web.api import routes

    class Session:
        model = "deepseek-v4-flash"
        model_provider = "opencode-go"

    def fail_catalog():
        raise AssertionError("matching provider should not build model catalog")

    monkeypatch.setattr(routes, "get_available_models", fail_catalog)
    monkeypatch.setattr(
        routes,
        "resolve_active_provider_context",
        lambda **kwargs: {"provider": "opencode-go", "model": "deepseek-v4-flash"},
    )

    assert routes._resolve_effective_session_model_for_display(Session()) == "deepseek-v4-flash"
    assert routes._resolve_effective_session_model_provider_for_display(Session()) == "opencode-go"


def test_explicit_session_model_request_skips_catalog(monkeypatch):
    from web.api import routes

    def fail_catalog():
        raise AssertionError("explicit session model/provider request should not build model catalog")

    monkeypatch.setattr(routes, "get_available_models", fail_catalog)
    monkeypatch.setattr(
        routes,
        "resolve_active_provider_context",
        lambda **kwargs: {"provider": "", "model": ""},
    )

    assert routes._session_model_state_from_request(
        "gpt-test",
        "openai",
        current_provider=None,
    ) == ("gpt-test", "openai")


def test_game_mode_setting_persists_and_detects_local_model_servers(monkeypatch, tmp_path):
    from web.api import config as cfg

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")

    assert cfg.load_settings()["game_mode_enabled"] is False
    assert cfg.game_mode_blocks_local_model_request("ollama", "") is False

    saved = cfg.save_settings({"game_mode_enabled": "yes"})

    assert saved["game_mode_enabled"] is True
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["game_mode_enabled"] is True
    assert cfg.game_mode_blocks_local_model_request("ollama", "") is True
    assert cfg.game_mode_blocks_local_model_request("custom:local-gpu", "http://127.0.0.1:8080/v1") is True
    assert cfg.game_mode_blocks_local_model_request("openai", "https://api.openai.com/v1") is False


def test_game_mode_chat_guard_builds_409_payload_for_local_models(monkeypatch, tmp_path):
    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    def fail_stream_resolution(model_id):
        raise AssertionError("game mode guard should not require stream startup")

    monkeypatch.setattr(routes, "resolve_model_provider", fail_stream_resolution, raising=False)

    payload = routes._game_mode_guard_payload_for_model(
        "qwen3:4b",
        "ollama",
        {"provider": "ollama", "model": "qwen3:4b", "base_url": "http://127.0.0.1:11434"},
    )

    assert payload["error"]["code"] == "game_mode_enabled"
    assert payload["game_mode_enabled"] is True
    assert "local model" in payload["error"]["message"].lower()


def test_image_generation_tool_returns_game_mode_error(monkeypatch, tmp_path):
    from web.api import config as cfg
    from tools import image_generation_tool as image_tool

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})
    monkeypatch.setattr(
        image_tool,
        "_dispatch_to_plugin_provider",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("image provider should not run")),
    )

    payload = json.loads(image_tool._handle_image_generate({"prompt": "neon cockpit", "aspect_ratio": "1:1"}))

    assert payload["success"] is False
    assert payload["error_type"] == "game_mode_enabled"
    assert "game mode" in payload["error"].lower()


def test_game_mode_resource_release_cancels_local_runs_and_unloads_ollama(monkeypatch, tmp_path):
    from web.api import config as cfg
    from web.api import game_mode

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    with cfg.ACTIVE_RUNS_LOCK:
        cfg.ACTIVE_RUNS.clear()
        cfg.ACTIVE_RUNS.update(
            {
                "local-stream": {"provider": "ollama", "model": "qwen3:4b"},
                "remote-stream": {"provider": "openai", "model": "gpt-test"},
            }
        )

    cancelled = []
    monkeypatch.setattr(game_mode, "_cancel_stream", lambda stream_id: cancelled.append(stream_id) or True)
    monkeypatch.setattr(game_mode, "_loaded_ollama_models", lambda _base_url: ["qwen3:4b"])
    monkeypatch.setattr(game_mode, "_unload_ollama_model", lambda _base_url, model: {"ok": True, "model": model})
    monkeypatch.setattr(game_mode, "_terminate_known_local_model_servers", lambda: [])
    monkeypatch.setattr(game_mode, "_release_local_image_generation_queues", lambda: {"queues": []})

    payload = game_mode.release_game_mode_resources()

    assert cancelled == ["local-stream"]
    assert payload["cancelled_local_streams"] == ["local-stream"]
    assert payload["ollama"]["unloaded"][0]["model"] == "qwen3:4b"
    assert "image_generation_queue" in payload


def test_game_mode_release_targets_all_nova_local_model_ports():
    from web.api import game_mode

    ports = game_mode._nova_local_model_ports()

    assert 8081 in ports
    assert 8082 in ports


def test_game_mode_release_flushes_and_stops_local_image_queue(monkeypatch):
    from web.api import game_mode

    calls = []
    monkeypatch.setattr(game_mode, "_flush_local_image_generation_queue", lambda base_url: calls.append(("flush", base_url)) or {"ok": True, "cancelled": 2})
    monkeypatch.setattr(game_mode, "_terminate_local_image_generation_queue_processes", lambda ports: calls.append(("terminate", tuple(sorted(ports)))) or [{"ok": True, "pid": 1234, "port": 8283}])

    payload = game_mode._release_local_image_generation_queues()

    assert calls == [
        ("flush", "http://127.0.0.1:8283"),
        ("terminate", (8283,)),
    ]
    assert payload["queues"][0]["flush"]["cancelled"] == 2
    assert payload["terminated"][0]["pid"] == 1234


def test_game_mode_image_queue_flush_skips_closed_port(monkeypatch):
    from web.api import game_mode

    monkeypatch.setattr(game_mode, "_tcp_endpoint_open", lambda _base_url: False)

    assert game_mode._flush_local_image_generation_queue("http://127.0.0.1:8283") == {
        "ok": False,
        "skipped": "not_listening",
    }


def test_game_mode_recognizes_local_image_queue_process():
    from web.api import game_mode

    class Proc:
        def name(self):
            return "python.exe"

        def cmdline(self):
            return ["python", "C:/HermesPortable/home/scripts/local_gen_queue.py"]

    assert game_mode._process_looks_like_local_image_generation_queue(Proc()) is True


def test_local_gen_queue_rejects_generate_when_game_mode_enabled():
    source = Path(r"C:\HermesPortable\home\scripts\local_gen_queue.py").read_text(encoding="utf-8")

    assert "def _game_mode_enabled()" in source
    assert "def _game_mode_settings_candidates()" in source
    assert '"state", "webui", "settings.json"' in source
    assert "C:/sidekick/home/state/webui/settings.json" in source
    assert "if _game_mode_enabled():" in source
    assert 'self._json(409, _game_mode_payload())' in source
    assert 'job.error = "game_mode_enabled"' in source


def test_settings_post_runs_game_mode_release_when_enabling(monkeypatch, tmp_path):
    import io
    from urllib.parse import urlparse

    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    from web.api import config as cfg
    from web.api import game_mode
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": False})
    monkeypatch.setattr(
        game_mode,
        "release_game_mode_resources",
        lambda: {
            "cancelled_local_streams": [],
            "ollama": {"checked": [], "unloaded": []},
            "local_model_servers": [],
        },
    )

    body = json.dumps({"game_mode_enabled": True}).encode("utf-8")

    class _Handler:
        headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            "Host": "127.0.0.1",
        }
        client_address = ("127.0.0.1", 12345)

        def __init__(self):
            self.status_code = None
            self.response_headers = {}
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()

        def send_response(self, status):
            self.status_code = status

        def send_header(self, name, value):
            self.response_headers[name.lower()] = value

        def end_headers(self):
            pass

    handler = _Handler()
    handled = routes.handle_post(
        handler,
        urlparse("/api/settings"),
    )

    assert handled is None
    assert handler.status_code == 200
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["game_mode_enabled"] is True
    assert "game_mode_release" in payload
    assert "cancelled_local_streams" in payload["game_mode_release"]
    assert "ollama" in payload["game_mode_release"]
    assert "local_model_servers" in payload["game_mode_release"]


def test_game_mode_status_endpoint_returns_current_setting(monkeypatch, tmp_path):
    import io
    from urllib.parse import urlparse

    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    class _Handler:
        headers = {"Host": "127.0.0.1"}
        client_address = ("127.0.0.1", 12345)

        def __init__(self):
            self.status_code = None
            self.response_headers = {}
            self.rfile = io.BytesIO()
            self.wfile = io.BytesIO()

        def send_response(self, status):
            self.status_code = status

        def send_header(self, name, value):
            self.response_headers[name.lower()] = value

        def end_headers(self):
            pass

    handler = _Handler()
    handled = routes.handle_get(handler, urlparse("/api/game-mode/status"))

    assert handled is None
    assert handler.status_code == 200
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {"ok": True, "game_mode_enabled": True}


def test_server_startup_runs_game_mode_release_when_already_enabled(monkeypatch, tmp_path):
    from web.api import config as cfg
    from web.api import game_mode
    from web import server

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    calls = []
    monkeypatch.setattr(
        game_mode,
        "release_game_mode_resources",
        lambda: calls.append("release") or {"local_model_servers": []},
    )

    server._release_game_mode_resources_on_startup()

    assert calls == ["release"]


def test_game_mode_titlebar_button_and_settings_ui_are_wired():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    boot_js = Path("web/static/boot.js").read_text(encoding="utf-8")
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    titlebar_start = index_html.index('<div class="titlebar-actions" id="titlebarActions">')
    lang_start = index_html.index("titlebarLangSelector", titlebar_start)
    cast_start = index_html.index("btnCastToggle", lang_start)
    titlebar_actions = index_html[titlebar_start:cast_start]

    assert 'id="btnGameModeToggle"' in titlebar_actions
    assert "toggleGameMode()" in titlebar_actions
    assert "game_mode_toggle" in titlebar_actions
    assert "settingsGameModeEnabled" in index_html
    assert "window._gameModeEnabled=!!s.game_mode_enabled" in boot_js
    assert "function syncGameModeButton()" in panels_js
    assert "async function toggleGameMode()" in panels_js
    assert "function _gameModeReleaseSummary(release)" in panels_js
    assert "saved&&saved.game_mode_release" in panels_js
    assert "No Sidekick local GPU processes found." in panels_js
    assert "btn.setAttribute('data-i18n-title',enabled?'game_mode_on':'game_mode_off')" in panels_js
    assert "btn.setAttribute('data-i18n-aria-label',enabled?'game_mode_on':'game_mode_off')" in panels_js
    assert "game_mode_enabled" in panels_js
    assert ".game-mode-toggle-btn" in style_css
    assert ".game-mode-toggle-btn.active" in style_css


def test_initial_space_labels_use_url_workspace_before_spaces_js_loads():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")

    titlebar_default = index_html.index('id="titlebarSpaceName">default</span>')
    titlebar_script = index_html.index("function initInitialSpaceLabel()", titlebar_default)
    titlebar_actions = index_html.index('<div class="titlebar-actions"', titlebar_script)
    sidebar_default = index_html.index('id="sidebarSpaceName">default</span>')
    sidebar_script = index_html.index("function initInitialSidebarSpaceLabel()", sidebar_default)
    spaces_js = index_html.index("static/spaces.js")

    assert titlebar_default < titlebar_script < titlebar_actions
    assert sidebar_default < sidebar_script < spaces_js
    assert index_html.count("new URLSearchParams(window.location.search || '').get('workspace')") >= 2
    assert index_html.count("if (!slug) slug = 'nova';") >= 2
    assert "btn.setAttribute('title', 'Switch space (' + slug + ')')" in index_html


def test_preferences_controls_are_disabled_until_autosave_handlers_are_ready():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")

    assert "function _setPreferencesControlsBusy(busy)" in panels_js
    assert "el.dataset.settingsLoadingDisabled='1'" in panels_js
    assert "delete el.dataset.settingsLoadingDisabled" in panels_js

    load_start = panels_js.index("async function loadSettingsPanel()")
    busy_start = panels_js.index("_setPreferencesControlsBusy(true)", load_start)
    slow_models = panels_js.index("models=await api('/api/models')", load_start)
    bot_name = panels_js.index("const botNameField=$('settingsBotName')", load_start)
    busy_end = panels_js.index("_setPreferencesControlsBusy(false)", bot_name)

    assert busy_start < slow_models
    assert bot_name < busy_end


def test_background_stream_requests_keep_owner_workspace():
    messages_js = Path("web/static/messages.js").read_text(encoding="utf-8")
    sessions_js = Path("web/static/sessions.js").read_text(encoding="utf-8")

    assert "const scopedPath = (typeof _spaceScopedApiPath === 'function')" in sessions_js
    assert "return await api(scopedPath, {signal: controller.signal})" in sessions_js
    assert "msg_before=${_oldestIdx}&msg_limit=${_INITIAL_MSG_LIMIT}`,\n      _SESSION_MESSAGES_TIMEOUT_MS" in sessions_js
    assert "messages=1&resolve_model=0`,\n      _SESSION_MESSAGES_TIMEOUT_MS" in sessions_js
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


def test_space_switch_excludes_explicit_foreign_sessions_from_default_space():
    spaces_js = Path("web/static/spaces.js").read_text(encoding="utf-8")
    sessions_js = Path("web/static/sessions.js").read_text(encoding="utf-8")

    assert "function _spaceSessionMatchesSlug(session, slug)" in spaces_js
    assert "function _clearSessionRoutePath(pathname)" in spaces_js
    assert "function _locationHasSessionRoute()" in spaces_js
    assert "if (explicit) return explicit === target" in spaces_js
    assert "return _shouldTrustUnscopedSessionsForSpace(target)" in spaces_js
    assert "const previousSpace = _activeSpace" in spaces_js
    assert "|| _locationHasSessionRoute()" in spaces_js
    assert "localStorage.removeItem('sidekick-webui-session')" in spaces_js
    assert "_syncActiveSpaceUrl(slug, {clearSessionRoute: shouldClearSessionRoute})" in spaces_js
    assert "sessionsInSpace = _allSessions.filter(s => _spaceSessionMatchesSlug(s, slug))" in spaces_js
    assert "const hasCurrentInSpace = !!(currentSid && activeSessionInTargetSpace" in spaces_js
    assert "_spaceSessionMatchesSlug," in spaces_js
    assert "typeof window._spaceSessionMatchesSlug==='function'" in sessions_js
    assert "if(sessionSpace) return sessionSpace===active" in sessions_js
    assert "return active==='nova'||active==='default'" in sessions_js


def test_space_dropdown_renders_cached_spaces_before_refresh():
    spaces_js = Path("web/static/spaces.js").read_text(encoding="utf-8")

    assert "function _openSpaceDropdown(dd, btn, className)" in spaces_js
    assert "const cachedSpaces = Array.isArray(_spacesCache) ? _spacesCache.filter(Boolean) : []" in spaces_js
    assert "if (cachedSpaces.length)" in spaces_js
    assert "_renderSpaceDropdownItems(dd, cachedSpaces)" in spaces_js
    assert "if (cachedSpaces.length) setTimeout(refresh, 0)" in spaces_js
    assert "loadSpaces().then(spaces => {" in spaces_js
    assert "if (dd.hidden) return" in spaces_js
    assert "_openSpaceDropdown(dd, btn, 'sidebar-space-dropdown')" in spaces_js
    assert "requestAnimationFrame(runSelect)" in spaces_js


def test_space_switch_does_not_block_on_space_config_load():
    spaces_js = Path("web/static/spaces.js").read_text(encoding="utf-8")

    assert "async function _loadSpaceConfigForSwitch(slug, switchRev, timeoutMs)" in spaces_js
    assert "const spaceConfigPromise = _loadSpaceConfigForSwitch(slug, switchRev, 1200)" in spaces_js
    assert "void _continueSpaceSessionSelection(slug, switchRev, sessionsInSpace, spaceConfigPromise)" in spaces_js
    assert "_markSpaceSwitchTiming(slug, switchRev, 'session-list-rendered')" in spaces_js


def test_session_html_cache_ignores_loading_placeholder():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")

    assert "!/Loading conversation/i.test(String(cached.html||''))" in ui_js
    assert "!/Loading conversation/i.test(String(_html))" in ui_js


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


def test_proxy_sync_returns_502_on_backend_connection_reset(monkeypatch):
    from cli import web_server

    def reset_urlopen(req, timeout):
        raise ConnectionResetError("backend closed connection")

    monkeypatch.setattr(web_server, "_ensure_stdlib_backend", lambda: 9123)
    monkeypatch.setattr(web_server.urllib.request, "urlopen", reset_urlopen)

    status, body, headers, content_type = web_server._proxy_sync(
        "GET",
        "/api/workspaces",
        {"host": "127.0.0.1:9119"},
        None,
    )

    payload = json.loads(body.decode("utf-8"))
    assert status == 502
    assert payload["error"].startswith("proxy failed:")
    assert headers["connection"] == "close"
    assert content_type == "application/json"


def test_asyncio_disconnect_context_is_suppressed():
    from cli import web_server

    assert web_server._is_asyncio_client_disconnect_context(
        {"exception": ConnectionResetError("client reset")}
    )
    assert web_server._is_asyncio_client_disconnect_context(
        {"exception": BrokenPipeError("client closed")}
    )
    assert not web_server._is_asyncio_client_disconnect_context(
        {"exception": RuntimeError("real failure")}
    )


def test_asyncio_disconnect_exception_filter_delegates_real_errors():
    from cli import web_server

    loop = asyncio.new_event_loop()
    calls = []

    def previous(loop_arg, context):
        calls.append((loop_arg, context))

    old_loop = None
    try:
        try:
            old_loop = asyncio.get_event_loop()
        except RuntimeError:
            old_loop = None
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(previous)

        web_server._install_asyncio_disconnect_exception_filter()
        handler = loop.get_exception_handler()

        handler(loop, {"exception": ConnectionResetError("client reset")})
        assert calls == []

        context = {"exception": RuntimeError("real failure")}
        handler(loop, context)
        assert calls == [(loop, context)]
    finally:
        asyncio.set_event_loop(old_loop)
        loop.close()


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


def test_legacy_sse_paths_are_streamed_not_buffered():
    from cli import web_server

    streamed_paths = [
        "/api/chat/stream?stream_id=s1",
        "/api/terminal/stream?session_id=s1",
        "/api/sessions/gateway/stream",
        "/api/approval/stream?session_id=s1",
        "/api/clarify/stream?session_id=s1",
        "/api/browser/events?session_id=s1",
        "/api/nova/events",
        "/api/gmail/ai/summary/stream?id=m1",
        "/api/kanban/events/stream?board=default",
        "/api/agents/workspace/stream/s1",
    ]

    for path in streamed_paths:
        assert web_server._is_streaming_api_path(path), path

    buffered_paths = [
        "/api/chat/stream/status?stream_id=s1",
        "/api/kanban/events?board=default",
        "/api/browser/state?session_id=s1",
        "/api/workspaces",
    ]
    for path in buffered_paths:
        assert not web_server._is_streaming_api_path(path), path


def test_browser_frame_image_uses_authenticated_fetch_blob():
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")

    assert "let _browserFrameObjectUrl = '';" in browser_js
    assert "img.dataset.frameSrc" in browser_js
    assert "fetch(frameRequestUrl, {credentials:'same-origin'})" in browser_js
    assert "if (!img.getAttribute('src')) img.style.visibility = 'hidden';" in browser_js
    assert "URL.createObjectURL(blob)" in browser_js
    assert "URL.revokeObjectURL(_browserFrameObjectUrl)" in browser_js


def test_stdlib_proxy_uses_streaming_proxy_for_legacy_sse(monkeypatch):
    from cli import web_server

    captured = {}

    def fake_stream(method, path, headers, body):
        captured["stream_path"] = path
        return iter([b"event: ping\n", b"data: {}\n", b"\n"])

    def fail_sync(method, path, headers, body):
        raise AssertionError(f"SSE path must not use buffered proxy: {path}")

    monkeypatch.setattr(web_server, "_proxy_stream", fake_stream)
    monkeypatch.setattr(web_server, "_proxy_sync", fail_sync)

    client = TestClient(web_server.app)
    response = client.get(
        f"/api/approval/stream?session_id=s1&token={web_server._SESSION_TOKEN}",
    )

    assert response.status_code == 200
    assert captured["stream_path"] == (
        f"/api/approval/stream?session_id=s1&token={web_server._SESSION_TOKEN}"
    )
    assert "event: ping" in response.text


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
