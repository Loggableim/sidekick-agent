import hashlib
import hmac
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


DASHBOARD_ACTOR = "dashboard:" + "a" * 64


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


def test_space_management_get_is_authenticated_and_does_not_write(monkeypatch, tmp_path):
    """Reading management state must not create or rewrite a Space identity."""
    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    def unexpected_mutating_resolver(_value):
        raise AssertionError("GET management must use the read-only resolver")

    monkeypatch.setattr(web_server, "resolve_trusted_workspace", unexpected_mutating_resolver)
    monkeypatch.setattr(
        web_server,
        "resolve_enrollment_trusted_workspace_read_only",
        lambda _value: project,
    )
    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha", "project_dir": str(project)}, mint_space_id=True)
    before = space.config_path.read_bytes()

    client = TestClient(web_server.app)
    assert client.get("/api/space/nova-management?slug=alpha").status_code == 401

    response = client.get(
        "/api/space/nova-management?slug=alpha", headers=_headers(web_server)
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["space_id"] == space.load_config()["space_id"]
    assert payload["nova_management"] == {
        "yolo": False,
        "enrolled": False,
        "revision": 0,
    }
    assert payload["root_fingerprint"] == space_engine.space_root_fingerprint(project)
    assert space.config_path.read_bytes() == before


def test_space_management_enrollment_uses_server_resolved_root(monkeypatch, tmp_path):
    """Enrollment accepts only a confirmation bound to the server-derived root."""
    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    resolved_inputs: list[str] = []

    def resolve_project(value):
        resolved_inputs.append(str(value))
        return project

    monkeypatch.setattr(web_server, "resolve_trusted_workspace", resolve_project)
    monkeypatch.setattr(web_server, "resolve_trusted_workspace_read_only", resolve_project)
    monkeypatch.setattr(web_server, "resolve_enrollment_trusted_workspace_read_only", resolve_project)
    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha", "project_dir": str(project)}, mint_space_id=True)
    client = TestClient(web_server.app)
    headers = _headers(web_server)
    snapshot = client.get(
        "/api/space/nova-management?slug=alpha", headers=headers
    ).json()

    response = client.post(
        "/api/space/nova-management",
        headers=headers,
        json={
            "slug": "alpha",
            "yolo": True,
            "enrolled": True,
            "confirmation": {
                "space_id": snapshot["space_id"],
                "root_fingerprint": snapshot["root_fingerprint"],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["nova_management"] == {
        "yolo": True,
        "enrolled": True,
        "revision": 1,
    }
    assert resolved_inputs
    assert all(value == str(project) for value in resolved_inputs)


def test_generic_space_config_refuses_nova_management_patch(monkeypatch, tmp_path):
    """Governance cannot be changed through the broad Space config endpoint."""
    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha"})

    response = TestClient(web_server.app).post(
        "/api/space/config",
        headers=_headers(web_server),
        json={
            "slug": "alpha",
            "nova_management": {"yolo": True, "enrolled": True, "revision": 999},
        },
    )

    assert response.status_code == 400
    assert space.load_config()["nova_management"] == {
        "yolo": False,
        "enrolled": False,
        "revision": 0,
    }


def test_space_management_get_is_pure_with_a_cold_space_cache(monkeypatch, tmp_path):
    """A management read must not scan/seed or create any Space-side state."""
    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(space_engine, "_SPACE_CACHE", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_TS", 0.0)
    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha", "project_dir": str(project)})
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    def unexpected_seed():
        raise AssertionError("management GET must not seed or scan Spaces")

    monkeypatch.setattr(space_engine, "_seed_default_space_from_consciousness", unexpected_seed)
    monkeypatch.setattr(web_server, "resolve_trusted_workspace_read_only", lambda _value: project)

    response = TestClient(web_server.app).get(
        "/api/space/nova-management?slug=alpha", headers=_headers(web_server)
    )

    assert response.status_code == 200
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
    assert not (spaces_root / "nova").exists()


def test_enrollment_rejects_a_project_dir_that_only_the_space_config_trusts(monkeypatch, tmp_path):
    """A generic project_dir write cannot become its own enrollment trust root."""
    from cli import web_server
    from web.api import space_engine, workspace

    spaces_root = tmp_path / "spaces"
    project = Path(r"C:\\sidekick")
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(workspace.Path, "home", lambda: tmp_path / "isolated-home")
    monkeypatch.setattr(workspace, "_read_only_saved_workspace_paths", lambda *_args: set())
    monkeypatch.setattr(workspace, "_read_only_default_workspaces", lambda: ())
    monkeypatch.setattr(
        workspace,
        "_read_only_space_project_roots",
        lambda *_args: (_ for _ in ()).throw(AssertionError("enrollment trust must not consult Space project roots")),
    )
    monkeypatch.setattr(
        space_engine,
        "get_all_spaces",
        lambda: (_ for _ in ()).throw(AssertionError("enrollment trust must not scan Spaces")),
    )
    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha", "project_dir": str(project)})
    snapshot = TestClient(web_server.app).get(
        "/api/space/nova-management?slug=alpha", headers=_headers(web_server)
    ).json()

    response = TestClient(web_server.app).post(
        "/api/space/nova-management",
        headers=_headers(web_server),
        json={
            "slug": "alpha",
            "yolo": True,
            "enrolled": True,
            "confirmation": {
                "space_id": snapshot["space_id"],
                "root_fingerprint": snapshot["root_fingerprint"],
            },
        },
    )

    assert response.status_code == 400


def test_management_migrates_legacy_identity_and_exposes_append_only_audit(monkeypatch, tmp_path):
    """An explicit management write migrates legacy identity and records evidence."""
    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    space_dir = spaces_root / "alpha"
    space_dir.mkdir(parents=True)
    (space_dir / "space.yaml").write_text("name: Alpha\n", encoding="utf-8")
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(web_server, "resolve_trusted_workspace_read_only", lambda _value: (_ for _ in ()).throw(ValueError()))
    client = TestClient(web_server.app)
    headers = _headers(web_server)

    response = client.post(
        "/api/space/nova-management",
        headers=headers,
        json={"slug": "alpha", "yolo": False, "enrolled": False, "confirmation": None},
    )
    audit = client.get("/api/space/nova-management/audit?slug=alpha", headers=headers)

    assert response.status_code == 200
    assert response.json()["space_id"]
    assert audit.status_code == 200
    event = audit.json()["events"][-1]
    assert event["actor"].startswith("dashboard:")
    assert event["space_id"] == response.json()["space_id"]
    assert event["previous"] == {"yolo": False, "enrolled": False, "revision": 0}
    assert event["next"] == {"yolo": False, "enrolled": False, "revision": 1}


def test_generic_config_does_not_mint_a_legacy_space_identity(monkeypatch, tmp_path):
    """Only create or explicit management migration may mint a legacy Space ID."""
    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    legacy = spaces_root / "alpha"
    legacy.mkdir(parents=True)
    (legacy / "space.yaml").write_text("name: Alpha\n", encoding="utf-8")
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")

    response = TestClient(web_server.app).post(
        "/api/space/config",
        headers=_headers(web_server),
        json={"slug": "alpha", "description": "ordinary edit"},
    )

    assert response.status_code == 200
    assert space_engine.Space("alpha").load_config()["space_id"] == ""


def test_generic_config_preserves_management_audit_and_refuses_malformed_evidence(monkeypatch, tmp_path):
    """Ordinary config changes cannot erase governance evidence."""
    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha"}, mint_space_id=True)
    space_engine.update_nova_management(
        space,
        yolo=True,
        enrolled=False,
        confirmation=None,
        trusted_project_root=None,
        actor=DASHBOARD_ACTOR,
    )
    before = space.load_config()

    response = TestClient(web_server.app).post(
        "/api/space/config",
        headers=_headers(web_server),
        json={"slug": "alpha", "description": "ordinary edit"},
    )

    assert response.status_code == 200
    after = space.load_config()
    assert after["space_id"] == before["space_id"]
    assert after["nova_management"] == before["nova_management"]
    assert after["nova_management_audit"] == before["nova_management_audit"]

    space.config_path.write_text("nova_management_audit: malformed\n", encoding="utf-8")
    malformed_before = space.config_path.read_bytes()
    malformed = TestClient(web_server.app).post(
        "/api/space/config",
        headers=_headers(web_server),
        json={"slug": "alpha", "description": "must not overwrite"},
    )

    assert malformed.status_code == 409
    assert space.config_path.read_bytes() == malformed_before


def test_management_post_fails_closed_without_dashboard_principal(monkeypatch, tmp_path):
    """A management write cannot use a request body or generic fallback actor."""
    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space_engine.Space("alpha", "Alpha").save_config({"name": "Alpha"}, mint_space_id=True)
    monkeypatch.setattr(web_server, "dashboard_session_principal", lambda _request: None)

    response = TestClient(web_server.app).post(
        "/api/space/nova-management",
        headers=_headers(web_server),
        json={"slug": "alpha", "yolo": False, "enrolled": False, "actor": "attacker"},
    )

    assert response.status_code == 403


def test_generic_config_refuses_malformed_legacy_audit(monkeypatch, tmp_path):
    """Legacy JSONL corruption cannot be erased through an ordinary config edit."""
    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha"}, mint_space_id=True)
    (space.root / "nova-management-audit.jsonl").write_text("not json\n", encoding="utf-8")
    before = space.config_path.read_bytes()

    response = TestClient(web_server.app).post(
        "/api/space/config",
        headers=_headers(web_server),
        json={"slug": "alpha", "description": "must not write"},
    )

    assert response.status_code == 409
    assert space.config_path.read_bytes() == before


def test_audit_get_reads_legacy_then_generic_update_migrates_it(monkeypatch, tmp_path):
    """A pure audit GET exposes JSONL, while a later generic write preserves it in YAML."""
    import json
    import uuid

    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space_id = uuid.uuid4().hex
    event = {
        "actor": DASHBOARD_ACTOR,
        "timestamp": 1_700_000_001.0,
        "space_id": space_id,
        "root_fingerprint": "",
        "policy_revision": 1,
        "governance_revision": 1,
        "previous": {"yolo": False, "enrolled": False, "revision": 0},
        "next": {"yolo": True, "enrolled": False, "revision": 1},
    }
    space.save_config({
        "name": "Alpha",
        "space_id": space_id,
        "nova_management": event["next"],
    })
    (space.root / "nova-management-audit.jsonl").write_text(
        json.dumps(event) + "\n", encoding="utf-8"
    )
    before_get = space.config_path.read_bytes()
    client = TestClient(web_server.app)
    headers = _headers(web_server)

    audit = client.get("/api/space/nova-management/audit?slug=alpha", headers=headers)

    assert audit.status_code == 200
    assert audit.json()["events"] == [event]
    assert space.config_path.read_bytes() == before_get

    update = client.post(
        "/api/space/config",
        headers=headers,
        json={"slug": "alpha", "description": "legacy retained"},
    )

    assert update.status_code == 200
    assert space.load_config()["nova_management_audit"] == [event]
    assert (space.root / "nova-management-audit.jsonl").is_file()


def test_audit_get_returns_conflict_for_corrupt_evidence(monkeypatch, tmp_path):
    """Audit GET is pure but fail-closed, not an internal-server-error, on corruption."""
    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space.root.mkdir(parents=True)
    space.config_path.write_text("nova_management_audit:\n- broken\n", encoding="utf-8")

    response = TestClient(web_server.app).get(
        "/api/space/nova-management/audit?slug=alpha", headers=_headers(web_server)
    )

    assert response.status_code == 409


@pytest.mark.parametrize(
    "corrupt_yaml",
    ["{not valid", "- not-a-mapping\n", "[]\n", "false\n", "null\n", "~\n"],
)
def test_all_space_management_routes_fail_closed_for_corrupt_top_level_yaml(
    monkeypatch, tmp_path, corrupt_yaml,
):
    """No management or generic route may replace syntactically invalid/non-mapping YAML."""
    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space.root.mkdir(parents=True)
    space.config_path.write_text(corrupt_yaml, encoding="utf-8")
    before = space.config_path.read_bytes()
    client = TestClient(web_server.app)
    headers = _headers(web_server)

    management_get = client.get("/api/space/nova-management?slug=alpha", headers=headers)
    audit_get = client.get("/api/space/nova-management/audit?slug=alpha", headers=headers)
    generic_get = client.get("/api/space/config?slug=alpha", headers=headers)
    management_post = client.post(
        "/api/space/nova-management",
        headers=headers,
        json={"slug": "alpha", "yolo": False, "enrolled": False},
    )
    generic_post = client.post(
        "/api/space/config",
        headers=headers,
        json={"slug": "alpha", "description": "must not replace source"},
    )

    assert management_get.status_code == 409
    assert audit_get.status_code == 409
    assert generic_get.status_code == 409
    assert "_space_config_malformed" not in generic_get.text
    assert management_post.status_code == 409
    assert generic_post.status_code == 409
    assert space.config_path.read_bytes() == before
