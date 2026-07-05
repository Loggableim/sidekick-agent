"""Shared provider response metadata captured from LLM calls."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from runtime.rate_limit_tracker import RateLimitState, parse_rate_limit_headers


@dataclass
class ProviderResponseState:
    provider: str
    captured_at: float = field(default_factory=time.time)
    rate_limit: RateLimitState | None = None
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.captured_at)


_LOCK = threading.RLock()
_STATES: dict[str, ProviderResponseState] = {}


def _normalize_provider(provider: str | None) -> str:
    return str(provider or "").strip().lower()


def _usage_get(usage: Any, key: str) -> Any:
    if usage is None:
        return None
    if isinstance(usage, Mapping):
        return usage.get(key)
    return getattr(usage, key, None)


def _safe_usage_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_usage(usage: Any) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
    ):
        value = _safe_usage_int(_usage_get(usage, key))
        if value is not None:
            normalized[key] = value
    return normalized


def record_provider_response(
    provider: str | None,
    *,
    headers: Mapping[str, str] | None = None,
    usage: Any = None,
) -> ProviderResponseState | None:
    """Store the latest response metadata for a provider."""
    provider_id = _normalize_provider(provider)
    if not provider_id:
        return None

    rate_limit = parse_rate_limit_headers(headers or {}, provider=provider_id) if headers else None
    normalized_usage = _normalize_usage(usage)
    if rate_limit is None and not normalized_usage:
        return None

    state = ProviderResponseState(
        provider=provider_id,
        rate_limit=rate_limit,
        usage=normalized_usage,
    )
    with _LOCK:
        _STATES[provider_id] = state
    return state


def get_provider_response_state(provider: str | None) -> ProviderResponseState | None:
    provider_id = _normalize_provider(provider)
    if not provider_id:
        return None
    with _LOCK:
        return _STATES.get(provider_id)


def clear_provider_response_states() -> None:
    with _LOCK:
        _STATES.clear()
