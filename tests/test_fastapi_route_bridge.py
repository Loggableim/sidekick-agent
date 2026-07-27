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


def test_login_uses_public_route_page_when_password_auth_is_enabled(monkeypatch):
    from cli import web_server
    from web.api import auth

    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)

    response = TestClient(web_server.app).get("/login?next=%2Fworkspace")

    assert response.status_code == 200
    assert "id=\"login-form\"" in response.text
    assert "location" not in response.headers
