from __future__ import annotations

import importlib
from pathlib import Path

import pytest


TestClient = pytest.importorskip("fastapi.testclient").TestClient


def test_worktree_settings_endpoint_roundtrips(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import config as cli_config
    from cli import web_server

    cli_config = importlib.reload(cli_config)
    web_server = importlib.reload(web_server)

    config_state = cli_config.load_config()
    config_state["worktree"] = {"enabled": False, "cleanup_on_exit": True}
    cli_config.save_config(config_state)

    client = TestClient(web_server.app)
    headers = {"X-Hermes-Session-Token": web_server._SESSION_TOKEN}

    response = client.get("/api/worktree/settings", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "cleanup_on_exit": True}

    saved = client.post(
        "/api/worktree/settings",
        headers=headers,
        json={"enabled": True, "cleanup_on_exit": False},
    )

    assert saved.status_code == 200
    assert saved.json() == {"ok": True, "enabled": True, "cleanup_on_exit": False}
    reloaded = cli_config.load_config()
    assert reloaded["worktree"] == {"enabled": True, "cleanup_on_exit": False}


def test_worktree_settings_ui_contract_mentions_worktree_controls():
    repo_root = Path(__file__).resolve().parents[1]
    index_html = (repo_root / "web/static/index.html").read_bytes().decode("utf-8", errors="replace")
    ui_js = (repo_root / "web/static/ui.js").read_bytes().decode("utf-8", errors="replace")

    assert 'id="settingsWorktreeEnabled"' in index_html
    assert 'id="settingsWorktreeCleanupOnExit"' in index_html
    assert 'onclick="saveWorktreeSettings()"' in index_html
    assert "async function loadWorktreeSettings()" in ui_js
    assert "async function saveWorktreeSettings()" in ui_js
    assert "loadWorktreeSettings();" in ui_js
