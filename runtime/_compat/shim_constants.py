"""
Compat shim — bridges old ``sidekick_constants`` / ``hermes_constants`` imports
to the new ``shared.constants`` and ``shared.paths`` layouts.

All names that agent modules import from ``sidekick_constants`` or
``hermes_constants`` are re-exported here so that the import-rewrite rules
in ``copy_runtime_modules.py`` and ``copy_dependent_modules.py`` can point
at ``runtime._compat.shim_constants`` and everything works.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── Re-export everything from shared.constants ─────────────────────────────
from shared.constants import (
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

# ── Re-export from shared.paths ────────────────────────────────────────────
from shared.paths import (
    sidekick_home,
    state_dir,
)

# ── Constants missing from shared.constants that agent code expects ────────
# These were defined in the original hermes_constants.py

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE_URL}/models"
AI_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"


def get_default_hermes_root() -> Path:
    """Return the root Hermes directory (legacy name, delegates to get_default_sidekick_root)."""
    return get_default_sidekick_root()


def get_hermes_dir(new_subpath: str, old_name: str) -> Path:
    """Resolve a Hermes subdirectory with backward compat (delegates to get_sidekick_dir)."""
    return get_sidekick_dir(new_subpath, old_name)


# ── Also add the sidekick-specific helpers that existed in the old sidekick_constants ──

def get_subprocess_home_sidekick() -> str | None:
    """Return a per-profile HOME directory for subprocesses, or None.

    Uses ``SIDEKICK_HOME`` primary, ``HERMES_HOME`` secondary.
    Mirrors ``get_subprocess_home()`` in ``hermes_constants``.
    """
    home = os.getenv("SIDEKICK_HOME") or os.getenv("HERMES_HOME")
    if not home:
        return None
    profile_home = os.path.join(home, "home")
    if os.path.isdir(profile_home):
        return profile_home
    return None


# ── Explicit __all__ so star-imports see everything ──────────────────────────
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
    "get_subprocess_home_sidekick",
    "is_container",
    "is_termux",
    "is_wsl",
    "parse_reasoning_effort",
    "sidekick_home",
    "state_dir",
]
