from __future__ import annotations

from dataclasses import replace
import importlib
import os
from pathlib import Path
import subprocess
from typing import Any
from uuid import uuid4

import pytest

import nova.space_supervisor as space_supervisor_module
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from swarm_core.policy import PolicyGate, proposal_digest
from swarm_core.sidekick_adapter import SidekickToolAdapter
from swarm_core.store import ProjectSwarmStore
from swarm_core.tools import GatedToolExecutor
from swarm_core.types import ActionProposal, RequestedToolAction


def _gateway_api():
    return importlib.import_module("nova.managed_space_gateway")


def _governance(root: Path, **overrides: object) -> ManagedSpaceGovernance:
    values: dict[str, object] = {
        "space_id": str(uuid4()),
        "canonical_root": root,
        "yolo": True,
        "enrolled": True,
        "revision": 3,
        "policy_identity": "policy:managed-space-v1",
    }
    values.update(overrides)
    return ManagedSpaceGovernance.from_values(**values)


class _RecordingWorker:
    def __init__(self, result: object | None = None) -> None:
        self.calls: list[tuple[object, object]] = []
        self.result = result

    def execute(self, handle: object, request: object) -> object:
        self.calls.append((handle, request))
        return self.result


class _RecordingDiagnosis:
    def __init__(self, result: object | None = None) -> None:
        self.calls: list[tuple[object, int]] = []
        self.result = result

    def diagnose(self, failure: object, remaining_budget: int) -> object:
        self.calls.append((failure, remaining_budget))
        return self.result


class _RejectingAttestor:
    def attest(self, source_root: Path, worktree_root: Path, run_id: str):
        del source_root, worktree_root, run_id
        return None


class _WorktreeAttestor:
    def __init__(
        self,
        *,
        api: Any,
        identity: str = "worktree:managed-1",
        artifact_digest: str = "a" * 64,
        target_root: Path | None = None,
        run_id: str | None = None,
    ) -> None:
        self._api = api
        self._identity = identity
        self._artifact_digest = artifact_digest
        self._target_root = target_root
        self._run_id = run_id
        self.calls: list[tuple[Path, Path, str]] = []

    def attest(self, source_root: Path, worktree_root: Path, run_id: str):
        self.calls.append((source_root, worktree_root, run_id))
        claimed_source = self._target_root or source_root
        if claimed_source.is_absolute():
            claimed_source.mkdir(parents=True, exist_ok=True)
        return self._api.WorktreeAttestation(
            source_root=claimed_source,
            worktree_root=worktree_root,
            run_id=self._run_id or run_id,
            identity=self._identity,
            artifact_digest=self._artifact_digest,
        )


class _WorktreeProvider:
    def __init__(
        self,
        *,
        api: Any,
        path: Path,
        after_create: Any | None = None,
    ) -> None:
        self._api = api
        self._path = path
        self._after_create = after_create
        self.calls: list[tuple[Path, str]] = []

    def create(self, canonical_root: Path, run_id: str):
        self.calls.append((canonical_root, run_id))
        if self._path.is_absolute():
            self._path.mkdir(parents=True, exist_ok=True)
        created = self._api.CreatedWorktree(path=self._path)
        if self._after_create is not None:
            self._after_create()
        return created


def _managed_run(tmp_path: Path):
    root = (tmp_path / "target").resolve()
    records = {"target": _governance(root)}
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda key: records[key],
    )
    admission = supervisor.admit(
        "target",
        {"goal": "publish verified artifact", "kind": "maintenance"},
    )
    assert admission.capability is not None
    store = ProjectSwarmStore(root)
    store.resume_run(admission.run_id)
    return root, records, supervisor, admission, store


def _proposal(
    root: Path,
    *,
    proposal_id: str = "managed-proposal-1",
    operation: str = "github.push",
    artifact_digest: str = "a" * 64,
    arguments: dict[str, object] | None = None,
    use_worktree: bool = True,
) -> ActionProposal:
    payload = {
        "artifact_digest": artifact_digest,
        "branch": "feat/verified",
    }
    if arguments is not None:
        payload = arguments
    return ActionProposal(
        proposal_id=proposal_id,
        category="managed",
        reversible=False,
        external=True,
        cost_increasing=False,
        evidence_refs=("verifier:managed:1", "report:test:1"),
        requested_action=RequestedToolAction(
            name=operation,
            workspace=root,
            arguments=payload,
            use_worktree=use_worktree,
        ),
    )


