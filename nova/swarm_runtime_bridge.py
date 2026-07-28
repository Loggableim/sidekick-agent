"""Canonical, opt-in Nova submissions for the Swarm safety boundary.

This module deliberately only prepares immutable data and checks it locally.
It imports neither the Nova kernel nor any transport, tool, or model surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from nova.swarm_adapter import get_nova_action_spec
from swarm_core.config import load_integration_config, save_integration_config
from swarm_core.verifier import InvalidVerifierResult, VerificationResult, VERIFIED_DECISION


# This is intentionally the complete automatic-action allowlist.  Nova's
# wider ActionRegistry remains outside this bridge until separately reviewed.
NOVA_AUTOMATIC_ACTIONS = (
    "mind_diary",
    "agenda_update",
    "prioritize_thread",
)

_BRIDGE_CONFIG_VERSION = 1
_MAX_SOURCE_SLOT = (1 << 63) - 1
_MAX_JSON_DEPTH = 8
_MAX_JSON_ITEMS = 256
_MAX_TEXT_LENGTH = 4_096
_MAX_KEY_LENGTH = 128
_ACTION_OUTPUT_SCOPES: Mapping[str, str] = MappingProxyType(
    {
        "mind_diary": "nova_data/mind_diary.jsonl",
        "agenda_update": "nova_data/entity/agenda.json",
        "prioritize_thread": "nova_data/entity/agenda.json",
    }
)
_SENSITIVE_MARKERS = (
    "apply",
    "command",
    "secret",
    "url",
    "password",
    "credential",
    "auth.json",
    ".env",
    "http://",
    "https://",
)


@dataclass(frozen=True)
class NovaBridgeConfig:
    """The only persisted bridge controls: schema version and explicit opt-in."""

    version: int = _BRIDGE_CONFIG_VERSION
    enabled: bool = False


def load_nova_bridge_config(project_root: Path) -> NovaBridgeConfig:
    """Read the optional generic integration without initializing a project."""
    raw = load_integration_config(Path(project_root), "nova")
    if not raw:
        return NovaBridgeConfig()
    if set(raw) != {"version", "enabled"}:
        raise ValueError("Nova bridge config only permits version and enabled")
    version = raw.get("version")
    enabled = raw.get("enabled")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("Nova bridge config version must be an integer")
    if version != _BRIDGE_CONFIG_VERSION:
        raise ValueError("unsupported Nova bridge config version")
    if type(enabled) is not bool:
        raise ValueError("Nova bridge enabled must be a bool")
    return NovaBridgeConfig(version=version, enabled=enabled)


def configure_nova_bridge(project_root: Path, *, enabled: bool) -> NovaBridgeConfig:
    """Explicitly write the minimal generic integration opt-in configuration."""
    if type(enabled) is not bool:
        raise TypeError("Nova bridge enabled must be a bool")
    config = NovaBridgeConfig(enabled=enabled)
    save_integration_config(
        Path(project_root),
        "nova",
        {"version": config.version, "enabled": config.enabled},
    )
    return config


@dataclass(frozen=True)
class NovaIntentSnapshot:
    """A canonical, bounded Nova intent bound to one code-owned decision slot."""

    action: str
    need: str
    title: str
    why: str
    target: Mapping[str, Any]
    payload: Mapping[str, Any]
    expected_outcome: Mapping[str, Any]
    priority: float
    source_slot: int
    project_root: Path
    intent_digest: str

    @classmethod
    def from_submission(
        cls,
        submission: Mapping[str, Any],
        *,
        source_slot: int,
        project_root: Path,
    ) -> "NovaIntentSnapshot":
        """Discard caller identity/security metadata and retain safe content only."""
        if not isinstance(submission, Mapping):
            raise TypeError("Nova submission must be a mapping")
        action = _required_text(submission.get("action"), "Nova action")
        if action not in NOVA_AUTOMATIC_ACTIONS:
            raise ValueError("not an automatic Nova action")
        # Resolve the adapter-owned record before any suggestion reaches its
        # translation path; this can never instantiate or invoke a kernel.
        get_nova_action_spec(action)
        slot = _source_slot(source_slot)
        root = _resolved_root(project_root)
        expected_outcome = _canonical_mapping(
            submission.get("expected_outcome", {}), "Nova expected_outcome"
        )
        expected_outcome = dict(expected_outcome)
        expected_outcome["output_scope"] = _ACTION_OUTPUT_SCOPES[action]
        document = {
            "action": action,
            "need": _required_text(submission.get("need"), "Nova need"),
            "title": _required_text(submission.get("title"), "Nova title"),
            "why": _required_text(submission.get("why"), "Nova why"),
            "target": _canonical_mapping(submission.get("target", {}), "Nova target"),
            "payload": _canonical_mapping(submission.get("payload", {}), "Nova payload"),
            "expected_outcome": _freeze_json_mapping(
                expected_outcome, "Nova expected_outcome"
            ),
            "priority": _priority(submission.get("priority", 0.5)),
            "source_slot": slot,
            "project_root": str(root),
        }
        digest = _digest(document)
        return cls(
            action=action,
            need=document["need"],
            title=document["title"],
            why=document["why"],
            target=document["target"],
            payload=document["payload"],
            expected_outcome=document["expected_outcome"],
            priority=document["priority"],
            source_slot=slot,
            project_root=root,
            intent_digest=digest,
        )

    @property
    def proposal_id(self) -> str:
        return f"nova-{self.intent_digest}"

    @property
    def verifier_evidence_ref(self) -> str:
        return f"nova:verifier:{self.intent_digest}"

    @property
    def expected_output_scope(self) -> str:
        return _ACTION_OUTPUT_SCOPES[self.action]

    def to_suggestion(self) -> dict[str, Any]:
        """Return the sole safe adapter input for this immutable snapshot."""
        return {
            "id": self.proposal_id,
            "intent_id": self.proposal_id,
            "proposal_id": self.proposal_id,
            "action": self.action,
            "need": self.need,
            "title": self.title,
            "why": self.why,
            "target": _thaw_json(self.target),
            "payload": _thaw_json(self.payload),
            "expected_outcome": _thaw_json(self.expected_outcome),
            "priority": self.priority,
            "evidence_refs": [self.verifier_evidence_ref],
        }

    def _canonical_document(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "need": self.need,
            "title": self.title,
            "why": self.why,
            "target": _thaw_json(self.target),
            "payload": _thaw_json(self.payload),
            "expected_outcome": _thaw_json(self.expected_outcome),
            "priority": self.priority,
            "source_slot": self.source_slot,
            "project_root": str(self.project_root),
        }


class NovaIntentReadOnlyVerifier:
    """Validate a snapshot without kernel, adapter-action, tool, or I/O access."""

    def __init__(self, project_root: Path) -> None:
        self._project_root = _resolved_root(project_root)

    def verify(self, snapshot: NovaIntentSnapshot) -> VerificationResult:
        """Return independent positive evidence only for a valid local snapshot."""
        try:
            self._validate(snapshot)
        except (TypeError, ValueError, OSError) as exc:
            raise InvalidVerifierResult("invalid Nova intent snapshot") from exc
        return VerificationResult(
            work="Validated a canonical Nova automatic-action snapshot locally.",
            evidence=(snapshot.verifier_evidence_ref,),
            decision=VERIFIED_DECISION,
            provenance={
                "adapter": "nova-intent-read-only",
                "mode": "read_only",
                "operation": "canonical_snapshot_validation",
                "intent_digest": snapshot.intent_digest,
            },
        )

    def _validate(self, snapshot: NovaIntentSnapshot) -> None:
        if not isinstance(snapshot, NovaIntentSnapshot):
            raise TypeError("Nova verifier requires a NovaIntentSnapshot")
        if _resolved_root(snapshot.project_root) != self._project_root:
            raise ValueError("Nova snapshot root does not match verifier root")
        if snapshot.action not in NOVA_AUTOMATIC_ACTIONS:
            raise ValueError("Nova snapshot action is not allowlisted")
        if snapshot.expected_output_scope != _ACTION_OUTPUT_SCOPES[snapshot.action]:
            raise ValueError("Nova snapshot output scope is invalid")
        expected_outcome = _canonical_mapping(snapshot.expected_outcome, "Nova expected_outcome")
        if expected_outcome.get("output_scope") != snapshot.expected_output_scope:
            raise ValueError("Nova snapshot output is outside its expected scope")
        document = {
            "action": _required_text(snapshot.action, "Nova action"),
            "need": _required_text(snapshot.need, "Nova need"),
            "title": _required_text(snapshot.title, "Nova title"),
            "why": _required_text(snapshot.why, "Nova why"),
            "target": _canonical_mapping(snapshot.target, "Nova target"),
            "payload": _canonical_mapping(snapshot.payload, "Nova payload"),
            "expected_outcome": expected_outcome,
            "priority": _priority(snapshot.priority),
            "source_slot": _source_slot(snapshot.source_slot),
            "project_root": str(_resolved_root(snapshot.project_root)),
        }
        if document != snapshot._canonical_document():
            raise ValueError("Nova snapshot is not canonical")
        _validate_document(document)
        if _digest(document) != snapshot.intent_digest:
            raise ValueError("Nova snapshot digest mismatch")
        if snapshot.proposal_id != f"nova-{snapshot.intent_digest}":
            raise ValueError("Nova snapshot proposal id is invalid")
        if snapshot.verifier_evidence_ref != f"nova:verifier:{snapshot.intent_digest}":
            raise ValueError("Nova verifier evidence reference is invalid")


def _resolved_root(project_root: Path) -> Path:
    root = Path(project_root).expanduser().resolve()
    if not root.is_absolute():  # pragma: no cover - resolve() is absolute
        raise ValueError("Nova project root must be absolute")
    return root


def _source_slot(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Nova source_slot must be an integer")
    if not 0 <= value <= _MAX_SOURCE_SLOT:
        raise ValueError("Nova source_slot is out of range")
    return value


def _priority(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Nova priority must be numeric")
    priority = float(value)
    if not math.isfinite(priority):
        raise ValueError("Nova priority must be finite")
    return round(max(0.0, min(1.0, priority)), 4)


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    text = value.strip()
    if len(text) > _MAX_TEXT_LENGTH:
        raise ValueError(f"{label} is too long")
    return text


def _canonical_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    return _freeze_json_mapping(value, label)


def _freeze_json_mapping(value: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    item_count = [0]
    frozen = _freeze_json_value(value, label, depth=0, item_count=item_count)
    if not isinstance(frozen, Mapping):  # pragma: no cover - checked above
        raise TypeError(f"{label} must be a mapping")
    return frozen


def _freeze_json_value(
    value: Any,
    label: str,
    *,
    depth: int,
    item_count: list[int],
) -> Any:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError(f"{label} is nested too deeply")
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{label} must not contain non-finite numbers")
        if isinstance(value, str) and len(value) > _MAX_TEXT_LENGTH:
            raise ValueError(f"{label} contains text that is too long")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, child in value.items():
            item_count[0] += 1
            if item_count[0] > _MAX_JSON_ITEMS:
                raise ValueError(f"{label} contains too many items")
            if not isinstance(key, str) or not key or len(key) > _MAX_KEY_LENGTH:
                raise ValueError(f"{label} contains an invalid key")
            frozen[key] = _freeze_json_value(
                child, label, depth=depth + 1, item_count=item_count
            )
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        frozen_list: list[Any] = []
        for child in value:
            item_count[0] += 1
            if item_count[0] > _MAX_JSON_ITEMS:
                raise ValueError(f"{label} contains too many items")
            frozen_list.append(
                _freeze_json_value(child, label, depth=depth + 1, item_count=item_count)
            )
        return tuple(frozen_list)
    raise TypeError(f"{label} must contain JSON-safe values")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _digest(document: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        _thaw_json(document),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _validate_document(document: Mapping[str, Any]) -> None:
    if not isinstance(document, Mapping):
        raise TypeError("Nova snapshot canonical document must be a mapping")
    if _contains_sensitive_marker(document):
        raise ValueError("Nova snapshot contains a sensitive or effectful marker")


def _contains_sensitive_marker(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_sensitive_marker(key) or _contains_sensitive_marker(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_marker(child) for child in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in _SENSITIVE_MARKERS)
    return False
