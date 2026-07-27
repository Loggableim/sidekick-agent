"""Versionable project-local Swarm configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .types import SwarmConfig


_DEFAULT_CONFIG = {
    "version": 1,
    "default_provider": "ollama-cloud",
    "default_model": "deepseek-v4-flash",
    "default_autonomy": "reviewed_execution",
}

_AUTONOMY_LEVELS = {
    "observe",
    "suggest",
    "execute_safe",
    "reviewed_execution",
    "autonomous",
}


class SwarmProjectNotInitializedError(FileNotFoundError):
    """Raised when a read-only caller targets a project without Swarm state."""


def load_project_config(project_root: Path) -> SwarmConfig:
    """Load an existing project config without creating or upgrading anything.

    Status pages and SSE consumers must be safe to call against an arbitrary
    trusted project.  Unlike :func:`initialize_project`, this path never makes
    a ``.swarm`` directory, updates an old YAML document, or creates runtime
    state.
    """
    project_root = project_root.resolve()
    config_path = project_root / ".swarm" / "swarm.yaml"
    if not config_path.is_file():
        raise SwarmProjectNotInitializedError(
            f"Swarm project is not initialized: {project_root}"
        )
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw_config, dict):
        raise ValueError(f"Swarm configuration must be a mapping: {config_path}")
    return _to_config(project_root, config_path, raw_config)


def initialize_project(project_root: Path) -> SwarmConfig:
    """Create (or load) the versionable layout for one project."""
    project_root = project_root.resolve()
    swarm_dir = project_root / ".swarm"
    runtime_dir = swarm_dir / "runtime"
    config_path = swarm_dir / "swarm.yaml"
    ignore_path = swarm_dir / ".gitignore"

    runtime_dir.mkdir(parents=True, exist_ok=True)
    _ensure_runtime_is_ignored(ignore_path)

    if not config_path.exists():
        config_path.write_text(
            yaml.safe_dump(_DEFAULT_CONFIG, sort_keys=False), encoding="utf-8"
        )

    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw_config, dict):
        raise ValueError(f"Swarm configuration must be a mapping: {config_path}")
    if "default_autonomy" not in raw_config:
        raw_config["default_autonomy"] = _DEFAULT_CONFIG["default_autonomy"]
        config_path.write_text(
            yaml.safe_dump(raw_config, sort_keys=False), encoding="utf-8"
        )
    return _to_config(project_root, config_path, raw_config)


def _ensure_runtime_is_ignored(ignore_path: Path) -> None:
    entry = "runtime/"
    if not ignore_path.exists():
        ignore_path.write_text(f"{entry}\n", encoding="utf-8")
        return

    lines = ignore_path.read_text(encoding="utf-8").splitlines()
    if entry not in lines:
        suffix = "" if not lines else "\n"
        ignore_path.write_text("\n".join([*lines, entry]) + suffix, encoding="utf-8")


def _to_config(
    project_root: Path, config_path: Path, raw_config: dict[str, Any]
) -> SwarmConfig:
    try:
        default_autonomy = str(
            raw_config.get("default_autonomy", _DEFAULT_CONFIG["default_autonomy"])
        )
        if default_autonomy not in _AUTONOMY_LEVELS:
            raise ValueError(f"Unsupported Swarm autonomy level: {default_autonomy}")
        return SwarmConfig(
            project_root=project_root,
            config_path=config_path,
            version=int(raw_config["version"]),
            default_provider=str(raw_config["default_provider"]),
            default_model=str(raw_config["default_model"]),
            default_autonomy=default_autonomy,
        )
    except KeyError as exc:
        raise ValueError(f"Missing Swarm configuration value: {exc.args[0]}") from exc