def _record_bound_evidence(
    store: ProjectSwarmStore,
    proposal: ActionProposal,
    *,
    run_id: str,
    worktree_identity: str = "worktree:managed-1",
    artifact_digest: str = "a" * 64,
    passed: bool = True,
    verifier_decision: str = "verified",
) -> None:
    digest = proposal_digest(proposal)
    store.record_workflow_role_checkpoint(
        run_id,
        "verifier",
        model=None,
        data={
            "work": "verified exact managed artifact",
            "evidence": ["verifier:managed:1", "report:test:1"],
            "decision": verifier_decision,
            "provenance": {
                "adapter": "managed-local-verifier",
                "mode": "read_only",
                "operation": "inspect_worktree",
            },
            "test_evidence": {
                "run_id": run_id,
                "worktree_identity": worktree_identity,
                "artifact_digest": artifact_digest,
                "runner_identity": "runner:pytest-local",
                "report_ref": "report:test:1",
                "passed": passed,
            },
        },
    )
    bindings = {
        "run_id": run_id,
        "worktree_identity": worktree_identity,
        "artifact_digest": artifact_digest,
        "proposal_digest": digest,
    }
    for role, model in (
        ("review_a", "glm-5.2"),
        ("review_b", "kimi-k2.7-code"),
    ):
        store.record_workflow_role_checkpoint(
            run_id,
            role,
            model=model,
            data={
                "work": f"{role} reviewed exact artifact",
                "evidence": ["verifier:managed:1", "report:test:1"],
                "decision": "approved",
                "approved": True,
                **bindings,
            },
        )


def _gateway(
    tmp_path: Path,
    *,
    worktree_path: Path | None = None,
    provider_kwargs: dict[str, object] | None = None,
    deployment_result: object | None = None,
    diagnosis_result: object | None = None,
):
    api = _gateway_api()
    root, records, supervisor, admission, store = _managed_run(tmp_path)
    supplied = dict(provider_kwargs or {})
    provider_values = {
        key: supplied.pop(key) for key in ("path", "after_create") if key in supplied
    }
    provider_values.setdefault(
        "path",
        (worktree_path or (tmp_path / "worktrees" / "run-1")).resolve(),
    )
    provider = _WorktreeProvider(
        api=api,
        **provider_values,
    )
    attestor = _WorktreeAttestor(api=api, **supplied)
    local = _RecordingWorker(api.WorkerResult(True, "local_applied"))
    github = _RecordingWorker(api.WorkerResult(True, "published"))
    deployment = _RecordingWorker(
        deployment_result or api.WorkerResult(True, "deployed")
    )
    diagnosis = _RecordingDiagnosis(
        diagnosis_result or api.DiagnosisResult(False, "not_restored")
    )
    gateway = api.ManagedSpaceActionGateway(
        supervisor=supervisor,
        policy_gate=PolicyGate(store),
        worktree_provider=provider,
        worktree_attestor=attestor,
        local_worker=local,
        github_worker=github,
        deployment_worker=deployment,
        diagnosis_runner=diagnosis,
    )
    return (
        api,
        root,
        records,
        supervisor,
        admission,
        store,
        provider,
        local,
        github,
        deployment,
        diagnosis,
        gateway,
    )


@pytest.mark.parametrize("use_worktree", [False])
def test_local_action_requires_a_noncanonical_target_worktree(
    tmp_path: Path,
    use_worktree: bool,
) -> None:
    """Catches managed local writes reaching a canonical target checkout."""
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        store,
        provider,
        local,
        _github,
        _deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)
    proposal = _proposal(
        root,
        operation="local.apply_patch",
        arguments={
            "artifact_digest": "a" * 64,
            "path": "src/safe.py",
            "patch": "safe patch",
        },
        use_worktree=use_worktree,
    )
    _record_bound_evidence(store, proposal, run_id=admission.run_id)

    result = gateway.execute(admission.capability, proposal)

    assert result.code == "worktree_required"
    assert provider.calls == []
    assert local.calls == []


@pytest.mark.parametrize(
    ("provider_kwargs", "expected_code"),
    [
        ({"path": Path("placeholder")}, "worktree_not_absolute"),
        ({"target_root": Path("foreign")}, "worktree_attestation_failed"),
        ({"run_id": "foreign-run"}, "worktree_attestation_failed"),
    ],
)
def test_invalid_or_foreign_worktree_handles_reach_no_worker(
    tmp_path: Path,
    provider_kwargs: dict[str, object],
    expected_code: str,
) -> None:
    """Catches a provider swapping the approved target/run/worktree identity."""
    if "path" in provider_kwargs:
        provider_kwargs["path"] = Path("relative-worktree")
    if "target_root" in provider_kwargs:
        provider_kwargs["target_root"] = (tmp_path / "foreign").resolve()
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        store,
        _provider,
        local,
        github,
        deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path, provider_kwargs=provider_kwargs)
    proposal = _proposal(root)
    _record_bound_evidence(store, proposal, run_id=admission.run_id)

    result = gateway.execute(admission.capability, proposal)

    assert result.code == expected_code
    assert local.calls == github.calls == deployment.calls == []


