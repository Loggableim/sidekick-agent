from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
import yaml

from swarm_core.config import initialize_project
from swarm_core.policy import PolicyGate, PolicyStatus
from swarm_core.sidekick_adapter import SidekickToolAdapter
from swarm_core.store import ProjectSwarmStore
from swarm_core.tools import ActionNotAllowed, GatedToolExecutor
from swarm_core.types import ActionCapabilities, ActionProposal, RequestedToolAction


def _proposal(
    tmp_path: Path,
    *,
    proposal_id: str = "proposal-1",
    category: str = "project",
    reversible: bool = True,
    external: bool = False,
    cost_increasing: bool = False,
    evidence_refs: tuple[str, ...] = ("evidence:test",),
    use_worktree: bool = False,
) -> ActionProposal:
    return ActionProposal(
        proposal_id=proposal_id,
        category=category,
        reversible=reversible,
        external=external,
        cost_increasing=cost_increasing,
        evidence_refs=evidence_refs,
        requested_action=RequestedToolAction(
            name="write_project_file",
            workspace=tmp_path,
            arguments={"path": "result.txt"},
            use_worktree=use_worktree,
        ),
    )


def _run(tmp_path: Path, autonomy: str | None = None):
    metadata = {} if autonomy is None else {"autonomy": autonomy}
    return ProjectSwarmStore(tmp_path).create_run(metadata=metadata)


def test_reviewed_execution_is_the_versioned_project_default(tmp_path: Path):
    """Catches a fresh project silently defaulting to unreviewed execution."""
    config = initialize_project(tmp_path)

    assert config.default_autonomy == "reviewed_execution"


@pytest.mark.parametrize(
    ("autonomy", "expected"),
    [
        ("observe", PolicyStatus.BLOCKED),
        ("suggest", PolicyStatus.NEEDS_HUMAN_APPROVAL),
        ("execute_safe", PolicyStatus.ALLOWED),
        ("reviewed_execution", PolicyStatus.NEEDS_MODEL_QUORUM),
        ("autonomous", PolicyStatus.ALLOWED),
    ],
)
def test_local_reversible_action_obeys_each_autonomy_level(
    tmp_path: Path,
    autonomy: str,
    expected: PolicyStatus,
):
    """Catches an autonomy level accidentally inheriting a more powerful policy."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(metadata={"autonomy": autonomy})

    decision = PolicyGate(store).evaluate(_proposal(tmp_path), run)

    assert decision.status is expected


def test_run_without_override_uses_reviewed_execution(tmp_path: Path):
    """Catches missing run metadata bypassing the reviewed-execution default."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run()

    decision = PolicyGate(store).evaluate(_proposal(tmp_path), run)

    assert decision.status is PolicyStatus.NEEDS_MODEL_QUORUM


def test_policy_gate_uses_the_project_configured_default_autonomy(tmp_path: Path):
    """Catches PolicyGate ignoring a versioned project autonomy override."""
    initialize_project(tmp_path)
    config_path = tmp_path / ".swarm" / "swarm.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["default_autonomy"] = "execute_safe"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run()

    decision = PolicyGate(store).evaluate(_proposal(tmp_path), run)

    assert decision.status is PolicyStatus.ALLOWED


def test_run_snapshots_project_autonomy_when_it_is_created(tmp_path: Path):
    """Catches later config edits changing the policy of an existing run."""
    initialize_project(tmp_path)
    config_path = tmp_path / ".swarm" / "swarm.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["default_autonomy"] = "execute_safe"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run()
    config["default_autonomy"] = "reviewed_execution"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    decision = PolicyGate(store).evaluate(_proposal(tmp_path), run)

    assert run.metadata["autonomy"] == "execute_safe"
    assert decision.status is PolicyStatus.ALLOWED


