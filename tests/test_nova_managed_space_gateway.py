from __future__ import annotations

from dataclasses import replace
import importlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from swarm_core.policy import PolicyGate, proposal_digest
from swarm_core.store import ProjectSwarmStore
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


class _WorktreeProvider:
    def __init__(
        self,
        *,
        api: Any,
        path: Path,
        identity: str = "worktree:managed-1",
        artifact_digest: str = "a" * 64,
        target_root: Path | None = None,
        run_id: str | None = None,
        after_create: Any | None = None,
    ) -> None:
        self._api = api
        self._path = path
        self._identity = identity
        self._artifact_digest = artifact_digest
        self._target_root = target_root
        self._run_id = run_id
        self._after_create = after_create
        self.calls: list[tuple[Path, str]] = []

    def create(self, canonical_root: Path, run_id: str):
        self.calls.append((canonical_root, run_id))
        handle = self._api.ManagedWorktreeHandle(
            canonical_root=self._target_root or canonical_root,
            path=self._path,
            run_id=self._run_id or run_id,
            identity=self._identity,
            artifact_digest=self._artifact_digest,
        )
        if self._after_create is not None:
            self._after_create()
        return handle


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
    provider_values = dict(provider_kwargs or {})
    provider_values.setdefault(
        "path",
        (worktree_path or (tmp_path / "worktrees" / "run-1")).resolve(),
    )
    provider = _WorktreeProvider(
        api=api,
        **provider_values,
    )
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
        arguments={"artifact_digest": "a" * 64, "patch": "safe patch"},
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
        ({"target_root": Path("foreign")}, "worktree_target_mismatch"),
        ({"run_id": "foreign-run"}, "worktree_run_mismatch"),
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
    ("operation", "arguments", "worker_name", "result_code"),
    [
        (
            "local.apply_patch",
            {"artifact_digest": "a" * 64, "patch": "safe patch"},
            "local",
            "local_completed",
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
        ),
        (
            "deployment.deploy",
            {
                "artifact_digest": "a" * 64,
                "deployment_ref": "production",
            },
            "deployment",
            "deployment_completed",
        ),
    ],
)
def test_allowed_operation_routes_only_to_named_worker_with_narrow_request(
    tmp_path: Path,
    operation: str,
    arguments: dict[str, object],
    worker_name: str,
    result_code: str,
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
    calls = {"local": local.calls, "github": github.calls, "deployment": deployment.calls}
    assert len(calls[worker_name]) == 1
    assert all(calls[name] == [] for name in calls if name != worker_name)
    _handle, request = calls[worker_name][0]
    assert request.operation == operation
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
        arguments={
            "artifact_digest": "a" * 64,
            "deployment_ref": "production",
        },
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
        arguments={
            "artifact_digest": "a" * 64,
            "deployment_ref": "production",
        },
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
        arguments={
            "artifact_digest": "a" * 64,
            "deployment_ref": "production",
        },
    )
    _record_bound_evidence(store, proposal, run_id=admission.run_id)

    diagnosed = gateway.execute(admission.capability, proposal)
    replay = gateway.execute(admission.capability, proposal)

    assert diagnosed.code == "deployment_retry_requires_fresh_evidence"
    assert replay.code == "execution_already_claimed"
    assert len(deployment.calls) == 1
    assert len(diagnosis.calls) == 1