def test_canonical_root_returned_as_worktree_reaches_no_worker(tmp_path: Path) -> None:
    """Catches managed writes accidentally operating on the canonical checkout."""
    api = _gateway_api()
    root, _records, supervisor, admission, store = _managed_run(tmp_path)
    provider = _WorktreeProvider(api=api, path=root)
    worker = _RecordingWorker(api.WorkerResult(True, "published"))
    gateway = api.ManagedSpaceActionGateway(
        supervisor=supervisor,
        policy_gate=PolicyGate(store),
        worktree_provider=provider,
        worktree_attestor=_WorktreeAttestor(api=api),
        local_worker=worker,
        github_worker=worker,
        deployment_worker=worker,
        diagnosis_runner=_RecordingDiagnosis(),
    )
    proposal = _proposal(root)
    _record_bound_evidence(store, proposal, run_id=admission.run_id)

    result = gateway.execute(admission.capability, proposal)

    assert result.code == "canonical_worktree_forbidden"
    assert worker.calls == []


@pytest.mark.parametrize(
    ("evidence_change", "expected_code"),
    [
        ({"passed": False}, "test_evidence_not_positive"),
        ({"artifact_digest": "b" * 64}, "test_evidence_mismatch"),
        ({"worktree_identity": "worktree:other"}, "test_evidence_mismatch"),
    ],
)
def test_missing_negative_or_mismatched_evidence_blocks_before_worker(
    tmp_path: Path,
    evidence_change: dict[str, object],
    expected_code: str,
) -> None:
    """Catches stale or non-passing tests authorizing a changed artifact."""
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        store,
        _provider,
        local,
        github,
        deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)
    proposal = _proposal(root)
    _record_bound_evidence(
        store,
        proposal,
        run_id=admission.run_id,
        **evidence_change,
    )

    result = gateway.execute(admission.capability, proposal)

    assert result.code == expected_code
    assert local.calls == github.calls == deployment.calls == []


def test_negative_verifier_decision_blocks_before_worker(tmp_path: Path) -> None:
    """Catches positive tests overriding a negative durable verifier result."""
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        store,
        _provider,
        _local,
        github,
        _deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)
    proposal = _proposal(root)
    _record_bound_evidence(
        store,
        proposal,
        run_id=admission.run_id,
        verifier_decision="verification_failed",
    )

    result = gateway.execute(admission.capability, proposal)

    assert result.code == "managed_verifier_not_positive"
    assert github.calls == []


def test_missing_verifier_checkpoint_blocks_before_worker(tmp_path: Path) -> None:
    """Catches proposal metadata being mistaken for durable verifier evidence."""
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        _store,
        _provider,
        _local,
        github,
        _deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)

    result = gateway.execute(admission.capability, _proposal(root))

    assert result.code == "managed_verifier_required"
    assert github.calls == []


@pytest.mark.parametrize("defect", ["revoked", "stale_generation"])
def test_revoked_or_stale_capability_stops_before_worktree_creation(
    tmp_path: Path,
    defect: str,
) -> None:
    """Catches governance revocation or generation drift racing a worker call."""
    (
        _api,
        root,
        records,
        _supervisor,
        admission,
        store,
        provider,
        _local,
        github,
        _deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)
    proposal = _proposal(root)
    _record_bound_evidence(store, proposal, run_id=admission.run_id)
    if defect == "revoked":
        records["target"] = replace(records["target"], revision=4)
    else:
        object.__setattr__(
            admission.capability,
            "_attachment_generation",
            admission.capability._attachment_generation - 1,
        )

    result = gateway.execute(admission.capability, proposal)

    assert result.code == "capability_invalid"
    assert provider.calls == []
    assert github.calls == []


