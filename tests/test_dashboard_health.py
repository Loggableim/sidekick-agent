import asyncio
import json
import re
import sys
import warnings
import pytest
from pathlib import Path
from types import SimpleNamespace
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


def test_codex_device_login_uses_default_base_url_when_env_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SIDEKICK_CODEX_BASE_URL", raising=False)

    from cli import web_server
    from cli.auth import DEFAULT_CODEX_BASE_URL
    import httpx
    import runtime.credential_pool as credential_pool

    responses = iter(
        [
            SimpleNamespace(
                status_code=200,
                json=lambda: {"user_code": "ABCD-EFGH", "device_auth_id": "device-1", "interval": "0"},
            ),
            SimpleNamespace(
                status_code=200,
                json=lambda: {"authorization_code": "auth-code", "code_verifier": "verifier"},
            ),
            SimpleNamespace(
                status_code=200,
                json=lambda: {"access_token": "access-token", "refresh_token": "refresh-token"},
            ),
        ]
    )

    class FakeHttpxClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return next(responses)

    added_entries = []

    class FakePool:
        def add_entry(self, entry):
            added_entries.append(entry)

    monkeypatch.setattr(httpx, "Client", FakeHttpxClient)
    monkeypatch.setattr(web_server.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(credential_pool, "load_pool", lambda provider: FakePool())

    session_id = "test-codex-device-session"
    with web_server._oauth_sessions_lock:
        web_server._oauth_sessions[session_id] = {"status": "pending", "created_at": web_server.time.time()}
    try:
        web_server._codex_full_login_worker(session_id)
        session = web_server._oauth_sessions[session_id]
    finally:
        with web_server._oauth_sessions_lock:
            web_server._oauth_sessions.pop(session_id, None)

    assert session["status"] == "approved"
    assert added_entries[0].base_url == DEFAULT_CODEX_BASE_URL


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


def test_profile_switch_invalid_name_returns_bad_request(monkeypatch):
    from types import SimpleNamespace
    from web.api import routes

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {"name": "Bad Name"})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes.handle_post(
        SimpleNamespace(headers={}),
        SimpleNamespace(path="/api/profile/switch"),
    )

    assert response["status"] == 400


def test_profile_switch_missing_profile_returns_not_found(monkeypatch):
    from types import SimpleNamespace
    from web.api import profiles, routes

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {"name": "missing-profile"})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    def missing_profile(_name, *, process_wide=True):
        raise ValueError("Profile 'missing-profile' does not exist.")

    monkeypatch.setattr(profiles, "switch_profile", missing_profile)

    response = routes.handle_post(
        SimpleNamespace(headers={}),
        SimpleNamespace(path="/api/profile/switch"),
    )

    assert response["status"] == 404


def test_profile_delete_default_returns_delete_specific_error(monkeypatch):
    from types import SimpleNamespace
    from web.api import routes

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {"name": "default"})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes.handle_post(
        SimpleNamespace(headers={}),
        SimpleNamespace(path="/api/profile/delete"),
    )

    assert response["status"] == 400
    assert "Cannot delete the default profile" in response["payload"]["error"]


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
        "web.api.models.all_sessions",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("space listings must use _index.json directly")),
    )
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


def test_sessiondb_state_meta_roundtrips(tmp_path):
    from runtime._compat.shim_state import SessionDB

    db_path = tmp_path / "home" / "state.db"
    db = SessionDB(db_path=db_path)

    db.set_meta("goal:test-session", '{"goal":"Ship it","status":"active"}')
    assert db.get_meta("goal:test-session") == '{"goal":"Ship it","status":"active"}'
    assert db.get_meta("goal:missing") is None

    reopened = SessionDB(db_path=db_path)
    assert reopened.get_meta("goal:test-session") == '{"goal":"Ship it","status":"active"}'


