from __future__ import annotations

from cli.models import opencode_model_api_mode, opencode_model_runtime_fallback


def test_opencode_go_deepseek_falls_back_to_stable_model():
    assert opencode_model_runtime_fallback("opencode-go", "deepseek-v4-flash") == "glm-5"
    assert opencode_model_runtime_fallback("opencode-go", "deepseek-v4-pro") == "glm-5"


def test_opencode_go_keeps_supported_models():
    assert opencode_model_runtime_fallback("opencode-go", "glm-5.1") == "glm-5.1"
    assert opencode_model_api_mode("opencode-go", "glm-5.1") == "chat_completions"