def test_capability_is_revalidated_after_worktree_creation(tmp_path: Path) -> None:
    """Catches governance changing between worktree creation and policy claim."""
    api = _gateway_api()
    root, records, supervisor, admission, store = _managed_run(tmp_path)
    provider = _WorktreeProvider(
        api=api,
        path=(tmp_path / "worktree").resolve(),
        after_create=lambda: records.__setitem__(
            "target",
            replace(records["target"], revision=4),
        ),
    )
    worker = _RecordingWorker(api.WorkerResult(True, "published"))
    gateway = api.ManagedSpaceActionGateway(
        supervisor=supervisor,
        policy_gate=PolicyGate(store),
        worktree_provider=provider,
        worktree_attestor=_WorktreeAttestor(api=api),
        local_worker=worker,
        github_worker=worker,
        deployment_worker=worker,
        diagnosis_runner=_RecordingDiagnosis(),
    )
    proposal = _proposal(root)
    _record_bound_evidence(store, proposal, run_id=admission.run_id)

    result = gateway.execute(admission.capability, proposal)

    assert result.code == "capability_invalid"
    assert len(provider.calls) == 1
    assert worker.calls == []


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("delete.destroy", {"artifact_digest": "a" * 64}),
        (
            "github.push",
            {
                "artifact_digest": "a" * 64,
                "branch": "main",
                "remote_url": "https://attacker.invalid/repo",
            },
        ),
        (
            "deployment.deploy",
            {
                "artifact_digest": "a" * 64,
                "deployment_ref": "production",
                "options": {"env": {"TOKEN": "raw-secret"}},
            },
        ),
        (
            "local.apply_patch",
            {
                "artifact_digest": "a" * 64,
                "patch": {"credentials": {"password": "hunter2"}},
            },
        ),
        (
            "local.apply_patch",
            {
                "artifact_digest": "a" * 64,
                "patch": "API_KEY=super-secret-value",
            },
        ),
        ("admin.iam_grant", {"artifact_digest": "a" * 64}),
        ("payments.purchase", {"artifact_digest": "a" * 64}),
        ("host.package_install", {"artifact_digest": "a" * 64}),
        ("external.message", {"artifact_digest": "a" * 64}),
    ],
)
def test_hard_denials_and_nested_sensitive_arguments_never_reach_any_effect(
    tmp_path: Path,
    operation: str,
    arguments: dict[str, object],
) -> None:
    """Catches caller labels or nested payloads smuggling forbidden authority."""
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        _store,
        provider,
        local,
        github,
        deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)

    result = gateway.execute(
        admission.capability,
        _proposal(root, operation=operation, arguments=arguments),
    )

    assert result.code == "operation_hard_denied"
    assert provider.calls == []
    assert local.calls == github.calls == deployment.calls == []


@pytest.mark.parametrize(
    (
        "operation",
        "arguments",
        "worker_name",
        "result_code",
        "request_type",
    ),
    [
        (
            "local.apply_patch",
            {
                "artifact_digest": "a" * 64,
                "path": "src/safe.py",
                "patch": "safe patch",
            },
            "local",
            "local_completed",
            "LocalApplyPatchRequest",
        ),
        (
            "github.pull_request",
            {
                "artifact_digest": "a" * 64,
                "title": "Verified change",
                "body": "Evidence-backed update",
                "draft": True,
            },
            "github",
            "github_completed",
            "GitHubPullRequestRequest",
        ),
        (
            "deployment.deploy",
            {"artifact_digest": "a" * 64},
            "deployment",
            "deployment_completed",
            "TargetDeploymentRequest",
        ),
    ],
)
def test_allowed_operation_routes_only_to_named_worker_with_narrow_request(
    tmp_path: Path,
    operation: str,
    arguments: dict[str, object],
    worker_name: str,
    result_code: str,
    request_type: str,
) -> None:
    """Catches cross-worker routing or leaking host authority into a worker."""
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        store,
        _provider,
        local,
        github,
        deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)
    proposal = _proposal(root, operation=operation, arguments=arguments)
    _record_bound_evidence(store, proposal, run_id=admission.run_id)

    result = gateway.execute(admission.capability, proposal)

    assert result.ok is True
    assert result.code == result_code
    calls = {
        "local": local.calls,
        "github": github.calls,
        "deployment": deployment.calls,
    }
    assert len(calls[worker_name]) == 1
    assert all(calls[name] == [] for name in calls if name != worker_name)
    _handle, request = calls[worker_name][0]
    assert isinstance(request, getattr(_api, request_type))
    assert request.operation == operation
    assert not hasattr(request, "arguments")
    assert "secret" not in repr(request).lower()
    assert "admin" not in repr(request).lower()
    assert "remote" not in repr(request).lower()


def test_human_denial_blocks_managed_yolo_and_publication_is_exactly_once(
    tmp_path: Path,
) -> None:
    """Catches managed policy ignoring a denial or publishing one proposal twice."""
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        store,
        _provider,
        _local,
        github,
        _deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)
    denied = _proposal(root, proposal_id="managed-denied")
    _record_bound_evidence(store, denied, run_id=admission.run_id)
    PolicyGate(store).record_approval(
        denied,
        store.get_run(admission.run_id),
        approval_type="human",
        approver_id="dashboard:user",
        approved=False,
    )

    blocked = gateway.execute(admission.capability, denied)

    assert blocked.code == "approval_denied"
    assert github.calls == []

    # A fresh run gives the positive exactly-once case immutable checkpoints.
    second = _gateway(tmp_path / "second")
    (
        _api,
        second_root,
        _records,
        _supervisor,
        second_admission,
        second_store,
        _provider,
        _local,
        second_github,
        _deployment,
        _diagnosis,
        second_gateway,
    ) = second
    proposal = _proposal(second_root)
    _record_bound_evidence(second_store, proposal, run_id=second_admission.run_id)

    first = second_gateway.execute(second_admission.capability, proposal)
    replay = second_gateway.execute(second_admission.capability, proposal)

    assert first.ok is True
    assert replay.code == "execution_already_claimed"
    assert len(second_github.calls) == 1


