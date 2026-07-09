"""Path helpers for optional local Nova state.

Nova's consciousness files are local user state, not repository assets. Load
them from the active Sidekick home so renamed/migrated installs do not fall
back to a stale repo-local ``home`` directory.
"""

from __future__ import annotations

from pathlib import Path

from web.api._home import get_active_webui_home, get_webui_home


def _active_nova_home() -> Path:
    try:
        return Path(get_active_webui_home()).expanduser().resolve()
    except Exception:
        return get_webui_home()


def get_nova_space_root() -> Path:
    return _active_nova_home() / "spaces" / "nova"


def get_nova_session_start_path() -> Path:
    return get_nova_space_root() / "session_start.py"


def get_nova_state_snapshot_path() -> Path:
    return get_nova_space_root() / "state_snapshot.py"
