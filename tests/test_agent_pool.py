from __future__ import annotations

from importlib import reload


def test_agent_pool_resolves_auth_json_from_hermes_home_when_sidekick_home_is_missing(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes-home"
    auth_json = hermes_home / "auth.json"
    auth_json.parent.mkdir(parents=True, exist_ok=True)
    auth_json.write_text('{"credential_pool": {}}', encoding="utf-8")

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    from runtime.gateway import agent_pool

    reload(agent_pool)

    assert agent_pool._resolve_auth_path() == str(auth_json)
