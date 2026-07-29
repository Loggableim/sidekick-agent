"""Capability-bound action gateway for one supervised target Space."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import ClassVar, Protocol, TypeAlias

from nova.space_supervisor import (
    ManagedSpaceActionContext,
    ManagedSpaceCapability,
    ManagedSpaceSupervisor,
)
from swarm_core.policy import PolicyGate, PolicyStatus
from swarm_core.types import ActionProposal, thaw_json_value


_SHA256_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_SELECTOR = re.compile(r"[A-Za-z0-9_./:\\\-\[\]]{1,256}\Z")
_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}\Z")
_MANAGED_MODEL_CALL_BUDGET = 128
_FORBIDDEN_REF_PARTS = ("..", "@{", "//", ".lock")


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
    selector: str
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
        policy = self._policy_gate.authorize_managed_yolo_and_claim(
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
        return CreatedWorktree(resolved)

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
        used = sum(
            event.event_type == "model.attempt_started"
            for event in events
        )
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
        return LocalApplyPatchRequest(path, patch) if path and patch else None
    if operation == "local.write_file":
        if set(value) != {"artifact_digest", "path", "content"}:
            return None
        path = _relative_path(value["path"])
        content = _bounded_text(value["content"], 1_000_000, allow_empty=True)
        return (
            LocalWriteFileRequest(path, content)
            if path is not None and content is not None
            else None
        )
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
        selector = value["selector"]
        if not isinstance(selector, str) or _SAFE_SELECTOR.fullmatch(selector) is None:
            return None
        return LocalTestRequest(selector)
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
    if value.endswith(("/", ".")) or any(part in value for part in _FORBIDDEN_REF_PARTS):
        return None
    return value


def _contains_sensitive_value(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
            parts = [
                part
                for part in re.split(r"[^a-z0-9]+", normalized.lower())
                if part
            ]
            singular_parts = {
                part[:-1] if part.endswith("s") else part
                for part in parts
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
        return any(marker in normalized for marker in _SENSITIVE_VALUE_MARKERS)
    return False