def test_reviewed_execution_requires_verifier_evidence_and_two_model_families(
    tmp_path: Path,
):
    """Catches same-family reviews or evidence-free verification forming quorum."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run()
    proposal = _proposal(tmp_path)
    gate = PolicyGate(store)

    gate.record_approval(
        proposal,
        run,
        approval_type="verifier",
        approver_id="local-verifier",
        evidence_refs=(),
    )
    gate.record_approval(
        proposal,
        run,
        approval_type="model",
        approver_id="review-a",
        model_family="glm",
    )
    gate.record_approval(
        proposal,
        run,
        approval_type="model",
        approver_id="review-b",
        model_family="GLM",
    )
    assert gate.evaluate(proposal, run).status is PolicyStatus.NEEDS_MODEL_QUORUM

    gate.record_approval(
        proposal,
        run,
        approval_type="verifier",
        approver_id="local-verifier",
        evidence_refs=("evidence:test",),
    )
    assert gate.evaluate(proposal, run).status is PolicyStatus.NEEDS_MODEL_QUORUM

    gate.record_approval(
        proposal,
        run,
        approval_type="model",
        approver_id="review-c",
        model_family="kimi",
    )

    assert gate.evaluate(proposal, run).status is PolicyStatus.ALLOWED


def test_policy_gate_rejects_a_run_owned_by_another_project_store(tmp_path: Path):
    """Catches a run object from another project authorizing local execution."""
    store = ProjectSwarmStore(tmp_path / "first")
    foreign_run = ProjectSwarmStore(tmp_path / "second").create_run(
        metadata={"autonomy": "autonomous"}
    )

    decision = PolicyGate(store).evaluate(
        _proposal(tmp_path, proposal_id="foreign-run"),
        foreign_run,
    )

    assert decision.status is PolicyStatus.BLOCKED
    assert decision.reason == "unknown_run"


def test_two_family_labels_from_one_model_approver_do_not_form_quorum(
    tmp_path: Path,
):
    """Catches one reviewer identity impersonating the required second model."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run()
    proposal = _proposal(tmp_path)
    gate = PolicyGate(store)
    gate.record_approval(
        proposal,
        run,
        approval_type="verifier",
        approver_id="local-verifier",
        evidence_refs=("evidence:test",),
    )
    gate.record_approval(
        proposal,
        run,
        approval_type="model",
        approver_id="same-reviewer",
        model_family="glm",
    )
    gate.record_approval(
        proposal,
        run,
        approval_type="model",
        approver_id="same-reviewer",
        model_family="kimi",
    )

    assert gate.evaluate(proposal, run).status is PolicyStatus.NEEDS_MODEL_QUORUM


def test_quorum_requires_one_pair_with_both_distinct_approver_and_family(
    tmp_path: Path,
):
    """Catches independent approver/family sets being combined across wrong records."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run()
    proposal = _proposal(tmp_path)
    gate = PolicyGate(store)
    gate.record_approval(
        proposal,
        run,
        approval_type="verifier",
        approver_id="local-verifier",
        evidence_refs=("evidence:test",),
    )
    for approver_id, model_family in [
        ("review-a", "glm"),
        ("review-a", "kimi"),
        ("review-b", "glm"),
    ]:
        gate.record_approval(
            proposal,
            run,
            approval_type="model",
            approver_id=approver_id,
            model_family=model_family,
        )

    assert gate.evaluate(proposal, run).status is PolicyStatus.NEEDS_MODEL_QUORUM


def test_policy_gate_uses_durable_run_autonomy_instead_of_caller_metadata(
    tmp_path: Path,
):
    """Catches a caller upgrading autonomy by forging a SwarmRun value."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run()
    forged = replace(run, metadata={"autonomy": "autonomous"})

    decision = PolicyGate(store).evaluate(_proposal(tmp_path), forged)

    assert decision.status is PolicyStatus.NEEDS_MODEL_QUORUM


