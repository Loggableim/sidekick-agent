"""Shared helper for resolving the WebUI home directory.

The WebUI uses the Sidekick user home when no explicit home is configured.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_webui_home() -> Path:
    """Return the active WebUI home directory.

    Precedence:
    1. ``SIDEKICK_HOME``
    2. ``~/.sidekick``
    """
    raw_home = os.getenv("SIDEKICK_HOME", "").strip()
    if raw_home:
        return Path(raw_home).expanduser().resolve()
    return (Path.home() / ".sidekick").resolve()


def get_active_webui_home() -> Path:
    """Return the request-active WebUI home directory when available.

    Per-request profile handling lives in ``web.api.profiles``.  This helper
    keeps request-scoped callers on the active profile while preserving the
    existing environment-based fallback for startup code and standalone use.
    """
    try:
        from web.api.profiles import get_active_profile_home, get_active_profile_name

        active_profile = str(get_active_profile_name() or "").strip()
        if active_profile and active_profile != "default":
            return Path(get_active_profile_home()).expanduser().resolve()
    except Exception:
        pass
    return get_webui_home()
