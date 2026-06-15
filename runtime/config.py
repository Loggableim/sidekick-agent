"""Minimal Sidekick CLI config shim for runtime migration.

Provides the load_config(), ensure_sidekick_home(), and cfg_get() interfaces
that runtime modules need, delegating to shared.config where possible.
"""
from __future__ import annotations

import copy
import logging
import re
import os
from pathlib import Path
from typing import Any, Optional, Tuple, List, Dict

import yaml
from shared.config import get_config_value, load_config as _shared_load_config, normalize_env_key
from shared.constants import get_config_path, get_env_path, get_sidekick_home

logger = logging.getLogger(__name__)

# Re-export
from shared.config import ensure_sidekick_home as _shared_ensure_home
ensure_sidekick_home = _shared_ensure_home


def load_config() -> dict[str, Any]:
    """Load merged config (defaults + user config.yaml).

    Wraps shared.config.load_config() with the same interface signature
    that sidekick_cli.config.load_config() exposed.
    """
    return _shared_load_config()


def cfg_get(cfg: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    """Safely traverse a nested dict by a dotted key path."""
    if cfg is None:
        return default
    cursor: Any = cfg
    for key in keys:
        if not isinstance(cursor, dict):
            return default
        cursor = cursor.get(key)
        if cursor is None:
            return default
    return cursor


def edit_config(key: str, value: Any) -> None:
    """Set a config value and persist to disk."""
    from shared.config import set_config_value
    set_config_value(key, str(value))


def get_sidekick_home_path() -> Path:
    return get_sidekick_home()


def remove_env_value(key: str) -> bool:
    """Remove a key from the .env file."""
    env_path = get_env_path()
    if not env_path.exists():
        return False
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
        kept = [l for l in lines if not l.strip().startswith(key + "=") and not l.strip().startswith("#")]
        if len(kept) == len(lines):
            return False
        env_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def get_custom_provider_context_length(provider_name: str) -> int | None:
    """Return custom provider context_length from config, or None."""
    cfg = load_config()
    providers = cfg_get(cfg, "custom_providers") or {}
    if not isinstance(providers, dict):
        providers = {}
    for entry in providers.values():
        if isinstance(entry, dict) and entry.get("name") == provider_name:
            return entry.get("context_length")
    return None


def get_env_value(key: str, default: str | None = None) -> str | None:
    """Read a single value from the .env file.

    Mirrors ``sidekick_cli.config.get_env_value``.
    """
    env_path = get_env_path()
    if not env_path.exists():
        return default
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if normalize_env_key(k) == key:
                return v.strip().strip("\"'")
        return default
    except OSError:
        return default


def load_env(env_path: str | Path | None = None, *, quiet: bool = False) -> dict[str, str]:
    """Load the .env file into a dict without modifying ``os.environ``.

    Mirrors ``sidekick_cli.config.load_env``.
    """
    path = Path(env_path) if env_path else get_env_path()
    result: dict[str, str] = {}
    if not path.exists():
        return result
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            result[normalize_env_key(k)] = v.strip().strip("\"'")
    except OSError:
        if not quiet:
            logger.warning("Could not read env file: %s", path)
    return result


def _expand_env_vars(obj: Any) -> Any:
    """Recursively expand ``${VAR}`` references in config values.

    Only string values are processed. Unresolved references are kept verbatim.
    Mirrors ``sidekick_cli.config._expand_env_vars``.
    """
    if isinstance(obj, str):
        return re.sub(
            r"\$\{([^}]+)\}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            obj,
        )
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def print_config_warnings(config: dict[str, Any] | None = None) -> None:
    """Stub — config validation warnings (ported from sidekick_cli.config).

    In the monorepo, config structure validation is covered by
    ``sidekick doctor``.  This no-op prevents ImportError in gateway startup.
    """
    pass


def warn_deprecated_cwd_env_vars(config: dict[str, Any] | None = None) -> None:
    """Stub — deprecated env var warning (ported from sidekick_cli.config).

    MESSAGING_CWD and TERMINAL_CWD are deprecated.  In the monorepo the
    canonical setting is terminal.cwd in config.yaml.  This no-op prevents
    ImportError in gateway startup.
    """
    pass


__all__ = [
    "ensure_sidekick_home",
    "load_config",
    "cfg_get",
    "edit_config",
    "get_sidekick_home_path",
    "remove_env_value",
    "get_custom_provider_context_length",
    "get_env_value",
    "get_env_path",
    "get_config_path",
    "load_env",
    "_expand_env_vars",
    "print_config_warnings",
    "warn_deprecated_cwd_env_vars",
]
