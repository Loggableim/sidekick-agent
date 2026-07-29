"""Capability-bound action gateway for one supervised target Space."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
from typing import Callable, ClassVar, Mapping, Protocol, TypeAlias

from nova.space_supervisor import (
    ManagedSpaceActionContext,
    ManagedSpaceCapability,
    ManagedSpaceSupervisor,
)
from swarm_core.policy import (
    PolicyDecision,
    PolicyGate,
    PolicyStatus,
    proposal_digest,
)
from swarm_core.store import ProjectSwarmStore
from swarm_core.tools import (
    HostBoundExecutionRoute,
    create_host_bound_execution_route,
)
from swarm_core.types import (
    ActionProposal,
    ApprovalRecord,
    SwarmRun,
    WorkflowRoleCheckpoint,
    thaw_json_value,
)
from swarm_core.verifier import (
    InvalidVerifierResult,
    is_positive_verification_decision,
    verification_result_from_checkpoint_data,
)


_SHA256_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_TEST_NODE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\[[A-Za-z0-9_.:-]{1,128}\])?\Z")
_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}\Z")
_CURRENT_SECRET_TOKEN = re.compile(
    r"(?:github_pat_|gh[pousr]_|glpat-|xox[a-z]-)[A-Za-z0-9_-]{8,}",
    re.IGNORECASE,
)
_MANAGED_MODEL_CALL_BUDGET = 128
_FORBIDDEN_REF_PARTS = ("..", "@{", "//", ".lock")
_REQUIRED_MANAGED_REVIEW_MODELS = {
    "review_a": "glm-5.2",
    "review_b": "kimi-k2.7-code",
}
_APPROVING_REVIEW_DECISIONS = frozenset({"approve", "approved"})


@dataclass(frozen=True, slots=True)
class CreatedWorktree:
    """Untrusted creation result containing only the path to attest."""

    path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))


@dataclass(frozen=True, slots=True)
class WorktreeAttestation:
    """Trusted repository/worktree validator result."""

    source_root: Path
    worktree_root: Path
    run_id: str
    identity: str
    artifact_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_root", Path(self.source_root))
        object.__setattr__(self, "worktree_root", Path(self.worktree_root))


@dataclass(frozen=True, slots=True)
class ManagedWorktreeHandle:
    """Canonical attested handle supplied to managed workers."""

    canonical_root: Path
    path: Path
    run_id: str
    identity: str
    artifact_digest: str


@dataclass(frozen=True, slots=True)
class LocalApplyPatchRequest:
    path: str
    patch: str
    operation: ClassVar[str] = "local.apply_patch"


@dataclass(frozen=True, slots=True)
class LocalWriteFileRequest:
    path: str
    content: str
    operation: ClassVar[str] = "local.write_file"


@dataclass(frozen=True, slots=True)
class LocalFormatRequest:
    paths: tuple[str, ...]
    operation: ClassVar[str] = "local.format"


@dataclass(frozen=True, slots=True)
class LocalTestRequest:
    path: str
    nodes: tuple[str, ...]
    operation: ClassVar[str] = "local.test"


@dataclass(frozen=True, slots=True)
class GitHubCommitRequest:
    message: str
    operation: ClassVar[str] = "github.commit"


@dataclass(frozen=True, slots=True)
class GitHubPushRequest:
    branch: str
    operation: ClassVar[str] = "github.push"


@dataclass(frozen=True, slots=True)
class GitHubPullRequestRequest:
    title: str
    body: str
    draft: bool
    operation: ClassVar[str] = "github.pull_request"


@dataclass(frozen=True, slots=True)
class GitHubReleaseRequest:
    tag: str
    title: str
    notes: str
    operation: ClassVar[str] = "github.release"


@dataclass(frozen=True, slots=True)
class TargetDeploymentRequest:
    operation: ClassVar[str] = "deployment.deploy"


ManagedRequest: TypeAlias = (
    LocalApplyPatchRequest
    | LocalWriteFileRequest
    | LocalFormatRequest
    | LocalTestRequest
    | GitHubCommitRequest
    | GitHubPushRequest
    | GitHubPullRequestRequest
    | GitHubReleaseRequest
    | TargetDeploymentRequest
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
    def create(self, canonical_root: Path, run_id: str) -> CreatedWorktree: ...


class TargetWorktreeAttestor(Protocol):
    def attest(
        self,
        source_root: Path,
        worktree_root: Path,
        run_id: str,
    ) -> WorktreeAttestation | None: ...


RunTargetResolver = Callable[[Path, str], Path | None]


class GitWorktreeAttestor:
    """Read-only Git attestation for one host-bound linked worktree."""

    def __init__(self, *, run_target_resolver: RunTargetResolver) -> None:
        if not callable(run_target_resolver):
            raise TypeError("Git worktree attestor requires a run target resolver")
        self._resolve_run_target = run_target_resolver

    def attest(
        self,
        source_root: Path,
        worktree_root: Path,
        run_id: str,
    ) -> WorktreeAttestation | None:
        if not isinstance(run_id, str) or not run_id.strip():
            return None
        source_input = Path(source_root)
        target_input = Path(worktree_root)
        try:
            source = source_input.resolve(strict=True)
            target = target_input.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        # The gateway supplies canonical paths. Direct attestor callers cannot
        # smuggle aliases that resolve to an otherwise valid member.
        if source_input != source or target_input != target or source == target:
            return None
        try:
            expected_input = self._resolve_run_target(source, run_id)
            if expected_input is None:
                return None
            expected_path = Path(expected_input)
            expected = expected_path.resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        if expected_path != expected or expected != target:
            return None
        try:
            source_top = _git_path(
                source,
                _run_git(source, "rev-parse", "--show-toplevel"),
            )
            target_top = _git_path(
                target,
                _run_git(target, "rev-parse", "--show-toplevel"),
            )
            source_common = _git_path(
                source,
                _run_git(source, "rev-parse", "--git-common-dir"),
            )
            target_common = _git_path(
                target,
                _run_git(target, "rev-parse", "--git-common-dir"),
            )
            members = _git_worktree_members(source)
            artifact_digest = _git_artifact_digest(target)
        except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError):
            return None
        if (
            source_top != source
            or target_top != target
            or source_common != target_common
            or source not in members
            or target not in members
        ):
            return None
        identity_payload = f"{source_common}\0{target}\0{run_id}".encode("utf-8")
        return WorktreeAttestation(
            source_root=source,
            worktree_root=target,
            run_id=run_id,
            identity=f"git-worktree:{sha256(identity_payload).hexdigest()}",
            artifact_digest=artifact_digest,
        )


class ManagedWorker(Protocol):
    def execute(
        self,
        handle: ManagedWorktreeHandle,
        request: ManagedRequest,
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


_OPERATIONS = {
    "local.apply_patch": _OperationSpec("target_local_worktree", "local"),
    "local.write_file": _OperationSpec("target_local_worktree", "local"),
    "local.format": _OperationSpec("target_local_worktree", "local"),
    "local.test": _OperationSpec("target_local_worktree", "local"),
    "github.commit": _OperationSpec("github_publication", "github"),
    "github.push": _OperationSpec("github_publication", "github"),
    "github.pull_request": _OperationSpec("github_publication", "github"),
    "github.release": _OperationSpec("github_publication", "github"),
    "deployment.deploy": _OperationSpec("target_deployment_worker", "deployment"),
}
_SUCCESS_CODES = {
    "local": "local_completed",
    "github": "github_completed",
    "deployment": "deployment_completed",
}
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
        "environment",
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
    "aws_access_key",
    "bearer ",
    "begin private key",
    "credential=",
    "ghp_",
    "password=",
    "private_key=",
    "secret=",
    "sk-",
    "token=",
)
_DESTRUCTIVE_PATCH_MARKER = re.compile(
    r"(?im)^\s*(?:\*\*\*\s+delete\s+file:|deleted\s+file\s+mode\b)"
)
_UNIFIED_FILE_DELETION = re.compile(
    r"(?m)^---\s+(?!/dev/null\b)[^\r\n]+\r?\n\+\+\+\s+/dev/null\s*$"
)


class ManagedSpaceActionGateway:
    """The sole router from current supervisor authority to narrow workers."""

    def __init__(
        self,
        *,
        supervisor: ManagedSpaceSupervisor,
        policy_gate: PolicyGate,
        worktree_provider: TargetWorktreeProvider,
        worktree_attestor: TargetWorktreeAttestor,
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
        self._worktree_attestor = worktree_attestor
        self._workers = {
            "local": local_worker,
            "github": github_worker,
            "deployment": deployment_worker,
        }
        self._diagnosis_runner = diagnosis_runner

    @staticmethod
    def handles(operation: object) -> bool:
        return isinstance(operation, str) and operation in _OPERATIONS

    def execute(
        self,
        capability: ManagedSpaceCapability | None,
        proposal: ActionProposal,
    ) -> GatewayResult:
        if not isinstance(proposal, ActionProposal):
            return GatewayResult(False, "proposal_invalid")
        context = self._supervisor.resolve_action_context(capability)
        if context is None:
            return GatewayResult(False, "capability_invalid")

        operation = proposal.requested_action.name
        spec = _OPERATIONS.get(operation)
        arguments = thaw_json_value(proposal.requested_action.arguments)
        request = _build_request(operation, arguments)
        if (
            spec is None
            or spec.family not in context.allowed_action_families
            or request is None
        ):
            return GatewayResult(False, "operation_hard_denied")
        if (
            proposal.requested_action.workspace != context.canonical_root
            or self._policy_gate.store.project_root != context.canonical_root
        ):
            return GatewayResult(False, "target_root_mismatch")
        if not proposal.requested_action.use_worktree:
            return GatewayResult(False, "worktree_required")

        created = self._create_worktree(context)
        if isinstance(created, GatewayResult):
            return created
        current = self._resolve_same_context(capability, context)
        if current is None:
            return GatewayResult(False, "capability_invalid")
        handle = self._attest_worktree(created, current)
        if isinstance(handle, GatewayResult):
            return handle
        if not _request_is_contained(request, handle.path):
            return GatewayResult(False, "operation_hard_denied")

        current = self._resolve_same_context(capability, context)
        if current is None:
            return GatewayResult(False, "capability_invalid")
        policy = _authorize_managed_yolo_and_claim(
            self._policy_gate.store,
            proposal,
            current,
            worktree_identity=handle.identity,
            artifact_digest=handle.artifact_digest,
        )
        if policy.status is not PolicyStatus.ALLOWED:
            return GatewayResult(False, policy.reason)
        if self._resolve_same_context(capability, context) is None:
            return GatewayResult(False, "capability_invalid")

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
        return self._recover_deployment_failure(capability, context)

    def host_bound_execution_route(
        self,
        capability: ManagedSpaceCapability | None,
    ) -> HostBoundExecutionRoute:
        """Latch one exact supervisor attachment into a Core-neutral host route."""
        context = self._supervisor.resolve_action_context(capability)
        if context is None or capability is None:
            raise ValueError("A current managed capability is required")

        def dispatch(proposal: ActionProposal, run: SwarmRun) -> GatewayResult:
            if run.run_id != context.run_id:
                return GatewayResult(False, "capability_invalid")
            return self.execute(capability, proposal)

        return create_host_bound_execution_route(
            project_root=context.canonical_root,
            run_id=context.run_id,
            dispatch=dispatch,
        )

    def _create_worktree(
        self,
        context: ManagedSpaceActionContext,
    ) -> CreatedWorktree | GatewayResult:
        try:
            created = self._worktree_provider.create(
                context.canonical_root,
                context.run_id,
            )
        except Exception:
            return GatewayResult(False, "worktree_unavailable")
        if not isinstance(created, CreatedWorktree):
            return GatewayResult(False, "worktree_invalid")
        if not created.path.is_absolute():
            return GatewayResult(False, "worktree_not_absolute")
        try:
            resolved = created.path.resolve(strict=True)
        except (OSError, RuntimeError):
            return GatewayResult(False, "worktree_unavailable")
        try:
            source = context.canonical_root.resolve(strict=True)
        except (OSError, RuntimeError):
            return GatewayResult(False, "target_root_mismatch")
        if resolved == source:
            return GatewayResult(False, "canonical_worktree_forbidden")
        return created

    def _attest_worktree(
        self,
        created: CreatedWorktree,
        context: ManagedSpaceActionContext,
    ) -> ManagedWorktreeHandle | GatewayResult:
        try:
            attestation = self._worktree_attestor.attest(
                context.canonical_root,
                created.path,
                context.run_id,
            )
        except Exception:
            return GatewayResult(False, "worktree_attestation_failed")
        if not isinstance(attestation, WorktreeAttestation):
            return GatewayResult(False, "worktree_attestation_failed")
        try:
            source = attestation.source_root.resolve(strict=True)
            target = attestation.worktree_root.resolve(strict=True)
        except (OSError, RuntimeError):
            return GatewayResult(False, "worktree_attestation_failed")
        if (
            source != context.canonical_root
            or target != created.path
            or target == source
            or attestation.run_id != context.run_id
            or not isinstance(attestation.identity, str)
            or not attestation.identity.strip()
            or not isinstance(attestation.artifact_digest, str)
            or _SHA256_DIGEST.fullmatch(attestation.artifact_digest) is None
        ):
            return GatewayResult(False, "worktree_attestation_failed")
        return ManagedWorktreeHandle(
            canonical_root=source,
            path=target,
            run_id=context.run_id,
            identity=attestation.identity.strip(),
            artifact_digest=attestation.artifact_digest,
        )

    def _resolve_same_context(
        self,
        capability: ManagedSpaceCapability,
        expected: ManagedSpaceActionContext,
    ) -> ManagedSpaceActionContext | None:
        current = self._supervisor.resolve_action_context(capability)
        return current if current == expected else None

    def _recover_deployment_failure(
        self,
        capability: ManagedSpaceCapability,
        context: ManagedSpaceActionContext,
    ) -> GatewayResult:
        events = self._policy_gate.store.list_events(context.run_id)
        used = sum(event.event_type == "model.attempt_started" for event in events)
        remaining = max(0, _MANAGED_MODEL_CALL_BUDGET - used)
        if remaining == 0:
            self._supervisor.pause_action_context(
                capability,
                context,
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
        if self._resolve_same_context(capability, context) is None:
            return GatewayResult(False, "capability_invalid")
        if (
            isinstance(diagnosis, DiagnosisResult)
            and diagnosis.restoration_verified is True
        ):
            return GatewayResult(
                False,
                "deployment_retry_requires_fresh_evidence",
            )
        self._supervisor.pause_action_context(
            capability,
            context,
            "deployment_unverified",
        )
        return GatewayResult(False, "deployment_unverified")


def _build_request(operation: str, value: object) -> ManagedRequest | None:
    if not isinstance(value, dict):
        return None
    artifact_digest = value.get("artifact_digest")
    if (
        not isinstance(artifact_digest, str)
        or _SHA256_DIGEST.fullmatch(artifact_digest) is None
        or _contains_sensitive_value(
            {key: item for key, item in value.items() if key != "artifact_digest"}
        )
    ):
        return None
    if operation == "local.apply_patch":
        if set(value) != {"artifact_digest", "path", "patch"}:
            return None
        path = _relative_path(value["path"])
        patch = _bounded_text(value["patch"], 1_000_000)
        return (
            LocalApplyPatchRequest(path, patch)
            if path and patch and not _patch_deletes_file(patch)
            else None
        )
    if operation == "local.write_file":
        if set(value) != {"artifact_digest", "path", "content"}:
            return None
        path = _relative_path(value["path"])
        content = _bounded_text(value["content"], 1_000_000)
        return LocalWriteFileRequest(path, content) if path and content else None
    if operation == "local.format":
        if set(value) != {"artifact_digest", "paths"}:
            return None
        raw_paths = value["paths"]
        if not isinstance(raw_paths, list) or not 1 <= len(raw_paths) <= 64:
            return None
        paths = tuple(_relative_path(item) or "" for item in raw_paths)
        return LocalFormatRequest(paths) if all(paths) else None
    if operation == "local.test":
        if set(value) != {"artifact_digest", "selector"}:
            return None
        selector = _test_selector(value["selector"])
        return LocalTestRequest(*selector) if selector is not None else None
    if operation == "github.commit":
        if set(value) != {"artifact_digest", "message"}:
            return None
        message = _bounded_text(value["message"], 512)
        return GitHubCommitRequest(message) if message else None
    if operation == "github.push":
        if set(value) != {"artifact_digest", "branch"}:
            return None
        branch = _safe_ref(value["branch"])
        return GitHubPushRequest(branch) if branch else None
    if operation == "github.pull_request":
        if set(value) not in (
            {"artifact_digest", "title", "body"},
            {"artifact_digest", "title", "body", "draft"},
        ):
            return None
        title = _bounded_text(value["title"], 256)
        body = _bounded_text(value["body"], 32_000, allow_empty=True)
        draft = value.get("draft", False)
        if title is None or body is None or type(draft) is not bool:
            return None
        return GitHubPullRequestRequest(title, body, draft)
    if operation == "github.release":
        if set(value) != {"artifact_digest", "tag", "title", "notes"}:
            return None
        tag = _safe_ref(value["tag"])
        title = _bounded_text(value["title"], 256)
        notes = _bounded_text(value["notes"], 32_000, allow_empty=True)
        if tag is None or title is None or notes is None:
            return None
        return GitHubReleaseRequest(tag, title, notes)
    if operation == "deployment.deploy":
        return TargetDeploymentRequest() if set(value) == {"artifact_digest"} else None
    return None


def _patch_deletes_file(value: str) -> bool:
    """Reject patch formats that remove a file before any worktree is opened."""
    normalized = value.replace("\r\n", "\n")
    return bool(
        _DESTRUCTIVE_PATCH_MARKER.search(normalized)
        or _UNIFIED_FILE_DELETION.search(normalized)
    )


def _relative_path(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        return None
    candidate = value.strip().replace("\\", "/")
    posix = PurePosixPath(candidate)
    windows = PureWindowsPath(value.strip())
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        return None
    return posix.as_posix()


def _request_is_contained(request: ManagedRequest, worktree: Path) -> bool:
    paths: tuple[str, ...] = ()
    if isinstance(request, (LocalApplyPatchRequest, LocalWriteFileRequest)):
        paths = (request.path,)
    elif isinstance(request, LocalFormatRequest):
        paths = request.paths
    elif isinstance(request, LocalTestRequest):
        paths = (request.path,)
    for relative in paths:
        try:
            target = (worktree / relative).resolve(strict=False)
            if not target.is_relative_to(worktree):
                return False
        except (OSError, RuntimeError, ValueError):
            return False
    return True


def _bounded_text(
    value: object,
    limit: int,
    *,
    allow_empty: bool = False,
) -> str | None:
    if not isinstance(value, str) or len(value) > limit:
        return None
    if not allow_empty and not value.strip():
        return None
    return value


def _safe_ref(value: object) -> str | None:
    if not isinstance(value, str) or _SAFE_REF.fullmatch(value) is None:
        return None
    if value.endswith(("/", ".")) or any(
        part in value for part in _FORBIDDEN_REF_PARTS
    ):
        return None
    return value


def _test_selector(value: object) -> tuple[str, tuple[str, ...]] | None:
    if not isinstance(value, str) or not value or len(value) > 512 or "\\" in value:
        return None
    components = value.split("::")
    if not 1 <= len(components) <= 9:
        return None
    path = _relative_path(components[0])
    if path is None or PurePosixPath(path).suffix != ".py":
        return None
    nodes = tuple(components[1:])
    if any(_SAFE_TEST_NODE.fullmatch(node) is None for node in nodes):
        return None
    return path, nodes


def _contains_sensitive_value(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
            parts = [
                part for part in re.split(r"[^a-z0-9]+", normalized.lower()) if part
            ]
            singular_parts = {
                part[:-1] if part.endswith("s") else part for part in parts
            }
            if (set(parts) | singular_parts) & _SENSITIVE_FIELD_PARTS:
                return True
            if _contains_sensitive_value(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_value(item) for item in value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        return (
            any(marker in normalized for marker in _SENSITIVE_VALUE_MARKERS)
            or _CURRENT_SECRET_TOKEN.search(value) is not None
        )
    return False


def _authorize_managed_yolo_and_claim(
    store: ProjectSwarmStore,
    proposal: ActionProposal,
    context: ManagedSpaceActionContext,
    *,
    worktree_identity: str,
    artifact_digest: str,
) -> PolicyDecision:
    """Authorize one managed action from Nova-owned, artifact-bound evidence."""
    if not isinstance(context, ManagedSpaceActionContext):
        return _managed_decision(
            proposal,
            PolicyStatus.BLOCKED,
            "managed_action_context_required",
        )
    operation = _OPERATIONS.get(proposal.requested_action.name)
    if (
        operation is None
        or operation.family not in context.allowed_action_families
        or proposal.category != "managed"
        or proposal.requested_action.workspace != context.canonical_root
        or not proposal.requested_action.use_worktree
        or store.project_root != context.canonical_root
    ):
        return _managed_decision(
            proposal,
            PolicyStatus.BLOCKED,
            "managed_policy_scope_mismatch",
        )
    if (
        not isinstance(worktree_identity, str)
        or not worktree_identity.strip()
        or not isinstance(artifact_digest, str)
        or _SHA256_DIGEST.fullmatch(artifact_digest) is None
    ):
        return _managed_decision(
            proposal,
            PolicyStatus.BLOCKED,
            "managed_evidence_binding_invalid",
        )
    digest = proposal_digest(proposal)

    def authorize(
        durable_run: SwarmRun | None,
        approvals: list[ApprovalRecord],
        checkpoints: Mapping[str, WorkflowRoleCheckpoint],
    ) -> tuple[PolicyDecision, bool]:
        decision = _evaluate_managed_yolo(
            proposal,
            durable_run,
            [approval for approval in approvals if approval.proposal_digest == digest],
            checkpoints,
            run_id=context.run_id,
            worktree_identity=worktree_identity,
            artifact_digest=artifact_digest,
            proposal_digest_value=digest,
        )
        return decision, decision.status is PolicyStatus.ALLOWED

    decision, claimed = store.authorize_and_claim(
        context.run_id,
        proposal.proposal_id,
        digest,
        authorize,
    )
    if decision.status is not PolicyStatus.ALLOWED:
        return decision
    if not claimed:
        return _managed_decision(
            proposal,
            PolicyStatus.BLOCKED,
            "execution_already_claimed",
        )
    return decision


def _evaluate_managed_yolo(
    proposal: ActionProposal,
    durable_run: SwarmRun | None,
    approvals: list[ApprovalRecord],
    checkpoints: Mapping[str, WorkflowRoleCheckpoint],
    *,
    run_id: str,
    worktree_identity: str,
    artifact_digest: str,
    proposal_digest_value: str,
) -> PolicyDecision:
    if durable_run is None or durable_run.run_id != run_id:
        return _managed_decision(proposal, PolicyStatus.BLOCKED, "unknown_run")
    if durable_run.status != "running":
        return _managed_decision(proposal, PolicyStatus.BLOCKED, "run_not_running")
    if any(not approval.approved for approval in approvals):
        return _managed_decision(proposal, PolicyStatus.BLOCKED, "approval_denied")

    verifier = checkpoints.get("verifier")
    if verifier is None or verifier.model is not None:
        return _managed_decision(
            proposal,
            PolicyStatus.BLOCKED,
            "managed_verifier_required",
        )
    try:
        result = verification_result_from_checkpoint_data(verifier.data)
    except InvalidVerifierResult:
        return _managed_decision(
            proposal,
            PolicyStatus.BLOCKED,
            "managed_verifier_invalid",
        )
    if not is_positive_verification_decision(result.decision):
        return _managed_decision(
            proposal,
            PolicyStatus.BLOCKED,
            "managed_verifier_not_positive",
        )
    if not (set(result.evidence) & set(proposal.evidence_refs)):
        return _managed_decision(
            proposal,
            PolicyStatus.BLOCKED,
            "managed_verifier_evidence_mismatch",
        )
    test_evidence = result.test_evidence
    if test_evidence is None:
        return _managed_decision(
            proposal,
            PolicyStatus.BLOCKED,
            "test_evidence_required",
        )
    if test_evidence.passed is not True:
        return _managed_decision(
            proposal,
            PolicyStatus.BLOCKED,
            "test_evidence_not_positive",
        )
    if (
        test_evidence.run_id != run_id
        or test_evidence.worktree_identity != worktree_identity
        or test_evidence.artifact_digest != artifact_digest
        or test_evidence.report_ref not in proposal.evidence_refs
        or proposal.requested_action.arguments.get("artifact_digest") != artifact_digest
    ):
        return _managed_decision(
            proposal,
            PolicyStatus.BLOCKED,
            "test_evidence_mismatch",
        )

    review_bindings = {
        "run_id": run_id,
        "worktree_identity": worktree_identity,
        "artifact_digest": artifact_digest,
        "proposal_digest": proposal_digest_value,
    }
    for role, expected_model in _REQUIRED_MANAGED_REVIEW_MODELS.items():
        checkpoint = checkpoints.get(role)
        evidence = _valid_checkpoint_evidence(checkpoint)
        if (
            checkpoint is None
            or checkpoint.model != expected_model
            or not evidence
            or not _has_positive_review_vote(checkpoint)
            or test_evidence.report_ref not in evidence
            or any(
                checkpoint.data.get(field) != expected
                for field, expected in review_bindings.items()
            )
        ):
            return _managed_decision(
                proposal,
                PolicyStatus.NEEDS_MODEL_QUORUM,
                "managed_review_quorum_required",
            )
    return _managed_decision(
        proposal,
        PolicyStatus.ALLOWED,
        "managed_policy_satisfied",
    )


def _valid_checkpoint_evidence(
    checkpoint: WorkflowRoleCheckpoint | None,
) -> set[str]:
    if checkpoint is None:
        return set()
    data = checkpoint.data
    work = data.get("work")
    decision = data.get("decision")
    evidence = data.get("evidence")
    if (
        not isinstance(work, str)
        or not work.strip()
        or not isinstance(decision, str)
        or not decision.strip()
        or not isinstance(evidence, list)
        or not evidence
        or any(not isinstance(ref, str) or not ref.strip() for ref in evidence)
    ):
        return set()
    return {ref.strip() for ref in evidence}


def _has_positive_review_vote(checkpoint: WorkflowRoleCheckpoint) -> bool:
    decision = checkpoint.data.get("decision")
    return (
        checkpoint.data.get("approved") is True
        and isinstance(decision, str)
        and decision.strip().lower() in _APPROVING_REVIEW_DECISIONS
    )


def _managed_decision(
    proposal: ActionProposal,
    status: PolicyStatus,
    reason: str,
) -> PolicyDecision:
    return PolicyDecision(
        proposal_id=proposal.proposal_id,
        status=status,
        reason=reason,
    )


def _run_git(cwd: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *arguments],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return completed.stdout.rstrip(b"\r\n")


def _git_path(cwd: Path, raw: bytes) -> Path:
    value = os.fsdecode(raw)
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return candidate.resolve(strict=True)


def _git_worktree_members(source: Path) -> frozenset[Path]:
    raw = _run_git(source, "worktree", "list", "--porcelain", "-z")
    members: set[Path] = set()
    for field in raw.split(b"\0"):
        if not field.startswith(b"worktree "):
            continue
        member = Path(os.fsdecode(field.removeprefix(b"worktree ")))
        members.add(member.resolve(strict=True))
    return frozenset(members)


def _git_artifact_digest(worktree: Path) -> str:
    digest = sha256()
    digest.update(b"git-managed-artifact-v1\0")
    digest.update(_run_git(worktree, "rev-parse", "HEAD"))
    digest.update(b"\0index\0")
    digest.update(_run_git(worktree, "ls-files", "--stage", "-z"))
    raw_paths = _run_git(
        worktree,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
    )
    for raw_path in sorted(filter(None, raw_paths.split(b"\0"))):
        relative_text = os.fsdecode(raw_path)
        relative = _relative_path(relative_text)
        if relative is None:
            raise ValueError("Git worktree contains an unsafe path")
        candidate = worktree / relative
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(worktree):
            raise ValueError("Git worktree path escapes its root")
        digest.update(b"\0path\0")
        digest.update(raw_path)
        if candidate.is_symlink():
            digest.update(b"\0symlink\0")
            digest.update(os.fsencode(os.readlink(candidate)))
        elif candidate.is_file():
            digest.update(b"\0file\0")
            with candidate.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        elif candidate.exists():
            digest.update(b"\0gitlink-or-directory\0")
        else:
            digest.update(b"\0missing\0")
    return digest.hexdigest()


def create_managed_space_action_gateway(
    *,
    supervisor: ManagedSpaceSupervisor,
    policy_gate: PolicyGate,
    worktree_provider: TargetWorktreeProvider,
    run_target_resolver: RunTargetResolver,
    local_worker: ManagedWorker,
    github_worker: ManagedWorker,
    deployment_worker: ManagedWorker,
    diagnosis_runner: DiagnosisRunner,
) -> ManagedSpaceActionGateway:
    """Build the gateway with the production Git worktree attestor."""
    return ManagedSpaceActionGateway(
        supervisor=supervisor,
        policy_gate=policy_gate,
        worktree_provider=worktree_provider,
        worktree_attestor=GitWorktreeAttestor(
            run_target_resolver=run_target_resolver,
        ),
        local_worker=local_worker,
        github_worker=github_worker,
        deployment_worker=deployment_worker,
        diagnosis_runner=diagnosis_runner,
    )
