"""Minimal env-loader shim for runtime migration.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from shared.config import normalize_env_key

logger = logging.getLogger(__name__)


def _parse_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = normalize_env_key(key)
                value = value.strip()
                if not key:
                    continue
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                result[key] = value
    except UnicodeDecodeError:
        with open(path, "r", encoding="latin-1") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = normalize_env_key(key)
                value = value.strip()
                if not key:
                    continue
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                result[key] = value
    return result


def load_sidekick_dotenv(
    path: Path | None = None,
    *,
    override: bool = False,
    hermes_home: str | os.PathLike | None = None,
    project_env: str | os.PathLike | None = None,
) -> list[Path]:
    """Load env vars from a .env file, minimal runtime shim version."""
    from shared.constants import get_env_path

    loaded: list[Path] = []
    env_paths: list[tuple[Path, bool]] = []
    if path is not None:
        env_paths.append((Path(path), override))
    else:
        if hermes_home is not None:
            env_paths.append((Path(hermes_home) / ".env", True))
        else:
            env_paths.append((get_env_path(), True))
        if project_env is not None:
            env_paths.append((Path(project_env), False))

    for env_path, path_override in env_paths:
        if not env_path or not env_path.exists():
            continue
        env_vars = _parse_env_file(env_path)
        for key, value in env_vars.items():
            if path_override or key not in os.environ:
                os.environ[key] = value
        loaded.append(env_path)
    return loaded


load_hermes_dotenv = load_sidekick_dotenv


def _sanitize_env_file_if_needed(path: Path) -> None:
    """Stub — no sanitization needed in migration."""
    pass


def _format_offending_chars(value: str, limit: int = 3) -> str:
    """Stub — returns empty string."""
    return ""


__all__ = [
    "load_sidekick_dotenv",
    "load_hermes_dotenv",
    "_sanitize_env_file_if_needed",
]