def test_session_detail_includes_persisted_goal_state(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server
    from runtime._compat.shim_state import SessionDB
    from web.api.models import Session

    space_sessions = tmp_path / "home" / "spaces" / "color" / "sessions"
    space_sessions.mkdir(parents=True)
    db = SessionDB(db_path=tmp_path / "home" / "spaces" / "color" / "goals.db")
    db.set_meta(
        "goal:goal-session",
        json.dumps(
            {
                "goal": "Ship it",
                "status": "active",
                "turns_used": 2,
                "max_turns": 12,
                "created_at": 123.0,
                "last_turn_at": 456.0,
            }
        ),
    )

    session = Session(session_id="goal-session", profile="default", workspace=r"C:\\workspace")
    session.messages = []
    session.tool_calls = []
    session_payload = session.compact()
    session_payload.update(
        {
            "profile": "default",
            "workspace_slug": "color",
            "messages": [],
            "tool_calls": [],
        }
    )
    (space_sessions / "goal-session.json").write_text(json.dumps(session_payload), encoding="utf-8")

    monkeypatch.setattr("web.api.routes._clear_stale_stream_state", lambda s: None)
    monkeypatch.setattr("web.api.routes._lookup_cli_session_metadata", lambda sid: None)
    monkeypatch.setattr("web.api.routes._is_messaging_session_record", lambda record: False)

    class _FakeWorkspace:
        def __init__(self, root):
            self.root = root
            self.sessions_dir = root / "sessions"

    monkeypatch.setattr(
        "web.api.space_engine.get_workspace",
        lambda slug: _FakeWorkspace(tmp_path / "home" / "spaces" / slug) if slug == "color" else None,
    )

    response = TestClient(web_server.app).get(
        "/api/session?session_id=goal-session&workspace=color&messages=0&resolve_model=0",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    payload = response.json()["session"]
    assert payload["session_id"] == "goal-session"
    assert payload["goal"]["goal"] == "Ship it"
    assert payload["goal"]["status"] == "active"
    assert payload["goal"]["turns_used"] == 2
    assert payload["goal"]["max_turns"] == 12
    assert payload["goal"]["session_id"] == "goal-session"
    assert payload["goal"]["space"] == "color"


def test_goal_command_payload_uses_space_goal_store(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from runtime._compat.shim_state import SessionDB
    from web.api.goals import goal_command_payload

    space_root = tmp_path / "home" / "spaces" / "color"
    space_root.mkdir(parents=True)
    db = SessionDB(db_path=space_root / "goals.db")
    db.set_meta(
        "goal:goal-session",
        json.dumps(
            {
                "goal": "Ship it",
                "status": "active",
                "turns_used": 2,
                "max_turns": 12,
                "created_at": 123.0,
                "last_turn_at": 456.0,
            }
        ),
    )

    class _FakeWorkspace:
        def __init__(self, root):
            self.root = root

    monkeypatch.setattr(
        "web.api.space_engine.get_workspace",
        lambda slug: _FakeWorkspace(space_root) if slug == "color" else None,
    )

    payload = goal_command_payload("goal-session", "status", space_slug="color")

    assert payload["goal"]["goal"] == "Ship it"
    assert payload["goal"]["status"] == "active"
    assert payload["goal"]["turns_used"] == 2
    assert payload["goal"]["max_turns"] == 12
    assert payload["goal"]["space"] == "color"


def test_goal_state_for_session_omits_cleared_goal(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from runtime._compat.shim_state import SessionDB
    from web.api.goals import goal_state_for_session

    space_root = tmp_path / "home" / "spaces" / "color"
    space_root.mkdir(parents=True)
    db = SessionDB(db_path=space_root / "goals.db")
    db.set_meta(
        "goal:goal-session",
        json.dumps(
            {
                "goal": "Ship it",
                "status": "cleared",
                "turns_used": 2,
                "max_turns": 12,
                "created_at": 123.0,
                "last_turn_at": 456.0,
            }
        ),
    )

    class _FakeWorkspace:
        def __init__(self, root):
            self.root = root

    monkeypatch.setattr(
        "web.api.space_engine.get_workspace",
        lambda slug: _FakeWorkspace(space_root) if slug == "color" else None,
    )

    assert goal_state_for_session("goal-session", space_slug="color") is None


def test_webui_profile_goal_auto_pauses_after_repeated_judge_parse_failures(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from web.api.goals import evaluate_goal_after_turn, goal_command_payload, goal_state_for_session

    monkeypatch.setattr(
        "web.api.goals.judge_goal",
        lambda *args, **kwargs: ("continue", "judge reply was not JSON", True),
    )

    profile_home = tmp_path / "home" / "profiles" / "default"
    profile_home.mkdir(parents=True)

    payload = goal_command_payload("goal-session", "Ship it", profile_home=profile_home)
    assert payload["goal"]["status"] == "active"

    decision = None
    for _ in range(3):
        decision = evaluate_goal_after_turn(
            "goal-session",
            "assistant reply",
            profile_home=profile_home,
        )

    assert decision is not None
    assert decision["status"] == "paused"
    assert decision["should_continue"] is False

    state = goal_state_for_session("goal-session", profile_home=profile_home)
    assert state is not None
    assert state["status"] == "paused"
    assert "unparseable output" in str(state["paused_reason"])
    assert "judge model" in decision["message"]


def test_goal_command_status_omits_cleared_goal(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from runtime._compat.shim_state import SessionDB
    from web.api.goals import goal_command_payload

    space_root = tmp_path / "home" / "spaces" / "color"
    space_root.mkdir(parents=True)
    db = SessionDB(db_path=space_root / "goals.db")
    db.set_meta(
        "goal:goal-session",
        json.dumps(
            {
                "goal": "Ship it",
                "status": "cleared",
                "turns_used": 2,
                "max_turns": 12,
                "created_at": 123.0,
                "last_turn_at": 456.0,
            }
        ),
    )

    class _FakeWorkspace:
        def __init__(self, root):
            self.root = root

    monkeypatch.setattr(
        "web.api.space_engine.get_workspace",
        lambda slug: _FakeWorkspace(space_root) if slug == "color" else None,
    )

    payload = goal_command_payload("goal-session", "status", space_slug="color")

    assert payload["goal"] is None
    assert payload["message_key"] == "goal_status_none"


def test_goal_route_uses_workspace_slug_from_request_body(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from runtime._compat.shim_state import SessionDB
    from web.api import routes
    from web.api.models import Session
    import io

    space_root = tmp_path / "home" / "spaces" / "color"
    space_root.mkdir(parents=True)
    db = SessionDB(db_path=space_root / "goals.db")
    db.set_meta(
        "goal:goal-session",
        json.dumps(
            {
                "goal": "Ship it",
                "status": "active",
                "turns_used": 2,
                "max_turns": 12,
                "created_at": 123.0,
                "last_turn_at": 456.0,
            }
        ),
    )

    session = Session(session_id="goal-session", profile="default", workspace=r"C:\\workspace")
    monkeypatch.setattr("web.api.routes.get_session", lambda sid: session)
    monkeypatch.setattr(
        "web.api.space_engine.get_workspace",
        lambda slug: type("WS", (), {"root": space_root})() if slug == "color" else None,
    )

    class _FakeHandler:
        def __init__(self):
            self.headers = {}
            self.status = None
            self.sent_headers = {}
            self.wfile = io.BytesIO()

        def send_response(self, status):
            self.status = status

        def send_header(self, key, value):
            self.sent_headers[key] = value

        def end_headers(self):
            pass

    handler = _FakeHandler()
    routes._handle_goal_command(
        handler,
        {
            "session_id": "goal-session",
            "args": "status",
            "workspace_slug": "color",
            "workspace": r"C:\\workspace",
            "profile": "default",
        },
    )

    assert handler.status == 200
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))["goal"]
    assert payload["goal"] == "Ship it"
    assert payload["status"] == "active"
    assert payload["space"] == "color"


def test_goal_route_accepts_legacy_space_field_from_request_body(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server
    from runtime._compat.shim_state import SessionDB
    from web.api import routes
    from web.api.models import Session
    import io

    space_root = tmp_path / "home" / "spaces" / "color"
    space_root.mkdir(parents=True)
    db = SessionDB(db_path=space_root / "goals.db")
    db.set_meta(
        "goal:goal-session",
        json.dumps(
            {
                "goal": "Ship it",
                "status": "active",
                "turns_used": 2,
                "max_turns": 12,
                "created_at": 123.0,
                "last_turn_at": 456.0,
            }
        ),
    )

    session = Session(session_id="goal-session", profile="default", workspace=r"C:\\workspace")
    monkeypatch.setattr("web.api.routes.get_session", lambda sid: session)
    monkeypatch.setattr(
        "web.api.space_engine.get_workspace",
        lambda slug: type("WS", (), {"root": space_root})() if slug == "color" else None,
    )

    class _FakeHandler:
        def __init__(self):
            self.headers = {}
            self.status = None
            self.sent_headers = {}
            self.wfile = io.BytesIO()

        def send_response(self, status):
            self.status = status

        def send_header(self, key, value):
            self.sent_headers[key] = value

        def end_headers(self):
            pass

    handler = _FakeHandler()
    routes._handle_goal_command(
        handler,
        {
            "session_id": "goal-session",
            "args": "status",
            "space": "color",
            "workspace": r"C:\\workspace",
            "profile": "default",
        },
    )

    assert handler.status == 200
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))["goal"]
    assert payload["goal"] == "Ship it"
    assert payload["status"] == "active"
    assert payload["space"] == "color"


def test_chat_start_marks_active_goal_turns_as_goal_related(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from types import SimpleNamespace
    from web.api import routes

    captured = {}

    session = SimpleNamespace(
        session_id="goal-session",
        profile="default",
        workspace=r"C:\\workspace",
        model="gpt-4o",
        model_provider="openai",
        active_stream_id=None,
        messages=[],
        context_messages=[],
        pending_user_message=None,
    )

    monkeypatch.setattr("web.api.routes.get_session", lambda sid: session)
    monkeypatch.setattr("web.api.routes.resolve_trusted_workspace", lambda value: r"C:\\workspace")
    monkeypatch.setattr(
        "web.api.routes._resolve_compatible_session_model_state",
        lambda requested_model, requested_provider: ("gpt-4o", "openai", False),
    )
    monkeypatch.setattr(
        "web.api.routes.resolve_active_provider_context",
        lambda: {"provider": "openai", "model": "gpt-4o"},
    )
    monkeypatch.setattr("web.api.routes._game_mode_guard_payload_for_model", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "web.api.routes._start_chat_stream_for_session",
        lambda *args, **kwargs: captured.update(kwargs) or {"stream_id": "stream-1"},
    )
    monkeypatch.setattr("web.api.routes.j", lambda handler, payload, status=200, extra_headers=None: payload)
    monkeypatch.setattr("web.api.goals.has_active_goal", lambda *args, **kwargs: True)
    monkeypatch.setattr("web.api.profiles.get_hermes_home_for_profile", lambda profile: tmp_path / "home")

    routes._handle_chat_start(
        SimpleNamespace(headers={}),
        {
            "session_id": "goal-session",
            "message": "implement the feature",
            "workspace": r"C:\\workspace",
            "model": "gpt-4o",
            "model_provider": "openai",
        },
    )

    assert captured["goal_related"] is True


def test_goal_command_kickoff_routes_nova_local_models_to_ollama_cloud_deepseek(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from types import SimpleNamespace
    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    captured = {}
    session = SimpleNamespace(
        session_id="goal-session",
        profile="default",
        workspace=r"C:\\workspace",
        workspace_slug="nova",
        model="qwen3:4b",
        model_provider="ollama",
        active_stream_id=None,
        messages=[],
        context_messages=[],
        pending_user_message=None,
    )

    monkeypatch.setattr("web.api.routes.get_session", lambda sid: session)
    monkeypatch.setattr("web.api.routes.resolve_trusted_workspace", lambda value: r"C:\\workspace")
    monkeypatch.setattr(
        "web.api.routes._resolve_compatible_session_model_state",
        lambda requested_model, requested_provider: ("qwen3:4b", "ollama", False),
    )
    monkeypatch.setattr(
        "web.api.goals.goal_command_payload",
        lambda *args, **kwargs: {"ok": True, "kickoff_prompt": "kick off the goal"},
    )
    monkeypatch.setattr("web.api.goals.goal_state_snapshot", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr("web.api.goals.restore_goal_state", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.api.profiles.get_hermes_home_for_profile", lambda profile: tmp_path / "home")
    monkeypatch.setattr(
        "web.api.routes._start_chat_stream_for_session",
        lambda *args, **kwargs: captured.update(kwargs) or {"stream_id": "stream-1"},
    )
    monkeypatch.setattr("web.api.routes.j", lambda handler, payload, status=200, extra_headers=None: payload)

    payload = routes._handle_goal_command(
        SimpleNamespace(headers={}),
        {
            "session_id": "goal-session",
            "args": "Ship it",
            "workspace": r"C:\\workspace",
            "workspace_slug": "nova",
            "profile": "default",
            "model": "qwen3:4b",
            "model_provider": "ollama",
        },
    )

    assert payload["stream_id"] == "stream-1"
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["model_provider"] == "ollama-cloud"
    assert captured["normalized_model"] is True


def test_plan_handlers_route_nova_local_models_to_ollama_cloud_deepseek(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from types import SimpleNamespace
    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    captured = []
    session = SimpleNamespace(
        session_id="goal-session",
        profile="default",
        workspace=r"C:\\workspace",
        workspace_slug="nova",
        model="qwen3:4b",
        model_provider="ollama",
        active_stream_id=None,
        messages=[],
        context_messages=[],
        pending_user_message=None,
    )

    monkeypatch.setattr("web.api.routes.get_session", lambda sid: session)
    monkeypatch.setattr("web.api.routes.resolve_trusted_workspace", lambda value: r"C:\\workspace")
    monkeypatch.setattr(
        "web.api.routes._start_chat_stream_for_session",
        lambda *args, **kwargs: captured.append(kwargs) or {"stream_id": f"stream-{len(captured)}"},
    )
    monkeypatch.setattr("web.api.routes.j", lambda handler, payload, status=200, extra_headers=None: payload)

    accept_payload = routes._handle_plan_accept(
        SimpleNamespace(headers={}),
        {
            "session_id": "goal-session",
            "workspace": r"C:\\workspace",
            "model": "qwen3:4b",
            "model_provider": "ollama",
        },
    )
    revise_payload = routes._handle_plan_revise(
        SimpleNamespace(headers={}),
        {
            "session_id": "goal-session",
            "workspace": r"C:\\workspace",
            "feedback": "tighten the plan",
            "model": "qwen3:4b",
            "model_provider": "ollama",
        },
    )

    assert accept_payload["stream_id"] == "stream-1"
    assert revise_payload["stream_id"] == "stream-2"
    assert captured[0]["model"] == "deepseek-v4-flash"
    assert captured[0]["model_provider"] == "ollama-cloud"
    assert captured[0]["normalized_model"] is True
    assert captured[1]["model"] == "deepseek-v4-flash"
    assert captured[1]["model_provider"] == "ollama-cloud"
    assert captured[1]["normalized_model"] is True


def test_start_chat_stream_marks_turns_goal_related_when_goal_is_active(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from types import SimpleNamespace
    import threading
    from contextlib import nullcontext
    from web.api import routes

    captured = {}
    started = threading.Event()
    session = SimpleNamespace(
        session_id="goal-session",
        profile="default",
        workspace=r"C:\\workspace",
        workspace_slug="color",
        model="gpt-4o",
        model_provider="openai",
        active_stream_id=None,
        pending_started_at=0.0,
        pending_user_message=None,
        pending_attachments=[],
        messages=[],
        save=lambda: None,
    )

    monkeypatch.setattr(
        "web.api.routes._get_session_agent_lock",
        lambda sid: nullcontext(),
    )
    monkeypatch.setattr("web.api.routes._prepare_chat_start_session_for_stream", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.api.routes.create_stream_channel", lambda: object())
    monkeypatch.setattr("web.api.routes.set_last_workspace", lambda workspace: None)
    monkeypatch.setattr("web.api.routes._run_agent_streaming", lambda *args, **kwargs: captured.update(kwargs) or started.set())
    monkeypatch.setattr("web.api.goals.has_active_goal", lambda *args, **kwargs: True)

    response = routes._start_chat_stream_for_session(
        session,
        msg="implement the feature",
        attachments=[],
        workspace=r"C:\\workspace",
        model="gpt-4o",
        model_provider="openai",
    )

    assert response["stream_id"]
    assert started.wait(1), "stream thread did not start"
    assert captured["goal_related"] is True
    assert response["stream_id"] in routes.STREAM_GOAL_RELATED


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
    def _fail_stream_status_check(stream_id, slug):
        raise AssertionError("space session listing must not synchronously check stream status")

    monkeypatch.setattr(web_server, "_stream_is_active_for_space", _fail_stream_status_check)

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


def test_session_load_hydrates_goal_banner_from_server_state():
    sessions_js = Path("web/static/sessions.js").read_text(encoding="utf-8")

    assert "Object.prototype.hasOwnProperty.call(data.session, 'goal')" in sessions_js
    assert "data.session.goal && typeof _updateGoalState === 'function'" in sessions_js
    assert "typeof _clearGoalState === 'function'" in sessions_js
    assert sessions_js.index("if (data.session && Object.prototype.hasOwnProperty.call(data.session, 'goal')) {") < sessions_js.index("const activeStreamId=S.session.active_stream_id||null;")


def test_goal_command_sends_workspace_slug_for_space_sessions():
    commands_js = Path("web/static/commands.js").read_text(encoding="utf-8")

    assert "function _goalCommandRequestBody(args)" in commands_js
    assert "workspace_slug: S.session.workspace_slug || S.session.space_slug || S.session.space || null" in commands_js
    assert "space: S.session.space || S.session.space_slug || S.session.workspace_slug || null" in commands_js
    assert commands_js.count("_goalCommandRequestBody(args)") == 3


def test_switch_kanban_board_does_not_restart_polling_twice():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    start = panels_js.index("async function switchKanbanBoard(slug){")
    end = panels_js.index("// ── Create / rename / archive board modals ─", start)
    body = panels_js[start:end]

    assert body.count("_kanbanStartPolling();") == 0
    assert "_kanbanEnsurePollingActive();" in body


def test_switch_kanban_board_commits_local_state_after_server_confirmation():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    start = panels_js.index("async function switchKanbanBoard(slug){")
    end = panels_js.index("function openKanbanCreateBoard()", start)
    body = panels_js[start:end]

    assert body.index("await api('/api/kanban/boards/'") < body.index("_kanbanCurrentBoard = newBoard;")
    assert body.index("await api('/api/kanban/boards/'") < body.index("_kanbanSetSavedBoard(slug);")
    assert "showToast((t('kanban_unavailable') || 'Kanban unavailable') + ': ' + (e.message || e), 'error');" in body


def test_load_kanban_ensures_polling_without_restarting_an_active_stream():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    start = panels_js.index("async function loadKanban(animate){")
    end = panels_js.index("function filterKanban()", start)
    body = panels_js[start:end]

    assert "_kanbanEnsurePollingActive();" in body
    assert "_kanbanStartPolling();" not in body


def test_kanban_board_resolution_uses_saved_hint_only_for_fallback_current():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    start = panels_js.index("async function loadKanbanBoards(spaceLoadKey){")
    end = panels_js.index("// Restrict board.color", start)
    body = panels_js[start:end]

    assert "const currentSource = (data && data.current_source) || 'explicit';" in body
    assert "const savedExists = !!(saved && boards.some(b => b.slug === saved));" in body
    assert "if (currentSource === 'fallback' && savedExists) {" in body
    assert "if (serverCurrent === 'default' && savedExists) {" not in body


def test_kanban_board_create_and_archive_do_not_restart_polling_after_reload():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")

    create_start = panels_js.index("if (mode === 'create') {")
    create_end = panels_js.index("} else if (mode === 'rename') {", create_start)
    create_body = panels_js[create_start:create_end]
    assert create_body.count("_kanbanStartPolling();") == 0
    assert "_kanbanEnsurePollingActive();" in create_body

    archive_start = panels_js.index("async function archiveKanbanBoard(){")
    archive_end = panels_js.index("function _selectedLogsFile()", archive_start)
    archive_body = panels_js[archive_start:archive_end]
    assert archive_body.count("_kanbanStartPolling();") == 1
    assert "_kanbanEnsurePollingActive();" in archive_body


def test_game_mode_chat_start_rejection_keeps_chat_history_clean():
    messages_js = Path("web/static/messages.js").read_text(encoding="utf-8")

    assert "function _gameModeWouldBlockClientModel(model, provider, spaceSlug)" in messages_js
    assert "function _gameModeAllowsNovaRemoteFallback(spaceSlug)" in messages_js
    assert "localProviders.has(p)" in messages_js
    assert "p.startsWith('custom:')&&localProviders.has(p.slice(7))" in messages_js
    assert "m.startsWith('@ollama:')" in messages_js
    assert "const cfg=window._activeSpaceConfig;" in messages_js
    assert "if(slug&&activeSpace&&slug!==activeSpace) return false;" in messages_js
    assert "cfg&&typeof cfg==='object'&&cfg.nova&&typeof cfg.nova==='object'&&cfg.nova.enabled" in messages_js
    assert "const selectedWorkspaceSlug=String(" in messages_js
    assert "selectedProvider=S.session&&S.session.model_provider||null" in messages_js
    assert "if(_gameModeWouldBlockClientModel(selectedModel,selectedProvider,selectedWorkspaceSlug))" in messages_js
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


def test_kanban_db_missing_env_vars_fall_back_to_default_home(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    for name in (
        "HERMES_HOME",
        "SIDEKICK_KANBAN_HOME",
        "SIDEKICK_KANBAN_BOARD",
        "SIDEKICK_KANBAN_DB",
        "SIDEKICK_KANBAN_WORKSPACES_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)

    from cli import kanban_db

    assert kanban_db.kanban_home() == tmp_path / "home"
    assert kanban_db.kanban_db_path() == tmp_path / "home" / "kanban.db"
    assert kanban_db.workspaces_root() == tmp_path / "home" / "kanban" / "workspaces"
    assert kanban_db.get_current_board() == "default"

    boards = kanban_db.list_boards(include_archived=False)
    assert boards and boards[0]["slug"] == "default"


def test_kanban_db_honors_legacy_hermes_kanban_home(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SIDEKICK_KANBAN_HOME", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "legacy-kanban"))
    monkeypatch.delenv("SIDEKICK_KANBAN_BOARD", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    monkeypatch.delenv("SIDEKICK_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("SIDEKICK_KANBAN_WORKSPACES_ROOT", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_WORKSPACES_ROOT", raising=False)

    from cli import kanban_db

    assert kanban_db.kanban_home() == tmp_path / "legacy-kanban"
    assert kanban_db.kanban_db_path() == tmp_path / "legacy-kanban" / "kanban.db"
    assert kanban_db.workspaces_root() == tmp_path / "legacy-kanban" / "kanban" / "workspaces"


def test_kanban_db_honors_legacy_board_and_direct_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SIDEKICK_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("SIDEKICK_KANBAN_BOARD", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "blog-sprint")
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "legacy-db.sqlite"))
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACES_ROOT", str(tmp_path / "legacy-workspaces"))

    from cli import kanban_db

    assert kanban_db.get_current_board() == "blog-sprint"
    assert kanban_db.kanban_db_path() == tmp_path / "legacy-db.sqlite"
    assert kanban_db.workspaces_root() == tmp_path / "legacy-workspaces"


def test_kanban_command_restores_board_override_env(monkeypatch):
    import os
    from types import SimpleNamespace

    from cli import kanban, kanban_db

    monkeypatch.delenv("SIDEKICK_KANBAN_BOARD", raising=False)
    monkeypatch.setattr(kanban_db, "board_exists", lambda slug: True)
    monkeypatch.setattr(kanban_db, "init_db", lambda *args, **kwargs: None)

    def _fake_list(args):
        assert os.environ["SIDEKICK_KANBAN_BOARD"] == "blog-sprint"
        return 0

    monkeypatch.setattr(kanban, "_cmd_list", _fake_list)

    result = kanban.kanban_command(SimpleNamespace(kanban_action="list", board="blog-sprint"))

    assert result == 0
    assert os.environ.get("SIDEKICK_KANBAN_BOARD") is None


def test_kanban_command_restores_both_board_override_envs(monkeypatch):
    import os
    from types import SimpleNamespace

    from cli import kanban, kanban_db

    monkeypatch.setenv("SIDEKICK_KANBAN_BOARD", "legacy-sidekick")
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "legacy-hermes")
    monkeypatch.setattr(kanban_db, "board_exists", lambda slug: True)
    monkeypatch.setattr(kanban_db, "init_db", lambda *args, **kwargs: None)

    def _fake_list(args):
        assert os.environ["SIDEKICK_KANBAN_BOARD"] == "blog-sprint"
        assert os.environ["HERMES_KANBAN_BOARD"] == "blog-sprint"
        return 0

    monkeypatch.setattr(kanban, "_cmd_list", _fake_list)

    result = kanban.kanban_command(SimpleNamespace(kanban_action="list", board="blog-sprint"))

    assert result == 0
    assert os.environ["SIDEKICK_KANBAN_BOARD"] == "legacy-sidekick"
    assert os.environ["HERMES_KANBAN_BOARD"] == "legacy-hermes"


def test_pin_kanban_board_env_mirrors_legacy_board_env(monkeypatch):
    import os

    from cli import main

    monkeypatch.delenv("SIDEKICK_KANBAN_BOARD", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "legacy-board")

    main._pin_kanban_board_env()

    assert os.environ["SIDEKICK_KANBAN_BOARD"] == "legacy-board"
    assert os.environ["HERMES_KANBAN_BOARD"] == "legacy-board"


def test_pin_kanban_board_env_normalizes_existing_sidekick_board_env(monkeypatch):
    import os

    from cli import main

    monkeypatch.setenv("SIDEKICK_KANBAN_BOARD", "primary-board")
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "legacy-board")

    main._pin_kanban_board_env()

    assert os.environ["SIDEKICK_KANBAN_BOARD"] == "primary-board"
    assert os.environ["HERMES_KANBAN_BOARD"] == "primary-board"


def test_kanban_bridge_conn_preserves_legacy_hermes_env(monkeypatch, tmp_path):
    import os

    from web.api import kanban_bridge

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.delenv("SIDEKICK_KANBAN_HOME", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_HOME", "legacy-root")
    monkeypatch.setattr(kanban_bridge, "_get_ws_kanban_home", lambda: str(workspace_root))

    class _FakeKB:
        def init_db(self, board=None):
            assert os.environ["SIDEKICK_KANBAN_HOME"] == str(workspace_root)
            assert os.environ["HERMES_KANBAN_HOME"] == str(workspace_root)

        def connect(self, board=None):
            return object()

    monkeypatch.setattr(kanban_bridge, "_kb", lambda: _FakeKB())

    conn = kanban_bridge._conn()

    assert conn is not None
    assert os.environ.get("SIDEKICK_KANBAN_HOME") is None
    assert os.environ.get("HERMES_KANBAN_HOME") == "legacy-root"


def test_dispatcher_kanban_home_helpers_set_and_clear_both_env_vars(monkeypatch):
    import os

    from web.api import dispatcher

    monkeypatch.delenv("SIDEKICK_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)

    dispatcher._set_space_kanban_home("space-root")

    assert os.environ["SIDEKICK_KANBAN_HOME"] == "space-root"
    assert os.environ["HERMES_KANBAN_HOME"] == "space-root"

    dispatcher._clear_kanban_home()

    assert os.environ.get("SIDEKICK_KANBAN_HOME") is None
    assert os.environ.get("HERMES_KANBAN_HOME") is None


def test_dispatcher_kanban_home_override_restores_previous_env(monkeypatch):
    from web.api import dispatcher

    monkeypatch.setenv("SIDEKICK_KANBAN_HOME", "original-sidekick")
    monkeypatch.setenv("HERMES_KANBAN_HOME", "original-hermes")

    with dispatcher._kanban_home_override("space-root"):
        assert dispatcher.os.environ["SIDEKICK_KANBAN_HOME"] == "space-root"
        assert dispatcher.os.environ["HERMES_KANBAN_HOME"] == "space-root"

    assert dispatcher.os.environ["SIDEKICK_KANBAN_HOME"] == "original-sidekick"
    assert dispatcher.os.environ["HERMES_KANBAN_HOME"] == "original-hermes"


def test_kanban_board_list_reports_fallback_current_source(monkeypatch):
    from types import SimpleNamespace
    from web.api import kanban_bridge

    class _FakeKB:
        DEFAULT_BOARD = "default"

        def __init__(self):
            self.cleared = False

        def list_boards(self, include_archived=False):
            return [
                {"slug": "default", "name": "Default"},
                {"slug": "blog-sprint", "name": "Blog Sprint"},
            ]

        def get_current_board(self):
            return "ghost-board"

        def clear_current_board(self):
            self.cleared = True

    fake = _FakeKB()
    monkeypatch.setattr(kanban_bridge, "_kb", lambda: fake)
    monkeypatch.setattr(kanban_bridge, "_board_counts_for_slug", lambda slug: {})

    payload = kanban_bridge._list_boards_payload(SimpleNamespace(query=""))

    assert fake.cleared is True
    assert payload["current"] == "default"
    assert payload["current_source"] == "fallback"
    assert payload["boards"][0]["is_current"] is True
    assert payload["boards"][1]["is_current"] is False


def test_kanban_board_list_preserves_explicit_current_source(monkeypatch):
    from types import SimpleNamespace
    from web.api import kanban_bridge

    class _FakeKB:
        DEFAULT_BOARD = "default"

        def __init__(self):
            self.cleared = False

        def list_boards(self, include_archived=False):
            return [
                {"slug": "default", "name": "Default"},
                {"slug": "blog-sprint", "name": "Blog Sprint"},
            ]

        def get_current_board(self):
            return "blog-sprint"

        def clear_current_board(self):
            self.cleared = True

    fake = _FakeKB()
    monkeypatch.setattr(kanban_bridge, "_kb", lambda: fake)
    monkeypatch.setattr(kanban_bridge, "_board_counts_for_slug", lambda slug: {})

    payload = kanban_bridge._list_boards_payload(SimpleNamespace(query=""))

    assert fake.cleared is False
    assert payload["current"] == "blog-sprint"
    assert payload["current_source"] == "explicit"
    assert payload["boards"][0]["is_current"] is False
    assert payload["boards"][1]["is_current"] is True


def test_mobile_settings_has_main_section_switcher():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")

    assert 'id="settingsSectionDropdown"' in index_html
    assert 'onchange="switchSettingsSection(this.value)"' in index_html
    assert ".settings-section-switcher{display:none" in style_css
    assert ".settings-section-switcher{display:block" in style_css
    assert "const dd=$('settingsSectionDropdown')" in panels_js


def test_settings_navigation_and_locale_labels_are_i18n_driven():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    i18n_js = Path("web/static/i18n.js").read_text(encoding="utf-8")

    assert 'data-i18n="settings_section_switcher_label"' in index_html
    assert 'data-i18n="settings_section_switcher_conversation"' in index_html
    assert 'data-i18n="settings_section_switcher_appearance"' in index_html
    assert 'data-i18n="settings_section_switcher_preferences"' in index_html
    assert 'data-i18n="settings_section_switcher_providers"' in index_html
    assert 'data-i18n="settings_section_switcher_plugins"' in index_html
    assert 'data-i18n="settings_section_switcher_system"' in index_html
    assert 'data-i18n="plugins_tab_title"' in index_html
    assert 'data-i18n="settings_dashboard_mode_label"' in index_html
    assert 'data-i18n="settings_dashboard_link_save"' in index_html
    assert 'data-i18n="settings_gateway_status_label"' in index_html
    assert 'data-i18n="settings_subagents_label"' in index_html

    assert "settings_section_switcher_label: 'Settings section'" in i18n_js
    assert "settings_section_switcher_label: 'Bereich auswählen'" in i18n_js
    assert "settings_section_switcher_providers: 'Providers'" in i18n_js
    assert "settings_section_switcher_providers: 'Anbieter'" in i18n_js
    assert "plugins_section_meta: 'Installed apps and plugin settings. Gmail can be connected per space here.'" in i18n_js
    assert "plugins_section_meta: 'Installierte Apps und Plugin-Einstellungen. Gmail kann hier pro Space verbunden werden.'" in i18n_js
    assert "settings_dashboard_mode_desc: 'Show a nav-rail link when the official sidekick dashboard is reachable. Overrides are restricted to loopback URLs.'" in i18n_js
    assert "settings_dashboard_mode_desc: 'Zeige einen Link in der Navigationsleiste an, wenn das offizielle Sidekick-Dashboard erreichbar ist. Überschreibungen sind auf Loopback-URLs beschränkt.'" in i18n_js
    assert "settings_tab_appearance: 'Darstellung'" in i18n_js
    assert "settings_tab_conversation: 'Konversation'" in i18n_js
    assert "settings_tab_preferences: 'Einstellungen'" in i18n_js


def test_desktop_sidebar_and_rightpanel_keep_flex_flow():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert ".sidebar{" in style_css
    assert ".rightpanel{" in style_css
    assert "@media(min-width:641px)" in style_css
    assert ".sidebar{position:relative;}" in style_css
    assert ".rightpanel{position:relative;}" in style_css
    assert "main.main{width:100%!important;flex:1 1 auto!important;}" not in style_css


def test_mobile_nav_click_closes_sidebar_and_keeps_hamburger_clickable():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert "opts.fromRailClick && typeof closeMobileSidebar === 'function'" in panels_js
    assert "typeof _isDesktopWidth === 'function' && !_isDesktopWidth()" in panels_js
    assert "closeMobileSidebar();" in panels_js
    assert "function _syncMobileSidebarInlineOffset(sidebar,open)" in Path("web/static/boot.js").read_text(encoding="utf-8")
    assert "sidebar.style.setProperty('left','-300px','important')" in Path("web/static/boot.js").read_text(encoding="utf-8")
    assert "sidebar.style.setProperty('transform',open?'translate3d(300px,0,0)':'none','important')" in Path("web/static/boot.js").read_text(encoding="utf-8")
    assert "sidebar.style.removeProperty('left')" in Path("web/static/boot.js").read_text(encoding="utf-8")
    assert ".app-titlebar{display:flex;align-items:center;justify-content:flex-start;height:38px;flex-shrink:0;background:var(--sidebar);border-bottom:1px solid var(--border);padding:0 12px;padding-top:var(--app-titlebar-safe-top);padding-left:max(12px,env(safe-area-inset-left,0));padding-right:max(12px,env(safe-area-inset-right,0));box-sizing:content-box;font-size:12px;color:var(--muted);user-select:none;-webkit-app-region:drag;position:relative;z-index:20;}" in style_css
    assert ".app-titlebar-hamburger{-webkit-app-region:no-drag;align-items:center;justify-content:center;background:none;border:none;color:var(--muted);border-radius:8px;cursor:pointer;padding:0;-webkit-tap-highlight-color:transparent;transition:background-color .15s,color .15s;}" in style_css
    assert "z-index:220!important;" not in style_css
    assert "z-index:221!important;" not in style_css


def test_mobile_sidebar_and_workspace_panel_are_mutually_exclusive():
    boot_js = Path("web/static/boot.js").read_text(encoding="utf-8")

    workspace_start = boot_js.index("function _setWorkspacePanelMode(mode)")
    workspace_end = boot_js.index("function syncWorkspacePanelState()", workspace_start)
    workspace_body = boot_js[workspace_start:workspace_end]

    toggle_start = boot_js.index("function toggleMobileSidebar()")
    close_start = boot_js.index("function closeMobileSidebar()", toggle_start)
    toggle_body = boot_js[toggle_start:close_start]

    assert "if(open&&typeof closeMobileSidebar==='function') closeMobileSidebar();" in workspace_body
    assert "_isCompactWorkspaceViewport()" in toggle_body
    assert "typeof _setWorkspacePanelMode==='function'" in toggle_body
    assert "_setWorkspacePanelMode('closed')" in toggle_body


def test_titlebar_center_stays_desktop_aligned():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert ".app-titlebar-center{position:absolute;left:calc(50% + (var(--workspace-sidebar-width) - var(--workspace-rightpanel-width)) / 2);top:50%;transform:translate(-50%,-50%);display:flex;align-items:center;justify-content:center;gap:8px;min-width:0;max-width:220px;-webkit-app-region:no-drag;z-index:8;}" in style_css
    assert ".titlebar-space-spacer {" in style_css
    assert "width: 60px;" in style_css
    assert ".titlebar-space {" in style_css
    assert "margin-right: 2px;" in style_css
    assert "--space-color: var(--accent, #7c5cfc);" in style_css
    assert "max-width: 184px;" in style_css
    assert "overflow: visible;" in style_css
    assert "transition: max-width .18s ease;" in style_css
    assert ".titlebar-space-name {" in style_css
    assert "max-width: 120px;" in style_css
    assert "opacity: 1;" in style_css
    assert "font-weight: 500;" in style_css
    assert "color: var(--space-color);" in style_css
    assert "transition: max-width .18s ease, opacity .12s ease, margin .18s ease;" in style_css
    assert ".titlebar-space:hover .titlebar-space-name" not in style_css
    assert ".titlebar-space-name{margin-left:2px;}" in style_css


def test_titlebar_actions_remain_visible_on_desktop():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert ".titlebar-actions{display:flex;align-items:center;gap:2px;margin-right:4px;-webkit-app-region:no-drag;flex-shrink:0;position:relative;z-index:6;}" in style_css
    assert ".titlebar-actions #btnCastToggle," in style_css
    assert ".titlebar-actions #btnRebootSidekick," in style_css
    assert ".titlebar-actions #btnShutdownSidekick{display:inline-flex!important;}" in style_css
    assert ".titlebar-actions:hover #btnCastToggle," not in style_css


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


def test_gmail_refresh_reruns_pending_folder_or_filter_change():
    gmail_js = Path("web/static/gmail.js").read_text(encoding="utf-8")

    refresh_start = gmail_js.index("async function gmailRefresh()")
    render_start = gmail_js.index("// ── Render inbox list", refresh_start)
    refresh_body = gmail_js[refresh_start:render_start]

    assert "let _gmailRefreshSeq = 0;" in gmail_js
    assert "let _gmailRefreshPending = false;" in gmail_js
    assert "if (GMAIL.loading)" in refresh_body
    assert "_gmailRefreshPending = true;" in refresh_body
    assert "const refreshSeq = ++_gmailRefreshSeq;" in refresh_body
    assert "const requestedFolder = GMAIL.currentFolder;" in refresh_body
    assert "const requestedFilter = GMAIL.currentFilter || 'all';" in refresh_body
    assert "encodeURIComponent(requestedFolder)" in refresh_body
    assert "refreshSeq !== _gmailRefreshSeq" in refresh_body
    assert "requestedFolder !== GMAIL.currentFolder" in refresh_body
    assert "requestedFilter !== (GMAIL.currentFilter || 'all')" in refresh_body
    assert "if (_gmailRefreshPending)" in refresh_body
    assert "gmailRefresh();" in refresh_body[refresh_body.index("if (_gmailRefreshPending)") :]


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


def test_websearch_toggles_expose_pressed_state():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")

    assert 'class="websearch-mode-btn is-active" data-mode="quick"' in index_html
    assert 'aria-pressed="true">⚡ Quick Search</button>' in index_html
    assert 'class="websearch-mode-btn" data-mode="deep"' in index_html
    assert 'aria-pressed="false">🧠 Deep Research</button>' in index_html
    assert 'id="websearchSplitBtn"' in index_html
    assert 'aria-pressed="false">□ Split</button>' in index_html

    mode_start = browser_js.index("function websearchToggleMode(mode)")
    history_start = browser_js.index("// ── History Sidebar Toggle", mode_start)
    mode_body = browser_js[mode_start:history_start]
    split_start = browser_js.index("function websearchToggleSplit()")
    quick_start = browser_js.index("// ── Quick Search", split_start)
    split_body = browser_js[split_start:quick_start]

    assert "const active = b.dataset.mode === mode;" in mode_body
    assert "b.setAttribute('aria-pressed', active ? 'true' : 'false');" in mode_body
    assert "btn.setAttribute('aria-pressed', _websearchSplitOpen ? 'true' : 'false');" in split_body


def test_compact_layout_toggle_exposes_pressed_state():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")

    assert 'id="compactToggleBtn"' in index_html
    assert 'aria-label="Compact layout"' in index_html
    assert 'aria-pressed="false"' in index_html
    assert "workflowRunHeaderAction('compact-layout')" in index_html

    toggle_start = ui_js.index("function toggleCompactLayout()")
    init_start = ui_js.index("function _initCompactLayout()")
    context_start = ui_js.index("/*", init_start + 1)
    toggle_body = ui_js[toggle_start:init_start]
    init_body = ui_js[init_start:context_start]

    expected = "btn.setAttribute('aria-pressed',active?'true':'false');"
    assert expected in toggle_body
    assert expected in init_body


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
    for match in re.finditer(r"<button[^>]+class=\"nav-tab[^\"]*\"[^>]*>", sidebar_nav_html):
        button = match.group(0)
        assert 'aria-label="' in button or 'data-label="' in button


def test_space_dropdown_selection_uses_delegated_container_click():
    spaces_js = Path("web/static/spaces.js").read_text(encoding="utf-8")

    assert "function _bindSpaceDropdownSelection(dd)" in spaces_js
    assert "dd.dataset.boundSpaceSelection === '1'" in spaces_js
    assert "ev.target.closest('[data-space-slug]')" in spaces_js
    assert "const slug = String(item.dataset.spaceSlug || '').trim();" in spaces_js
    assert "closeSpaceDropdowns();" in spaces_js
    assert "const runSelect = () => selectSpace(slug);" in spaces_js
    assert "_bindSpaceDropdownSelection(dd);" in spaces_js[spaces_js.index("function _openSpaceDropdown") :]


def test_desktop_sidebar_nav_does_not_use_mobile_touch_compaction():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert ".sidebar-nav{overflow-x:auto!important;overflow-y:hidden!important;" not in style_css
    assert "scrollbar-width:none;-webkit-overflow-scrolling:touch;" not in style_css
    assert ".sidebar-nav::-webkit-scrollbar{display:none;}" not in style_css
    assert ".sidebar-nav .nav-tab:not(.nav-tab-space){flex:0 0 44px!important;" not in style_css
    assert ".sidebar-nav .nav-tab-space{flex:0 0 64px!important;" not in style_css


def test_desktop_rail_owns_vertical_overflow():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert ".rail{display:none;width:48px;" in style_css
    assert "min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior-y:contain;scrollbar-width:none;" in style_css
    assert ".rail::-webkit-scrollbar{display:none;}" in style_css


def test_short_desktop_rail_compacts_navigation_buttons():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert "@media(min-width:641px) and (max-height:760px)" in style_css
    assert ".rail{gap:2px;padding:6px 0;}" in style_css
    assert ".rail-btn{width:34px;height:34px;min-height:34px;}" in style_css
    assert ".rail-spacer{min-height:4px;}" in style_css
    assert ".rail-separator{margin:2px auto;}" in style_css
    assert 'html[data-rail-expanded="1"] .rail{padding:6px 8px;}' in style_css
    assert 'html[data-rail-expanded="1"] .rail-btn{height:34px;min-height:34px;}' in style_css


def test_mobile_open_sidebar_layering_rules_stay_scoped_to_mobile_media():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert "@media(max-width:640px)" in style_css
    assert ".sidebar.mobile-open{left:0;}" in style_css
    assert ".rightpanel.mobile-open{right:0!important;box-shadow:-4px 0 24px rgba(0,0,0,.4)!important;}" in style_css


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


def test_mobile_composer_config_layering_rules_were_removed():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert ".composer-wrap:has(.composer-mobile-config-panel.open){z-index:240!important;}" not in style_css


def test_browser_drawer_open_renders_as_fixed_bottom_sheet():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert "body.browser-drawer-open:not(.browser-maximized) .browser-drawer {" in style_css
    bottom_sheet = style_css[
        style_css.index("body.browser-drawer-open:not(.browser-maximized) .browser-drawer {") :
        style_css.index(".browser-drawer-shell {", style_css.index("body.browser-drawer-open:not(.browser-maximized) .browser-drawer {"))
    ]
    assert "position: fixed;" in bottom_sheet
    assert "bottom: calc(12px + env(safe-area-inset-bottom,0px));" in bottom_sheet
    assert "width: min(760px, calc(100vw - 32px));" in bottom_sheet
    assert "height: min(52vh, 560px);" in bottom_sheet
    assert "max-height: 560px !important;" in bottom_sheet
    assert "opacity: 1 !important;" in bottom_sheet
    assert "transition: none;" in bottom_sheet
    assert "z-index: 260;" in bottom_sheet

    mobile_drawer = style_css[
        style_css.index("@media (max-width: 760px)") :
        style_css.index("/* \u00e2\u201d\u20ac\u00e2\u201d\u20ac Rightpanel: Agents Dashboard", style_css.index("@media (max-width: 760px)"))
    ]
    assert "body.browser-drawer-open:not(.browser-maximized) .browser-drawer {" in mobile_drawer
    assert "width: 100vw;" in mobile_drawer
    assert "height: min(46vh, 380px);" in mobile_drawer
    assert "max-height: 380px !important;" in mobile_drawer
    assert "body.browser-drawer-open:not(.browser-maximized) .browser-drawer-shell {" in mobile_drawer
    assert "height: min(46vh, 380px);" in mobile_drawer


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
    assert "if(!hasSession&&!hasPreview){" in boot_js
    assert "emptyEl.textContent=typeof t==='function'?t('workspace_empty_no_path'):'No workspace selected.';" in boot_js
    assert "fileTree.innerHTML='';" in boot_js
    assert "fileTree.style.display='none';" in boot_js
    assert "openWorkspacePanel(nextMode,{force:true});" in boot_js
    assert "window.toggleFileTreePanel=function(force){return toggleWorkspacePanel(force);};" in boot_js


def test_workspace_load_errors_render_panel_error_state():
    workspace_js = Path("web/static/workspace.js").read_text(encoding="utf-8")
    i18n_js = Path("web/static/i18n.js").read_text(encoding="utf-8")

    catch_start = workspace_js.index("  }catch(e){", workspace_js.index("async function loadDir(path)"))
    load_dir_end = workspace_js.index("async function _refreshGitBadge", catch_start)
    catch_body = workspace_js[catch_start:load_dir_end]

    assert "$('wsEmptyState')" in catch_body
    assert "t('workspace_load_failed')" in catch_body
    assert "emptyEl.style.display = 'flex';" in catch_body
    assert "box.innerHTML = '';" in catch_body
    assert "box.style.display = 'none';" in catch_body
    assert "workspace_load_failed: 'Could not load this workspace.'" in i18n_js
    assert "workspace_load_failed: 'Dieser Workspace konnte nicht geladen werden.'" in i18n_js


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


def test_browser_panel_activation_swaps_full_view():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")

    assert 'id="mainBrowser"' in index_html
    assert 'data-panel="browser"' in index_html
    assert "const browserMain = document.getElementById('mainBrowser');" in panels_js
    assert "if (nextPanel === 'browser') {" in panels_js
    assert "if (chatMain) chatMain.style.display = 'none';" in panels_js
    assert "if (browserMain) browserMain.style.display = '';" in panels_js
    assert "if (browserMain) browserMain.style.display = 'none';" in panels_js
    assert "browserResearchPanelDeactivated();" in panels_js


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


def test_discord_fastapi_admin_reads_from_gateway_when_available(monkeypatch):
    from cli import web_server

    calls = []

    def fake_discord_api(method, path, data=None):
        calls.append((method, path, data))
        if path.endswith("/roles"):
            return [{"id": "role-1", "name": "Moderators"}]
        if path.endswith("/members?limit=2"):
            return [{"user": {"id": "user-1", "username": "Ada"}}]
        raise AssertionError(path)

    monkeypatch.setattr(web_server, "_DISCORD_GATEWAY_AVAILABLE", True)
    monkeypatch.setattr(web_server, "_discord_api", fake_discord_api, raising=False)

    client = TestClient(web_server.app)
    headers = {"X-Hermes-Session-Token": web_server._SESSION_TOKEN}
    roles = client.get("/api/discord/guilds/guild-1/roles", headers=headers)
    members = client.get("/api/discord/guilds/guild-1/members?limit=2", headers=headers)

    assert roles.status_code == 200
    assert roles.json() == {"roles": [{"id": "role-1", "name": "Moderators"}]}
    assert members.status_code == 200
    assert members.json() == {"members": [{"user": {"id": "user-1", "username": "Ada"}}]}
    assert calls == [
        ("GET", "/guilds/guild-1/roles", None),
        ("GET", "/guilds/guild-1/members?limit=2", None),
    ]


def test_discord_fastapi_admin_updates_member_roles_when_available(monkeypatch):
    from cli import web_server

    calls = []

    def fake_discord_api(method, path, data=None):
        calls.append((method, path, data))
        return {"status": 204}

    monkeypatch.setattr(web_server, "_DISCORD_GATEWAY_AVAILABLE", True)
    monkeypatch.setattr(web_server, "_discord_api", fake_discord_api, raising=False)

    client = TestClient(web_server.app)
    response = client.put(
        "/api/discord/members/member-1/roles",
        json={
            "guild_id": "guild-1",
            "add_role_ids": ["role-add"],
            "remove_role_ids": ["role-remove"],
        },
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "updated": {"added": ["role-add"], "removed": ["role-remove"]}}
    assert calls == [
        ("PUT", "/guilds/guild-1/members/member-1/roles/role-add", None),
        ("DELETE", "/guilds/guild-1/members/member-1/roles/role-remove", None),
    ]


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


def test_appstore_mail_contract_exposes_builtin_mail_app_and_setup_flow():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    appstore_js = panels_js[panels_js.index("const _APPSTORE_FALLBACK_APPS = ["):]

    assert "key: 'imap-mail'" in appstore_js
    assert "Mail einrichten" in panels_js
    assert "function _appstoreOpenMailSettings()" in panels_js
    assert "function _appstoreSaveMailSettings(" in panels_js
    assert "api('/api/mail/setup'" in panels_js
    assert "_appstoreSyncMailButtons()" in panels_js


def test_appstore_mail_setup_prefill_uses_default_inbox_when_present():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    setup_start = panels_js.index("async function _appstoreOpenMailSettings() {")
    save_start = panels_js.index("async function _appstoreSaveMailSettings(", setup_start)
    setup_block = panels_js[setup_start:save_start]

    assert re.search(
        r"currentConfig\.inboxes\.find\(\s*(?:\(inbox\)|inbox)\s*=>\s*(?:inbox\s*&&\s*)?inbox\.default",
        setup_block,
    )
    assert "currentConfig.inboxes.length > 0 ? currentConfig.inboxes[0] : {}" not in setup_block


def test_appstore_setup_overlay_uses_fullscreen_modal_shell():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert re.search(
        r"\.appstore-overlay,\.appstore-setup-overlay\{\s*position:fixed;inset:0;z-index:10000;\s*display:flex;align-items:center;justify-content:center;\s*background:rgba\(0,0,0,0\.5\);backdrop-filter:blur\(4px\);\s*animation:appstoreFadeIn \.15s ease-out;\s*\}",
        style_css,
    )
    assert re.search(
        r"\.appstore-overlay-closing,\.appstore-setup-overlay-closing\{\s*opacity:0;transition:opacity \.2s;\s*\}",
        style_css,
    )
    assert "@media(max-width:600px)" in style_css
    assert re.search(r"\.appstore-overlay,\.appstore-setup-overlay\{\s*align-items:stretch;padding:0;\s*\}", style_css)


def test_mail_panel_prefers_current_inbox_then_default_then_first():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    start = panels_js.index("async function loadMailPanel() {")
    save = panels_js.index("async function _mailSwitchInbox(", start)
    block = panels_js[start:save]

    assert "_currentMailInboxId" in block
    assert "inboxes.find(i => i.id === _currentMailInboxId)" in block
    assert "inboxes.find(i=>i.default)" in block
    assert "_mailSwitchInbox(selectedInbox.id);" in block


def test_appstore_mail_keeps_special_ui_state_even_when_installed():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    setup_start = panels_js.index("async function _appstoreOpenMailSettings() {")
    setup_fn = panels_js[setup_start:setup_start + 2600]

    assert "mailAppEmail" in setup_fn
    assert "mailAppPassword" in setup_fn
    assert "mailAppAccountId" not in setup_fn
    assert "mailAppLabel" not in setup_fn
    assert "mailAppActivate" not in setup_fn


def test_appstore_imap_mail_manifest_matches_auto_setup_contract():
    from web.api._home import get_webui_home

    manifest_path = get_webui_home() / "appstore" / "imap-mail.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["key"] == "imap-mail"
    assert manifest["name"] == "Mail"
    assert "automatisch" in manifest["desc"].lower()
    assert "mail.json" in manifest["fullDesc"].lower()
    assert manifest["setup_steps"] == []


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
    assert 'os.getenv("SIDEKICK_CAST_API_HOST", "").strip()' in routes_py


def test_cast_status_without_config_reports_default_host_when_cockpit_unavailable(monkeypatch):
    from web.api import routes

    captured = {}

    def fake_j(handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status

    def fail_urlopen(*args, **kwargs):
        raise TimeoutError("not running")

    monkeypatch.delenv("SIDEKICK_CAST_API_HOST", raising=False)
    monkeypatch.delenv("HERMES_CAST_API_HOST", raising=False)
    monkeypatch.setattr(routes, "j", fake_j)
    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)

    routes._handle_cast_proxy(object(), "/api/cast/status", "GET")

    assert captured["status"] == 200
    assert captured["payload"]["available"] is False
    assert captured["payload"]["active"] is False
    assert captured["payload"]["configured"] is False
    assert captured["payload"]["host"] == "http://127.0.0.1:8765"
    assert captured["payload"]["dashboard_url"] == "http://127.0.0.1:8765"
    assert "launch_available" in captured["payload"]
    assert "not configured" in captured["payload"]["detail"]


def test_cast_status_without_config_uses_local_cockpit_when_available(monkeypatch):
    from web.api import routes

    captured = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"active": true}'

    def fake_j(handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status

    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append((req.full_url, timeout))
        return Resp()

    monkeypatch.delenv("SIDEKICK_CAST_API_HOST", raising=False)
    monkeypatch.delenv("HERMES_CAST_API_HOST", raising=False)
    monkeypatch.setattr(routes, "j", fake_j)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    routes._handle_cast_proxy(object(), "/api/cast/status", "GET")

    assert captured["status"] == 200
    assert captured["payload"]["active"] is True
    assert captured["payload"]["available"] is True
    assert captured["payload"]["configured"] is True
    assert captured["payload"]["host"] == "http://127.0.0.1:8765"
    assert captured["payload"]["dashboard_url"] == "http://127.0.0.1:8765"
    assert seen == [("http://127.0.0.1:8765/api/cast/status", 0.75)]


def test_cast_toggle_without_config_starts_local_cockpit(monkeypatch, tmp_path):
    from web.api import routes

    captured = {}
    launcher = tmp_path / "launch_cockpit.py"
    launcher.write_text("print('ok')", encoding="utf-8")

    class Proc:
        pid = 4242

    def fake_j(handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status

    def fake_urlopen(*args, **kwargs):
        raise TimeoutError("not running")

    popen_calls = []

    def fake_popen(args, **kwargs):
        popen_calls.append((args, kwargs))
        return Proc()

    monkeypatch.delenv("SIDEKICK_CAST_API_HOST", raising=False)
    monkeypatch.delenv("HERMES_CAST_API_HOST", raising=False)
    monkeypatch.setenv("SIDEKICK_COCKPIT_LAUNCHER", str(launcher))
    monkeypatch.setattr(routes, "j", fake_j)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(routes.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(routes, "_wait_for_cockpit_ready", lambda host: True)

    routes._handle_cast_proxy(object(), "/api/cast/toggle", "POST")

    assert captured["status"] == 200
    assert captured["payload"]["configured"] is True
    assert captured["payload"]["available"] is True
    assert captured["payload"]["started"] is True
    assert captured["payload"]["host"] == "http://127.0.0.1:8765"
    assert captured["payload"]["dashboard_url"] == "http://127.0.0.1:8765"
    assert popen_calls
    assert popen_calls[0][0] == [sys.executable, str(launcher)]


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


def test_cast_start_configured_host_does_not_toggle_active_cast(monkeypatch):
    from web.api import routes

    captured = {}

    def fake_j(handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status

    monkeypatch.setenv("SIDEKICK_CAST_API_HOST", "http://127.0.0.1:8765/")
    monkeypatch.setattr(routes, "j", fake_j)
    monkeypatch.setattr(
        routes,
        "_cast_read_json",
        lambda url, method="GET", timeout=2.5: {"active": True, "available": True},
    )
    monkeypatch.setattr(
        routes,
        "_forward_cast_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("active cast must not be toggled off")),
    )

    routes._handle_cast_start_proxy(object())

    assert captured["status"] == 200
    assert captured["payload"]["active"] is True
    assert captured["payload"]["available"] is True
    assert captured["payload"]["configured"] is True
    assert captured["payload"]["host"] == "http://127.0.0.1:8765"
    assert captured["payload"]["dashboard_url"] == "http://127.0.0.1:8765"


def test_cast_start_configured_host_toggles_only_when_inactive(monkeypatch):
    from web.api import routes

    forwarded = {}

    monkeypatch.setenv("SIDEKICK_CAST_API_HOST", "http://127.0.0.1:8765/")
    monkeypatch.setattr(
        routes,
        "_cast_read_json",
        lambda url, method="GET", timeout=2.5: {"active": False, "available": True},
    )

    def fake_forward(handler, host, endpoint, method):
        forwarded["host"] = host
        forwarded["endpoint"] = endpoint
        forwarded["method"] = method
        return True

    monkeypatch.setattr(routes, "_forward_cast_request", fake_forward)

    assert routes._handle_cast_start_proxy(object()) is True
    assert forwarded == {
        "host": "http://127.0.0.1:8765",
        "endpoint": "/api/cast/toggle",
        "method": "POST",
    }


def test_cast_autostart_reuses_running_local_cockpit_without_launch(monkeypatch):
    from web.api import routes

    captured = {}

    def fake_j(handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status

    monkeypatch.delenv("SIDEKICK_CAST_API_HOST", raising=False)
    monkeypatch.delenv("HERMES_CAST_API_HOST", raising=False)
    monkeypatch.setattr(routes, "j", fake_j)
    monkeypatch.setattr(routes, "_cockpit_launch_available", lambda: True)
    monkeypatch.setattr(
        routes,
        "_cast_read_json",
        lambda url, method="GET", timeout=2.5: {"active": True, "available": True},
    )
    monkeypatch.setattr(
        routes,
        "_start_cockpit_launcher",
        lambda: (_ for _ in ()).throw(AssertionError("running cockpit must not be launched again")),
    )

    routes._handle_cast_autostart(object())

    assert captured["status"] == 200
    assert captured["payload"]["active"] is True
    assert captured["payload"]["available"] is True
    assert captured["payload"]["configured"] is True
    assert captured["payload"]["host"] == "http://127.0.0.1:8765"
    assert captured["payload"]["dashboard_url"] == "http://127.0.0.1:8765"


def test_boot_uses_realistic_metadata_timeouts():
    boot_js = Path("web/static/boot.js").read_text(encoding="utf-8")
    sessions_js = Path("web/static/sessions.js").read_text(encoding="utf-8")
    spaces_js = Path("web/static/spaces.js").read_text(encoding="utf-8")

    assert "_bootTimeout(api('/api/settings'),20000,'settings')" in boot_js
    assert "_bootTimeout(api('/api/profile/active'),20000,'active profile')" in boot_js
    assert "_bootTimeout(loadWorkspaceList(),10000,'workspace list')" in boot_js
    assert "_bootTimeout(_loadActiveSpaceConfig(),8000,'space config')" in boot_js
    assert "_bootTimeout(loadOnboardingWizard(),8000,'onboarding')" in boot_js
    assert "if (saved && !_bootMissingSession &&" in boot_js
    assert "suppressMissingSessionMessage ? {logError:false} : undefined" in sessions_js
    assert "_withSpaceTimeout(api('/api/spaces'), 20000, 'load spaces')" in spaces_js


def test_visible_static_ui_text_is_not_mojibake():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    assert "??" not in index_html
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")
    spaces_js = Path("web/static/spaces.js").read_text(encoding="utf-8")
    i18n_js = Path("web/static/i18n.js").read_text(encoding="utf-8")
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")
    commands_js = Path("web/static/commands.js").read_text(encoding="utf-8")
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    assert "📺" in index_html
    assert "📁" in index_html
    assert "🎯 Set Goal" in index_html
    assert 'yolo-pill-icon" aria-hidden="true">⚡<' in index_html
    assert "🤖 Eigenen Agenten erstellen" in index_html
    assert "✉️ Verfassen" in index_html
    assert "📧 Mail" in index_html
    assert "💬 Discord" in index_html
    assert "btn.setAttribute('aria-expanded', dd.hidden ? 'false' : 'true')" in i18n_js
    assert "btn.setAttribute('aria-expanded', 'false')" in i18n_js
    assert "return `  /${c.name}${usage} — ${c.desc}`;" in commands_js
    assert "const bullet=trimmed.match(/^(?:[-*•]|\\\\d+\\\\.)\\\\s+(.*)$/);" in commands_js
    assert commands_js.count("Running execute_code…") == 2
    assert commands_js.count("Generating image…") == 2
    assert commands_js.count("↩ ${t('undid_n_messages')} ${r.removed_count} ${t('undid_messages_suffix')}") == 2
    assert commands_js.count("meta.join(' · ')") == 2
    assert commands_js.count("const BRAIN='🧠';") == 2
    assert commands_js.count(" · display: ") == 2
    assert "Reasoning effort set to " in ui_js
    assert "Failed to set effort" in ui_js
    assert "?? Reasoning effort set to " not in ui_js
    assert "?? Failed to set effort" not in ui_js
    assert " · allowed: " in ui_js

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


def test_unconfigured_cast_status_keeps_hub_button_visible():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    cast_js = ui_js[ui_js.index("let _castActive=false;") : ui_js.index("function _initDashboardLinkProbe()")]

    cast_button_id = index_html.index('id="btnCastToggle"')
    cast_button_start = index_html.rfind("<button", 0, cast_button_id)
    cast_button_end = index_html.index("</button>", cast_button_start)
    cast_button = index_html[cast_button_start:cast_button_end]

    assert 'style="display:none"' not in cast_button
    assert "cast-unavailable" in cast_button
    assert "let _castConfigured=true;" in ui_js
    assert "let _castHost='';" in ui_js
    assert "s.configured!==false" in ui_js
    assert "if(!_castConfigured)_cleanupCastTimers()" not in cast_js
    assert "Hub Cast nicht konfiguriert" in ui_js
    assert "function openHubCastDashboard()" in ui_js
    assert "window.openHubCastDashboard=openHubCastDashboard;" in ui_js
    assert "window.toggleHubCast=toggleHubCast;" in ui_js
    assert "window.open(url,'_blank','noopener,noreferrer')" in ui_js
    assert "_castFetch('/api/cast/start',{method:'POST'},10000)" in ui_js
    assert "_castFetch('/api/cast/toggle',{method:'POST'})" not in ui_js
    assert "btn.style.display='none'" not in cast_js


def test_hub_cast_monitor_starts_immediately_and_retries_every_15_seconds():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    cast_js = ui_js[ui_js.index("let _castActive=false;") : ui_js.index("function _initDashboardLinkProbe()")]

    assert "const CAST_RECONNECT_INTERVAL_MS=15000;" in cast_js
    assert "let _castConnectPromise=null;" in cast_js
    assert "function _startHubCastMonitor()" in cast_js
    assert "_ensureHubCastConnected({interactive:false});" in cast_js
    assert "setInterval(()=>_ensureHubCastConnected({interactive:false}),CAST_RECONNECT_INTERVAL_MS)" in cast_js
    assert "setTimeout(_refreshCastStatus,2000)" not in cast_js


def test_hub_cast_connect_is_single_flight_and_auto_failures_are_silent():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    cast_js = ui_js[ui_js.index("let _castActive=false;") : ui_js.index("function _initDashboardLinkProbe()")]

    assert "if(_castConnectPromise)return _castConnectPromise;" in cast_js
    assert "_castFetch('/api/cast/start',{method:'POST'},10000)" in cast_js
    assert "if(!_castActive&&_castInteractiveRequested&&typeof showToast==='function')" in cast_js
    assert "const connected=await _ensureHubCastConnected({interactive:true});" in cast_js
    assert "if(connected)openHubCastDashboard();" in cast_js


def test_manual_hub_connect_escalates_an_inflight_auto_attempt_to_interactive():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    cast_js = ui_js[ui_js.index("let _castActive=false;") : ui_js.index("function _initDashboardLinkProbe()")]

    interactive_mark = cast_js.index("if(interactive)_castInteractiveRequested=true;")
    single_flight_return = cast_js.index("if(_castConnectPromise)return _castConnectPromise;")

    assert interactive_mark < single_flight_return
    assert "if(!_castActive&&_castInteractiveRequested&&typeof showToast==='function')" in cast_js
    assert "if(_castInteractiveRequested&&typeof showToast==='function')showToast(_castLastError,'error');" in cast_js


def test_inline_titlebar_and_mobile_handlers_are_exported():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")

    assert 'id="btnCastToggle"' in index_html
    assert 'onclick="toggleHubCast()"' in index_html
    assert "window.toggleHubCast=toggleHubCast;" in ui_js
    assert "window.openHubCastDashboard=openHubCastDashboard;" in ui_js

    assert 'id="composerMobileConfigBtn"' in index_html
    assert 'onclick="toggleMobileComposerConfig()"' in index_html
    assert "window.toggleMobileComposerConfig=toggleMobileComposerConfig;" in ui_js
    assert "window.closeMobileComposerConfig=closeMobileComposerConfig;" in ui_js


def test_show_toast_accepts_legacy_type_second_argument():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")
    show_toast = ui_js[ui_js.index("function showToast(msg,ms,type)") : ui_js.index("const APP_DIALOG")]

    assert "if(typeof ms==='string')" in show_toast
    assert "const legacyType=ms;" in show_toast
    assert "const legacyDuration=typeof type==='number'?type:null;" in show_toast
    assert "type=legacyType;" in show_toast
    assert "ms=legacyDuration;" in show_toast
    assert "const duration=(ms==null)?(t==='error'?TOAST_ERROR_DEFAULT_MS:TOAST_DEFAULT_MS):ms;" in show_toast


def test_main_view_headers_do_not_overflow_from_actions_or_hidden_tooltips():
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert ".main-view-header > :first-child{min-width:0;max-width:100%;}" in style_css
    assert ".main-view-actions{display:flex;align-items:center;justify-content:flex-end;gap:4px;flex:0 1 auto;min-width:0;max-width:100%;overflow:hidden;}" in style_css
    assert ".has-tooltip:not(:hover):not(:focus-visible)::after{content:none;}" in style_css
    assert ".todos-main-header > :first-child{min-width:0;}" in style_css
    assert ".todos-main-header{align-items:flex-start;flex-wrap:wrap;}" in style_css
    assert ".todos-main-header .main-view-actions{width:100%;justify-content:flex-start;}" in style_css


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


def test_game_mode_chat_start_routes_nova_local_models_to_ollama_cloud_deepseek(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    captured = {}
    session = SimpleNamespace(
        session_id="nova-session",
        profile="default",
        workspace=r"C:\\sidekick\\home\\spaces\\nova",
        workspace_slug="nova",
        model="qwen3:4b",
        model_provider="ollama",
        active_stream_id=None,
        messages=[],
        context_messages=[],
        pending_user_message=None,
    )

    monkeypatch.setattr(routes, "get_session", lambda sid: session)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda value: r"C:\\sidekick\\home\\spaces\\nova")
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda requested_model, requested_provider: ("qwen3:4b", "ollama", False),
    )
    monkeypatch.setattr(
        routes,
        "resolve_active_provider_context",
        lambda: {"provider": "ollama", "model": "qwen3:4b", "base_url": "http://127.0.0.1:11434"},
    )
    monkeypatch.setattr("web.api.goals.has_active_goal", lambda *args, **kwargs: False)
    monkeypatch.setattr("web.api.profiles.get_hermes_home_for_profile", lambda profile: tmp_path / "home")
    monkeypatch.setattr(
        "web.api.routes._start_chat_stream_for_session",
        lambda *args, **kwargs: captured.update(kwargs) or {"stream_id": "stream-1"},
    )
    monkeypatch.setattr(
        "web.api.routes.j",
        lambda handler, payload, status=200, extra_headers=None: payload,
    )

    payload = routes._handle_chat_start(
        SimpleNamespace(headers={}),
        {
            "session_id": "nova-session",
            "message": "hello nova",
            "workspace": r"C:\\sidekick\\home\\spaces\\nova",
        },
    )

    assert payload["stream_id"] == "stream-1"
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["model_provider"] == "ollama-cloud"
    assert captured["normalized_model"] is True


def test_game_mode_chat_start_infers_nova_from_workspace_path_without_slug(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    captured = {}
    session = SimpleNamespace(
        session_id="nova-session",
        profile="default",
        workspace=r"C:\\sidekick\\home\\spaces\\nova",
        model="qwen3:4b",
        model_provider="ollama",
        active_stream_id=None,
        messages=[],
        context_messages=[],
        pending_user_message=None,
    )

    monkeypatch.setattr(routes, "get_session", lambda sid: session)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda value: r"C:\\sidekick\\home\\spaces\\nova")
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda requested_model, requested_provider: ("qwen3:4b", "ollama", False),
    )
    monkeypatch.setattr(
        routes,
        "resolve_active_provider_context",
        lambda: {"provider": "ollama", "model": "qwen3:4b", "base_url": "http://127.0.0.1:11434"},
    )
    monkeypatch.setattr("web.api.goals.has_active_goal", lambda *args, **kwargs: False)
    monkeypatch.setattr("web.api.profiles.get_hermes_home_for_profile", lambda profile: tmp_path / "home")
    monkeypatch.setattr(
        "web.api.routes._start_chat_stream_for_session",
        lambda *args, **kwargs: captured.update(kwargs) or {"stream_id": "stream-1"},
    )
    monkeypatch.setattr(
        "web.api.routes.j",
        lambda handler, payload, status=200, extra_headers=None: payload,
    )

    payload = routes._handle_chat_start(
        SimpleNamespace(headers={}),
        {
            "session_id": "nova-session",
            "message": "hello nova",
            "workspace": r"C:\\sidekick\\home\\spaces\\nova",
        },
    )

    assert payload["stream_id"] == "stream-1"
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["model_provider"] == "ollama-cloud"
    assert captured["normalized_model"] is True


def test_game_mode_chat_start_routes_nova_instance_spaces_to_ollama_cloud_deepseek(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    captured = {}
    session = SimpleNamespace(
        session_id="nova-session",
        profile="default",
        workspace=r"C:\\sidekick\\home\\spaces\\studio-alpha",
        workspace_slug="studio-alpha",
        model="qwen3:4b",
        model_provider="ollama",
        active_stream_id=None,
        messages=[],
        context_messages=[],
        pending_user_message=None,
    )

    class FakeSpace:
        def load_config(self):
            return {"nova": {"enabled": True, "character": "Nova"}}

    monkeypatch.setattr(routes, "get_session", lambda sid: session)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda value: session.workspace)
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda requested_model, requested_provider: ("qwen3:4b", "ollama", False),
    )
    monkeypatch.setattr(
        routes,
        "resolve_active_provider_context",
        lambda: {"provider": "ollama", "model": "qwen3:4b", "base_url": "http://127.0.0.1:11434"},
    )
    monkeypatch.setattr("web.api.space_engine.get_space", lambda slug: FakeSpace() if slug == "studio-alpha" else None)
    monkeypatch.setattr("web.api.goals.has_active_goal", lambda *args, **kwargs: False)
    monkeypatch.setattr("web.api.profiles.get_hermes_home_for_profile", lambda profile: tmp_path / "home")
    monkeypatch.setattr(
        "web.api.routes._start_chat_stream_for_session",
        lambda *args, **kwargs: captured.update(kwargs) or {"stream_id": "stream-1"},
    )
    monkeypatch.setattr(
        "web.api.routes.j",
        lambda handler, payload, status=200, extra_headers=None: payload,
    )

    payload = routes._handle_chat_start(
        SimpleNamespace(headers={}),
        {
            "session_id": "nova-session",
            "message": "hello nova",
            "workspace": r"C:\\sidekick\\home\\spaces\\studio-alpha",
        },
    )

    assert payload["stream_id"] == "stream-1"
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["model_provider"] == "ollama-cloud"
    assert captured["normalized_model"] is True


def test_chat_sync_sets_webui_session_context_for_approval(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import os

    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": False})
    monkeypatch.delenv("SIDEKICK_EXEC_ASK", raising=False)
    monkeypatch.setenv("HERMES_EXEC_ASK", "legacy-ask")
    monkeypatch.setattr(cfg, "resolve_model_provider", lambda model: ("qwen3:4b", "openai", "https://api.openai.com/v1"))
    monkeypatch.setattr(cfg, "resolve_custom_provider_connection", lambda provider: (None, None))

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    captured = {}

    class FakeAIAgent:
        def __init__(self, *args, **kwargs):
            from gateway.session_context import get_session_env
            from tools.approval import _is_gateway_approval_context, get_current_session_key

            captured["approval_session_key"] = get_current_session_key()
            captured["approval_platform"] = get_session_env("HERMES_SESSION_PLATFORM")
            captured["is_gateway"] = _is_gateway_approval_context()

        def run_conversation(self, **kwargs):
            return {
                "final_response": "ok",
                "messages": [{"role": "assistant", "content": "ok"}],
                "completed": True,
            }

    session = SimpleNamespace(
        session_id="chat-sync-1",
        workspace=str(workspace),
        model="qwen3:4b",
        model_provider="openai",
        workspace_slug="nova",
        space_slug="nova",
        space="nova",
        messages=[],
        context_messages=[],
        title="Existing title",
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0,
        compact=lambda: {
            "session_id": "chat-sync-1",
            "title": "Existing title",
            "messages": [],
        },
        save=lambda: None,
    )

    monkeypatch.setattr(routes, "get_session", lambda sid: session)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda value: str(workspace))
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda model, provider: (model, provider, False))
    monkeypatch.setattr(routes, "j", lambda handler, payload, status=200, extra_headers=None: payload)
    monkeypatch.setattr("web.api.oauth.resolve_runtime_provider_with_anthropic_env_lock", lambda resolver, requested: {"api_key": "token", "provider": "openai", "base_url": "https://api.openai.com/v1"})
    monkeypatch.setattr("run_agent.AIAgent", FakeAIAgent)
    monkeypatch.setattr("web.api.streaming._merge_display_messages_after_agent_result", lambda previous_messages, previous_context_messages, result_messages, msg: [{"role": "assistant", "content": "ok"}])
    monkeypatch.setattr("web.api.streaming._restore_reasoning_metadata", lambda previous, result: result)
    monkeypatch.setattr("web.api.streaming._sanitize_messages_for_api", lambda messages: messages)
    monkeypatch.setattr("web.api.streaming._session_context_messages", lambda s: list(s.context_messages))
    monkeypatch.setattr("web.api.streaming._workspace_context_prefix", lambda workspace: "")

    payload = routes._handle_chat_sync(
        SimpleNamespace(headers={}),
        {
            "session_id": "chat-sync-1",
            "message": "hello",
            "workspace": str(workspace),
            "model": "qwen3:4b",
        },
    )

    assert payload["answer"] == "ok"
    assert captured["approval_session_key"] == "chat-sync-1"
    assert captured["approval_platform"] == "webui"
    assert captured["is_gateway"] is True
    assert os.environ["HERMES_EXEC_ASK"] == "legacy-ask"


def test_game_mode_chat_sync_routes_nova_local_model_to_ollama_cloud_deepseek(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    captured = {}
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    session = SimpleNamespace(
        session_id="chat-sync-gm-1",
        workspace=str(workspace),
        model="qwen3:4b",
        model_provider="ollama",
        workspace_slug="nova",
        space_slug="nova",
        space="nova",
        messages=[],
        context_messages=[],
        title="Existing title",
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0,
        compact=lambda: {
            "session_id": "chat-sync-gm-1",
            "title": "Existing title",
            "messages": [],
        },
        save=lambda: None,
    )

    monkeypatch.setattr(routes, "get_session", lambda sid: session)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda value: str(workspace))
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda model, provider: (model, provider, False))
    monkeypatch.setattr(routes, "j", lambda handler, payload, status=200, extra_headers=None: payload)
    monkeypatch.setattr(
        "web.api.config.resolve_model_provider",
        lambda model_id: (
            "deepseek-v4-flash",
            "ollama-cloud",
            "https://ollama.example/v1",
        )
        if "deepseek-v4-flash" in str(model_id) or "ollama-cloud" in str(model_id)
        else ("qwen3:4b", "ollama", "http://127.0.0.1:11434"),
    )
    monkeypatch.setattr(
        "web.api.oauth.resolve_runtime_provider_with_anthropic_env_lock",
        lambda resolver, requested=None: {
            "api_key": "token",
            "provider": requested,
            "base_url": "https://ollama.example/v1" if requested == "ollama-cloud" else "http://127.0.0.1:11434",
        },
    )

    class FakeAIAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run_conversation(self, **kwargs):
            return {
                "final_response": "ok",
                "messages": [{"role": "assistant", "content": "ok"}],
                "completed": True,
            }

    monkeypatch.setattr("run_agent.AIAgent", FakeAIAgent)
    monkeypatch.setattr("web.api.streaming._merge_display_messages_after_agent_result", lambda previous_messages, previous_context_messages, result_messages, msg: [{"role": "assistant", "content": "ok"}])
    monkeypatch.setattr("web.api.streaming._restore_reasoning_metadata", lambda previous, result: result)
    monkeypatch.setattr("web.api.streaming._sanitize_messages_for_api", lambda messages: messages)
    monkeypatch.setattr("web.api.streaming._session_context_messages", lambda s: list(s.context_messages))
    monkeypatch.setattr("web.api.streaming._workspace_context_prefix", lambda workspace: "")

    payload = routes._handle_chat_sync(
        SimpleNamespace(headers={}),
        {
            "session_id": "chat-sync-gm-1",
            "message": "hello",
            "workspace": str(workspace),
            "model": "qwen3:4b",
        },
    )

    assert payload["answer"] == "ok"
    assert captured["provider"] == "ollama-cloud"
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["base_url"] == "https://ollama.example/v1"
    assert session.model == "deepseek-v4-flash"
    assert session.model_provider == "ollama-cloud"


def test_game_mode_session_compress_routes_nova_local_model_to_ollama_cloud_deepseek(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    captured = {}
    session = SimpleNamespace(
        session_id="nova-session",
        profile="default",
        workspace=r"C:\\sidekick\\home\\spaces\\nova",
        model="qwen3:4b",
        model_provider="ollama",
        active_stream_id=None,
        messages=[
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
            {"role": "assistant", "content": "four"},
        ],
        context_messages=[],
        pending_user_message=None,
        tool_calls=[],
        save=lambda: None,
        compact=lambda: {"session_id": "nova-session", "workspace": r"C:\\sidekick\\home\\spaces\\nova"},
    )

    class _FakeCompressor:
        def compress(self, original_messages, current_tokens, focus_topic=None):
            return original_messages[:2]

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.context_compressor = _FakeCompressor()

    monkeypatch.setattr(routes, "get_session", lambda sid: session)
    monkeypatch.setattr("web.api.config.resolve_model_provider", lambda model_id: (model_id, "ollama", "http://127.0.0.1:11434"))
    monkeypatch.setattr(
        "web.api.oauth.resolve_runtime_provider_with_anthropic_env_lock",
        lambda resolver, requested=None: {"api_key": "test-key", "provider": requested, "base_url": "http://127.0.0.1:11434"},
    )
    monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)
    monkeypatch.setattr(
        "web.api.routes.j",
        lambda handler, payload, status=200, extra_headers=None: payload,
    )

    payload = routes._handle_session_compress(
        SimpleNamespace(headers={}),
        {"session_id": "nova-session"},
    )

    assert payload["ok"] is True
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["provider"] == "ollama-cloud"
    assert payload["session"]["session_id"] == "nova-session"


def test_game_mode_handoff_summary_routes_nova_local_model_to_ollama_cloud_deepseek(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    captured = {}
    session = SimpleNamespace(
        session_id="nova-session",
        profile="default",
        workspace=r"C:\\sidekick\\home\\spaces\\nova",
        model="qwen3:4b",
        model_provider="ollama",
        workspace_slug=None,
        space_slug=None,
        space=None,
        source_label="Nova",
        raw_source=None,
        source_tag=None,
        session_source=None,
    )
    messages = [
        {"role": "user", "content": "hello", "timestamp": 1},
        {"role": "assistant", "content": "hi", "timestamp": 2},
    ]

    class _FakeMessage:
        def __init__(self, content):
            self.content = content

    class _FakeChoice:
        def __init__(self, content):
            self.message = _FakeMessage(content)
            self.finish_reason = "stop"
            self.stop_reason = None

    class _FakeResponse:
        def __init__(self, content):
            self.choices = [_FakeChoice(content)]

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["api_kwargs"] = kwargs
            return _FakeResponse("Remote summary")

    class _FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    class _FakeAgent:
        api_mode = ""

        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.reasoning_config = {"enabled": True}

        def _build_api_kwargs(self, api_messages):
            return {"messages": api_messages, "model": captured.get("model")}

        def _ensure_primary_openai_client(self, reason=""):
            return _FakeClient()

    monkeypatch.setattr("web.api.models.get_session", lambda sid: session)
    monkeypatch.setattr("web.api.models.get_cli_session_messages", lambda sid: messages)
    monkeypatch.setattr("web.api.models.count_conversation_rounds", lambda sid, since=None: 4)
    monkeypatch.setattr("web.api.models.CONVERSATION_ROUND_THRESHOLD", 1, raising=False)
    monkeypatch.setattr("web.api.config.resolve_model_provider", lambda model_id: (model_id, "ollama", "http://127.0.0.1:11434"))
    monkeypatch.setattr(
        "web.api.oauth.resolve_runtime_provider_with_anthropic_env_lock",
        lambda resolver, requested=None: {"api_key": "test-key", "provider": requested, "base_url": "http://127.0.0.1:11434"},
    )
    monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)
    monkeypatch.setattr(
        "web.api.routes.j",
        lambda handler, payload, status=200, extra_headers=None: payload,
    )

    payload = routes._handle_handoff_summary(
        SimpleNamespace(headers={}),
        {"session_id": "nova-session"},
    )

    assert payload["ok"] is True
    assert payload["summary"] == "Remote summary"
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["provider"] == "ollama-cloud"
    assert captured["session_id"] == "nova-session"


def test_handoff_summary_handles_missing_session_without_crashing(monkeypatch, tmp_path):
    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": False})

    messages = [
        {"role": "user", "content": "hello", "timestamp": 1},
        {"role": "assistant", "content": "hi", "timestamp": 2},
    ]
    captured = {}
    warnings = []

    monkeypatch.setattr("web.api.models.get_session", lambda sid: (_ for _ in ()).throw(KeyError(sid)))
    monkeypatch.setattr("web.api.models.get_cli_session_messages", lambda sid: messages)
    monkeypatch.setattr("web.api.models.count_conversation_rounds", lambda sid, since=None: 4)
    monkeypatch.setattr("web.api.models.CONVERSATION_ROUND_THRESHOLD", 1, raising=False)
    monkeypatch.setattr(
        "web.api.config.resolve_model_provider",
        lambda model_id: ("qwen3:4b", "ollama", "http://127.0.0.1:11434"),
    )
    monkeypatch.setattr(
        "web.api.oauth.resolve_runtime_provider_with_anthropic_env_lock",
        lambda resolver, requested=None: {"api_key": "", "provider": requested, "base_url": "http://127.0.0.1:11434"},
    )
    monkeypatch.setattr("web.api.routes._persist_handoff_summary", lambda *args, **kwargs: captured.update({"persisted": True}) or {})
    monkeypatch.setattr(
        "web.api.routes.j",
        lambda handler, payload, status=200, extra_headers=None: payload,
    )
    monkeypatch.setattr(routes.logger, "warning", lambda msg, *args, **kwargs: warnings.append(msg))

    payload = routes._handle_handoff_summary(
        SimpleNamespace(headers={}),
        {"session_id": "missing-session"},
    )

    assert payload["ok"] is True
    assert payload["fallback"] is True
    assert payload["summary"]
    assert captured["persisted"] is True
    assert warnings == []


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
    gpu_snapshots = [
        {"available": True, "top": [{"process": "python.exe", "used_gpu_memory_mb": 2048, "local_gpu_workload": True}]},
        {"available": True, "top": [{"process": "VRChat.exe", "used_gpu_memory_mb": 4096, "local_gpu_workload": False}], "non_sidekick_top": [{"process": "VRChat.exe", "used_gpu_memory_mb": 4096, "local_gpu_workload": False}]},
    ]
    monkeypatch.setattr(game_mode, "_gpu_process_memory_snapshot", lambda: gpu_snapshots.pop(0))

    payload = game_mode.release_game_mode_resources()

    assert cancelled == ["local-stream"]
    assert payload["cancelled_local_streams"] == ["local-stream"]
    assert payload["ollama"]["unloaded"][0]["model"] == "qwen3:4b"
    assert "image_generation_queue" in payload
    assert payload["gpu_processes"]["after"]["non_sidekick_top"][0]["process"] == "VRChat.exe"


def test_game_mode_resource_release_skips_ollama_cloud_endpoints(monkeypatch):
    from web.api import game_mode

    monkeypatch.setenv("OLLAMA_HOST", "https://ollama.com/v1")
    monkeypatch.setattr(
        game_mode.cfg,
        "get_config",
        lambda: {
            "model": {"provider": "ollama-cloud", "base_url": "https://ollama.com/v1"},
            "providers": {"ollama-cloud": {"base_url": "https://ollama.com/v1"}},
            "custom_providers": [{"name": "Ollama Cloud", "base_url": "https://ollama.com/v1"}],
        },
    )
    with game_mode.cfg.ACTIVE_RUNS_LOCK:
        game_mode.cfg.ACTIVE_RUNS.clear()

    seen = []

    def record_models(base_url):
        seen.append(base_url)
        if "ollama.com" in str(base_url).lower():
            raise AssertionError(f"Game Mode release should not inspect remote Ollama URL: {base_url}")
        return []

    monkeypatch.setattr(game_mode, "_cancel_stream", lambda stream_id: False)
    monkeypatch.setattr(game_mode, "_loaded_ollama_models", record_models)
    monkeypatch.setattr(game_mode, "_terminate_known_local_model_servers", lambda: [])
    monkeypatch.setattr(game_mode, "_release_local_image_generation_queues", lambda: {"queues": []})
    monkeypatch.setattr(game_mode, "_gpu_process_memory_snapshot", lambda: {"available": True, "top": []})

    payload = game_mode.release_game_mode_resources()

    assert "http://127.0.0.1:11434" in seen
    assert all("ollama.com" not in str(base_url).lower() for base_url in seen)
    assert payload["ollama"]["checked"] == ["http://127.0.0.1:11434"]
    assert payload["ollama"]["unloaded"] == []


def test_game_mode_resource_release_keeps_local_ollama_default_even_with_cloud_host(monkeypatch):
    from web.api import game_mode

    monkeypatch.setenv("OLLAMA_HOST", "https://ollama.com/v1")
    monkeypatch.setattr(game_mode.cfg, "get_config", lambda: {})

    seen = []

    def record_models(base_url):
        seen.append(base_url)
        return []

    monkeypatch.setattr(game_mode, "_loaded_ollama_models", record_models)
    monkeypatch.setattr(game_mode, "_unload_ollama_model", lambda _base_url, model: {"ok": True, "model": model})
    monkeypatch.setattr(game_mode, "_cancel_stream", lambda stream_id: False)
    monkeypatch.setattr(game_mode, "_terminate_known_local_model_servers", lambda: [])
    monkeypatch.setattr(game_mode, "_release_local_image_generation_queues", lambda: {"queues": []})
    monkeypatch.setattr(game_mode, "_gpu_process_memory_snapshot", lambda: {"available": True, "top": []})

    payload = game_mode.release_game_mode_resources()

    assert "http://127.0.0.1:11434" in seen
    assert "https://ollama.com" not in "\n".join(seen)
    assert payload["ollama"]["checked"] == ["http://127.0.0.1:11434"]


def test_game_mode_release_targets_all_nova_local_model_ports():
    from web.api import game_mode

    ports = game_mode._nova_local_model_ports()

    assert 8081 in ports
    assert 8082 in ports


def test_game_mode_release_targets_configured_local_model_ports(monkeypatch):
    from web.api import game_mode

    monkeypatch.setattr(
        game_mode.cfg,
        "get_config",
        lambda: {
            "custom_providers": [
                {"name": "LM Studio", "base_url": "http://127.0.0.1:1234/v1"},
                {"name": "Remote", "base_url": "https://api.example.test/v1"},
            ],
        },
    )

    assert 1234 in game_mode._configured_local_model_ports()
    assert 1234 in game_mode._known_local_model_ports()


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

    settings_file = tmp_path / "home" / "state" / "webui" / "settings.json"
    active_lock = tmp_path / "home" / "state" / "webui" / "game_mode.lock"
    legacy_lock = tmp_path / "home" / "state" / "game_mode.lock"
    watchdog_state = tmp_path / "home" / "state" / "gpu_watchdog_state.json"

    monkeypatch.setattr(cfg, "SETTINGS_FILE", settings_file)
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
    assert payload["game_mode_sync"]["ok"] is True
    assert "game_mode_release" in payload
    assert "cancelled_local_streams" in payload["game_mode_release"]
    assert "ollama" in payload["game_mode_release"]
    assert "local_model_servers" in payload["game_mode_release"]
    assert active_lock.exists()
    assert legacy_lock.exists()
    assert json.loads(watchdog_state.read_text(encoding="utf-8"))["last_game_mode"] is True


def test_settings_post_clears_game_mode_lock_when_disabling(monkeypatch, tmp_path):
    import io
    from urllib.parse import urlparse

    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    from web.api import config as cfg
    from web.api import routes

    settings_file = tmp_path / "home" / "state" / "webui" / "settings.json"
    active_lock = tmp_path / "home" / "state" / "webui" / "game_mode.lock"
    legacy_lock = tmp_path / "home" / "state" / "game_mode.lock"
    watchdog_state = tmp_path / "home" / "state" / "gpu_watchdog_state.json"

    monkeypatch.setattr(cfg, "SETTINGS_FILE", settings_file)
    cfg.save_settings({"game_mode_enabled": True})
    active_lock.parent.mkdir(parents=True, exist_ok=True)
    legacy_lock.parent.mkdir(parents=True, exist_ok=True)
    active_lock.write_text("stale", encoding="utf-8")
    legacy_lock.write_text("stale", encoding="utf-8")
    watchdog_state.parent.mkdir(parents=True, exist_ok=True)
    watchdog_state.write_text(
        json.dumps({"last_game_mode": True, "last_action": "blocked"}),
        encoding="utf-8",
    )

    body = json.dumps({"game_mode_enabled": False}).encode("utf-8")

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
    handled = routes.handle_post(handler, urlparse("/api/settings"))

    assert handled is None
    assert handler.status_code == 200
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["game_mode_enabled"] is False
    assert payload["game_mode_sync"]["ok"] is True
    assert not active_lock.exists()
    assert not legacy_lock.exists()
    assert json.loads(watchdog_state.read_text(encoding="utf-8"))["last_game_mode"] is False


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


def test_settings_endpoint_exposes_legacy_password_env_var(monkeypatch, tmp_path):
    import io
    from urllib.parse import urlparse

    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SIDEKICK_WEBUI_PASSWORD", raising=False)
    monkeypatch.setenv("HERMES_WEBUI_PASSWORD", "legacy-secret")
    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({})

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
    handled = routes.handle_get(handler, urlparse("/api/settings"))

    assert handled is None
    assert handler.status_code == 200
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["password_env_var"] is True


def test_settings_post_rejects_password_change_when_legacy_password_env_var_set(monkeypatch, tmp_path):
    import io
    from urllib.parse import urlparse

    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SIDEKICK_WEBUI_PASSWORD", raising=False)
    monkeypatch.setenv("HERMES_WEBUI_PASSWORD", "legacy-secret")
    from web.api import config as cfg
    from web.api import routes

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({})
    body = json.dumps({"_set_password": "new-password"}).encode("utf-8")

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
    handled = routes.handle_post(handler, urlparse("/api/settings"))

    assert handled is None
    assert handler.status_code == 409
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is False
    assert "overrides the settings password" in payload["error"]["message"]


def test_onboarding_probe_accepts_legacy_open_env_var(monkeypatch, tmp_path):
    import io
    from urllib.parse import urlparse

    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SIDEKICK_WEBUI_ONBOARDING_OPEN", raising=False)
    monkeypatch.setenv("HERMES_WEBUI_ONBOARDING_OPEN", "1")
    from web.api import auth
    from web.api import routes

    monkeypatch.setattr(auth, "is_auth_enabled", lambda: False)
    seen = {}

    def fake_probe(provider, base_url, api_key):
        seen["args"] = (provider, base_url, api_key)
        return {"ok": True, "provider": provider, "base_url": base_url, "api_key": api_key}

    monkeypatch.setattr(routes, "probe_provider_endpoint", fake_probe)
    body = json.dumps({"provider": "ollama", "base_url": "http://example.com", "api_key": "secret"}).encode("utf-8")

    class _Handler:
        headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            "Host": "127.0.0.1",
        }
        client_address = ("203.0.113.10", 12345)

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
    handled = routes.handle_post(handler, urlparse("/api/onboarding/probe"))

    assert handled is None
    assert handler.status_code == 200
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert seen["args"] == ("ollama", "http://example.com", "secret")


def test_session_ttl_accepts_legacy_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_WEBUI_SESSION_TTL", "600")
    monkeypatch.delenv("SIDEKICK_WEBUI_SESSION_TTL", raising=False)
    from web.api import auth

    monkeypatch.setattr(auth, "load_settings", lambda: {})

    assert auth._resolve_session_ttl() == 600


def test_media_endpoint_serves_allowed_local_file(monkeypatch, tmp_path):
    import io
    from urllib.parse import quote, urlparse

    from web.api import routes

    media_root = tmp_path / "media"
    media_root.mkdir()
    media_file = media_root / "preview.txt"
    media_file.write_text("hello from media", encoding="utf-8")
    monkeypatch.setenv("MEDIA_ALLOWED_ROOTS", str(media_root))

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
    handled = routes.handle_get(
        handler,
        urlparse(f"/api/media?path={quote(str(media_file))}"),
    )

    assert handled is True
    assert handler.status_code == 200
    assert handler.wfile.getvalue() == b"hello from media"
    assert handler.response_headers["content-type"] == "application/octet-stream"


def test_media_endpoint_falls_back_to_hermes_home(monkeypatch, tmp_path):
    import io
    from urllib.parse import quote, urlparse

    from web.api import routes

    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    media_file = hermes_home / "preview.txt"
    media_file.write_text("hello from hermes home", encoding="utf-8")
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

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
    handled = routes.handle_get(
        handler,
        urlparse(f"/api/media?path={quote(str(media_file))}"),
    )

    assert handled is True
    assert handler.status_code == 200
    assert handler.wfile.getvalue() == b"hello from hermes home"


def test_extension_url_list_caps_without_crashing(monkeypatch, tmp_path):
    from web.api import extensions

    extension_root = tmp_path / "extensions"
    extension_root.mkdir()
    values = [f"/extensions/script-{idx}.js" for idx in range(40)]

    monkeypatch.setenv("SIDEKICK_WEBUI_EXTENSION_DIR", str(extension_root))
    monkeypatch.setenv("SIDEKICK_WEBUI_EXTENSION_SCRIPT_URLS", ",".join(values))
    extensions._warned_urls.clear()

    config = extensions.get_extension_config()

    assert config["enabled"] is True
    assert config["script_urls"] == values[:32]


def test_cli_session_messages_stitch_continuation_parent(monkeypatch, tmp_path):
    import sqlite3

    from web.api import models

    home = tmp_path / "home"
    home.mkdir()
    db_path = home / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                source TEXT,
                started_at REAL,
                parent_session_id TEXT,
                ended_at REAL,
                end_reason TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                timestamp REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
            ("parent", "cli", 1.0, None, 2.0, "compression"),
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
            ("child", "cli", 3.0, "parent", None, None),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            ("parent", "user", "from parent", 1.1),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            ("child", "assistant", "from child", 3.1),
        )

    monkeypatch.setattr("web.api.profiles.get_active_hermes_home", lambda: str(home))

    messages = models.get_cli_session_messages("child")

    assert [m["content"] for m in messages] == ["from parent", "from child"]


def test_server_startup_runs_game_mode_release_when_already_enabled(monkeypatch, tmp_path):
    from web.api import config as cfg
    from web.api import game_mode
    from web import server

    settings_file = tmp_path / "home" / "state" / "webui" / "settings.json"
    active_lock = tmp_path / "home" / "state" / "webui" / "game_mode.lock"
    legacy_lock = tmp_path / "home" / "state" / "game_mode.lock"
    watchdog_state = tmp_path / "home" / "state" / "gpu_watchdog_state.json"

    monkeypatch.setattr(cfg, "SETTINGS_FILE", settings_file)
    cfg.save_settings({"game_mode_enabled": True})

    calls = []
    monkeypatch.setattr(
        game_mode,
        "release_game_mode_resources",
        lambda: calls.append("release") or {"local_model_servers": []},
    )

    server._release_game_mode_resources_on_startup()

    assert calls == ["release"]
    assert active_lock.exists()
    assert legacy_lock.exists()
    assert json.loads(watchdog_state.read_text(encoding="utf-8"))["last_game_mode"] is True


def test_game_mode_titlebar_button_and_settings_ui_are_wired():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    boot_js = Path("web/static/boot.js").read_text(encoding="utf-8")
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    titlebar_start = index_html.index('<div class="titlebar-actions" id="titlebarActions">')
    lang_start = index_html.index("titlebarLangSelector", titlebar_start)
    cast_start = index_html.index("btnCastToggle", lang_start)
    titlebar_actions = index_html[titlebar_start:cast_start]

    assert re.search(
        r'id="btnLangSelector"[^>]+aria-label="Language"[^>]+aria-haspopup="menu"[^>]+aria-expanded="false"[^>]+aria-controls="langDropdown"[^>]+onclick="toggleLangDropdown\(event\)"',
        titlebar_actions,
        re.S,
    )
    assert 'id="btnGameModeToggle"' in titlebar_actions
    assert "toggleGameMode()" in titlebar_actions
    assert "game_mode_toggle" in titlebar_actions
    assert "settingsGameModeEnabled" in index_html
    assert "window._gameModeEnabled=!!s.game_mode_enabled" in boot_js
    assert "_syncGameModeStateFromServer" in boot_js
    assert "api('/api/game-mode/status')" in boot_js
    assert "function syncGameModeButton()" in panels_js
    assert "async function toggleGameMode()" in panels_js
    assert "function _gameModeReleaseSummary(release)" in panels_js
    assert "function _gameModeGpuUsersSummary(snapshot, key)" in panels_js
    assert "saved&&saved.game_mode_release" in panels_js
    assert "No Sidekick local GPU processes found." in panels_js
    assert "Top remaining GPU users:" in panels_js
    assert "Local GPU workload still detected:" in panels_js
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


def test_provider_key_input_is_wrapped_in_a_form_for_enter_submit_semantics():
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")

    assert "const row=document.createElement('form');" in panels_js
    assert "row.noValidate=true;" in panels_js
    assert "row.addEventListener('submit',e=>{" in panels_js
    assert "saveBtn.type='submit';" in panels_js


def test_background_stream_requests_keep_owner_workspace():
    messages_js = Path("web/static/messages.js").read_text(encoding="utf-8")
    sessions_js = Path("web/static/sessions.js").read_text(encoding="utf-8")

    assert "const scopedPath = (typeof _spaceScopedApiPath === 'function')" in sessions_js
    assert "const apiOptions = Object.assign({signal: controller.signal}, options || {});" in sessions_js
    assert "return await api(scopedPath, apiOptions);" in sessions_js
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


def test_session_list_loads_projects_in_parallel_with_sessions():
    sessions_js = Path("web/static/sessions.js").read_text(encoding="utf-8")

    render_start = sessions_js.index("async function renderSessionList()")
    render_end = sessions_js.index("let _gatewaySSE", render_start)
    body = sessions_js[render_start:render_end]

    sessions_promise = body.index("const sessionsPromise = _apiWithTimeout(")
    projects_promise = body.index("const projectsPromise = _apiWithTimeout(")
    await_sessions = body.index("const sessData = await sessionsPromise;")
    await_projects = body.index("const projData = await projectsPromise;")
    first_render = body.index("renderSessionListFromCache();  // no-ops if rename is in progress")
    second_render = body.index("renderSessionListFromCache();", await_projects)

    assert sessions_promise < await_sessions
    assert projects_promise < await_sessions
    assert await_sessions < first_render < await_projects
    assert await_projects < second_render


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
    routes_py = Path("web/api/routes.py").read_text(encoding="utf-8")

    assert "function _startGoalContinuation(goalNext, attempt=0)" in messages_js
    assert "api(_ownerScopedApiPath('/api/chat/start')" in messages_js
    assert "setTimeout(()=>_startGoalContinuation(_goalNext),250)" in messages_js
    assert "already has an active stream" in messages_js
    assert "merely reports progress" in goals_py
    assert "If any required work remains" in goals_py
    assert "goal_related = has_active_goal(" in routes_py
    assert "goal_related=goal_related" in routes_py


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


def test_sse_write_disconnect_is_suppressed():
    from web.api.streaming import _sse

    class FailingWFile:
        def write(self, data):
            raise ConnectionResetError("client closed")

        def flush(self):
            raise AssertionError("flush should not run after a disconnect")

    handler = SimpleNamespace(wfile=FailingWFile())

    _sse(handler, "snapshot", {"ok": True})


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
