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


def test_login_uses_public_route_page_when_password_auth_is_enabled(monkeypatch):
    from cli import web_server
    from web.api import auth

    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)

    response = TestClient(web_server.app).get("/login?next=%2Fworkspace")

    assert response.status_code == 200
    assert "id=\"login-form\"" in response.text
    assert "location" not in response.headers
