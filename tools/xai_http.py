"""Shared helpers for direct xAI HTTP integrations."""

from __future__ import annotations


def sidekick_xai_user_agent() -> str:
    """Return a stable Sidekick-specific User-Agent for xAI HTTP calls."""
    try:
        from sidekick_cli import __version__
    except Exception:
        __version__ = "unknown"
    return f"Sidekick-Agent/{__version__}"
