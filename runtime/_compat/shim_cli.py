"""Compat shim — routes ``from runtime._compat.shim_cli.<X> import ...``
to the real runtime modules that have been ported.

Modules not yet ported raise a clear ImportError.
"""
from __future__ import annotations

import sys
import types

__version__ = "0.1.0-migration"
# Map of already-ported submodule names to their canonical runtime module path
_PORTED_ROUTES: dict[str, str] = {
    "config": "runtime.config",
    "models": "runtime.models",
    "plugins": "runtime.plugins",
    "skin_engine": "runtime.skin_engine",
    # "nous_subscription" removed — Nous branding stripped
    "env_loader": "runtime._compat.shim_env_loader",
    "auth": "runtime._compat.shim_auth",
    "runtime_provider": "runtime._compat.shim_runtime_provider",
}

_KNOWN_UNPORTED = [
    "commands",
    "banner",
    "model_switch",
    "model_normalize",
    "codex_models",
    "pt_input_extras",
    "browser_connect",
    "callbacks",
    "voice",
    "setup",
    "profiles",
    "logs",
    "gateway",
    "web_server",
    "kanban",
    "kanban_db",
    "debug",
    "status",
    "tips",
    "doctor",
    "dump",
    "fallback_cmd",
    "clipboard",
    "checkpoints",
    "completion",
    "cron",
    "curator",
    "curses_ui",
    "hooks",
    "mcp_config",
    "memory_setup",
    "model_catalog",
    "oneshot",
    "pairing",
    "platforms",
    "providers",
    "relaunch",
    "skills_config",
    "skills_hub",
    "slack_cli",
    "stdio",
    "timeouts",
    "tools_config",
    "uninstall",
    "vercel_auth",
    "webhook",
    "copilot_auth",
    "claw",
    "backup",
    "dingtalk_auth",
    "gateway_windows",
    "azuer_detect",
]

import importlib


def _install_route(sub_name: str, route: str) -> types.ModuleType:
    """Load the real module and wire it into sys.modules under shim_cli path."""
    real = importlib.import_module(route)
    fqn = f"runtime._compat.shim_cli.{sub_name}"
    sys.modules[fqn] = real
    # Also register as sidekick_cli.<sub_name> for direct imports
    sys.modules[f"sidekick_cli.{sub_name}"] = real
    return real


def _make_stub(sub_name: str) -> types.ModuleType:
    """Build a placeholder sub-module that raises ImportError on any attribute access."""
    mod = types.ModuleType(f"runtime._compat.shim_cli.{sub_name}")
    mod.__package__ = "runtime._compat.shim_cli"
    mod.__path__ = []
    mod.__file__ = __file__

    original_path = f"C:\\HermesPortable\\cids-hermes-agent\\sidekick_cli\\{sub_name}.py"

    def _getattr(name: str, sub=sub_name, orig=original_path) -> None:
        raise ImportError(
            f"sidekick_cli.{sub} has not been ported to the new structure yet.\n\n"
            f"The original module lives at:\n"
            f"  {orig}\n\n"
            f"You tried to access {sub}.{name!r}. To fix this, port that module\n"
            f"into the new sidekick runtime or create a proper replacement shim.\n"
            f"See PENDING_IMPORTS.txt in this directory for the full list."
        )

    mod.__getattr__ = _getattr
    return mod


# Register routes for ported modules
for _sub, _route in _PORTED_ROUTES.items():
    try:
        _install_route(_sub, _route)
    except Exception as exc:
        # If the route module can't be loaded, fall back to stub
        stub = _make_stub(_sub)
        sys.modules[f"runtime._compat.shim_cli.{_sub}"] = stub

# Register stubs for unported modules
for _sub in _KNOWN_UNPORTED:
    sys.modules[f"runtime._compat.shim_cli.{_sub}"] = _make_stub(_sub)


def __getattr__(name: str) -> types.ModuleType:
    """Return a real or placeholder sub-module."""
    if name in _PORTED_ROUTES:
        return _install_route(name, _PORTED_ROUTES[name])
    if name in _KNOWN_UNPORTED:
        mod = _make_stub(name)
        sys.modules[f"runtime._compat.shim_cli.{name}"] = mod
        return mod
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}. "
        f"Expected one of: {', '.join(list(_PORTED_ROUTES)[:10] + _KNOWN_UNPORTED[:5])}..."
    )


__all__: list[str] = []