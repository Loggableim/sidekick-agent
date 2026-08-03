from pathlib import Path
from web.api.subagent_history import list_history, record

def test_session_scoped_redacted_history(tmp_path: Path):
    record(tmp_path, subagent_id="sa-1", session_id="chat-a", space_slug="aquarium-zentrum", status="completed", summary="token=secret C:\\private\\file.py")
    record(tmp_path, subagent_id="sa-2", session_id="chat-b", space_slug="finanzjunkie", status="failed", summary="other")
    rows = list_history(tmp_path, session_id="chat-a")
    assert len(rows) == 1 and rows[0]["space_slug"] == "aquarium-zentrum"
    assert "secret" not in rows[0]["summary"] and "private" not in rows[0]["summary"]
    assert list_history(tmp_path, session_id="chat-missing") == []

def test_subagents_get_returns_only_requested_session(monkeypatch, tmp_path: Path):
    from cli import web_server
    from web.api import routes
    from fastapi.testclient import TestClient
    record(tmp_path, subagent_id="sa-a", session_id="chat-a", space_slug="aquarium-zentrum", status="completed", summary="ok")
    record(tmp_path, subagent_id="sa-b", session_id="chat-b", space_slug="finanzjunkie", status="completed", summary="hidden")
    monkeypatch.setattr(routes, "_routes_active_home", lambda: tmp_path)
    response = TestClient(web_server.app).get("/api/subagents?session_id=chat-a", headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN, "Origin": "http://testserver"})
    assert response.status_code == 200
    assert [row["subagent_id"] for row in response.json()["history"]] == ["sa-a"]
