"""Three-Space action-gate regressions without workers, credentials, or providers."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from nova.managed_space_gateway import ManagedSpaceActionGateway
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from swarm_core.policy import PolicyGate
from swarm_core.store import ProjectSwarmStore
from swarm_core.types import ActionProposal, RequestedToolAction
from test_nova_managed_space_gateway import _WorktreeAttestor, _WorktreeProvider


class _NoEffectWorker:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def execute(self, handle: object, request: object) -> object:
        self.calls.append((handle, request))
        return type("Result", (), {"ok": True, "message": "unexpected"})()


def _proposal(api, root: Path, *, operation: str, arguments: dict[str, object]):
    return ActionProposal(
        proposal_id=f"cycle30-{operation}", category="managed", reversible=False,
        external=True, cost_increasing=False,
        evidence_refs=("verifier:missing",),
        requested_action=RequestedToolAction(
            name=operation, workspace=root, arguments=arguments, use_worktree=True,
        ),
    )


def test_three_space_verifier_and_hard_denies_precede_effects(tmp_path: Path) -> None:
    import nova.managed_space_gateway as api

    roots = {slug: tmp_path / slug for slug in ("nova", "finanzjunkie", "aquarium-zentrum")}
    for root in roots.values():
        root.mkdir(parents=True)
    governance = {
        "nova": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["nova"], yolo=False,
            enrolled=False, revision=1, policy_identity="cycle30",
        ),
        "finanzjunkie": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["finanzjunkie"], yolo=True,
            enrolled=True, revision=1, policy_identity="cycle30",
        ),
        "aquarium-zentrum": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["aquarium-zentrum"], yolo=True,
            enrolled=True, revision=1, policy_identity="cycle30",
        ),
    }
    for target in ("finanzjunkie", "aquarium-zentrum"):
        supervisor = ManagedSpaceSupervisor(
            ledger_path=tmp_path / "state" / (target + ".sqlite"),
            governance_resolver=governance.get,
        )
        assert supervisor.admit("nova", {"goal": "controller", "kind": "maintenance"}).reason == "not_yolo_enrolled"
        admission = supervisor.admit(target, {"goal": target, "kind": "maintenance"})
        assert admission.capability is not None
        root = roots[target]
        store = ProjectSwarmStore(root)
        assert store.resume_run(admission.run_id).status == "running"
        provider = _WorktreeProvider(api=api, path=(tmp_path / target / "worktree").resolve())
        local = _NoEffectWorker()
        github = _NoEffectWorker()
        deployment = _NoEffectWorker()
        gateway = ManagedSpaceActionGateway(
            supervisor=supervisor, policy_gate=PolicyGate(store),
            worktree_provider=provider, worktree_attestor=_WorktreeAttestor(api=api),
            local_worker=local, github_worker=github, deployment_worker=deployment,
            diagnosis_runner=lambda *args: None,
        )

        missing_verifier = gateway.execute(
            admission.capability,
            _proposal(api, root, operation="github.push", arguments={"artifact_digest": "a" * 64, "branch": "feat/safe"}),
        )
        assert missing_verifier.code == "managed_verifier_required"
        assert len(provider.calls) == 1
        assert local.calls == github.calls == deployment.calls == []

        if target == "aquarium-zentrum":
            denied = gateway.execute(
                admission.capability,
                _proposal(api, root, operation="payments.purchase", arguments={"amount": 1}),
            )
            assert denied.code == "operation_hard_denied"
            assert len(provider.calls) == 1
            assert local.calls == github.calls == deployment.calls == []

        # The supervisor is intentionally globally single-run. Finish this
        # isolated admission before exercising the next managed Space.
        assert supervisor.cancel(admission.admission_id, actor="dashboard:" + ("a" * 64)) is True












