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
import threading
from typing import Any, Callable, Mapping
import unicodedata

from cli.swarm_host import SwarmExecutionOptions
from nova.swarm_adapter import NovaSwarmAdapter, get_nova_action_spec
from swarm_core.engine import PreCompletionContext, PreCompletionResult
from swarm_core.policy import PolicyGate, proposal_digest
from swarm_core.config import load_integration_config, save_integration_config
from swarm_core.store import ProjectSwarmStore
from swarm_core.types import IntegrationAdmissionRequest, SwarmRun
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
_RUNTIME_HOOK_ID = "nova-runtime-v1"
_NOVA_NAMESPACE = "nova"
_RUNTIME_BINDINGS_LOCK = threading.RLock()
_RUNTIME_BINDINGS: dict[Path, "_NovaRuntimeBinding"] = {}


@dataclass(frozen=True)
class _NovaRuntimeBinding:
    """Non-serializable host capability retained only in this process."""

    run_id: str
    intent_digest: str
    proposal_digest: str
    mode: str
    max_calls: int
    adapter: NovaSwarmAdapter
    context: "_NovaBridgeContext"
    verifier: "NovaIntentReadOnlyVerifier"


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


@dataclass(frozen=True)
class NovaBridgeResult:
    """Bounded public result of admitting one Nova intent."""

    status: str
    run_id: str | None = None
    reason: str | None = None


class NovaPreCompletionHook:
    """The only runtime path from a completed Swarm workflow to Nova action."""

    hook_id = _RUNTIME_HOOK_ID

    def __init__(
        self,
        adapter: NovaSwarmAdapter | None,
        project_root: _NovaBridgeContext | None = None,
    ) -> None:
        self._adapter = adapter
        self._project_root = project_root

    def run(self, context: PreCompletionContext) -> PreCompletionResult:
        """Revalidate durable admission state immediately before the adapter."""
        try:
            metadata = context.run.metadata
            if metadata.get("integration_namespace") != _NOVA_NAMESPACE:
                return PreCompletionResult(False, "nova_invalid_admission")
            if metadata.get("required_pre_completion_hook") != self.hook_id:
                return PreCompletionResult(False, "nova_required_hook_mismatch")
            if self._adapter is None or self._project_root is None:
                return PreCompletionResult(False, "nova_bridge_unavailable")
            snapshot = _snapshot_from_metadata(metadata, self._project_root)
            verifier = NovaIntentReadOnlyVerifier(self._project_root)
            verifier.verify(snapshot)
            suggestion = snapshot.to_suggestion(self._project_root)
            proposal = self._adapter.translate(suggestion)
            if proposal_digest(proposal) != metadata.get("proposal_digest"):
                return PreCompletionResult(False, "nova_proposal_digest_mismatch")
            if snapshot.intent_digest != metadata.get("nova_intent_digest"):
                return PreCompletionResult(False, "nova_snapshot_digest_mismatch")
            checkpoints = context.store.get_workflow_role_checkpoints(context.run.run_id)
            if not PolicyGate._has_durable_review_quorum(proposal, checkpoints):
                return PreCompletionResult(False, "nova_review_evidence_unavailable")
            context.store.append_event(
                context.run.run_id,
                "nova.bridge.action_proposed",
                {"proposal_id": proposal.proposal_id},
            )
            execution = self._adapter.execute_suggestion(suggestion, context.run)
            if execution.reason == "execution_already_claimed":
                context.store.append_event_once(
                    context.run.run_id,
                    "nova.bridge.recovery_required",
                    {},
                    idempotency_key="nova-action-claim-recovery",
                )
                return PreCompletionResult(
                    False, "nova_action_claimed_requires_human_recovery"
                )
            if not execution.executed:
                return PreCompletionResult(False, _nova_pause_reason(execution.reason))
            context.store.append_event(
                context.run.run_id,
                "nova.bridge.action_result",
                {"proposal_id": proposal.proposal_id, "executed": True},
            )
            return PreCompletionResult(True)
        except Exception:
            return PreCompletionResult(False, "nova_pre_completion_validation_failed")


