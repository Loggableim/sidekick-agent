"""Admission-to-policy quorum integration for the fixed Spaces."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from swarm_core.policy import PolicyGate
from swarm_core.store import ProjectSwarmStore
from swarm_core.types import ActionProposal, RequestedToolAction


def _local_proposal(root: Path) -> ActionProposal:
    return ActionProposal(
        proposal_id="quorum-local",
        category="project", reversible=True, external=False, cost_increasing=False,
        evidence_refs=("verifier:managed:1", "report:test:1", "review:a", "review:b"),
        requested_action=RequestedToolAction(
            name="local.apply_patch", workspace=root,
            arguments={"path": "src/safe.py", "patch": "safe"}, use_worktree=True,
        ),
    )


def test_three_space_review_quorum_blocks_until_verifier_and_two_independent_reviewers(tmp_path: Path) -> None:
    roots = {slug: tmp_path / slug for slug in ("nova", "finanzjunkie", "aquarium-zentrum")}
    for root in roots.values(): root.mkdir(parents=True)
    governance = {
        slug: ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=root, yolo=slug != "nova",
            enrolled=slug != "nova", revision=1, policy_identity="cycle33",
        ) for slug, root in roots.items()
    }
    for target in ("finanzjunkie", "aquarium-zentrum"):
        supervisor = ManagedSpaceSupervisor(
            ledger_path=tmp_path / (target + ".sqlite"), governance_resolver=governance.get,
        )
        assert supervisor.admit("nova", {"goal": "controller", "kind": "maintenance"}).reason == "not_yolo_enrolled"
        admission = supervisor.admit(target, {"goal": target, "kind": "maintenance", "autonomy": "reviewed_execution"})
        assert admission.capability is not None
        root = roots[target]; store = ProjectSwarmStore(root)
        assert store.resume_run(admission.run_id).status == "running"
        proposal = _local_proposal(root)
        gate = PolicyGate(store)
        assert PolicyGate._has_durable_review_quorum(proposal, store.get_workflow_role_checkpoints(admission.run_id)) is False
        store.record_workflow_role_checkpoint(admission.run_id, "verifier", model=None, data={"work": "verified", "decision": "verified", "evidence": ["verifier:managed:1", "report:test:1"], "provenance": {"adapter": "managed-local-verifier", "mode": "read_only", "operation": "inspect_worktree"}, "test_evidence": {"run_id": admission.run_id, "worktree_identity": "cycle33", "artifact_digest": "a" * 64, "runner_identity": "runner:pytest-local", "report_ref": "report:test:1", "passed": True}})
        store.record_workflow_role_checkpoint(admission.run_id, "review_a", model="glm-5.2", data={"work": "reviewed", "evidence": ["review:ok"], "decision": "approved", "approved": True})
        assert PolicyGate._has_durable_review_quorum(proposal, store.get_workflow_role_checkpoints(admission.run_id)) is False
        store.record_workflow_role_checkpoint(admission.run_id, "review_b", model="kimi-k2.7-code", data={"work": "reviewed", "evidence": ["review:ok"], "decision": "approved", "approved": True})
        assert PolicyGate._has_durable_review_quorum(proposal, store.get_workflow_role_checkpoints(admission.run_id)) is True






