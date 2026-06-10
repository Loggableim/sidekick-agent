"""Cross-session rate limit guard — no-op after migration."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


def _state_path() -> str:
    return ""


def _parse_reset_seconds(headers: Optional[Mapping[str, str]]) -> Optional[float]:
    return None


def record_rate_limit(
    *,
    headers: Optional[Mapping[str, str]] = None,
    error_context: Optional[dict[str, Any]] = None,
    default_cooldown: float = 300.0,
) -> None:
    pass


def rate_limit_remaining() -> Optional[float]:
    return None


def clear_rate_limit() -> None:
    pass


def format_remaining(seconds: float) -> str:
    return ""


_MIN_RESET_FOR_BREAKER_SECONDS = 60.0


def is_genuine_rate_limit(
    *,
    headers: Optional[Mapping[str, str]] = None,
    last_known_state: Optional[Any] = None,
) -> bool:
    return False


def _parse_buckets_from_headers(
    headers: Optional[Mapping[str, str]],
) -> dict[str, tuple[Optional[int], Optional[float]]]:
    return {}


def _has_exhausted_bucket(
    buckets: Mapping[str, tuple[Optional[int], Optional[float]]],
) -> bool:
    return False


def _has_exhausted_bucket_in_object(state: Any) -> bool:
    return False
