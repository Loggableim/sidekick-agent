"""Kanban remains an optional projection, never a Presence truth source."""

from pathlib import Path

from fastapi.testclient import TestClient

from test_fastapi_route_bridge import _headers


def test_presence_get_never_projects_or_writes_kanban_for_three_spaces(monkeypatch, tmp_path: Path) -> None:
    from cli import web_server
    from web.api import space_engine, swarm

    spaces_root = tmp_path / "spaces"
    # Redirect the spaces root BEFORE any Space.save_config call: Space.root resolves
    # _spaces_root() at property-access time, so an unpatched write lands in the LIVE
    # Sidekick home and survives this test's tmp cleanup as a dead project_dir
    # (observed 2026-09-14: live nova/finanzjunkie/aquarium-zentrum space.yaml
    # contaminated with .test-tmp paths + 1265 "project_dir does not exist" warnings).
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "legacy-spaces")
    monkeypatch.setattr(space_engine, "_SPACE_CACHE", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_ROOTS", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_TS", 0.0)
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path))
    for slug in ("nova", "finanzjunkie", "aquarium-zentrum"):
        root = spaces_root / slug; root.mkdir(parents=True)
        space_engine.Space(slug, slug).save_config({"name": slug, "project_dir": str(root)}, mint_space_id=True)
    monkeypatch.setattr(web_server, "_start_nova_space_supervision_ticker", lambda: (_ for _ in ()).throw(AssertionError("GET started Nova")))
    monkeypatch.setattr(swarm, "project_swarm_run_to_kanban", lambda *args: (_ for _ in ()).throw(AssertionError("Presence projected Kanban")))
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    response = TestClient(web_server.app).get("/api/nova/presence-card", headers=_headers(web_server))
    assert response.status_code == 200
    after = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after
