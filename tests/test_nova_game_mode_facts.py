from pathlib import Path
from types import SimpleNamespace


def test_async_extract_facts_uses_remote_deepseek_in_game_mode(monkeypatch, tmp_path):
    from web.api import models
    from web.api import routes

    calls = []
    memory_dir = tmp_path / "memories"
    session = SimpleNamespace(
        session_id="sess_1",
        archived=True,
        title="Archived Nova chat",
        workspace_slug="nova",
        messages=[
            {"role": "user", "content": "Ich mag kurze, klare Titel."},
            {"role": "assistant", "content": "Verstanden."},
        ],
    )

    def fake_call_llm(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            "[PREFERENCE] User mag kurze, klare Titel.\n"
                            "[DECISION] Nova arbeitet im Game Mode mit Remote-LLM."
                        )
                    )
                )
            ]
        )

    monkeypatch.setattr(models.Session, "load", lambda sid: session)
    monkeypatch.setattr(routes, "is_game_mode_enabled", lambda: True)
    monkeypatch.setattr(
        "web.api.space_engine.get_or_create_space",
        lambda slug: SimpleNamespace(memory_dir=memory_dir),
    )
    monkeypatch.setattr("runtime.auxiliary_client.call_llm", fake_call_llm)

    routes._async_extract_facts("sess_1")

    assert calls, "Game Mode should route fact extraction through Ollama Cloud"
    assert calls[0]["kwargs"]["provider"] == "ollama-cloud"
    assert calls[0]["kwargs"]["model"] == "deepseek-v4-flash"
    assert (memory_dir / "MEMORY.md").exists()
    text = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "[SESSION: sess_1]" in text
    assert "Remote-LLM" in text


def test_extract_facts_via_llamacpp_returns_none_in_game_mode(monkeypatch, tmp_path):
    from web.api import config as cfg
    from web.api import models

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("llama.cpp must not be called in Game Mode")),
    )

    assert (
        models._extract_facts_via_llamacpp(
            [
                {"role": "user", "content": "Ich mag kurze, klare Titel."},
                {"role": "assistant", "content": "Verstanden."},
            ],
            "sess_1",
            "Archived Nova chat",
        )
        is None
    )
