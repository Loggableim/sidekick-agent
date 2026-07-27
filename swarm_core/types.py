"""Typed values shared by the project-local Swarm Core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SwarmConfig:
    project_root: Path
    config_path: Path
    version: int
    default_provider: str
    default_model: str


@dataclass(frozen=True)
class SwarmRun:
    run_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SwarmEvent:
    event_id: str
    sequence: int
    timestamp: datetime
    event_type: str
    run_id: str
    payload: dict[str, Any]
    visibility: str
