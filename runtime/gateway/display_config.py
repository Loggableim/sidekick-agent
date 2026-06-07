"""Per-platform display/verbosity configuration resolver.

Provides ``resolve_display_setting()`` — the single entry-point for reading
display settings with platform-specific overrides and sensible defaults.

Resolution order (first non-None wins):
    1. ``display.platforms.<platform>.<key>``  — explicit per-platform user override
    2. ``display.<key>``                       — global user setting
    3. ``_PLATFORM_DEFAULTS[<platform>][<key>]``  — built-in sensible default
    4. ``_GLOBAL_DEFAULTS[<key>]``              — built-in global default

Exception: ``display.streaming`` is CLI-only.  Gateway streaming follows the
top-level ``streaming`` config unless ``display.platforms.<platform>.streaming``
sets an explicit per-platform override.

Backward compatibility: ``display.tool_progress_overrides`` is still read as a
fallback for ``tool_progress`` when no ``display.platforms`` entry exists.  A
config migration (version bump) automatically moves the old format into the new
``display.platforms`` structure.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Overrideable display settings and their global defaults
# ---------------------------------------------------------------------------
# These are the settings that can be configured per-platform.
# Other display settings (compact, personality, skin, etc.) are CLI-only
# and don't participate in per-platform resolution.

_GLOBAL_DEFAULTS: dict[str, Any] = {
    "tool_progress": "all",
    "show_reasoning": False,
    "tool_preview_length": 0,
    "streaming": None,  # None = follow top-level streaming config
    # When true, delete tool-progress / "Still working..." / status bubbles
    # after the final response lands on platforms that support message
    # deletion (e.g. Telegram). Off by default — progress is still shown
    # live, just cleaned up after success so the chat doesn't fill up with
    # stale breadcrumbs. Failed runs leave bubbles in place as breadcrumbs.
    "cleanup_progress": False,
}

# ---------------------------------------------------------------------------
# Sensible per-platform defaults — tiered by platform capability
# ---------------------------------------------------------------------------
# Tier 1 (high): Supports message editing, typically personal/team use
# Tier 2 (medium): Supports editing but often workspace/customer-facing
# Tier 3 (low): No edit support — each progress msg is permanent
# Tier 4 (minimal): Batch/non-interactive delivery

_TIER_HIGH = {
    "tool_progress": "all",
    "show_reasoning": False,
    "tool_preview_length": 40,
    "streaming": None,  # follow global
}

_TIER_MEDIUM = {
    "tool_progress": "minimal",
    "show_reasoning": False,
    "tool_preview_length": 40,
    "streaming": None,
}

_TIER_LOW = {
    "tool_progress": "minimal",
    "show_reasoning": False,
    "tool_preview_length": 0,
    "streaming": False,  # no edit support — disable streaming
}

_TIER_MINIMAL = {
    "tool_progress": "none",
    "show_reasoning": False,
    "tool_preview_length": 0,
    "streaming": False,
}

# Map platform keys to their tier defaults
_PLATFORM_DEFAULTS: dict[str, dict[str, Any]] = {
    "telegram": _TIER_HIGH,
    "discord": _TIER_HIGH,
    "slack": _TIER_HIGH,
    "signal": _TIER_HIGH,
    "whatsapp": _TIER_HIGH,
    "matrix": _TIER_MEDIUM,
    "mattermost": _TIER_MEDIUM,
    "wecom": _TIER_MEDIUM,
    "weixin": _TIER_LOW,
    "feishu": _TIER_MEDIUM,
    "qqbot": _TIER_LOW,
    "email": _TIER_MINIMAL,
    "sms": _TIER_MINIMAL,
    "homeassistant": _TIER_MEDIUM,
    "webhook": _TIER_MINIMAL,
}


def resolve_display_setting(
    user_config: dict[str, Any] | None,
    platform_key: str | None,
    setting_name: str,
) -> Any:
    """Resolve a display setting with per-platform override support.

    Args:
        user_config: The full user config dict (from load_config()).
        platform_key: Platform identifier (e.g. "telegram", "discord").
        setting_name: Name of the setting to resolve.

    Returns:
        The resolved value, or the global default if nothing is configured.
    """
    if not user_config:
        return _GLOBAL_DEFAULTS.get(setting_name)

    display = user_config.get("display", {})

    # 1. Per-platform override
    if platform_key:
        platforms = display.get("platforms", {})
        platform_cfg = platforms.get(platform_key, {})
        if setting_name in platform_cfg:
            return platform_cfg[setting_name]

    # 2. Backward compat: tool_progress_overrides
    if setting_name == "tool_progress":
        overrides = display.get("tool_progress_overrides", {})
        if platform_key and platform_key in overrides:
            return overrides[platform_key]

    # 3. Global user setting
    if setting_name in display:
        return display[setting_name]

    # 4. Platform tier default
    if platform_key and platform_key in _PLATFORM_DEFAULTS:
        tier_defaults = _PLATFORM_DEFAULTS[platform_key]
        if setting_name in tier_defaults:
            return tier_defaults[setting_name]

    # 5. Global built-in default
    return _GLOBAL_DEFAULTS.get(setting_name)