class NovaSwarmRuntimeBridge:
    """Explicit host-owned admission bridge; it has no startup side effects."""

    def __init__(
        self,
        kernel: Any,
        *,
        project_root: Path,
        trusted_project_root: _NovaBridgeContext | None = None,
        dispatcher: Callable[[Path, str], Any] | None = None,
    ) -> None:
        self._kernel = kernel
        self._project_root = Path(project_root).expanduser().resolve()
        self._trusted_project_root = trusted_project_root
        self._dispatcher = dispatcher

    def submit(self, suggestion: Mapping[str, Any], *, source_slot: int) -> NovaBridgeResult:
        """Admit one canonical intent and dispatch exactly one newly-created worker."""
        config = load_nova_bridge_config(self._project_root)
        if not config.enabled:
            return NovaBridgeResult("bridge_disabled")
        action = suggestion.get("action") if isinstance(suggestion, Mapping) else None
        key = _submission_key(suggestion, source_slot)
        if action not in NOVA_AUTOMATIC_ACTIONS:
            store = ProjectSwarmStore(self._project_root)
            admission = store.record_integration_rejection(
                _NOVA_NAMESPACE, key, reason="unsupported_action"
            )
            return NovaBridgeResult("unsupported_action", reason=admission.reason)
        context = self._runtime_context()
        snapshot = NovaIntentSnapshot.from_submission(
            suggestion, source_slot=source_slot, project_root=context
        )
        store = ProjectSwarmStore(self._project_root)
        adapter = NovaSwarmAdapter(self._kernel, PolicyGate(store), enabled=True)
        proposal = adapter.translate(snapshot.to_suggestion(context))
        yolo = self._kernel.is_yolo_enabled()
        if type(yolo) is not bool:
            yolo = False
        mode, max_calls, rolling_limit = (
            ("autonomous", 128, None) if yolo else ("reviewed_execution", 48, 6)
        )
        metadata = {
            "goal": snapshot.title,
            "pack": "coding-team",
            "project_root": str(self._project_root),
            "autonomy": mode,
            "integration_namespace": _NOVA_NAMESPACE,
            "nova_intent_digest": snapshot.intent_digest,
            "nova_snapshot": _snapshot_metadata(snapshot),
            "nova_mode": mode,
            "nova_max_calls": max_calls,
            "proposal_digest": proposal_digest(proposal),
            "required_pre_completion_hook": _RUNTIME_HOOK_ID,
        }
        admission = store.admit_integration_run(
            IntegrationAdmissionRequest(
                namespace=_NOVA_NAMESPACE,
                idempotency_key=snapshot.intent_digest,
                metadata=metadata,
                max_active=1,
                rolling_window_seconds=24 * 60 * 60,
                rolling_run_limit=rolling_limit,
            )
        )
        if admission.status != "created" or admission.run is None:
            if admission.run is not None:
                store.append_event(admission.run.run_id, "nova.bridge.admission_not_dispatched", {"status": admission.status})
            return NovaBridgeResult(admission.status, admission.run.run_id, admission.reason)
        _register_runtime_binding(
            self._project_root,
            _NovaRuntimeBinding(
                admission.run.run_id,
                snapshot.intent_digest,
                proposal_digest(proposal),
                mode,
                max_calls,
                adapter,
                context,
                NovaIntentReadOnlyVerifier(context),
            ),
        )
        store.append_event(admission.run.run_id, "nova.bridge.admitted", {"mode": mode, "max_calls": max_calls})
        if not self._dispatch(admission.run.run_id):
            _pause_dispatch_failure(store, admission.run.run_id)
            return NovaBridgeResult("dispatch_failed", admission.run.run_id, "nova_dispatch_failed")
        return NovaBridgeResult("created", admission.run.run_id)

    def _runtime_context(self) -> _NovaBridgeContext:
        context = self._trusted_project_root
        if not isinstance(context, _NovaBridgeContext):
            raise ValueError("Nova runtime bridge requires host-owned trusted root")
        root = _trusted_project_root(context)
        kernel_root = Path(self._kernel.space_dir).expanduser().resolve()
        actions_root = Path(self._kernel.actions.space_dir).expanduser().resolve()
        if root != self._project_root or kernel_root != root or actions_root != root:
            raise ValueError("Nova runtime roots do not match")
        return context

    def _dispatch(self, run_id: str) -> bool:
        dispatcher = self._dispatcher or _default_runtime_dispatcher
        thread = threading.Thread(
            target=_run_worker, args=(dispatcher, self._project_root, run_id),
            name=f"nova-swarm-{run_id}", daemon=True,
        )
        try:
            thread.start()
        except RuntimeError:
            return False
        return True