def test_deployment_failure_uses_only_durable_budget_and_pauses_redacted_blocker(
    tmp_path: Path,
) -> None:
    """Catches caller budget/worker errors leaking into diagnosis or blocker output."""
    api = _gateway_api()
    failure = api.WorkerResult(
        False,
        "provider_failed",
        detail="token=super-secret https://internal.invalid/admin",
    )
    diagnosis = api.DiagnosisResult(
        False,
        "unverified",
        detail="credential still invalid",
    )
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        store,
        _provider,
        _local,
        _github,
        deployment,
        diagnosis_runner,
        gateway,
    ) = _gateway(
        tmp_path,
        deployment_result=failure,
        diagnosis_result=diagnosis,
    )
    proposal = _proposal(
        root,
        operation="deployment.deploy",
        arguments={"artifact_digest": "a" * 64},
    )
    _record_bound_evidence(store, proposal, run_id=admission.run_id)
    for index in range(127):
        store.append_event(
            admission.run_id,
            "model.attempt_started",
            {"role": f"diagnostic-{index}", "model": "bounded-model"},
        )

    result = gateway.execute(admission.capability, proposal)

    assert len(deployment.calls) == 1
    assert len(diagnosis_runner.calls) == 1
    redacted_failure, remaining = diagnosis_runner.calls[0]
    assert remaining == 1
    assert redacted_failure.code == "deployment_failed"
    assert "secret" not in repr(redacted_failure).lower()
    assert "internal.invalid" not in repr(redacted_failure).lower()
    assert result.code == "deployment_unverified"
    assert "secret" not in repr(result).lower()
    assert store.get_run(admission.run_id).status == "paused"


def test_exhausted_durable_budget_skips_diagnosis_and_pauses(tmp_path: Path) -> None:
    """Catches deployment diagnostics exceeding the durable model-call budget."""
    api = _gateway_api()
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        store,
        _provider,
        _local,
        _github,
        _deployment,
        diagnosis,
        gateway,
    ) = _gateway(
        tmp_path,
        deployment_result=api.WorkerResult(False, "provider_failed"),
    )
    proposal = _proposal(
        root,
        operation="deployment.deploy",
        arguments={"artifact_digest": "a" * 64},
    )
    _record_bound_evidence(store, proposal, run_id=admission.run_id)
    for index in range(128):
        store.append_event(
            admission.run_id,
            "model.attempt_started",
            {"role": f"attempt-{index}", "model": "bounded-model"},
        )

    result = gateway.execute(admission.capability, proposal)

    assert result.code == "deployment_budget_exhausted"
    assert diagnosis.calls == []
    assert store.get_run(admission.run_id).status == "paused"


def test_verified_diagnosis_never_retries_the_claimed_deployment_proposal(
    tmp_path: Path,
) -> None:
    """Catches restoration diagnosis reusing stale proposal/test evidence."""
    api = _gateway_api()
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        store,
        _provider,
        _local,
        _github,
        deployment,
        diagnosis,
        gateway,
    ) = _gateway(
        tmp_path,
        deployment_result=api.WorkerResult(False, "provider_failed"),
        diagnosis_result=api.DiagnosisResult(True, "restoration_verified"),
    )
    proposal = _proposal(
        root,
        operation="deployment.deploy",
        arguments={"artifact_digest": "a" * 64},
    )
    _record_bound_evidence(store, proposal, run_id=admission.run_id)

    diagnosed = gateway.execute(admission.capability, proposal)
    replay = gateway.execute(admission.capability, proposal)

    assert diagnosed.code == "deployment_retry_requires_fresh_evidence"
    assert replay.code == "execution_already_claimed"
    assert len(deployment.calls) == 1
    assert len(diagnosis.calls) == 1


