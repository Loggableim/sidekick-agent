from __future__ import annotations

from importlib import reload


def test_agent_pool_resolves_auth_json_from_sidekick_home(monkeypatch, tmp_path):
    sidekick_home = tmp_path / "sidekick-home"
    auth_json = sidekick_home / "auth.json"
    auth_json.parent.mkdir(parents=True, exist_ok=True)
    auth_json.write_text('{"credential_pool": {}}', encoding="utf-8")

    monkeypatch.setenv("SIDEKICK_HOME", str(sidekick_home))

    from runtime.gateway import agent_pool

    reload(agent_pool)

    assert agent_pool._resolve_auth_path() == str(auth_json)
