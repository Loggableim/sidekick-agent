"""Path helpers for optional local Nova state.

Nova's consciousness files are local user state, not repository assets. Load
them from the active Sidekick home so renamed/migrated installs do not fall
back to a stale repo-local ``home`` directory.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_nova_space_root() -> Path:
    raw_home = (os.getenv("SIDEKICK_HOME") or "").strip()
    if raw_home:
        return Path(raw_home).expanduser() / "spaces" / "nova"
    return Path(__file__).resolve().parents[2] / "home" / "spaces" / "nova"


def get_nova_session_start_path() -> Path:
    return get_nova_space_root() / "session_start.py"


def get_nova_state_snapshot_path() -> Path:
    return get_nova_space_root() / "state_snapshot.py"
