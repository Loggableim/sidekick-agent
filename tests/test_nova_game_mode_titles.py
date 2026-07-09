from types import SimpleNamespace


def test_async_ollama_title_uses_remote_deepseek_in_game_mode(monkeypatch):
    from web.api import models
    from web.api import routes

    session = SimpleNamespace(
        title="⏳ Titel wird generiert...",
        messages=[
            {"role": "user", "content": "Erstelle eine kurze Projektüberschrift."},
            {"role": "assistant", "content": "Klar."},
        ],
        save=lambda: None,
    )

    remote_calls = []

    monkeypatch.setattr(models.Session, "load", lambda session_id: session)
    monkeypatch.setattr(routes, "is_game_mode_enabled", lambda: True)
    monkeypatch.setattr(routes, "title_from", lambda messages, fallback: "heuristic title")
    monkeypatch.setattr(
        "web.api.streaming.generate_title_raw_via_aux",
        lambda user_text, assistant_text, provider="", model="", base_url="": remote_calls.append(
            (user_text, assistant_text, provider, model, base_url)
        )
        or ("DeepSeek V4 Flash title", "llm_aux"),
    )

    routes._async_ollama_title("session_1")

    assert remote_calls, "Game Mode should route title generation through Ollama Cloud"
    assert session.title == "DeepSeek V4 Flash title"


def test_generate_title_via_ollama_returns_none_in_game_mode(monkeypatch, tmp_path):
    from web.api import config as cfg
    from web.api import models

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Ollama must not be called in Game Mode")),
    )

    assert models._generate_title_via_ollama(
        [
            {"role": "user", "content": "Erstelle eine kurze Projektüberschrift."},
            {"role": "assistant", "content": "Klar."},
        ]
    ) is None
