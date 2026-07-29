"""Capability-bound action gateway for one supervised target Space."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from nova.space_supervisor import ManagedSpaceCapability, ManagedSpaceSupervisor
from swarm_core.policy import PolicyGate, PolicyStatus
from swarm_core.types import ActionProposal, thaw_json_value


_SHA256_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MANAGED_MODEL_CALL_BUDGET = 128


@dataclass(frozen=True, slots=True)
class ManagedWorktreeHandle:
    canonical_root: Path
    path: Path
    run_id: str
    identity: str
    artifact_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_root", Path(self.canonical_root))
        object.__setattr__(self, "path", Path(self.path))


@dataclass(frozen=True, slots=True)
class ManagedOperationRequest:
    operation: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "arguments",
            MappingProxyType(dict(self.arguments)),
        )


@dataclass(frozen=True, slots=True)
class WorkerResult:
    ok: bool
    code: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class RedactedFailure:
    code: str


@dataclass(frozen=True, slots=True)
class DiagnosisResult:
    restoration_verified: bool
    code: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class GatewayResult:
    ok: bool
    code: str


class TargetWorktreeProvider(Protocol):
    def create(self, canonical_root: Path, run_id: str) -> ManagedWorktreeHandle: ...


class ManagedWorker(Protocol):
    def execute(
        self,
        handle: ManagedWorktreeHandle,
        request: ManagedOperationRequest,
    ) -> WorkerResult: ...


class DiagnosisRunner(Protocol):
    def diagnose(
        self,
        failure: RedactedFailure,
        remaining_budget: int,
    ) -> DiagnosisResult: ...


@dataclass(frozen=True, slots=True)
class _OperationSpec:
    family: str
    worker: str
    required_fields: frozenset[str]
    optional_fields: frozenset[str] = frozenset()


_OPERATIONS: Mapping[str, _OperationSpec] = MappingProxyType(
    {
        "local.apply_patch": _OperationSpec(
            "target_local_worktree",
            "local",
            frozenset({"artifact_digest", "patch"}),
        ),
        "local.write_file": _OperationSpec(
            "target_local_worktree",
            "local",
            frozenset({"artifact_digest", "path", "content"}),
        ),
        "local.format": _OperationSpec(
            "target_local_worktree",
            "local",
            frozenset({"artifact_digest", "paths"}),
        ),
        "local.test": _OperationSpec(
            "target_local_worktree",
            "local",
            frozenset({"artifact_digest", "selector"}),
        ),
        "github.commit": _OperationSpec(
            "github_publication",
            "github",
            frozenset({"artifact_digest", "message"}),
        ),
        "github.push": _OperationSpec(
            "github_publication",
            "github",
            frozenset({"artifact_digest", "branch"}),
        ),
        "github.pull_request": _OperationSpec(
            "github_publication",
            "github",
            frozenset({"artifact_digest", "title", "body"}),
            frozenset({"draft"}),
        ),
        "github.release": _OperationSpec(
            "github_publication",
            "github",
            frozenset({"artifact_digest", "tag", "title", "notes"}),
        ),
        "deployment.deploy": _OperationSpec(
            "target_deployment_worker",
            "deployment",
            frozenset({"artifact_digest", "deployment_ref"}),
        ),
    }
)
_SUCCESS_CODES = MappingProxyType(
    {
        "local": "local_completed",
        "github": "github_completed",
        "deployment": "deployment_completed",
    }
)

_SENSITIVE_FIELD_PARTS = frozenset(
    {
        "admin",
        "authorization",
        "billing",
        "command",
        "credential",
        "delete",
        "destroy",
        "env",
        "host",
        "iam",
        "package",
        "password",
        "payment",
        "privilege",
        "purchase",
        "remote",
        "remove",
        "secret",
        "shell",
        "sudo",
        "token",
        "url",
    }
)
_SENSITIVE_VALUE_MARKERS = (
    "://",
    "api_key=",
    "authorization:",
    "bearer ",
    "begin private key",
    "credential=",
    "password=",
    "private_key=",
    "secret=",
    "token=",
)


class ManagedSpaceActionGateway:
    """The sole router from an opaque managed capability to narrow workers."""

    def __init__(
        self,
        *,
        supervisor: ManagedSpaceSupervisor,
        policy_gate: PolicyGate,
        worktree_provider: TargetWorktreeProvider,
        local_worker: ManagedWorker,
        github_worker: ManagedWorker,
        deployment_worker: ManagedWorker,
        diagnosis_runner: DiagnosisRunner,
    ) -> None:
        if not isinstance(supervisor, ManagedSpaceSupervisor):
            raise TypeError("managed gateway requires ManagedSpaceSupervisor")
        if not isinstance(policy_gate, PolicyGate):
            raise TypeError("managed gateway requires PolicyGate")
        self._supervisor = supervisor
        self._policy_gate = policy_gate
        self._worktree_provider = worktree_provider
        self._workers = {
            "local": local_worker,
            "github": github_worker,
            "deployment": deployment_worker,
        }
        self._diagnosis_runner = diagnosis_runner

    def execute(
        self,
        capability: ManagedSpaceCapability | None,
        proposal: ActionProposal,
    ) -> GatewayResult:
        if not isinstance(capability, ManagedSpaceCapability):
            return GatewayResult(False, "capability_invalid")
        if not isinstance(proposal, ActionProposal):
            return GatewayResult(False, "proposal_invalid")

        spec = _OPERATIONS.get(proposal.requested_action.name)
        arguments = thaw_json_value(proposal.requested_action.arguments)
        if (
            spec is None
            or spec.family not in capability._allowed_action_families
            or not _safe_operation_arguments(spec, arguments)
        ):
            return GatewayResult(False, "operation_hard_denied")
        if (
            proposal.requested_action.workspace != capability._canonical_root
            or self._policy_gate.store.project_root != capability._canonical_root
        ):
            return GatewayResult(False, "target_root_mismatch")
        if not proposal.requested_action.use_worktree:
            return GatewayResult(False, "worktree_required")
        if not self._supervisor.revalidate_action_boundary(capability):
            return GatewayResult(False, "capability_invalid")

        try:
            handle = self._worktree_provider.create(
                capability._canonical_root,
                capability._run_id,
            )
        except Exception:
            return GatewayResult(False, "worktree_unavailable")
        handle_error = _worktree_error(handle, capability)
        if handle_error is not None:
            return GatewayResult(False, handle_error)
        if not self._supervisor.revalidate_action_boundary(capability):
            return GatewayResult(False, "capability_invalid")

        policy = self._policy_gate.authorize_managed_yolo_and_claim(
            proposal,
            capability,
            worktree_identity=handle.identity,
            artifact_digest=handle.artifact_digest,
        )
        if policy.status is not PolicyStatus.ALLOWED:
            return GatewayResult(False, policy.reason)
        if not self._supervisor.revalidate_action_boundary(capability):
            return GatewayResult(False, "capability_invalid")

        request = ManagedOperationRequest(
            proposal.requested_action.name,
            {
                key: value
                for key, value in arguments.items()
                if key != "artifact_digest"
            },
        )
        worker = self._workers[spec.worker]
        try:
            outcome = worker.execute(handle, request)
        except Exception:
            outcome = WorkerResult(False, "worker_failed")
        if not isinstance(outcome, WorkerResult):
            outcome = WorkerResult(False, "worker_invalid_result")
        if outcome.ok is True:
            return GatewayResult(True, _SUCCESS_CODES[spec.worker])
        if spec.worker != "deployment":
            return GatewayResult(False, "worker_failed")
        return self._recover_deployment_failure(capability)

    def _recover_deployment_failure(
        self,
        capability: ManagedSpaceCapability,
    ) -> GatewayResult:
        events = self._policy_gate.store.list_events(capability._run_id)
        used = sum(
            event.event_type == "model.attempt_started"
            for event in events
        )
        remaining = max(0, _MANAGED_MODEL_CALL_BUDGET - used)
        if remaining == 0:
            self._supervisor._pause(
                capability,
                "deployment_budget_exhausted",
            )
            return GatewayResult(False, "deployment_budget_exhausted")
        try:
            diagnosis = self._diagnosis_runner.diagnose(
                RedactedFailure("deployment_failed"),
                remaining,
            )
        except Exception:
            diagnosis = DiagnosisResult(False, "diagnosis_failed")
        if not self._supervisor.revalidate_action_boundary(capability):
            return GatewayResult(False, "capability_invalid")
        if (
            isinstance(diagnosis, DiagnosisResult)
            and diagnosis.restoration_verified is True
        ):
            return GatewayResult(
                False,
                "deployment_retry_requires_fresh_evidence",
            )
        self._supervisor._pause(
            capability,
            "deployment_unverified",
        )
        return GatewayResult(False, "deployment_unverified")


def _worktree_error(
    handle: object,
    capability: ManagedSpaceCapability,
) -> str | None:
    if not isinstance(handle, ManagedWorktreeHandle):
        return "worktree_invalid"
    if not handle.path.is_absolute():
        return "worktree_not_absolute"
    if handle.canonical_root != capability._canonical_root:
        return "worktree_target_mismatch"
    if handle.run_id != capability._run_id:
        return "worktree_run_mismatch"
    if handle.path == capability._canonical_root:
        return "canonical_worktree_forbidden"
    if (
        not isinstance(handle.identity, str)
        or not handle.identity.strip()
        or not isinstance(handle.artifact_digest, str)
        or _SHA256_DIGEST.fullmatch(handle.artifact_digest) is None
    ):
        return "worktree_invalid"
    return None


def _safe_operation_arguments(
    spec: _OperationSpec,
    arguments: object,
) -> bool:
    if not isinstance(arguments, dict):
        return False
    fields = frozenset(arguments)
    if not spec.required_fields <= fields:
        return False
    if not fields <= spec.required_fields | spec.optional_fields:
        return False
    artifact_digest = arguments.get("artifact_digest")
    if (
        not isinstance(artifact_digest, str)
        or _SHA256_DIGEST.fullmatch(artifact_digest) is None
    ):
        return False
    return not _contains_sensitive_value(
        {
            key: value
            for key, value in arguments.items()
            if key != "artifact_digest"
        }
    )


def _contains_sensitive_value(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            parts = frozenset(filter(None, normalized.split("_")))
            if parts & _SENSITIVE_FIELD_PARTS:
                return True
            if _contains_sensitive_value(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_value(item) for item in value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        return any(marker in normalized for marker in _SENSITIVE_VALUE_MARKERS)
    return False
