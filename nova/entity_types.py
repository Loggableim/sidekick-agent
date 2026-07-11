"""Shared wire types for the canonical Nova Entity Runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


@dataclass(slots=True)
class EntityEvent:
    type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    salience: float = 0.5
    visibility: str = "private"
    correlation_id: str | None = None
    event_id: str = field(default_factory=lambda: new_id("event"))
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["salience"] = max(0.0, min(1.0, float(self.salience)))
        data["correlation_id"] = self.correlation_id or self.event_id
        return data


@dataclass(slots=True)
class Intent:
    need: str
    action: str
    title: str
    why: str
    target: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    expected_outcome: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    priority: float = 0.5
    policy_tier: str = "silent"
    source: str = "entity_kernel"
    intent_id: str = field(default_factory=lambda: new_id("intent"))
    correlation_id: str = field(default_factory=lambda: new_id("corr"))
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["priority"] = max(0.0, min(1.0, float(self.priority)))
        # v1 compatibility while callers migrate to the explicit names.
        data["id"] = self.intent_id
        data["tier"] = self.policy_tier
        return data


@dataclass(slots=True)
class Outcome:
    intent_id: str
    correlation_id: str
    status: str
    effects: dict[str, Any] = field(default_factory=dict)
    observation_due_at: str | None = None
    reward: float | None = None
    outcome_id: str = field(default_factory=lambda: new_id("outcome"))
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.reward is not None:
            data["reward"] = max(0.0, min(1.0, float(self.reward)))
        return data


@dataclass(slots=True)
class SelfRevision:
    path: str
    before: Any
    after: Any
    evidence_refs: list[str]
    confidence: float
    mode: str
    reason: str
    revision_id: str = field(default_factory=lambda: new_id("revision"))
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = max(0.0, min(1.0, float(self.confidence)))
        return data
