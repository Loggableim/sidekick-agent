"""Morph provider profile.

Morph offers OpenAI-compatible fast models, Fast Apply, WarpGrep, Compact,
and Router — all through a single API key at https://api.morphllm.com/v1.
"""

from providers import register_provider
from providers.base import ProviderProfile

morph = ProviderProfile(
    name="morph",
    aliases=("morphllm", "morph-llm"),
    api_mode="chat_completions",
    env_vars=("MORPH_API_KEY",),
    base_url="https://api.morphllm.com/v1",
    auth_type="api_key",
    default_aux_model="morph-qwen35-397b",
)

register_provider(morph)
