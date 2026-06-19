from __future__ import annotations

import importlib
import builtins
from pathlib import Path
from cli.models import normalize_opencode_model_id, opencode_model_api_mode, opencode_provider_for_model
import cli.runtime_provider as runtime_provider
from runtime.auxiliary_client import _API_KEY_PROVIDER_AUX_MODELS_FALLBACK
from runtime.transports import ChatCompletionsTransport
from types import SimpleNamespace


def test_opencode_go_keeps_deepseek_model_ids():
    assert normalize_opencode_model_id("opencode-go", "deepseek-v4-flash") == "deepseek-v4-flash"
    assert normalize_opencode_model_id("opencode-go", "opencode-go/deepseek-v4-flash") == "deepseek-v4-flash"
    assert normalize_opencode_model_id("opencode-go", "deepseek/deepseek-v4-flash") == "deepseek-v4-flash"
    assert opencode_model_api_mode("opencode-go", "deepseek-v4-flash") == "chat_completions"
    assert opencode_provider_for_model("opencode-zen", "deepseek-v4-flash") == "opencode-go"


def test_opencode_go_aux_default_has_large_context_for_compression():
    assert _API_KEY_PROVIDER_AUX_MODELS_FALLBACK["opencode-go"] == "deepseek-v4-flash"
    assert opencode_model_api_mode("opencode-go", "glm-5.1") == "chat_completions"


def test_runtime_provider_routes_deepseek_flash_to_opencode_go(monkeypatch):
    monkeypatch.setattr(runtime_provider, "resolve_provider", lambda *args, **kwargs: "opencode-zen")
    monkeypatch.setattr(
        runtime_provider,
        "_get_model_config",
        lambda: {
            "provider": "opencode-zen",
            "default": "deepseek-v4-flash",
            "base_url": "https://opencode.ai/zen/v1",
        },
    )
    monkeypatch.setattr(runtime_provider, "load_pool", lambda provider: None)
    monkeypatch.setattr(
        runtime_provider,
        "resolve_api_key_provider_credentials",
        lambda provider: {
            "api_key": "test-key",
            "base_url": runtime_provider.PROVIDER_REGISTRY[provider].inference_base_url,
            "source": "test",
        },
    )

    runtime = runtime_provider.resolve_runtime_provider(
        requested="opencode-zen",
        target_model="deepseek-v4-flash",
    )

    assert runtime["provider"] == "opencode-go"
    assert runtime["api_mode"] == "chat_completions"
    assert runtime["base_url"] == "https://opencode.ai/zen/go/v1"


def test_cron_scheduler_uses_modern_runtime_provider_import():
    source = Path("runtime/cron/scheduler.py").read_text(encoding="utf-8")

    assert "from cli.runtime_provider import (" in source
    assert "from runtime._compat.shim_cli.runtime_provider import" not in source


def test_cron_scheduler_prefers_direct_tools_config_import():
    source = Path("runtime/cron/scheduler.py").read_text(encoding="utf-8")

    assert "from cli.tools_config import _get_platform_tools" in source
    assert source.index("from cli.tools_config import _get_platform_tools") < source.index("from runtime._compat.shim_cli.tools_config import _get_platform_tools")


def test_cron_tools_config_shim_exports_platform_tool_resolver():
    mod = importlib.import_module("runtime._compat.shim_cli.tools_config")

    assert callable(mod._get_platform_tools)


def test_cron_tools_config_shim_resolves_without_cli_tools_config(monkeypatch):
    mod = importlib.import_module("runtime._compat.shim_tools_config")
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "cli.tools_config":
            raise ImportError("blocked cli.tools_config")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    resolved = mod._get_platform_tools({}, "cron")

    assert "terminal" in resolved
    assert "cronjob" in resolved
    assert "moa" not in resolved


def test_chat_completions_transport_validates_choices_response():
    class Response:
        choices = [object()]

    assert ChatCompletionsTransport().validate_response(Response()) is True
    assert ChatCompletionsTransport().validate_response(None) is False


def test_chat_completions_transport_normalizes_first_choice_message():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ]
    )

    message = ChatCompletionsTransport().normalize_response(response)

    assert message.content == "ok"
    assert message.tool_calls is None
    assert message.finish_reason == "stop"
