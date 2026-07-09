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


def test_streaming_title_aux_forces_remote_deepseek_in_game_mode(monkeypatch, tmp_path):
    from web.api import config as cfg
    from web.api import streaming

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    calls = []

    def fake_call_llm(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Game Mode DeepSeek title")
                )
            ]
        )

    monkeypatch.setattr("runtime.auxiliary_client.call_llm", fake_call_llm)

    raw, status = streaming.generate_title_raw_via_aux("User text", "Assistant text")

    assert raw == "Game Mode DeepSeek title"
    assert status == "llm_aux"
    assert calls, "Game Mode should route streaming title generation through Ollama Cloud"
    assert calls[0]["kwargs"]["provider"] == "ollama-cloud"
    assert calls[0]["kwargs"]["model"] == "deepseek-v4-flash"


def test_streaming_title_agent_uses_remote_deepseek_in_game_mode(monkeypatch, tmp_path):
    from web.api import config as cfg
    from web.api import streaming

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    remote_calls = []

    def fake_generate_title_raw_via_aux(user_text, assistant_text, provider="", model="", base_url=""):
        remote_calls.append(
            {
                "user_text": user_text,
                "assistant_text": assistant_text,
                "provider": provider,
                "model": model,
                "base_url": base_url,
            }
        )
        return "Game Mode DeepSeek title", "llm_aux"

    fake_agent = SimpleNamespace(
        provider="ollama",
        model="qwen3:4b",
        base_url="http://127.0.0.1:11434/v1",
        reasoning_config=None,
        _build_api_kwargs=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("local agent path must not be used in Game Mode")
        ),
        _run_codex_stream=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("local agent path must not be used in Game Mode")
        ),
        _normalize_codex_response=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("local agent path must not be used in Game Mode")
        ),
        _anthropic_messages_create=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("local agent path must not be used in Game Mode")
        ),
        _anthropic_preserve_dots=lambda: False,
        _ensure_primary_openai_client=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("local agent path must not be used in Game Mode")
        ),
    )

    monkeypatch.setattr(streaming, "generate_title_raw_via_aux", fake_generate_title_raw_via_aux)

    raw, status = streaming.generate_title_raw_via_agent(
        fake_agent,
        "User text",
        "Assistant text",
    )

    assert raw == "Game Mode DeepSeek title"
    assert status == "llm_aux"
    assert remote_calls, "Game Mode should bypass the local agent title path"
    assert remote_calls[0]["provider"] == "ollama-cloud"
    assert remote_calls[0]["model"] == "deepseek-v4-flash"
