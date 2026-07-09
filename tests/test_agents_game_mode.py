from types import SimpleNamespace


def test_agents_llm_uses_remote_deepseek_in_game_mode(monkeypatch, tmp_path):
    from web.api import agents
    from web.api import config as cfg

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    monkeypatch.setattr(
        agents,
        "_load_llm_config",
        lambda: {
            "provider": "ollama",
            "api_key": "local-key",
            "model": "qwen3:4b",
            "base_url": "http://127.0.0.1:11434",
        },
    )

    captured = {}

    def fake_call_llm(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="remote agent reply"))
            ]
        )

    monkeypatch.setattr("runtime.auxiliary_client.call_llm", fake_call_llm)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local agent model must not be called in Game Mode")),
    )

    result = agents._call_llm([{"role": "user", "content": "hello"}], timeout=15)

    assert captured["provider"] == "ollama-cloud"
    assert captured["model"] == "deepseek-v4-flash"
    assert result == "remote agent reply"