def nova_execution_options_for_run(
    project_root: Path, run: SwarmRun
) -> SwarmExecutionOptions | None:
    """Resolve only durable Nova runs; ordinary Swarm runs keep default options."""
    if run.metadata.get("integration_namespace") != _NOVA_NAMESPACE:
        return None
    mode = run.metadata.get("nova_mode")
    max_calls = 128 if mode == "autonomous" else 48 if mode == "reviewed_execution" else None
    if (
        max_calls is None
        or run.metadata.get("autonomy") != mode
        or run.metadata.get("nova_max_calls") != max_calls
        or run.metadata.get("required_pre_completion_hook") != _RUNTIME_HOOK_ID
    ):
        return SwarmExecutionOptions(blocked_reason="execution_options_blocked")
    if load_nova_bridge_config(Path(project_root)).enabled is not True:
        return SwarmExecutionOptions(blocked_reason="nova_bridge_disabled")
    binding = _runtime_binding_for(Path(project_root), run)
    if binding is None:
        return SwarmExecutionOptions(blocked_reason="nova_bridge_unavailable")
    return SwarmExecutionOptions(
        max_calls=max_calls,
        verifier=binding.verifier,
        pre_completion_hook=NovaPreCompletionHook(binding.adapter, binding.context),
    )


def _snapshot_metadata(snapshot: NovaIntentSnapshot) -> dict[str, Any]:
    return snapshot._canonical_document()


def _snapshot_from_metadata(metadata: Mapping[str, Any], context: _NovaBridgeContext) -> NovaIntentSnapshot:
    raw = metadata.get("nova_snapshot")
    if not isinstance(raw, Mapping):
        raise ValueError("missing Nova snapshot")
    return NovaIntentSnapshot.from_submission(raw, source_slot=raw.get("source_slot"), project_root=context)


def _submission_key(suggestion: Any, source_slot: Any) -> str:
    try:
        return sha256(json.dumps({"suggestion": suggestion, "source_slot": source_slot}, sort_keys=True, default=str).encode()).hexdigest()
    except Exception:
        return "invalid-nova-submission"


def _nova_pause_reason(reason: object) -> str:
    allowed = {
        "adapter_disabled": "adapter_disabled",
        "execution_already_claimed": "action_claimed",
        "nova_policy_blocked": "policy_denied",
        "kernel_workspace_mismatch": "root_mismatch",
        "kernel_policy_unavailable": "policy_unavailable",
        "kernel_policy_tier_mismatch": "policy_mismatch",
        "nova_governance_malformed": "governance_invalid",
        "nova_governance_intent_mismatch": "governance_mismatch",
        "nova_governance_policy_mismatch": "governance_mismatch",
    }
    return "nova_" + allowed.get(reason, "action_blocked")