def test_fix_round1_reconstructed_capability_is_not_current_authority(
    tmp_path: Path,
) -> None:
    """Catches ledger-value reconstruction impersonating the installed binding."""
    (
        _api,
        root,
        _records,
        supervisor,
        admission,
        store,
        _provider,
        _local,
        github,
        _deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)
    proposal = _proposal(root)
    _record_bound_evidence(store, proposal, run_id=admission.run_id)
    record = supervisor._record(admission.admission_id)
    assert record is not None
    reconstructed = space_supervisor_module._capability_from_record(record)
    assert reconstructed is not None and reconstructed is not admission.capability

    result = gateway.execute(reconstructed, proposal)

    assert result.code == "capability_invalid"
    assert github.calls == []
    context = supervisor.resolve_action_context(admission.capability)
    assert context is not None
    assert context.run_id == admission.run_id


def test_fix_round1_traversal_alias_to_canonical_root_is_rejected(
    tmp_path: Path,
) -> None:
    """Catches lexical path comparison accepting an alias of the source checkout."""
    root, records, supervisor, admission, store = _managed_run(tmp_path)
    alias_parent = tmp_path / "alias-parent"
    alias_parent.mkdir()
    lexical_alias = alias_parent / ".." / root.name
    api = _gateway_api()
    provider = _WorktreeProvider(api=api, path=lexical_alias)
    worker = _RecordingWorker(api.WorkerResult(True, "published"))
    gateway = api.ManagedSpaceActionGateway(
        supervisor=supervisor,
        policy_gate=PolicyGate(store),
        worktree_provider=provider,
        worktree_attestor=_WorktreeAttestor(api=api),
        local_worker=worker,
        github_worker=worker,
        deployment_worker=worker,
        diagnosis_runner=_RecordingDiagnosis(),
    )
    proposal = _proposal(root)
    _record_bound_evidence(store, proposal, run_id=admission.run_id)

    result = gateway.execute(admission.capability, proposal)

    assert result.code == "canonical_worktree_forbidden"
    assert worker.calls == []
    assert records["target"].canonical_root == root


def test_fix_round1_symlink_alias_to_canonical_root_is_rejected(
    tmp_path: Path,
) -> None:
    """Catches a symlink/junction alias bypassing the canonical-root denial."""
    root, _records, supervisor, admission, store = _managed_run(tmp_path)
    alias = tmp_path / "root-alias"
    try:
        os.symlink(root, alias, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    api = _gateway_api()
    provider = _WorktreeProvider(api=api, path=alias)
    worker = _RecordingWorker(api.WorkerResult(True, "published"))
    gateway = api.ManagedSpaceActionGateway(
        supervisor=supervisor,
        policy_gate=PolicyGate(store),
        worktree_provider=provider,
        worktree_attestor=_WorktreeAttestor(api=api),
        local_worker=worker,
        github_worker=worker,
        deployment_worker=worker,
        diagnosis_runner=_RecordingDiagnosis(),
    )
    proposal = _proposal(root)
    _record_bound_evidence(store, proposal, run_id=admission.run_id)

    result = gateway.execute(admission.capability, proposal)

    assert result.code == "canonical_worktree_forbidden"
    assert worker.calls == []


def test_fix_round1_unrelated_path_requires_independent_worktree_attestation(
    tmp_path: Path,
) -> None:
    """Catches a provider self-asserting source/run metadata for any directory."""
    root, _records, supervisor, admission, store = _managed_run(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    api = _gateway_api()
    provider = _WorktreeProvider(api=api, path=unrelated)
    worker = _RecordingWorker(api.WorkerResult(True, "published"))
    gateway = api.ManagedSpaceActionGateway(
        supervisor=supervisor,
        policy_gate=PolicyGate(store),
        worktree_provider=provider,
        worktree_attestor=_RejectingAttestor(),
        local_worker=worker,
        github_worker=worker,
        deployment_worker=worker,
        diagnosis_runner=_RecordingDiagnosis(),
    )
    proposal = _proposal(root)
    _record_bound_evidence(store, proposal, run_id=admission.run_id)

    result = gateway.execute(admission.capability, proposal)

    assert result.code == "worktree_attestation_failed"
    assert worker.calls == []


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        (
            "local.write_file",
            {
                "artifact_digest": "a" * 64,
                "path": "../outside.txt",
                "content": "safe",
            },
        ),
        (
            "local.write_file",
            {
                "artifact_digest": "a" * 64,
                "path": "C:\\outside.txt",
                "content": "safe",
            },
        ),
        (
            "local.apply_patch",
            {
                "artifact_digest": "a" * 64,
                "path": "safe.txt",
                "patch": {"accessToken": "abc123"},
            },
        ),
        (
            "local.apply_patch",
            {
                "artifact_digest": "a" * 64,
                "path": "safe.txt",
                "patch": {"credentials": ["abc123"]},
            },
        ),
        (
            "local.apply_patch",
            {
                "artifact_digest": "a" * 64,
                "path": "safe.txt",
                "patch": {"safe": ["nested", "mutable"]},
            },
        ),
        (
            "local.test",
            {
                "artifact_digest": "a" * 64,
                "selector": "tests/test_safe.py; remove everything",
            },
        ),
        (
            "github.push",
            {
                "artifact_digest": "a" * 64,
                "branch": ["feat/one", "feat/two"],
            },
        ),
        (
            "deployment.deploy",
            {
                "artifact_digest": "a" * 64,
                "deployment_ref": "attacker-selected-target",
            },
        ),
    ],
)
def test_fix_round1_invalid_typed_operation_arguments_are_hard_denied(
    tmp_path: Path,
    operation: str,
    arguments: dict[str, object],
) -> None:
    """Catches loose JSON reaching workers instead of immutable narrow requests."""
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        _store,
        _provider,
        local,
        github,
        deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)

    result = gateway.execute(
        admission.capability,
        _proposal(root, operation=operation, arguments=arguments),
    )

    assert result.code == "operation_hard_denied"
    assert local.calls == github.calls == deployment.calls == []


