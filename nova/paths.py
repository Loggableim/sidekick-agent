"""Paths for Nova's persistent, per-user space."""

from __future__ import annotations

import os
from pathlib import Path

from shared.constants import get_sidekick_home


def get_nova_space_root() -> Path:
    """Return Nova's mutable space for the active Sidekick home."""
    override = os.environ.get("SIDEKICK_NOVA_SPACE", "").strip()
    root = Path(override).expanduser() if override else get_sidekick_home() / "spaces" / "nova"
    return root.resolve()


def get_nova_data_dir() -> Path:
    """Return Nova v2's canonical private entity-state directory."""
    path = get_nova_space_root() / "nova_data" / "entity"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_legacy_nova_data_dirs() -> tuple[Path, ...]:
    """Locations used by v1 implementations, in migration priority order."""
    root = get_nova_space_root()
    return (
        root / "nova_data" / "entity_kernel",
        root / "entity_kernel",
    )
