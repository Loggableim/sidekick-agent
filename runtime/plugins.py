"""Minimal plugins shim for runtime migration.

Provides get_plugin_manager() and VALID_HOOKS.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# From original sidekick_cli/plugins.py
VALID_HOOKS = frozenset({
    "pre_tool_call",
    "post_tool_call",
    "pre_response",
    "post_response",
    "on_session_start",
    "on_session_end",
    "on_error",
    "pre_message_processing",
    "post_message_processing",
})


class _StubPluginManager:
    """Minimal stub that returns empty results for all queries."""

    def get_toolsets(self) -> list[Any]:
        return []

    def get_commands(self) -> list[Any]:
        return []

    def get_tools(self) -> list[Any]:
        return []

    def get_context_engine(self) -> Any | None:
        return None

    def dispatch_tool(self, *args: Any, **kwargs: Any) -> Any:
        return None


_plugin_manager = _StubPluginManager()


def get_plugin_manager() -> Any:
    """Return the plugin manager instance."""
    return _plugin_manager


def discover_and_load(*args: Any, **kwargs: Any) -> None:
    """Stub — no plugins loaded yet in the monorepo migration."""
    pass


__all__ = [
    "VALID_HOOKS",
    "get_plugin_manager",
    "discover_and_load",
]