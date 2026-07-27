import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient


def _headers(web_server):
    return {
        web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN,
        "Origin": "http://testserver",
    }


def test_unmatched_api_route_runs_in_fastapi_without_http_proxy(monkeypatch):
    from cli import web_server
    from web.api import routes

    def fake_get(handler, parsed):
        assert parsed.path == "/api/bridge-regression"
        assert handler.headers.get("Host") == "testserver"
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Set-Cookie", "profile=default; Path=/; SameSite=Lax")
        handler.end_headers()
        handler.wfile.write(b'{"ok":true}')
        return True

    monkeypatch.setattr(routes, "handle_get", fake_get)
    monkeypatch.setattr(routes, "_setup_workspace_from_request", lambda *_: None)
    monkeypatch.setattr(routes, "_teardown_workspace_context", lambda: None)

    response = TestClient(web_server.app).get(
        "/api/bridge-regression", headers=_headers(web_server)
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.headers["set-cookie"] == "profile=default; Path=/; SameSite=Lax"


def test_unmatched_sse_route_streams_from_fastapi_bridge(monkeypatch):
    from cli import web_server
    from web.api import routes

    def fake_get(handler, parsed):
        assert parsed.path == "/api/bridge-stream"
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(b"event: ping\ndata: {}\n\n")
        return True

    monkeypatch.setattr(routes, "handle_get", fake_get)
    monkeypatch.setattr(routes, "_setup_workspace_from_request", lambda *_: None)
    monkeypatch.setattr(routes, "_teardown_workspace_context", lambda: None)

    response = TestClient(web_server.app).get(
        "/api/bridge-stream", headers=_headers(web_server)
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"
    assert "event: ping" in response.text


def test_swarm_get_skips_workspace_setup_in_fastapi_bridge(monkeypatch):
    """Catches a pure Swarm read creating a Space before the route dispatch."""
    from cli import web_server
    from web.api import routes, swarm

    def unexpected_setup(*_args):
        raise AssertionError("Swarm GET must not initialize a workspace")

    def fake_swarm_get(handler, parsed):
        assert parsed.path == "/api/swarm/runs"
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(b'{"ok":true}')
        return True

    monkeypatch.setattr(routes, "_setup_workspace_from_request", unexpected_setup)
    monkeypatch.setattr(routes, "_teardown_workspace_context", unexpected_setup)
    monkeypatch.setattr(swarm, "handle_swarm_get", fake_swarm_get)

    response = TestClient(web_server.app).get(
        "/api/swarm/runs?project_path=C%3A%2Ftrusted",
        headers=_headers(web_server),
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_swarm_get_skips_global_webui_bootstrap_in_fastapi_bridge(monkeypatch):
    """Catches a pure Swarm read initializing the global Agent/WebUI state."""
    from cli import web_server
    from web.api import fastapi_bridge, swarm

    def unexpected_bootstrap():
        raise AssertionError("Swarm GET must not bootstrap the WebUI runtime")

    def fake_swarm_get(handler, parsed):
        assert parsed.path == "/api/swarm/runs"
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(b'{"ok":true}')
        return True

    monkeypatch.setattr(
        fastapi_bridge, "_prepare_webui_runtime", unexpected_bootstrap
    )
    monkeypatch.setattr(swarm, "handle_swarm_get", fake_swarm_get)

    response = TestClient(web_server.app).get(
        "/api/swarm/runs?project_path=C%3A%2Ftrusted",
        headers=_headers(web_server),
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_pure_swarm_get_uses_the_cookie_profile_without_bootstrap(monkeypatch):
    """A pure read must retain profile-local workspace trust without setup."""
    from cli import web_server
    from web.api import fastapi_bridge, profiles, swarm

    seen_profiles: list[str] = []

    def unexpected_bootstrap():
        raise AssertionError("pure Swarm GET must not bootstrap WebUI state")

    def fake_swarm_get(handler, parsed):
        assert parsed.path == "/api/swarm/runs"
        seen_profiles.append(profiles.get_active_profile_name())
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(b'{"ok":true}')
        return True

    monkeypatch.setattr(fastapi_bridge, "_prepare_webui_runtime", unexpected_bootstrap)
    monkeypatch.setattr(swarm, "handle_swarm_get", fake_swarm_get)
    headers = _headers(web_server)
    headers["Cookie"] = "sidekick_profile=alice"

    response = TestClient(web_server.app).get(
        "/api/swarm/runs?project_path=C%3A%2Ftrusted",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert seen_profiles == ["alice"]


def test_pure_swarm_get_password_auth_fails_closed_without_creating_auth_state(
    monkeypatch, tmp_path
):
    """A missing cookie must not make a pure read create a signing key."""
    from cli import web_server
    from web.api import auth, swarm

    auth_state = tmp_path / "auth-state"
    reached_swarm_route: list[bool] = []
    monkeypatch.setenv("SIDEKICK_WEBUI_PASSWORD", "test-password")
    monkeypatch.setattr(auth, "_state_dir", lambda: auth_state)
    monkeypatch.setattr(auth, "_refresh_state_paths", lambda: None)

    def unexpected_swarm_get(*_args):
        reached_swarm_route.append(True)
        raise AssertionError("unauthenticated pure Swarm GET must fail before routing")

    monkeypatch.setattr(swarm, "handle_swarm_get", unexpected_swarm_get)

    response = TestClient(web_server.app).get(
        "/api/swarm/runs?project_path=C%3A%2Ftrusted",
        headers=_headers(web_server),
    )

    assert response.status_code == 401
    assert reached_swarm_route == []
    assert not auth_state.exists()


def test_pure_swarm_get_expired_password_session_does_not_prune_or_write(
    monkeypatch, tmp_path
):
    """An expired cookie fails closed without changing persisted sessions."""
    from cli import web_server
    from web.api import auth, swarm

    auth_state = tmp_path / "auth-state"
    auth_state.mkdir()
    key = b"K" * 32
    token = "expired-session-token"
    signature = hmac.new(key, token.encode(), hashlib.sha256).hexdigest()[:32]
    sessions_file = auth_state / ".sessions.json"
    sessions_file.write_text(
        json.dumps({token: time.time() - 60}), encoding="utf-8"
    )
    original_sessions = sessions_file.read_bytes()
    (auth_state / ".signing_key").write_bytes(key)

    reached_swarm_route: list[bool] = []
    monkeypatch.setenv("SIDEKICK_WEBUI_PASSWORD", "test-password")
    monkeypatch.setattr(auth, "_state_dir", lambda: auth_state)
    monkeypatch.setattr(auth, "_refresh_state_paths", lambda: None)
    monkeypatch.setattr(auth, "_SESSIONS_FILE", sessions_file)
    monkeypatch.setattr(auth, "_sessions", {token: time.time() - 60})

    def unexpected_swarm_get(*_args):
        reached_swarm_route.append(True)
        raise AssertionError("expired pure Swarm GET must fail before routing")

    monkeypatch.setattr(swarm, "handle_swarm_get", unexpected_swarm_get)
    headers = _headers(web_server)
    headers["Cookie"] = f"{auth.COOKIE_NAME}={token}.{signature}"

    response = TestClient(web_server.app).get(
        "/api/swarm/runs?project_path=C%3A%2Ftrusted",
        headers=headers,
    )

    assert response.status_code == 401
    assert reached_swarm_route == []
    assert sessions_file.read_bytes() == original_sessions


def test_pure_swarm_get_accepts_a_valid_password_session_without_writing(
    monkeypatch, tmp_path
):
    """The non-mutating auth branch remains usable with existing credentials."""
    from cli import web_server
    from web.api import auth, swarm

    auth_state = tmp_path / "auth-state"
    auth_state.mkdir()
    key = b"V" * 32
    token = "valid-session-token"
    signature = hmac.new(key, token.encode(), hashlib.sha256).hexdigest()[:32]
    sessions_file = auth_state / ".sessions.json"
    sessions_file.write_text(
        json.dumps({token: time.time() + 3600}), encoding="utf-8"
    )
    original_sessions = sessions_file.read_bytes()
    (auth_state / ".signing_key").write_bytes(key)
    monkeypatch.setenv("SIDEKICK_WEBUI_PASSWORD", "test-password")
    monkeypatch.setattr(auth, "_state_dir", lambda: auth_state)
    monkeypatch.setattr(auth, "_refresh_state_paths", lambda: None)

    def fake_swarm_get(handler, parsed):
        assert parsed.path == "/api/swarm/runs"
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(b'{"ok":true}')
        return True

    monkeypatch.setattr(swarm, "handle_swarm_get", fake_swarm_get)
    headers = _headers(web_server)
    headers["Cookie"] = f"{auth.COOKIE_NAME}={token}.{signature}"

    response = TestClient(web_server.app).get(
        "/api/swarm/runs?project_path=C%3A%2Ftrusted",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert sessions_file.read_bytes() == original_sessions


def test_non_swarm_post_keeps_webui_bootstrap(monkeypatch):
    """The pure-read exemption must not change normal write-route lifecycle."""
    from cli import web_server
    from web.api import fastapi_bridge, routes

    bootstraps: list[None] = []

    def fake_post(handler, parsed):
        assert parsed.path == "/api/bridge-bootstrap"
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(b'{"ok":true}')
        return True

    monkeypatch.setattr(
        fastapi_bridge, "_prepare_webui_runtime", lambda: bootstraps.append(None)
    )
    monkeypatch.setattr(routes, "_setup_workspace_from_request", lambda *_: None)
    monkeypatch.setattr(routes, "_teardown_workspace_context", lambda: None)
    monkeypatch.setattr(routes, "handle_post", fake_post)

    response = TestClient(web_server.app).post(
        "/api/bridge-bootstrap",
        headers=_headers(web_server),
        json={},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert bootstraps == [None]


def test_swarm_approval_actor_comes_from_dashboard_session_not_profile_cookie(
    monkeypatch,
    tmp_path,
):
    """Catches an arbitrary profile cookie becoming durable approval identity."""
    from cli import web_server
    from web.api import routes, swarm

    project = tmp_path / "project"
    project.mkdir()
    recorded_actors: list[str] = []

    monkeypatch.setattr(routes, "_setup_workspace_from_request", lambda *_: None)
    monkeypatch.setattr(routes, "_teardown_workspace_context", lambda: None)
    monkeypatch.setattr(
        swarm, "resolve_trusted_workspace", lambda _value: project.resolve()
    )

    class FakeService:
        def record_human_approval(
            self, _project_root, _run_id, _proposal_id, *, actor_id, approved
        ):
            recorded_actors.append(actor_id)
            return {"approval_type": "human", "approved": approved}

    monkeypatch.setattr(swarm, "get_swarm_service", lambda: FakeService())
    headers = _headers(web_server)
    headers["Cookie"] = "sidekick_profile=arbitrary"

    response = TestClient(web_server.app).post(
        "/api/swarm/runs/run-1/approve",
        headers=headers,
        json={"project_path": str(project), "proposal_id": "proposal-1"},
    )

    assert response.status_code == 200
    assert recorded_actors and recorded_actors[0].startswith("dashboard:")
    assert recorded_actors[0] != "webui:arbitrary"
    assert web_server._SESSION_TOKEN not in recorded_actors[0]


def test_swarm_recovery_actor_comes_from_dashboard_session(monkeypatch, tmp_path):
    """The recover UI needs the same trusted principal as a human approval."""
    from cli import web_server
    from web.api import routes, swarm

    project = tmp_path / "project"
    project.mkdir()
    recorded_actors: list[str] = []

    monkeypatch.setattr(routes, "_setup_workspace_from_request", lambda *_: None)
    monkeypatch.setattr(routes, "_teardown_workspace_context", lambda: None)
    monkeypatch.setattr(
        swarm, "resolve_trusted_workspace", lambda _value: project.resolve()
    )

    class FakeService:
        def recover_execution_lease(self, _project_root, _run_id, *, actor_id):
            recorded_actors.append(actor_id)
            return {"run_id": "run-1", "status": "paused"}

    monkeypatch.setattr(swarm, "get_swarm_service", lambda: FakeService())
    headers = _headers(web_server)
    headers["Cookie"] = "sidekick_profile=arbitrary"

    response = TestClient(web_server.app).post(
        "/api/swarm/runs/run-1/recover",
        headers=headers,
        json={"project_path": str(project)},
    )

    assert response.status_code == 200
    assert recorded_actors and recorded_actors[0].startswith("dashboard:")
    assert recorded_actors[0] != "webui:arbitrary"
    assert web_server._SESSION_TOKEN not in recorded_actors[0]


def test_swarm_kanban_projection_actor_comes_from_dashboard_session(
    monkeypatch, tmp_path
):
    """The optional cross-surface write needs the same durable human actor."""
    from cli import web_server
    from swarm_core.store import ProjectSwarmStore
    from web.api import routes, swarm

    project = tmp_path / "project"
    project.mkdir()
    ProjectSwarmStore(project).create_run(run_id="run-1")
    projected: list[tuple[object, str]] = []

    monkeypatch.setattr(routes, "_setup_workspace_from_request", lambda *_: None)
    monkeypatch.setattr(routes, "_teardown_workspace_context", lambda: None)
    monkeypatch.setattr(
        swarm, "resolve_trusted_workspace", lambda _value: project.resolve()
    )
    monkeypatch.setattr(
        swarm,
        "project_swarm_run_to_kanban",
        lambda project_root, run_id: (
            projected.append((project_root, run_id))
            or {"task_id": "task-1", "board": "default", "space_slug": "test"}
        ),
    )
    headers = _headers(web_server)
    headers["Cookie"] = "sidekick_profile=arbitrary"

    response = TestClient(web_server.app).post(
        "/api/swarm/runs/run-1/kanban-projection",
        headers=headers,
        json={"project_path": str(project)},
    )

    assert response.status_code == 201
    assert projected == [(project.resolve(), "run-1")]
    audit = ProjectSwarmStore(project).list_events("run-1")
    assert audit[-1].event_type == "sidekick.kanban_projection_requested_by_human"
    actor_id = audit[-1].payload["actor_id"]
    assert actor_id.startswith("dashboard:")
    assert actor_id != "webui:arbitrary"
    assert web_server._SESSION_TOKEN not in actor_id


def test_login_uses_public_route_page_when_password_auth_is_enabled(monkeypatch):
    from cli import web_server
    from web.api import auth

    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)

    response = TestClient(web_server.app).get("/login?next=%2Fworkspace")

    assert response.status_code == 200
    assert "id=\"login-form\"" in response.text
    assert "location" not in response.headers
