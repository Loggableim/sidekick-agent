"""Typed values shared by the project-local Swarm Core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class SwarmConfig:
    project_root: Path
    config_path: Path
    version: int
    default_provider: str
    default_model: str
    default_autonomy: str


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


@dataclass(frozen=True)
class RequestedToolAction:
    """One adapter action requested by a policy-governed proposal."""

    name: str
    workspace: Path
    arguments: Mapping[str, Any]
    use_worktree: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "arguments",
            _freeze_json_value(dict(self.arguments)),
        )


@dataclass(frozen=True)
class ActionProposal:
    """The complete safety-relevant description of a requested action."""

    proposal_id: str
    category: str
    reversible: bool
    external: bool
    cost_increasing: bool
    evidence_refs: tuple[str, ...]
    requested_action: RequestedToolAction


@dataclass(frozen=True)
class ApprovalRecord:
    """A durable approval or denial bound to an exact proposal digest."""

    approval_id: str
    sequence: int
    run_id: str
    proposal_id: str
    proposal_digest: str
    approval_type: str
    approver_id: str
    approved: bool
    model_family: str | None
    evidence_refs: tuple[str, ...]
    created_at: datetime


def thaw_json_value(value: Any) -> Any:
    """Return a mutable JSON-safe copy of a frozen tool argument value."""
    if isinstance(value, Mapping):
        return {key: thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json_value(item) for item in value]
    return value


def _freeze_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        import math

        if not math.isfinite(value):
            raise ValueError("Tool arguments must contain finite JSON numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Tool argument object keys must be strings")
            frozen[key] = _freeze_json_value(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    raise TypeError(f"Unsupported tool argument value: {type(value).__name__}")
