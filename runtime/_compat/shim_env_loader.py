"""Minimal env-loader shim for runtime migration.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from shared.config import parse_env_file

logger = logging.getLogger(__name__)


def load_hermes_dotenv(path: Path | None = None, *, override: bool = False) -> None:
    """Load env vars from .env file, minimal version."""
    from shared.constants import get_env_path
    env_path = path or get_env_path()
    if not env_path or not env_path.exists():
        return
    env_vars = parse_env_file()
    for key, value in env_vars.items():
        if override or key not in os.environ:
            os.environ[key] = value


def _sanitize_env_file_if_needed(path: Path) -> None:
    """Stub — no sanitization needed in migration."""
    pass


def _format_offending_chars(value: str, limit: int = 3) -> str:
    """Stub — returns empty string."""
    return ""


__all__ = [
    "load_hermes_dotenv",
    "_sanitize_env_file_if_needed",
]