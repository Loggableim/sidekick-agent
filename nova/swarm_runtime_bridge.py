"""Canonical, opt-in Nova submissions for the Swarm safety boundary.

This module deliberately only prepares immutable data and checks it locally.
It imports neither the Nova kernel nor any transport, tool, or model surface.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping
import unicodedata

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
_MAX_TEXT_LENGTH = 4_096
_MAX_PRIORITY_ABS = 1_000_000
_PERCENT_ESCAPE = re.compile(r"%[0-9a-fA-F]{2}")
_OPAQUE_ENCODING_PREFIX = re.compile(r"^(?:base64(?:url)?|b64|data):", re.IGNORECASE)
_BASE64_TEXT = re.compile(r"^[A-Za-z0-9+/_-]+={0,2}$")
_ENCODED_CONTROL_TOKENS = (
    b"command",
    b"apply",
    b"secret",
    b"url",
    b"password",
    b"credential",
    b"auth.json",
    b".env",
)
_CONTEXT_CONSTRUCTION_TOKEN = object()


class _NovaBridgeContext:
    """Private host-owned context that revalidates its trusted root at use."""

    __slots__ = ("_root", "_validator")

    def __init__(
        self,
        root: Path,
        validator: Callable[[Path], Path],
        construction_token: object,
    ) -> None:
        if construction_token is not _CONTEXT_CONSTRUCTION_TOKEN:
            raise TypeError("Nova bridge contexts are host-owned")
        self._root = root
        self._validator = validator

    def __reduce__(self):
        raise TypeError("Nova bridge contexts cannot be serialized")

    def _validated_root(self) -> Path:
        root = self._root
        if not root.exists() or not root.is_dir():
            raise ValueError("trusted Nova project root is no longer a directory")
        validated = Path(self._validator(root)).expanduser().resolve()
        if validated != root:
            raise ValueError("trusted Nova project root validation changed the root")
        return root


def _create_nova_bridge_context(
    validated_project_root: Path,
    *,
    validator: Callable[[Path], Path],
) -> _NovaBridgeContext:
    """Create a host-only context after its owner independently validates root.

    This deliberately remains private until the runtime host integration owns
    trusted-workspace admission.  It accepts no YAML, intent, or request data.
    """
    if not callable(validator):
        raise TypeError("Nova bridge context validator must be callable")
    root = Path(validated_project_root).expanduser().resolve()
    context = _NovaBridgeContext(root, validator, _CONTEXT_CONSTRUCTION_TOKEN)
    context._validated_root()
    return context


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


@dataclass(frozen=True, slots=True)
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
        project_root: _NovaBridgeContext,
    ) -> "NovaIntentSnapshot":
        """Discard caller identity/security metadata and retain safe content only."""
        if not isinstance(submission, Mapping):
            raise TypeError("Nova submission must be a mapping")
        action = _required_text(submission.get("action"), "Nova action")
        if action not in NOVA_AUTOMATIC_ACTIONS:
            raise ValueError("not an automatic Nova action")
        # Resolve the adapter-owned record before any suggestion reaches its
        # translation path; this can never instantiate or invoke a kernel.
        spec = get_nova_action_spec(action)
        if spec.output_scope is None:  # defensive: only explicit local outputs
            raise ValueError("automatic Nova action has no local output scope")
        slot = _source_slot(source_slot)
        root = _trusted_project_root(project_root)
        document = {
            "action": action,
            "need": _required_text(submission.get("need"), "Nova need"),
            "title": _required_text(submission.get("title"), "Nova title"),
            "why": _required_text(submission.get("why"), "Nova why"),
            "target": _canonical_action_mapping(
                submission.get("target", {}),
                "Nova target",
                allowed_keys=spec.target_keys,
                requires_value=spec.requires_target,
                trim_values=True,
            ),
            "payload": _canonical_action_mapping(
                submission.get("payload", {}),
                "Nova payload",
                allowed_keys=spec.payload_keys,
            ),
            "expected_outcome": MappingProxyType({"output_scope": spec.output_scope}),
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
        spec = get_nova_action_spec(self.action)
        if spec.output_scope is None:
            raise ValueError("Nova action has no automatic output scope")
        return spec.output_scope

    def to_suggestion(self, project_root: _NovaBridgeContext) -> Mapping[str, Any]:
        """Return the sole safe adapter input for this immutable snapshot."""
        _canonical_snapshot_document(self, project_root)
        return MappingProxyType(
            {
                "id": self.proposal_id,
                "intent_id": self.proposal_id,
                "proposal_id": self.proposal_id,
                "action": self.action,
                "need": self.need,
                "title": self.title,
                "why": self.why,
                "target": self.target,
                "payload": self.payload,
                "expected_outcome": self.expected_outcome,
                "priority": self.priority,
                "evidence_refs": (self.verifier_evidence_ref,),
            }
        )

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

    def __init__(
        self,
        project_root: _NovaBridgeContext,
    ) -> None:
        self._project_root = _trusted_project_root(project_root)
        self._context = project_root

    def verify(self, snapshot: NovaIntentSnapshot) -> VerificationResult:
        """Return independent positive evidence only for a valid local snapshot."""
        try:
            self._validate(snapshot)
        except (TypeError, ValueError, OSError, OverflowError) as exc:
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
        _canonical_snapshot_document(snapshot, self._context)


def _canonical_snapshot_document(
    snapshot: NovaIntentSnapshot,
    project_root: _NovaBridgeContext,
) -> dict[str, Any]:
    if not isinstance(snapshot, NovaIntentSnapshot):
        raise TypeError("Nova verifier requires a NovaIntentSnapshot")
    root = _trusted_project_root(project_root)
    if Path(snapshot.project_root).expanduser().resolve() != root:
        raise ValueError("Nova snapshot root does not match verifier root")
    if snapshot.action not in NOVA_AUTOMATIC_ACTIONS:
        raise ValueError("Nova snapshot action is not allowlisted")
    spec = get_nova_action_spec(snapshot.action)
    if spec.output_scope is None or snapshot.expected_output_scope != spec.output_scope:
        raise ValueError("Nova snapshot output scope is invalid")
    expected_outcome = MappingProxyType({"output_scope": spec.output_scope})
    if _thaw_json(snapshot.expected_outcome) != _thaw_json(expected_outcome):
        raise ValueError("Nova snapshot output is outside its expected scope")
    document = {
        "action": _required_text(snapshot.action, "Nova action"),
        "need": _required_text(snapshot.need, "Nova need"),
        "title": _required_text(snapshot.title, "Nova title"),
        "why": _required_text(snapshot.why, "Nova why"),
        "target": _canonical_action_mapping(
            snapshot.target,
            "Nova target",
            allowed_keys=spec.target_keys,
            requires_value=spec.requires_target,
            trim_values=True,
        ),
        "payload": _canonical_action_mapping(
            snapshot.payload,
            "Nova payload",
            allowed_keys=spec.payload_keys,
        ),
        "expected_outcome": expected_outcome,
        "priority": _priority(snapshot.priority),
        "source_slot": _source_slot(snapshot.source_slot),
        "project_root": str(root),
    }
    if document != snapshot._canonical_document():
        raise ValueError("Nova snapshot is not canonical")
    if _digest(document) != snapshot.intent_digest:
        raise ValueError("Nova snapshot digest mismatch")
    if snapshot.proposal_id != f"nova-{snapshot.intent_digest}":
        raise ValueError("Nova snapshot proposal id is invalid")
    if snapshot.verifier_evidence_ref != f"nova:verifier:{snapshot.intent_digest}":
        raise ValueError("Nova verifier evidence reference is invalid")
    return document


def _trusted_project_root(project_root: _NovaBridgeContext) -> Path:
    if not isinstance(project_root, _NovaBridgeContext):
        raise TypeError("Nova bridge requires a host-owned trusted root context")
    return project_root._validated_root()


def _source_slot(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Nova source_slot must be an integer")
    if not 0 <= value <= _MAX_SOURCE_SLOT:
        raise ValueError("Nova source_slot is out of range")
    return value


def _priority(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Nova priority must be numeric")
    if isinstance(value, int) and abs(value) > _MAX_PRIORITY_ABS:
        raise ValueError("Nova priority is too large")
    try:
        priority = float(value)
    except OverflowError as exc:
        raise ValueError("Nova priority is too large") from exc
    if not math.isfinite(priority):
        raise ValueError("Nova priority must be finite")
    return round(max(0.0, min(1.0, priority)), 4)


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    text = _normalized_plain_text(value, label).strip()
    if len(text) > _MAX_TEXT_LENGTH:
        raise ValueError(f"{label} is too long")
    return text


def _canonical_action_mapping(
    value: Any,
    label: str,
    *,
    allowed_keys: frozenset[str],
    requires_value: bool = False,
    trim_values: bool = False,
) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    canonical: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _normalized_field_key(raw_key, label)
        if key not in allowed_keys or key in canonical:
            raise ValueError(f"{label} contains an unsupported field")
        text = _normalized_plain_text(raw_value, label)
        if trim_values:
            text = text.strip()
            if not text:
                continue
        canonical[key] = text
    if requires_value and not any(item.strip() for item in canonical.values()):
        raise ValueError(f"{label} requires a concrete target")
    return MappingProxyType(canonical)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
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


def _normalized_field_key(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} contains an invalid key")
    return _normalized_plain_text(value, label).casefold()


def _normalized_plain_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} values must be plain text")
    normalized = unicodedata.normalize("NFC", value)
    if len(normalized) > _MAX_TEXT_LENGTH:
        raise ValueError(f"{label} contains text that is too long")
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise ValueError(f"{label} contains control characters")
    if _PERCENT_ESCAPE.search(normalized) or _is_opaque_encoded_text(normalized):
        raise ValueError(f"{label} contains opaque encoded material")
    return normalized


def _is_opaque_encoded_text(value: str) -> bool:
    candidate = value.strip()
    if _OPAQUE_ENCODING_PREFIX.match(candidate):
        return True
    if not _BASE64_TEXT.fullmatch(candidate):
        return False
    try:
        padded = candidate.rstrip("=") + "=" * (-len(candidate.rstrip("=")) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    except ValueError:
        return False
    return any(token in decoded.lower() for token in _ENCODED_CONTROL_TOKENS)
