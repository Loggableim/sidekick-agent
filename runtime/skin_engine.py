"""Minimal skin engine shim.

Provides get_active_skin() for runtime modules that need it.
"""
from __future__ import annotations

from typing import Any


def get_active_skin() -> dict[str, Any]:
    """Return the active skin configuration.

    In the migrated repo, returns sensible defaults until the full skin
    engine is ported from sidekick_cli/skin_engine.py.
    """
    return {
        "name": "default",
        "banner": {
            "color": "cyan",
            "style": "bold",
        },
        "spinner": {
            "faces": ["(◕‿◕)", "(◕‿◕✿)", "(ﾉ◕ヮ◕)ﾉ*:･ﾟ✧", "✨"],
            "verbs": ["thinking", "wiring", "weaving", "dreaming"],
            "wings": ["", "", "", ""],
        },
        "tool_prefix": "┊",
        "response_box": {
            "border_style": "round",
            "padding": (1, 2),
        },
        "branding": "Sidekick",
    }


__all__ = ["get_active_skin"]