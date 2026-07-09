import pytest


def test_managed_message_handles_unset_env(monkeypatch):
    monkeypatch.delenv("SIDEKICK_MANAGED", raising=False)

    from cli.config import format_managed_message

    message = format_managed_message("update Sidekick")

    assert "Cannot update Sidekick" in message


def test_runtime_provider_handles_unset_provider_env(monkeypatch):
    monkeypatch.delenv("SIDEKICK_INFERENCE_PROVIDER", raising=False)

    import cli.runtime_provider as runtime_provider

    monkeypatch.setattr(runtime_provider, "_get_model_config", lambda: {})

    assert runtime_provider.resolve_requested_provider(None) == "auto"


def test_default_github_mcp_skips_without_pat(monkeypatch):
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)

    from tools.mcp_tool import _load_mcp_config

    servers = _load_mcp_config()

    assert "github" not in servers


def test_oneshot_provider_without_model_handles_unset_model_env(monkeypatch, capsys):
    monkeypatch.delenv("SIDEKICK_INFERENCE_MODEL", raising=False)

    from cli.oneshot import run_oneshot

    assert run_oneshot("hello", provider="openrouter") == 2
    assert "--provider requires --model" in capsys.readouterr().err


def test_oneshot_blocks_local_model_requests_in_game_mode(monkeypatch, tmp_path, capsys):
    from web.api import config as web_cfg

    monkeypatch.setattr(web_cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    web_cfg.save_settings({"game_mode_enabled": True})

    import cli.oneshot as oneshot
    import cli.runtime_provider as runtime_provider

    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "api_mode": "chat_completions",
            "api_key": None,
        },
    )

    monkeypatch.setattr(
        "run_agent.AIAgent",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("oneshot should not start a local model in Game Mode")),
    )

    code = oneshot.run_oneshot("hello", model="qwen3:4b", provider="ollama")

    assert code == 1
    stderr = capsys.readouterr().err
    assert "Game Mode is active" in stderr
    assert "local model requests are blocked" in stderr.lower()


def test_oneshot_allows_ollama_cloud_deepseek_v4_flash_in_game_mode(monkeypatch, tmp_path, capsys):
    from web.api import config as web_cfg

    monkeypatch.setattr(web_cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    web_cfg.save_settings({"game_mode_enabled": True})

    import cli.oneshot as oneshot
    import cli.runtime_provider as runtime_provider

    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: {
            "provider": "ollama-cloud",
            "base_url": "https://ollama.com/v1",
            "api_mode": "chat_completions",
            "api_key": "token",
        },
    )

    captured = {}

    class FakeAIAgent:
        def __init__(self, *args, **kwargs):
            captured["provider"] = kwargs.get("provider")
            captured["model"] = kwargs.get("model")
            captured["base_url"] = kwargs.get("base_url")

        def chat(self, prompt):
            return "remote ok"

    monkeypatch.setattr("run_agent.AIAgent", FakeAIAgent)

    code = oneshot.run_oneshot("hello", model="deepseek-v4-flash", provider="ollama-cloud")

    assert code == 0
    assert captured["provider"] == "ollama-cloud"
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["base_url"] == "https://ollama.com/v1"
    assert capsys.readouterr().out == "remote ok\n"


