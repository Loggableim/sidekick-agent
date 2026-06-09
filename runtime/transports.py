"""Transport layer — builds API kwargs from normalized parameters.

Provides ``get_transport(api_mode)`` which returns a transport instance
for the given mode (chat_completions, codex_responses, anthropic_messages,
bedrock_converse).  Each transport implements ``build_kwargs()``.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

from runtime.lmstudio_reasoning import resolve_lmstudio_effort
from runtime.moonshot_schema import is_moonshot_model, sanitize_moonshot_tools
from runtime.prompt_builder import DEVELOPER_ROLE_MODELS
from providers.base import OMIT_TEMPERATURE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

_REGISTRY: dict = {}


def register_transport(api_mode: str, transport_cls: type) -> None:
    _REGISTRY[api_mode] = transport_cls


def get_transport(api_mode: str):
    """Return a transport instance for the given api_mode."""
    cls = _REGISTRY.get(api_mode)
    if cls is not None:
        return cls()
    # Auto-discover on first call
    _discover_transports()
    cls = _REGISTRY.get(api_mode)
    if cls is None:
        return None
    return cls()


def _discover_transports() -> None:
    pass  # All transports are registered at import time above


# ---------------------------------------------------------------------------
# ChatCompletionsTransport
# ---------------------------------------------------------------------------

_chat_completions_discovered = False


class ChatCompletionsTransport:
    """Builds kwargs for ``client.chat.completions.create(**kwargs)``."""

    def validate_response(self, response: Any) -> bool:
        """Return True for a usable Chat Completions response."""
        if response is None:
            return False
        choices = getattr(response, "choices", None)
        return bool(choices)

    def normalize_response(self, response: Any, **_: Any) -> Any:
        """Return the first assistant message with finish_reason attached."""
        choice = response.choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            message = {}
        finish_reason = getattr(choice, "finish_reason", None) or "stop"
        try:
            setattr(message, "finish_reason", finish_reason)
            return message
        except Exception:
            normalized = dict(message) if isinstance(message, dict) else {"content": str(message)}
            normalized["finish_reason"] = finish_reason
            return normalized

    def build_kwargs(
        self,
        *,
        model: str,
        messages: list,
        tools: Optional[list] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
        ephemeral_max_output_tokens: Optional[int] = None,
        max_tokens_param_fn=None,
        reasoning_config: Optional[dict] = None,
        request_overrides: Optional[dict] = None,
        session_id: Optional[str] = None,
        model_lower: str = "",
        # Provider detection flags
        is_openrouter: bool = False,
        is_nous: bool = False,
        is_qwen_portal: bool = False,
        is_github_models: bool = False,
        is_nvidia_nim: bool = False,
        is_kimi: bool = False,
        is_tokenhub: bool = False,
        is_lmstudio: bool = False,
        is_custom_provider: bool = False,
        ollama_num_ctx: Optional[int] = None,
        provider_preferences: Optional[dict] = None,
        openrouter_min_coding_score: Optional[float] = None,
        # Qwen-specific
        qwen_prepare_fn=None,
        qwen_prepare_inplace_fn=None,
        qwen_session_metadata: Optional[dict] = None,
        # Temperature
        fixed_temperature: Optional[float] = None,
        omit_temperature: bool = False,
        # Reasoning
        supports_reasoning: bool = False,
        github_reasoning_extra: Optional[dict] = None,
        lmstudio_reasoning_options=None,
        anthropic_max_output: Optional[int] = None,
        # Provider profile (from providers/ registry)
        provider_profile=None,
        provider_name: Optional[str] = None,
    ) -> dict:
        """Build the keyword arguments dict for ``chat.completions.create``."""
        messages = copy.deepcopy(messages)
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }

        # ── Provider profile hooks ──────────────────────────────────────────
        if provider_profile is not None:
            # 1. Message preprocessing
            messages = provider_profile.prepare_messages(messages)
            kwargs["messages"] = messages

        # Tools
        if tools:
            sanitized = list(tools)
            # Moonshot has strict tool schema requirements
            if is_kimi or is_tokenhub:
                sanitized = sanitize_moonshot_tools(sanitized)
            kwargs["tools"] = sanitized

        # Max tokens — priority: ephemeral > user > profile default > api default
        if max_tokens_param_fn is not None:
            if ephemeral_max_output_tokens is not None:
                kwargs.update(max_tokens_param_fn(ephemeral_max_output_tokens))
            elif max_tokens is not None:
                kwargs.update(max_tokens_param_fn(max_tokens))
            elif provider_profile is not None and provider_profile.default_max_tokens is not None:
                kwargs.update(max_tokens_param_fn(provider_profile.default_max_tokens))
        elif anthropic_max_output is not None:
            kwargs["max_tokens"] = anthropic_max_output
        elif provider_profile is not None and provider_profile.default_max_tokens is not None:
            kwargs["max_tokens"] = provider_profile.default_max_tokens

        # Temperature
        _temp_override = None
        if provider_profile is not None:
            _temp_override = provider_profile.fixed_temperature
        if omit_temperature or _temp_override is OMIT_TEMPERATURE:
            pass  # Don't send temperature at all
        elif fixed_temperature is not None:
            kwargs["temperature"] = fixed_temperature
        elif _temp_override is not None:
            kwargs["temperature"] = _temp_override
        else:
            kwargs["temperature"] = 0.6

        # Top-p
        kwargs["top_p"] = 0.95

        # Timeout
        if timeout is not None:
            kwargs["timeout"] = timeout

        # Extra body for reasoning
        extra_body: Dict[str, Any] = {}

        # Provider profile extra body
        if provider_profile is not None:
            extra_body.update(provider_profile.build_extra_body(
                session_id=session_id,
            ))

        if supports_reasoning:
            if reasoning_config and isinstance(reasoning_config, dict):
                effort = reasoning_config.get("effort")
                if effort:
                    extra_body["reasoning"] = {"effort": effort}

        if github_reasoning_extra:
            extra_body.update(github_reasoning_extra)

        if lmstudio_reasoning_options:
            effort = resolve_lmstudio_effort(
                model, lmstudio_reasoning_options, reasoning_config or {}
            )
            if effort:
                extra_body["reasoning"] = {"effort": effort}

        # Qwen session metadata
        if is_qwen_portal and qwen_session_metadata:
            kwargs["metadata"] = qwen_session_metadata

        # Provider preferences (OpenRouter routing)
        if provider_preferences:
            extra_body["provider"] = provider_preferences

        # OpenRouter min coding score
        if is_openrouter and openrouter_min_coding_score is not None:
            extra_body["min_coding_score"] = openrouter_min_coding_score

        # Ollama num_ctx
        if ollama_num_ctx is not None:
            extra_body["num_ctx"] = ollama_num_ctx

        # NVIDIA NIM — known to support max_tokens
        if is_nvidia_nim:
            pass

        # Developer role model support
        if model_lower in DEVELOPER_ROLE_MODELS:
            try:
                from runtime.prompt_builder import _upgrade_system_to_developer
                kwargs["messages"] = _upgrade_system_to_developer(messages)
            except ImportError:
                pass

        if extra_body:
            kwargs["extra_body"] = extra_body

        # Provider profile extra_body + top-level kwargs extras
        if provider_profile is not None:
            _eb_extras, _tl_extras = provider_profile.build_api_kwargs_extras(
                reasoning_config=reasoning_config,
                session_id=session_id,
            )
            if _eb_extras:
                kwargs.setdefault("extra_body", {}).update(_eb_extras)
            if _tl_extras:
                kwargs.update(_tl_extras)

        # Anthropic max output tokens
        if anthropic_max_output is not None:
            kwargs["max_tokens"] = anthropic_max_output

        return kwargs

    def preflight_kwargs(self, api_kwargs: dict, *, allow_stream: bool = True) -> dict:
        """Pre-flight adjustments before the API call.
        
        Returns the (possibly modified) kwargs dict.
        """
        kwargs = copy.deepcopy(api_kwargs)

        # If not streaming, ensure stream=None
        if not allow_stream:
            kwargs.pop("stream", None)

        return kwargs


register_transport("chat_completions", ChatCompletionsTransport)