def test_fix_round1_generic_sidekick_adapter_requires_managed_gateway_handoff(
    tmp_path: Path,
) -> None:
    """Catches a managed operation reaching the generic Sidekick executor."""
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        store,
        _provider,
        _local,
        github,
        _deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)
    proposal = _proposal(root)
    _record_bound_evidence(store, proposal, run_id=admission.run_id)
    generic_calls: list[object] = []
    adapter = SidekickToolAdapter(
        trusted_workspace_resolver=lambda workspace: Path(workspace),
        action_executor=lambda *args: generic_calls.append(args),
        managed_gateway=gateway,
    )

    with pytest.raises(RuntimeError, match="managed gateway"):
        adapter.execute(proposal.requested_action)
    run = store.get_run(admission.run_id)
    assert run is not None
    result = adapter.execute_managed(proposal, run)

    assert result.ok is True
    assert generic_calls == []
    assert len(github.calls) == 1


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        (
            "local.test",
            {
                "artifact_digest": "a" * 64,
                "selector": "../outside/test_escape.py::test_escape",
            },
        ),
        (
            "local.test",
            {
                "artifact_digest": "a" * 64,
                "selector": "C:\\Windows\\System32\\test_escape.py",
            },
        ),
        (
            "local.test",
            {
                "artifact_digest": "a" * 64,
                "selector": "tests/test_safe.py\\..\\outside.py",
            },
        ),
        (
            "local.test",
            {
                "artifact_digest": "a" * 64,
                "selector": "tests/test_safe.py::test_ok;whoami",
            },
        ),
        (
            "github.pull_request",
            {
                "artifact_digest": "a" * 64,
                "title": "Unsafe token",
                "body": "github_pat_" + "11AA22BB33CC44DD55EE",
            },
        ),
        (
            "github.pull_request",
            {
                "artifact_digest": "a" * 64,
                "title": "Unsafe token",
                "body": "xox" + "b-" + "1234567890" + "-" + "abcdefghijklmnop",
            },
        ),
    ],
)
def test_fix_round2_selector_and_current_token_forms_are_hard_denied(
    tmp_path: Path,
    operation: str,
    arguments: dict[str, object],
) -> None:
    """Catches path-like selectors and current token prefixes reaching workers."""
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        _store,
        provider,
        local,
        github,
        deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)

    result = gateway.execute(
        admission.capability,
        _proposal(root, operation=operation, arguments=arguments),
    )

    assert result.code == "operation_hard_denied"
    assert provider.calls == []
    assert local.calls == github.calls == deployment.calls == []


def test_fix_round2_safe_test_selector_is_typed_and_worktree_contained(
    tmp_path: Path,
) -> None:
    """Catches a valid node selector losing its contained path binding."""
    (
        api,
        root,
        _records,
        _supervisor,
        admission,
        store,
        _provider,
        local,
        _github,
        _deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)
    proposal = _proposal(
        root,
        operation="local.test",
        arguments={
            "artifact_digest": "a" * 64,
            "selector": "tests/test_safe.py::TestSafe::test_ok[param-1]",
        },
    )
    _record_bound_evidence(store, proposal, run_id=admission.run_id)

    result = gateway.execute(admission.capability, proposal)

    assert result.ok is True
    assert len(local.calls) == 1
    _handle, request = local.calls[0]
    assert isinstance(request, api.LocalTestRequest)
    assert request.path == "tests/test_safe.py"
    assert request.nodes == ("TestSafe", "test_ok[param-1]")
    assert not hasattr(request, "selector")