def test_approval_records_survive_reopening_the_project_store(tmp_path: Path):
    """Catches approvals living only in a PolicyGate process instance."""
    first_store = ProjectSwarmStore(tmp_path)
    run = first_store.create_run()
    proposal = _proposal(tmp_path)
    first_gate = PolicyGate(first_store)
    first_gate.record_approval(
        proposal,
        run,
        approval_type="verifier",
        approver_id="local-verifier",
        evidence_refs=("evidence:test",),
    )
    first_gate.record_approval(
        proposal,
        run,
        approval_type="model",
        approver_id="review-a",
        model_family="glm",
    )
    first_gate.record_approval(
        proposal,
        run,
        approval_type="model",
        approver_id="review-b",
        model_family="kimi",
    )

    reopened_gate = PolicyGate(ProjectSwarmStore(tmp_path))

    assert reopened_gate.evaluate(proposal, run).status is PolicyStatus.ALLOWED
    assert len(ProjectSwarmStore(tmp_path).list_approvals(run.run_id)) == 3


@pytest.mark.parametrize(
    "proposal_overrides",
    [
        {"external": True},
        {"reversible": False},
        {"cost_increasing": True},
    ],
)
def test_sensitive_actions_require_human_approval_even_when_autonomous(
    tmp_path: Path,
    proposal_overrides: dict[str, bool],
):
    """Catches autonomous mode bypassing a human-only safety boundary."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(metadata={"autonomy": "autonomous"})
    proposal = _proposal(tmp_path, **proposal_overrides)
    gate = PolicyGate(store)

    assert gate.evaluate(proposal, run).status is PolicyStatus.NEEDS_HUMAN_APPROVAL

    gate.record_approval(
        proposal,
        run,
        approval_type="human",
        approver_id="owner",
    )

    assert gate.evaluate(proposal, run).status is PolicyStatus.ALLOWED


def test_human_approval_is_sufficient_for_sensitive_reviewed_action(
    tmp_path: Path,
):
    """Catches model quorum being added to a human-only sensitive action gate."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run()
    proposal = _proposal(tmp_path, category="external", external=True)
    gate = PolicyGate(store)

    assert gate.evaluate(proposal, run).status is PolicyStatus.NEEDS_HUMAN_APPROVAL

    gate.record_approval(
        proposal,
        run,
        approval_type="human",
        approver_id="owner",
    )

    assert gate.evaluate(proposal, run).status is PolicyStatus.ALLOWED


def test_denial_blocks_the_exact_proposal_and_approval_cannot_authorize_a_mutation(
    tmp_path: Path,
):
    """Catches ignored denials or proposal-id reuse replaying stale approval."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(metadata={"autonomy": "autonomous"})
    proposal = _proposal(tmp_path, external=True)
    gate = PolicyGate(store)
    gate.record_approval(
        proposal,
        run,
        approval_type="human",
        approver_id="owner",
        approved=False,
    )

    assert gate.evaluate(proposal, run).status is PolicyStatus.BLOCKED

    approved = _proposal(tmp_path, proposal_id="approved", external=True)
    gate.record_approval(
        approved,
        run,
        approval_type="human",
        approver_id="owner",
    )
    mutated = _proposal(
        tmp_path,
        proposal_id="approved",
        external=True,
        cost_increasing=True,
    )

    assert gate.evaluate(mutated, run).status is PolicyStatus.NEEDS_HUMAN_APPROVAL


def test_unknown_local_action_category_is_blocked(tmp_path: Path):
    """Catches a new category receiving execution rights without policy review."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(metadata={"autonomy": "autonomous"})

    decision = PolicyGate(store).evaluate(
        _proposal(tmp_path, category="unclassified"),
        run,
    )

    assert decision.status is PolicyStatus.BLOCKED


class _RecordingAdapter:
    def __init__(
        self,
        capabilities: ActionCapabilities | None = None,
    ) -> None:
        self.executed: list[RequestedToolAction] = []
        self.capabilities = capabilities or ActionCapabilities(
            category="project",
            reversible=True,
            external=False,
            cost_increasing=False,
        )

    def classify(self, action: RequestedToolAction) -> ActionCapabilities:
        return self.capabilities

    def preview(self, action: RequestedToolAction) -> dict[str, str]:
        return {"preview": action.name}

    def execute(self, action: RequestedToolAction) -> dict[str, str]:
        self.executed.append(action)
        return {"result": action.name}


