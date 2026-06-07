"""Minimal model utilities for runtime migration.

Provides normalize_provider() and model context length lookups that
runtime modules import from sidekick_cli.models.
"""
from __future__ import annotations

import logging
from typing import Any

from runtime.config import get_custom_provider_context_length

logger = logging.getLogger(__name__)


def normalize_provider(provider: str) -> str:
    """Normalize provider name to canonical form."""
    mapping = {
        "openai": "openai",
        "anthropic": "anthropic",
        "google": "google",
        "gemini": "gemini",
        "bedrock": "bedrock",
        "aws": "bedrock",
        "openrouter": "openrouter",
        "ollama": "ollama",
        "lmstudio": "lmstudio",
        "deepseek": "deepseek",
        "mistral": "mistral",
        "groq": "groq",
        "together": "together",
        "xai": "xai",
        "perplexity": "perplexity",
        "cohere": "cohere",
        "azure": "azure",
        "github": "github",
        "copilot": "copilot",
        "codex": "codex",
        "claude": "anthropic",
        "gpt": "openai",
    }
    key = provider.strip().lower()
    result = mapping.get(key)
    if result:
        return result
    # Check if it matches a known custom provider
    if "/" in key:
        parts = key.split("/", 1)
        if parts[0] and parts[1]:
            return parts[0]
    return provider


def get_copilot_model_context(model_id: str) -> int:
    """Return context length for a GitHub Copilot model ID."""
    known = {
        "gpt-4o": 128000,
        "gpt-4o-mini": 128000,
        "claude-sonnet-4": 200000,
        "claude-3.5-sonnet": 200000,
        "claude-3.5-haiku": 200000,
        "o1": 200000,
        "o3-mini": 200000,
        "deepseek-v3": 65536,
        "deepseek-r1": 65536,
        "gemini-2.0-flash": 1048576,
        "gemini-2.5-pro": 1048576,
    }
    return known.get(model_id, 128000)


__all__ = [
    "normalize_provider",
    "get_copilot_model_context",
]