def test_fix_round2_gated_executor_never_generically_claims_managed_proposal(
    tmp_path: Path,
) -> None:
    """Catches policy-first generic claim making later gateway use impossible."""
    (
        _api,
        root,
        _records,
        _supervisor,
        admission,
        store,
        _provider,
        _local,
        github,
        _deployment,
        _diagnosis,
        gateway,
    ) = _gateway(tmp_path)
    proposal = _proposal(root)
    _record_bound_evidence(store, proposal, run_id=admission.run_id)
    run = store.get_run(admission.run_id)
    assert run is not None
    gate = PolicyGate(store)
    gate.record_approval(
        proposal,
        run,
        approval_type="human",
        approver_id="dashboard:user",
    )
    generic_calls: list[object] = []
    unavailable = SidekickToolAdapter(
        trusted_workspace_resolver=lambda workspace: Path(workspace),
        action_executor=lambda *args: generic_calls.append(args),
    )

    with pytest.raises(RuntimeError, match="managed gateway is not configured"):
        GatedToolExecutor(gate, unavailable).execute(proposal, run)

    configured = SidekickToolAdapter(
        trusted_workspace_resolver=lambda workspace: Path(workspace),
        action_executor=lambda *args: generic_calls.append(args),
        managed_gateway=gateway,
    )
    first = GatedToolExecutor(PolicyGate(store), configured).execute(proposal, run)
    replay = GatedToolExecutor(PolicyGate(store), configured).execute(proposal, run)

    assert first.ok is True
    assert replay.code == "execution_already_claimed"
    assert generic_calls == []
    assert len(github.calls) == 1


def _git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def _git_repository(tmp_path: Path, name: str) -> Path:
    repository = tmp_path / name
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Managed Gateway Test")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "initial")
    return repository.resolve()


def test_fix_round2_git_attestor_accepts_only_bound_same_repository_worktree(
    tmp_path: Path,
) -> None:
    """Catches path-only attestation accepting a foreign or plain directory."""
    api = _gateway_api()
    source = _git_repository(tmp_path, "source")
    worktree = (tmp_path / "managed-worktree").resolve()
    _git(source, "worktree", "add", "-b", "managed-test", str(worktree))
    foreign = _git_repository(tmp_path, "foreign")
    plain = (source / "plain-directory").resolve()
    plain.mkdir()
    bindings = {"run-1": worktree}
    attestor = api.GitWorktreeAttestor(
        run_target_resolver=lambda _source, run_id: bindings.get(run_id),
    )

    valid = attestor.attest(source, worktree, "run-1")

    assert valid is not None
    assert valid.source_root == source
    assert valid.worktree_root == worktree
    assert valid.run_id == "run-1"
    assert valid.identity.startswith("git-worktree:")
    assert len(valid.artifact_digest) == 64
    assert attestor.attest(source, source, "run-1") is None
    bindings["run-1"] = foreign
    assert attestor.attest(source, foreign, "run-1") is None
    bindings["run-1"] = plain
    assert attestor.attest(source, plain, "run-1") is None


def test_fix_round2_git_attestor_rejects_symlink_alias_and_wrong_run_binding(
    tmp_path: Path,
) -> None:
    """Catches aliases or another run borrowing a valid worktree membership."""
    api = _gateway_api()
    source = _git_repository(tmp_path, "source")
    worktree = (tmp_path / "managed-worktree").resolve()
    _git(source, "worktree", "add", "-b", "managed-test", str(worktree))
    alias = tmp_path / "worktree-alias"
    try:
        os.symlink(worktree, alias, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    attestor = api.GitWorktreeAttestor(
        run_target_resolver=lambda _source, run_id: (
            worktree if run_id == "run-1" else None
        ),
    )

    assert attestor.attest(source, alias, "run-1") is None
    assert attestor.attest(source, worktree, "run-2") is None


def test_fix_round2_gateway_factory_builds_repository_backed_attestor(
    tmp_path: Path,
) -> None:
    """Catches host construction still requiring a test-only attestor."""
    (
        _api,
        root,
        _records,
        supervisor,
        _admission,
        store,
        provider,
        local,
        github,
        deployment,
        diagnosis,
        _gateway_instance,
    ) = _gateway(tmp_path)

    gateway = _api.create_managed_space_action_gateway(
        supervisor=supervisor,
        policy_gate=PolicyGate(store),
        worktree_provider=provider,
        run_target_resolver=lambda _source, _run_id: root / "bound-worktree",
        local_worker=local,
        github_worker=github,
        deployment_worker=deployment,
        diagnosis_runner=diagnosis,
    )

    assert isinstance(gateway, _api.ManagedSpaceActionGateway)
    assert isinstance(gateway._worktree_attestor, _api.GitWorktreeAttestor)