def test_run_agent_blocks_local_models_in_game_mode(monkeypatch, tmp_path):
    from web.api import config as web_cfg

    monkeypatch.setattr(web_cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    web_cfg.save_settings({"game_mode_enabled": True})

    from run_agent import AIAgent

    with pytest.raises(RuntimeError, match="Game Mode is active"):
        AIAgent(
            provider="ollama",
            base_url="http://127.0.0.1:11434",
            model="qwen3:4b",
        )


def test_delegate_task_returns_tool_error_for_blocked_game_mode_child(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from web.api import config as web_cfg

    monkeypatch.setattr(web_cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    web_cfg.save_settings({"game_mode_enabled": True})

    import tools.delegate_tool as delegate_tool

    monkeypatch.setattr(
        delegate_tool,
        "_resolve_delegation_credentials",
        lambda cfg, parent_agent: {
            "model": "qwen3:4b",
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "api_key": "no-key-required",
            "api_mode": "chat_completions",
        },
    )

    parent = SimpleNamespace(
        _delegate_depth=0,
        _credential_pool=None,
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="gpt-4.1",
        api_key="x",
        api_mode="chat_completions",
        session_id="s1",
        platform="cli",
        _session_db=None,
        _fallback_chain=None,
        providers_allowed=None,
        providers_ignored=None,
        providers_order=None,
        provider_sort=None,
        openrouter_min_coding_score=None,
        max_tokens=None,
        reasoning_config=None,
        prefill_messages=None,
        _active_children=[],
        _active_children_lock=None,
    )

    result = delegate_tool.delegate_task(goal="hello", parent_agent=parent)

    assert "Game Mode is active" in result
    assert "local model requests are blocked" in result.lower()


def test_gateway_detached_mode_handles_unset_env(monkeypatch):
    monkeypatch.delenv("SIDEKICK_GATEWAY_DETACHED", raising=False)

    import cli.gateway as gateway

    monkeypatch.setattr(gateway, "is_windows", lambda: True)

    assert isinstance(gateway._windows_gateway_should_absorb_console_controls(), bool)


def test_gateway_restart_drain_timeout_handles_unset_env(monkeypatch):
    monkeypatch.delenv("SIDEKICK_RESTART_DRAIN_TIMEOUT", raising=False)

    import cli.gateway as gateway

    monkeypatch.setattr(gateway, "read_raw_config", lambda: {})

    assert gateway._get_restart_drain_timeout() == gateway.DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT


def test_openrouter_cache_headers_handle_unset_env(monkeypatch):
    monkeypatch.delenv("SIDEKICK_OPENROUTER_CACHE", raising=False)
    monkeypatch.delenv("SIDEKICK_OPENROUTER_CACHE_TTL", raising=False)

    from runtime.auxiliary_client import build_or_headers

    headers = build_or_headers(
        {
            "response_cache": True,
            "response_cache_ttl": 600,
        }
    )

    assert headers["X-OpenRouter-Cache"] == "true"
    assert headers["X-OpenRouter-Cache-TTL"] == "600"


def test_shell_hooks_accept_env_handles_unset_env(monkeypatch):
    monkeypatch.delenv("SIDEKICK_ACCEPT_HOOKS", raising=False)

    from runtime.shell_hooks import _resolve_effective_accept

    assert _resolve_effective_accept({"hooks_auto_accept": False}, False) is False
    assert _resolve_effective_accept({"hooks_auto_accept": True}, False) is True


def test_voice_debug_handles_unset_env(monkeypatch):
    monkeypatch.delenv("SIDEKICK_VOICE_DEBUG", raising=False)

    from cli.voice import _debug

    _debug("noop")


def test_portable_home_bootstrap_sets_both_home_vars(monkeypatch):
    import importlib
    import sys
    from pathlib import Path

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("SIDEKICK_DISABLE_PORTABLE_HOME", raising=False)

    sys.modules.pop("cli.main", None)
    main = importlib.import_module("cli.main")

    portable_home = main.PROJECT_ROOT.parent / "home"

    assert Path(main.os.environ["SIDEKICK_HOME"]) == portable_home
    assert Path(main.os.environ["HERMES_HOME"]) == portable_home


def test_profile_override_bootstrap_sets_both_home_vars(monkeypatch, tmp_path):
    import importlib
    import sys
    from pathlib import Path

    base_home = tmp_path / "base-home"
    profile_home = base_home / "profiles" / "coder"
    profile_home.mkdir(parents=True)

    monkeypatch.setenv("SIDEKICK_HOME", str(base_home))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(sys, "argv", ["sidekick", "--profile", "coder"])

    sys.modules.pop("cli.main", None)
    main = importlib.import_module("cli.main")

    assert Path(main.os.environ["SIDEKICK_HOME"]) == profile_home
    assert Path(main.os.environ["HERMES_HOME"]) == profile_home


def test_profile_override_trusts_existing_profile_hermes_home(monkeypatch, tmp_path):
    import importlib
    import sys
    from pathlib import Path

    base_home = tmp_path / "base-home"
    profile_home = base_home / "profiles" / "coder"
    other_profile_home = base_home / "profiles" / "other"
    profile_home.mkdir(parents=True)
    other_profile_home.mkdir(parents=True)
    (base_home / "active_profile").write_text("other", encoding="utf-8")

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    monkeypatch.setenv("SIDEKICK_DISABLE_PORTABLE_HOME", "1")
    monkeypatch.setattr(sys, "argv", ["sidekick"])

    sys.modules.pop("cli.main", None)
    main = importlib.import_module("cli.main")

    assert Path(main.os.environ["HERMES_HOME"]) == profile_home
    assert main.os.environ.get("SIDEKICK_HOME") in {None, str(profile_home)}