@pytest.mark.parametrize("interleave", ["pause", "denial"])
def test_atomic_authorization_blocks_state_committed_at_claim_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    interleave: str,
):
    """Catches a stale read authorizing an adapter after a new pause or denial."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(metadata={"autonomy": "autonomous"})
    proposal = _proposal(tmp_path, proposal_id=f"atomic-{interleave}")
    gate = PolicyGate(store)
    adapter = _RecordingAdapter()
    original = getattr(store, "authorize_and_claim", None)

    def commit_state_then_authorize(*args: Any, **kwargs: Any):
        if interleave == "pause":
            store.set_run_status(run.run_id, "paused")
        else:
            gate.record_approval(
                proposal,
                run,
                approval_type="human",
                approver_id="owner",
                approved=False,
            )
        assert original is not None
        return original(*args, **kwargs)

    monkeypatch.setattr(
        store,
        "authorize_and_claim",
        commit_state_then_authorize,
        raising=False,
    )

    with pytest.raises(ActionNotAllowed) as raised:
        GatedToolExecutor(gate, adapter).execute(proposal, run)

    assert raised.value.decision.reason in {"run_not_running", "approval_denied"}
    assert adapter.executed == []


def test_proposal_freezes_evidence_and_workspace_before_cwd_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Catches approval for one evidence/path snapshot dispatching a later one."""
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    raw_evidence = ["evidence:first"]
    monkeypatch.chdir(first_cwd)
    proposal = ActionProposal(
        proposal_id="frozen-proposal",
        category="project",
        reversible=True,
        external=False,
        cost_increasing=False,
        evidence_refs=raw_evidence,
        requested_action=RequestedToolAction(
            name="write_project_file",
            workspace=Path("workspace"),
            arguments={"path": "result.txt"},
        ),
    )
    raw_evidence[0] = "evidence:second"
    monkeypatch.chdir(second_cwd)
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run()
    gate = PolicyGate(store)
    gate.record_approval(
        proposal,
        run,
        approval_type="verifier",
        approver_id="local-verifier",
        evidence_refs=("evidence:first",),
    )
    gate.record_approval(
        proposal,
        run,
        approval_type="model",
        approver_id="review-a",
        model_family="glm",
    )
    gate.record_approval(
        proposal,
        run,
        approval_type="model",
        approver_id="review-b",
        model_family="kimi",
    )
    adapter = _RecordingAdapter()

    GatedToolExecutor(gate, adapter).execute(proposal, run)

    assert proposal.evidence_refs == ("evidence:first",)
    assert adapter.executed == [proposal.requested_action]
    assert adapter.executed[0].workspace == first_cwd / "workspace"


def test_trusted_capabilities_block_a_mislabeled_external_payment(tmp_path: Path):
    """Catches caller flags downgrading an externally costly adapter action."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(metadata={"autonomy": "execute_safe"})
    proposal = _proposal(tmp_path)
    proposal = replace(
        proposal,
        requested_action=replace(
            proposal.requested_action,
            name="send_external_payment",
        ),
    )
    adapter = _RecordingAdapter(
        ActionCapabilities(
            category="external",
            reversible=False,
            external=True,
            cost_increasing=True,
        )
    )

    with pytest.raises(ActionNotAllowed) as raised:
        GatedToolExecutor(PolicyGate(store), adapter).execute(proposal, run)

    assert raised.value.decision.reason == "untrusted_action_capabilities_mismatch"
    assert adapter.executed == []


def test_gated_executor_never_invokes_adapter_before_required_approvals(
    tmp_path: Path,
):
    """Catches adapter execution occurring before the policy decision is allowed."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run()
    proposal = _proposal(tmp_path)
    adapter = _RecordingAdapter()

    with pytest.raises(ActionNotAllowed) as raised:
        GatedToolExecutor(PolicyGate(store), adapter).execute(proposal, run)

    assert raised.value.decision.status is PolicyStatus.NEEDS_MODEL_QUORUM
    assert adapter.executed == []


