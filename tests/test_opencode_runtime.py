from __future__ import annotations

from cli.models import normalize_opencode_model_id, opencode_model_api_mode
from runtime.auxiliary_client import _API_KEY_PROVIDER_AUX_MODELS_FALLBACK
from runtime.transports import ChatCompletionsTransport
from types import SimpleNamespace


def test_opencode_go_keeps_deepseek_model_ids():
    assert normalize_opencode_model_id("opencode-go", "deepseek-v4-flash") == "deepseek-v4-flash"
    assert normalize_opencode_model_id("opencode-go", "opencode-go/deepseek-v4-flash") == "deepseek-v4-flash"
    assert opencode_model_api_mode("opencode-go", "deepseek-v4-flash") == "chat_completions"


def test_opencode_go_aux_default_has_large_context_for_compression():
    assert _API_KEY_PROVIDER_AUX_MODELS_FALLBACK["opencode-go"] == "deepseek-v4-flash"
    assert opencode_model_api_mode("opencode-go", "glm-5.1") == "chat_completions"


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
