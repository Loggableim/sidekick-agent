"""Minimal runtime-provider shim for runtime migration.

Routes resolve_runtime_provider() calls to the new runtime provider logic.
"""
from __future__ import annotations

import logging
from typing import Any

from runtime.config import load_config

logger = logging.getLogger(__name__)

# API mode constants
API_MODE_CHAT_COMPLETIONS = "chat_completions"
API_MODE_CODEX_RESPONSES = "codex_responses"


def resolve_runtime_provider(
    provider: str,
    model: str = "",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve runtime provider configuration.

    Returns a dict with at minimum base_url and api_mode keys.
    """
    if config is None:
        config = load_config()

    base_urls = {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com/v1",
        "google": "https://generativelanguage.googleapis.com/v1beta",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
        "bedrock": "",
        "openrouter": "https://openrouter.ai/api/v1",
        "ollama": "http://localhost:11434/v1",
        "lmstudio": "http://localhost:30000/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "mistral": "https://api.mistral.ai/v1",
        "groq": "https://api.groq.com/openai/v1",
        "together": "https://api.together.xyz/v1",
        "xai": "https://api.x.ai/v1",
        "perplexity": "https://api.perplexity.ai",
        "azure": "",
        "github": "https://models.inference.ai.azure.com",
        "copilot": "",
        "codex": "",
    }

    provider_lower = provider.strip().lower() if provider else ""

    # Check custom providers in config
    custom_providers = config.get("custom_providers", {}) if isinstance(config, dict) else {}
    if provider_lower in custom_providers:
        entry = custom_providers[provider_lower]
        if isinstance(entry, dict):
            return {
                "base_url": entry.get("base_url", ""),
                "api_mode": entry.get("api_mode", API_MODE_CHAT_COMPLETIONS),
                "provider": provider_lower,
                "model": model,
            }

    base_url = base_urls.get(provider_lower, "")
    return {
        "base_url": base_url,
        "api_mode": API_MODE_CHAT_COMPLETIONS,
        "provider": provider_lower,
        "model": model,
    }


def resolve_requested_provider(
    provider: str | None,
    model: str | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the effective provider from provider name and/or model."""
    effective_provider = provider or ""
    effective_model = model or ""

    # If no provider but model with slash (e.g. openai/gpt-4o), extract provider
    if not effective_provider and effective_model and "/" in effective_model:
        parts = effective_model.split("/", 1)
        effective_provider = parts[0]

    return resolve_runtime_provider(effective_provider, effective_model, config)


def format_runtime_provider_error(provider: str, error: str) -> str:
    """Format a runtime provider error message."""
    return f"Runtime provider '{provider}': {error}"


__all__ = [
    "resolve_runtime_provider",
    "resolve_requested_provider",
    "format_runtime_provider_error",
    "API_MODE_CHAT_COMPLETIONS",
    "API_MODE_CODEX_RESPONSES",
]