from __future__ import annotations

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
