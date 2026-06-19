"""Compat shim for platform toolset resolution.

Runtime-only processes can import ``sidekick_cli.tools_config`` through
``runtime._compat.shim_cli`` before the CLI package is importable.  The cron
scheduler only needs ``_get_platform_tools``; delegate to the full CLI
implementation when available and keep a small fallback for isolated runtime
contexts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Set


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_CONFIGURABLE_TOOLSET_KEYS = (
    "web",
    "browser",
    "terminal",
    "file",
    "code_execution",
    "vision",
    "video",
    "image_gen",
    "moa",
    "tts",
    "skills",
    "todo",
    "memory",
    "session_search",
    "clarify",
    "delegation",
    "cronjob",
    "messaging",
    "rl",
    "homeassistant",
    "spotify",
    "discord",
    "discord_admin",
    "yuanbao",
    "computer_use",
)
_DEFAULT_OFF_TOOLSETS = {"moa", "homeassistant", "rl", "spotify", "discord", "discord_admin", "video"}
_TOOLSET_PLATFORM_RESTRICTIONS = {
    "discord": {"discord"},
    "discord_admin": {"discord"},
}
_PLATFORM_DEFAULT_TOOLSETS = {
    "cron": "sidekick-cron",
    "cli": "sidekick-cli",
}


def _parse_enabled_flag(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def _toolset_allowed_for_platform(ts_key: str, platform: str) -> bool:
    allowed = _TOOLSET_PLATFORM_RESTRICTIONS.get(ts_key)
    return allowed is None or platform in allowed


def _fallback_get_platform_tools(
    config: dict,
    platform: str,
    *,
    include_default_mcp_servers: bool = True,
) -> Set[str]:
    from toolsets import TOOLSETS, resolve_toolset

    platform_toolsets = config.get("platform_toolsets") or {}
    toolset_names = platform_toolsets.get(platform)
    if toolset_names is None or not isinstance(toolset_names, list):
        toolset_names = [_PLATFORM_DEFAULT_TOOLSETS.get(platform, f"sidekick-{platform}")]
    toolset_names = [str(ts) for ts in toolset_names]

    configurable_keys = set(_CONFIGURABLE_TOOLSET_KEYS)
    platform_default_keys = set(_PLATFORM_DEFAULT_TOOLSETS.values())
    has_explicit_config = any(ts in configurable_keys for ts in toolset_names)

    if has_explicit_config:
        enabled_toolsets = {
            ts
            for ts in toolset_names
            if ts in configurable_keys and _toolset_allowed_for_platform(ts, platform)
        }
        composite_tools = set()
        for ts_name in toolset_names:
            if ts_name in configurable_keys or ts_name not in TOOLSETS:
                continue
            composite_tools.update(resolve_toolset(ts_name))
        if composite_tools:
            for ts_key in configurable_keys:
                if not _toolset_allowed_for_platform(ts_key, platform):
                    continue
                ts_tools = set(resolve_toolset(ts_key))
                if ts_tools and ts_tools.issubset(composite_tools):
                    enabled_toolsets.add(ts_key)
    else:
        all_tool_names = set()
        for ts_name in toolset_names:
            all_tool_names.update(resolve_toolset(ts_name))
        enabled_toolsets = set()
        for ts_key in configurable_keys:
            if not _toolset_allowed_for_platform(ts_key, platform):
                continue
            ts_tools = set(resolve_toolset(ts_key))
            if ts_tools and ts_tools.issubset(all_tool_names):
                enabled_toolsets.add(ts_key)

    default_off = set(_DEFAULT_OFF_TOOLSETS)
    if platform in default_off and platform not in _TOOLSET_PLATFORM_RESTRICTIONS:
        default_off.remove(platform)
    if "homeassistant" in default_off and os.getenv("HASS_TOKEN"):
        default_off.remove("homeassistant")
    enabled_toolsets -= default_off

    explicit_passthrough = {
        ts
        for ts in toolset_names
        if ts not in configurable_keys and ts not in platform_default_keys
    }
    mcp_servers = config.get("mcp_servers") or {}
    enabled_mcp_servers = {
        str(name)
        for name, server_cfg in mcp_servers.items()
        if isinstance(server_cfg, dict)
        and _parse_enabled_flag(server_cfg.get("enabled", True), default=True)
    }
    if "no_mcp" in toolset_names:
        explicit_mcp_servers = set()
        enabled_toolsets.update(explicit_passthrough - enabled_mcp_servers - {"no_mcp"})
    else:
        explicit_mcp_servers = explicit_passthrough & enabled_mcp_servers
        enabled_toolsets.update(explicit_passthrough - enabled_mcp_servers)
    if include_default_mcp_servers:
        enabled_toolsets.update(explicit_mcp_servers or (() if "no_mcp" in toolset_names else enabled_mcp_servers))
    else:
        enabled_toolsets.update(explicit_mcp_servers)

    agent_cfg = config.get("agent") or {}
    disabled_toolsets = agent_cfg.get("disabled_toolsets") or []
    if disabled_toolsets:
        enabled_toolsets -= {str(ts) for ts in disabled_toolsets}
    return enabled_toolsets


def _get_platform_tools(
    config: dict,
    platform: str,
    *,
    include_default_mcp_servers: bool = True,
) -> Set[str]:
    try:
        from cli.tools_config import _get_platform_tools as _real_get_platform_tools
    except Exception:
        return _fallback_get_platform_tools(
            config,
            platform,
            include_default_mcp_servers=include_default_mcp_servers,
        )
    return _real_get_platform_tools(
        config,
        platform,
        include_default_mcp_servers=include_default_mcp_servers,
    )


__all__ = ["_get_platform_tools"]