def test_gated_executor_blocks_a_non_running_durable_run(tmp_path: Path):
    """Catches paused or completed work continuing to execute adapters."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(metadata={"autonomy": "autonomous"})
    store.set_run_status(run.run_id, "paused")
    adapter = _RecordingAdapter()

    with pytest.raises(ActionNotAllowed) as raised:
        GatedToolExecutor(PolicyGate(store), adapter).execute(
            _proposal(tmp_path),
            run,
        )

    assert raised.value.decision.reason == "run_not_running"
    assert adapter.executed == []


def test_gated_executor_invokes_adapter_only_after_persisted_quorum(tmp_path: Path):
    """Catches an allowed decision failing to reach the injected adapter."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run()
    proposal = _proposal(tmp_path)
    gate = PolicyGate(store)
    gate.record_approval(
        proposal,
        run,
        approval_type="verifier",
        approver_id="local-verifier",
        evidence_refs=("evidence:test",),
    )
    gate.record_approval(
        proposal,
        run,
        approval_type="model",
        approver_id="review-a",
        model_family="glm",
    )
    gate.record_approval(
        proposal,
        run,
        approval_type="model",
        approver_id="review-b",
        model_family="kimi",
    )
    adapter = _RecordingAdapter()

    result = GatedToolExecutor(gate, adapter).execute(proposal, run)

    assert result == {"result": "write_project_file"}
    assert adapter.executed == [proposal.requested_action]


