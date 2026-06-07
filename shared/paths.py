from __future__ import annotations

import os
from pathlib import Path

SIDEKICK_HOME_ENV = "SIDEKICK_HOME"
LEGACY_HOME_ENV = "HERMES_HOME"
STATE_DIR_ENV = "SIDEKICK_STATE_DIR"
LEGACY_STATE_DIR_ENV = "HERMES_STATE_DIR"


def _env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def sidekick_home() -> Path:
    configured = _env(SIDEKICK_HOME_ENV, LEGACY_HOME_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".sidekick").resolve()


def state_dir() -> Path:
    configured = _env(STATE_DIR_ENV, LEGACY_STATE_DIR_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return sidekick_home() / "state"


def runtime_warnings(repo_root: Path | None = None) -> list[str]:
    warnings: list[str] = []
    legacy_home = os.getenv(LEGACY_HOME_ENV, "").strip()
    legacy_state = os.getenv(LEGACY_STATE_DIR_ENV, "").strip()
    home = sidekick_home()
    root = repo_root.resolve() if repo_root else None

    if legacy_home:
        warnings.append(f"legacy env in use: {LEGACY_HOME_ENV}={legacy_home}")
    if legacy_state:
        warnings.append(f"legacy env in use: {LEGACY_STATE_DIR_ENV}={legacy_state}")
    if root and home.is_relative_to(root):
        warnings.append(
            "sidekick home resolves inside the repo workspace; migrate to an external "
            "or user-profile path before shipping"
        )
    return warnings


def build_runtime_snapshot() -> dict[str, object]:
    return {
        "sidekick_home": str(sidekick_home()),
        "state_dir": str(state_dir()),
        "config_path": str(sidekick_home() / "config.yaml"),
        "env_path": str(sidekick_home() / ".env"),
        "skills_dir": str(sidekick_home() / "skills"),
        "legacy_env_detected": any(
            bool(os.getenv(name, "").strip())
            for name in (LEGACY_HOME_ENV, LEGACY_STATE_DIR_ENV)
        ),
    }