def register_nova_runtime_context(
    project_root: Path,
    *,
    run: SwarmRun,
    adapter: NovaSwarmAdapter,
    trusted_project_root: _NovaBridgeContext,
) -> None:
    """Explicit Task-6 seam for a host to attach a non-persisted capability."""
    metadata = run.metadata
    if metadata.get("integration_namespace") != _NOVA_NAMESPACE:
        raise ValueError("Nova runtime context requires a Nova run")
    context = _trusted_project_root(trusted_project_root)
    if context != Path(project_root).expanduser().resolve():
        raise ValueError("Nova runtime context root mismatch")
    digest = metadata.get("nova_intent_digest")
    proposal = metadata.get("proposal_digest")
    mode = metadata.get("nova_mode")
    max_calls = metadata.get("nova_max_calls")
    expected_calls = {"reviewed_execution": 48, "autonomous": 128}.get(mode)
    if (
        not isinstance(digest, str)
        or not isinstance(proposal, str)
        or expected_calls is None
        or max_calls != expected_calls
        or metadata.get("autonomy") != mode
        or metadata.get("required_pre_completion_hook") != _RUNTIME_HOOK_ID
    ):
        raise ValueError("Nova runtime context requires immutable digests")
    _register_runtime_binding(
        context,
        _NovaRuntimeBinding(
            run.run_id,
            digest,
            proposal,
            mode,
            max_calls,
            adapter,
            trusted_project_root,
            NovaIntentReadOnlyVerifier(trusted_project_root),
        ),
    )


def _register_runtime_binding(project_root: Path, binding: _NovaRuntimeBinding) -> None:
    with _RUNTIME_BINDINGS_LOCK:
        _RUNTIME_BINDINGS[Path(project_root).resolve()] = binding


def _runtime_binding_for(project_root: Path, run: SwarmRun) -> _NovaRuntimeBinding | None:
    with _RUNTIME_BINDINGS_LOCK:
        binding = _RUNTIME_BINDINGS.get(Path(project_root).resolve())
    if binding is None:
        return None
    metadata = run.metadata
    if (
        binding.run_id != run.run_id
        or metadata.get("integration_namespace") != _NOVA_NAMESPACE
        or metadata.get("project_root") != str(Path(project_root).resolve())
        or binding.intent_digest != metadata.get("nova_intent_digest")
        or binding.proposal_digest != metadata.get("proposal_digest")
        or binding.mode != metadata.get("nova_mode")
        or metadata.get("autonomy") != binding.mode
        or metadata.get("nova_max_calls") != binding.max_calls
        or metadata.get("required_pre_completion_hook") != _RUNTIME_HOOK_ID
    ):
        return None
    try:
        snapshot = _snapshot_from_metadata(metadata, binding.context)
        binding.verifier.verify(snapshot)
    except (TypeError, ValueError, InvalidVerifierResult):
        return None
    return binding


def _default_runtime_dispatcher(project_root: Path, run_id: str) -> None:
    """Concrete named worker; Cloud calls remain guarded by the host service."""
    from cli.swarm_host import SidekickSwarmService

    SidekickSwarmService(
        execution_options_resolver=nova_execution_options_for_run
    ).execute_run(project_root, run_id)


def _pause_dispatch_failure(store: ProjectSwarmStore, run_id: str) -> None:
    try:
        store.set_run_status(run_id, "paused")
    except (RuntimeError, ValueError):
        pass
    store.append_event(run_id, "nova.bridge.dispatch_failed", {"reason": "nova_dispatch_failed"})


def _run_worker(dispatcher: Callable[[Path, str], Any], project_root: Path, run_id: str) -> None:
    """Never leave an admitted run running if its in-process worker crashes."""
    try:
        dispatcher(project_root, run_id)
    except BaseException:
        _pause_dispatch_failure(ProjectSwarmStore(project_root), run_id)
        return
    run = ProjectSwarmStore.open_read_only(project_root).get_run(run_id)
    if run is not None and run.status == "completed":
        _unregister_runtime_binding(project_root, run_id)


def _unregister_runtime_binding(project_root: Path, run_id: str) -> None:
    with _RUNTIME_BINDINGS_LOCK:
        root = Path(project_root).resolve()
        binding = _RUNTIME_BINDINGS.get(root)
        if binding is not None and binding.run_id == run_id:
            del _RUNTIME_BINDINGS[root]
