"""Shared utility functions for Sidekick Runtime.

Ported from cids-hermes-agent/utils.py. Dependency-free module —
safe to import from anywhere without risk of circular imports.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Union
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})


def is_truthy_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY_STRINGS
    return bool(value)


def env_var_enabled(name: str, default: str = "") -> bool:
    return is_truthy_value(os.getenv(name, default), default=False)


def _preserve_file_mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    except OSError:
        return None


def _restore_file_mode(path: Path, mode: int | None) -> None:
    if mode is None:
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def atomic_replace(tmp_path: Union[str, Path], target: Union[str, Path]) -> str:
    """Atomically move *tmp_path* onto *target*, preserving symlinks."""
    target_str = str(target)
    real_path = os.path.realpath(target_str) if os.path.islink(target_str) else target_str
    os.replace(str(tmp_path), real_path)
    return real_path


def atomic_json_write(
    path: Union[str, Path],
    data: Any,
    *,
    indent: int = 2,
    **dump_kwargs: Any,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prev_mode = _preserve_file_mode(path)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=str(path.parent),
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as f:
            json.dump(data, f, ensure_ascii=False, indent=indent, **dump_kwargs)
            f.flush()
            os.fsync(f.fileno())
            tmp_name = f.name
        atomic_replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
        raise
    finally:
        if prev_mode is not None:
            _restore_file_mode(path, prev_mode)


def atomic_yaml_write(path: Union[str, Path], data: Any, *, default_flow_style: bool = False, extra_content: str | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prev_mode = _preserve_file_mode(path)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=str(path.parent),
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as f:
            yaml.safe_dump(data, f, default_flow_style=default_flow_style, allow_unicode=True, sort_keys=False)
            if extra_content:
                f.write("\n")
                f.write(extra_content)
            f.flush()
            os.fsync(f.fileno())
            tmp_name = f.name
        atomic_replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
        raise
    finally:
        if prev_mode is not None:
            _restore_file_mode(path, prev_mode)


def safe_json_loads(text: str, default: Any = None) -> Any:
    if not text or not text.strip():
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return default


def env_int(name: str, default: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in TRUTHY_STRINGS


def normalize_proxy_url(raw: str) -> str:
    """Normalize a proxy URL: ensure it has a scheme, strip trailing slashes."""
    raw = raw.strip().rstrip("/")
    if not raw:
        return ""
    if not urlparse(raw).scheme:
        raw = "http://" + raw
    return raw


def normalize_proxy_env_vars(prefix: str = "") -> dict[str, str]:
    """Read proxy env vars (http_proxy, https_proxy, no_proxy) and their
    upper/lower variants, returning a unified dict with uppercase keys."""
    result: dict[str, str] = {}
    for key in ("http_proxy", "https_proxy", "no_proxy"):
        upper = key.upper()
        prefixed = prefix + key
        prefixed_upper = prefix + upper
        value = (
            os.getenv(prefixed_upper)
            or os.getenv(prefixed)
            or os.getenv(upper)
            or os.getenv(key)
        )
        if value:
            result[key] = normalize_proxy_url(value)
            result[upper] = result[key]
    return result


def base_url_hostname(url: str) -> str:
    """Extract hostname from a base URL like 'https://api.openai.com/v1'."""
    try:
        return urlparse(url).hostname or url
    except Exception:
        return url


def base_url_host_matches(url: str, hostname: str) -> bool:
    """Check if the hostname of *url* matches *hostname*."""
    try:
        parsed = urlparse(url)
        if parsed.hostname:
            return parsed.hostname == hostname or parsed.hostname.endswith("." + hostname)
        return False
    except Exception:
        return False