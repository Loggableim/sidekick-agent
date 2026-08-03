"""Kanban remains an optional projection, never a Presence truth source."""

from pathlib import Path

from fastapi.testclient import TestClient

from test_fastapi_route_bridge import _headers


def test_presence_get_never_projects_or_writes_kanban_for_three_spaces(monkeypatch, tmp_path: Path) -> None:
    from cli import web_server
    from web.api import space_engine, swarm

    spaces_root = tmp_path / "spaces"
    for slug in ("nova", "finanzjunkie", "aquarium-zentrum"):
        root = spaces_root / slug; root.mkdir(parents=True)
        space_engine.Space(slug, slug).save_config({"name": slug, "project_dir": str(root)}, mint_space_id=True)
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path))
    monkeypatch.setattr(web_server, "_start_nova_space_supervision_ticker", lambda: (_ for _ in ()).throw(AssertionError("GET started Nova")))
    monkeypatch.setattr(swarm, "project_swarm_run_to_kanban", lambda *args: (_ for _ in ()).throw(AssertionError("Presence projected Kanban")))
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    response = TestClient(web_server.app).get("/api/nova/presence-card", headers=_headers(web_server))
    assert response.status_code == 200
    after = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after
