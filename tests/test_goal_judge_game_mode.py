from types import SimpleNamespace


def test_goal_judge_uses_remote_deepseek_in_game_mode(monkeypatch):
    from cli import goals
    from web.api import config as cfg

    monkeypatch.setattr(cfg, "is_game_mode_enabled", lambda: True)
    monkeypatch.setattr(
        "runtime.auxiliary_client.get_text_auxiliary_client",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("local judge path must not be used in Game Mode")
        ),
    )

    captured = {}

    def fake_call_llm(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"done": true, "reason": "goal complete"}'
                    )
                )
            ]
        )

    monkeypatch.setattr("runtime.auxiliary_client.call_llm", fake_call_llm)

    verdict, reason, parse_failed = goals.judge_goal(
        "Ship the feature",
        "All done",
        timeout=12,
    )

    assert verdict == "done"
    assert reason == "goal complete"
    assert parse_failed is False
    assert captured["provider"] == "ollama-cloud"
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["temperature"] == 0
    assert captured["max_tokens"] == 200
    assert captured["timeout"] == 12
