"""Sidekick constants — monolithic compat shim.

Re-exports everything the old sidekick_constants / hermes_constants exposed.
During the monorepo migration this module delegates to runtime._compat.shim_constants
for all names, then adds the handful of additional constants that tools/*.py
still import from here directly.
"""
from __future__ import annotations

import os
from pathlib import Path

# ─── Re-export everything from the runtime compat shim ──────────────────────
from runtime._compat.shim_constants import (  # noqa: F401
    AI_GATEWAY_BASE_URL,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODELS_URL,
    VALID_REASONING_EFFORTS,
    apply_ipv4_preference,
    display_sidekick_home,
    get_config_path,
    get_default_sidekick_root,
    get_env_path,
    get_optional_skills_dir,
    get_sidekick_dir,
    get_sidekick_home,
    get_skills_dir,
    get_subprocess_home,
    is_container,
    is_termux,
    is_wsl,
    parse_reasoning_effort,
)

# ─── Legacy names still imported by tools/*.py and plugins ──────────────────

def get_hermes_dir(new_subpath: str, old_name: str) -> Path:
    """Legacy alias — delegates to get_sidekick_dir."""
    return get_sidekick_dir(new_subpath, old_name)


def get_default_hermes_root() -> Path:
    """Legacy alias — delegates to get_default_sidekick_root."""
    return get_default_sidekick_root()


def get_subprocess_home() -> str | None:
    """Return a per-profile HOME directory for subprocesses, or None."""
    home = os.environ.get("SIDEKICK_HOME") or os.environ.get("HERMES_HOME")
    if not home:
        return None
    profile_home = os.path.join(home, "home")
    if os.path.isdir(profile_home):
        return profile_home
    return None


__all__ = [
    "AI_GATEWAY_BASE_URL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_MODELS_URL",
    "VALID_REASONING_EFFORTS",
    "apply_ipv4_preference",
    "display_sidekick_home",
    "get_config_path",
    "get_default_hermes_root",
    "get_default_sidekick_root",
    "get_env_path",
    "get_hermes_dir",
    "get_optional_skills_dir",
    "get_sidekick_dir",
    "get_sidekick_home",
    "get_skills_dir",
    "get_subprocess_home",
    "is_container",
    "is_termux",
    "is_wsl",
    "parse_reasoning_effort",
]