def test_approved_proposal_can_execute_only_once(tmp_path: Path):
    """Catches one approval being replayed for repeated adapter side effects."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(metadata={"autonomy": "autonomous"})
    proposal = _proposal(tmp_path)
    adapter = _RecordingAdapter()
    executor = GatedToolExecutor(PolicyGate(store), adapter)

    assert executor.execute(proposal, run) == {"result": "write_project_file"}
    with pytest.raises(ActionNotAllowed) as raised:
        executor.execute(proposal, run)

    assert raised.value.decision.reason == "execution_already_claimed"
    assert adapter.executed == [proposal.requested_action]


def test_action_arguments_are_snapshotted_before_approval_and_execution(
    tmp_path: Path,
):
    """Catches a stateful Mapping changing the value after policy evaluation."""

    class SequencedArguments(Mapping[str, str]):
        def __init__(self) -> None:
            self.values = iter(["safe.txt", "safe.txt", "dangerous.txt"])

        def __getitem__(self, key: str) -> str:
            if key != "path":
                raise KeyError(key)
            return next(self.values)

        def __iter__(self) -> Iterator[str]:
            return iter(("path",))

        def __len__(self) -> int:
            return 1

    action = RequestedToolAction(
        name="write_project_file",
        workspace=tmp_path,
        arguments=SequencedArguments(),
    )
    proposal = ActionProposal(
        proposal_id="snapshot",
        category="project",
        reversible=True,
        external=True,
        cost_increasing=False,
        evidence_refs=(),
        requested_action=action,
    )
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(metadata={"autonomy": "autonomous"})
    gate = PolicyGate(store)
    gate.record_approval(
        proposal,
        run,
        approval_type="human",
        approver_id="owner",
    )
    captured: list[dict[str, Any]] = []
    adapter = SidekickToolAdapter(
        trusted_workspace_resolver=lambda workspace: Path(workspace),
        action_executor=lambda _name, _workspace, arguments: captured.append(arguments),
        action_classifier=lambda _action: ActionCapabilities(
            category="project",
            reversible=True,
            external=True,
            cost_increasing=False,
        ),
    )

    GatedToolExecutor(gate, adapter).execute(proposal, run)

    assert captured == [{"path": "safe.txt"}]


def test_sidekick_adapter_rejects_untrusted_workspace_before_action(tmp_path: Path):
    """Catches an executable Sidekick action bypassing trusted-workspace resolution."""
    calls: list[Any] = []

    def reject(_workspace: str | Path) -> Path:
        calls.append("resolve")
        raise ValueError("untrusted workspace")

    adapter = SidekickToolAdapter(
        trusted_workspace_resolver=reject,
        action_executor=lambda *args: calls.append(args),
    )

    with pytest.raises(ValueError, match="untrusted workspace"):
        adapter.execute(_proposal(tmp_path).requested_action)

    assert calls == ["resolve"]


def test_sidekick_adapter_uses_injected_trusted_workspace_and_worktree(
    tmp_path: Path,
):
    """Catches direct imports or skipping injected worktree handling."""
    trusted = tmp_path
    worktree = tmp_path / "worktree"
    calls: list[tuple[Any, ...]] = []

    def resolve(workspace: str | Path) -> Path:
        calls.append(("resolve", Path(workspace)))
        return Path(workspace)

    def create_workspace(resolved: Path) -> dict[str, str]:
        calls.append(("worktree", resolved))
        return {"path": str(worktree), "branch": "swarm/test"}

    def execute_action(
        name: str,
        workspace: Path,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append(("execute", name, workspace, arguments))
        return {"workspace": str(workspace)}

    adapter = SidekickToolAdapter(
        trusted_workspace_resolver=resolve,
        action_executor=execute_action,
        worktree_creator=create_workspace,
        worktree_validator=lambda source, target: source == trusted
        and target == worktree,
    )

    result = adapter.execute(_proposal(tmp_path, use_worktree=True).requested_action)

    assert result == {"workspace": str(worktree)}
    assert calls == [
        ("resolve", tmp_path),
        ("worktree", trusted),
        ("resolve", worktree),
        (
            "execute",
            "write_project_file",
            worktree,
            {"path": "result.txt"},
        ),
    ]


def test_sidekick_adapter_rejects_worktree_from_another_trusted_project(
    tmp_path: Path,
):
    """Catches a valid-but-unrelated worktree replacing the approved source."""
    source = tmp_path / "source"
    unrelated = tmp_path / "unrelated"
    executed: list[tuple[Any, ...]] = []
    adapter = SidekickToolAdapter(
        trusted_workspace_resolver=lambda workspace: Path(workspace),
        action_executor=lambda *args: executed.append(args),
        worktree_creator=lambda _source: {"path": str(unrelated)},
        worktree_validator=lambda expected, created: expected == source
        and created == source,
    )
    action = RequestedToolAction(
        name="write_project_file",
        workspace=source,
        arguments={"path": "result.txt"},
        use_worktree=True,
    )

    with pytest.raises(ValueError, match="not bound"):
        adapter.execute(action)

    assert executed == []


def test_sidekick_adapter_preview_never_creates_a_worktree(tmp_path: Path):
    """Catches a supposedly read-only preview causing worktree mutation."""
    trusted = tmp_path
    calls: list[tuple[Any, ...]] = []

    def resolve(workspace: str | Path) -> Path:
        calls.append(("resolve", Path(workspace)))
        return Path(workspace)

    def create_workspace(resolved: Path) -> dict[str, str]:
        calls.append(("worktree", resolved))
        return {"path": str(tmp_path / "worktree")}

    def preview_action(
        name: str,
        workspace: Path,
        arguments: dict[str, Any],
    ) -> dict[str, str]:
        calls.append(("preview", name, workspace, arguments))
        return {"preview": name}

    adapter = SidekickToolAdapter(
        trusted_workspace_resolver=resolve,
        action_executor=lambda *_args: None,
        action_previewer=preview_action,
        worktree_creator=create_workspace,
    )

    result = adapter.preview(_proposal(tmp_path, use_worktree=True).requested_action)

    assert result == {"preview": "write_project_file"}
    assert calls == [
        ("resolve", tmp_path),
        (
            "preview",
            "write_project_file",
            trusted,
            {"path": "result.txt"},
        ),
    ]
