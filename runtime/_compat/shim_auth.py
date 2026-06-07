"""Minimal auth shim for runtime migration.

Provides the auth interfaces that runtime modules import from sidekick_cli.auth.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from runtime.config import load_config
from shared.constants import get_sidekick_home

logger = logging.getLogger(__name__)

_auth_store_lock = threading.Lock()

# Constants from original sidekick_cli.auth
DEFAULT_AGENT_KEY_MIN_TTL_SECONDS = 300
CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120

# Provider registry stub
PROVIDER_REGISTRY: dict[str, Any] = {}


class AuthError(Exception):
    """Authentication error."""
    pass


def _decode_jwt_claims(token: str) -> dict[str, Any] | None:
    """Decode JWT payload without verification (for expiry checking only)."""
    try:
        import base64
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return None


def _codex_access_token_is_expiring(token: str, skew: int = CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS) -> bool:
    """Check if a Codex access token is expiring within the skew window."""
    import time
    claims = _decode_jwt_claims(token)
    if claims is None:
        return True
    exp = claims.get("exp")
    if exp is None:
        return True
    return time.time() + skew >= exp


def _load_auth_store() -> dict[str, Any]:
    """Load auth store from disk."""
    path = get_sidekick_home() / "auth.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_auth_store(data: dict[str, Any]) -> None:
    """Save auth store to disk."""
    path = get_sidekick_home() / "auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_provider_state() -> dict[str, Any]:
    """Load provider-specific state."""
    store = _load_auth_store()
    return store.get("provider_state", {})


def _resolve_kimi_base_url() -> str:
    return "https://api.moonshot.cn/v1"


def _resolve_zai_base_url() -> str:
    return "https://api.zai.jina.ai"


def _read_codex_tokens() -> dict[str, str] | None:
    """Read Codex access/refresh tokens from auth store."""
    store = _load_auth_store()
    codex = store.get("codex", {})
    if not codex:
        return None
    return {
        "access_token": codex.get("access_token", ""),
        "refresh_token": codex.get("refresh_token", ""),
    }


def resolve_codex_runtime_credentials() -> dict[str, str] | None:
    """Resolve Codex runtime credentials from auth store."""
    return _read_codex_tokens()


def _save_provider_state(state: dict[str, Any]) -> None:
    """Stub — saves provider state (real implementation coming in next pass)."""
    store = _load_auth_store()
    store["provider_state"] = state
    _save_auth_store(store)


def read_credential_pool(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read credential pool from auth store."""
    store = _load_auth_store()
    return store.get("credential_pool", {})


def suppress_credential_source(source: str) -> bool:
    """Stub — always returns False."""
    return False


def _is_expiring(expiry: float, skew: float = 300.0) -> bool:
    """Check if a Unix timestamp is expiring within the skew window."""
    import time
    return time.time() + skew >= expiry


__all__ = [
    "AuthError",
    "DEFAULT_AGENT_KEY_MIN_TTL_SECONDS",
    "CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS",
    "PROVIDER_REGISTRY",
    "_auth_store_lock",
    "_codex_access_token_is_expiring",
    "_decode_jwt_claims",
    "_load_auth_store",
    "_load_provider_state",
    "_resolve_kimi_base_url",
    "_resolve_zai_base_url",
    "_save_auth_store",
    "_read_codex_tokens",
    "resolve_codex_runtime_credentials",
    "suppress_credential_source",
    "_is_expiring",
]