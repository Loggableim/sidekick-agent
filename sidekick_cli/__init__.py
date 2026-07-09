"""Legacy sidekick_cli package — lazy redirect to cli.* modules.

This package exists so Python resolves ``from sidekick_cli.banner import X``
correctly (a plain sys.modules alias doesn't work for submodule imports).
Every submodule is forwarded to the corresponding cli.* module on first access.
"""
from __future__ import annotations

import importlib
import sys
import types

__version__ = "0.8.64"
__release_date__ = "2026.7.9"

_ROUTE_MAP: dict[str, str] = {
    "config": "cli.config",
    "auth": "cli.auth",
    "models": "cli.models",
    "banner": "cli.banner",
    "plugins": "cli.plugins",
    "commands": "cli.commands",
    "setup": "cli.setup",
    "profiles": "cli.profiles",
    "gateway": "cli.gateway",
    "doctor": "cli.doctor",
    "status": "cli.status",
    "debug": "cli.debug",
    "dump": "cli.dump",
    "logs": "cli.logs",
    "cron": "cli.cron",
    "kanban": "cli.kanban",
    "skin_engine": "cli.skin_engine",
    "env_loader": "cli.env_loader",
    "runtime_provider": "cli.runtime_provider",
    "model_switch": "cli.model_switch",
    "tools_config": "cli.tools_config",
    "skills_config": "cli.skills_config",
    "hooks": "cli.hooks",
    "backup": "cli.backup",
    "callbacks": "cli.callbacks",
    "checkpoints": "cli.checkpoints",
    "curator": "cli.curator",
    "fallback_cmd": "cli.fallback_cmd",
    "kanban_db": "cli.kanban_db",
    "mcp_config": "cli.mcp_config",
    "memory_setup": "cli.memory_setup",
    "model_catalog": "cli.model_catalog",
    "oneshot": "cli.oneshot",
    "pairing": "cli.pairing",
    "platforms": "cli.platforms",
    "plugins_cmd": "cli.plugins_cmd",
    "profile_distribution": "cli.profile_distribution",
    "providers": "cli.providers",
    "relaunch": "cli.relaunch",
    "slack_cli": "cli.slack_cli",
    "stdio": "cli.stdio",
    "timeouts": "cli.timeouts",
    "tips": "cli.tips",
    "uninstall": "cli.uninstall",
    "vercel_auth": "cli.vercel_auth",
    "voice": "cli.voice",
    "web_server": "cli.web_server",
    "webhook": "cli.webhook",
    "claw": "cli.claw",
    "clipboard": "cli.clipboard",
    "codex_models": "cli.codex_models",
    "colors": "cli.colors",
    "completion": "cli.completion",
    "curses_ui": "cli.curses_ui",
    "default_soul": "cli.default_soul",
    "dingtalk_auth": "cli.dingtalk_auth",
    "goals": "cli.goals",
    "pty_bridge": "cli.pty_bridge",
    "browser_connect": "cli.browser_connect",
    "azure_detect": "cli.azure_detect",
    "copilot_auth": "cli.copilot_auth",
    "gateway_windows": "cli.gateway_windows",
    "skills_hub": "cli.skills_hub",
    "auth_commands": "cli.auth_commands",
    "cli_output": "cli.cli_output",
    "kanban_diagnostics": "cli.kanban_diagnostics",
    "kanban_specify": "cli.kanban_specify",
    "model_normalize": "cli.model_normalize",
}

# Only register the map for the CLI-related name.  Don't eagerly import.
_loaded: set[str] = set()


def __getattr__(name: str) -> types.ModuleType:
    if name in _ROUTE_MAP:
        mod = importlib.import_module(_ROUTE_MAP[name])
        sys.modules[f"sidekick_cli.{name}"] = mod
        _loaded.add(name)
        return mod
    raise AttributeError(f"module sidekick_cli has no attribute {name!r}")


__all__: list[str] = []
