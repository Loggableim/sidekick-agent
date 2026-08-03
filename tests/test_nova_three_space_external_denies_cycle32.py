"""External/irreversible action boundaries across all Nova-managed Spaces."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from nova.managed_space_gateway import ManagedSpaceActionGateway
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from swarm_core.policy import PolicyGate
from swarm_core.store import ProjectSwarmStore
from test_nova_three_space_action_gates_cycle30 import _NoEffectWorker, _proposal


def test_three_space_external_irreversible_actions_are_hard_denied_and_unaudited_effects_free(tmp_path: Path) -> None:
    import nova.managed_space_gateway as api

    roots = {slug: tmp_path / slug for slug in ("nova", "finanzjunkie", "aquarium-zentrum")}
    for root in roots.values():
        root.mkdir(parents=True)
    governance = {
        slug: ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=root,
            yolo=slug != "nova", enrolled=slug != "nova", revision=1,
            policy_identity="cycle32",
        )
        for slug, root in roots.items()
    }
    denied = {
        "external.message": {"message": "notify"},
        "secrets.read": {"path": ".env"},
        "payments.purchase": {"amount": 1},
        "admin.iam_grant": {"principal": "attacker"},
        "delete.destroy": {"artifact_digest": "a" * 64},
    }
    for target in ("finanzjunkie", "aquarium-zentrum"):
        supervisor = ManagedSpaceSupervisor(
            ledger_path=tmp_path / (target + ".sqlite"), governance_resolver=governance.get,
        )
        admission = supervisor.admit(target, {"goal": target, "kind": "maintenance"})
        assert admission.capability is not None
        store = ProjectSwarmStore(roots[target])
        assert store.resume_run(admission.run_id).status == "running"
        provider = _NoEffectWorker()
        local = _NoEffectWorker(); github = _NoEffectWorker(); deployment = _NoEffectWorker()
        gateway = ManagedSpaceActionGateway(
            supervisor=supervisor, policy_gate=PolicyGate(store),
            worktree_provider=provider, worktree_attestor=lambda *args: None,
            local_worker=local, github_worker=github, deployment_worker=deployment,
            diagnosis_runner=lambda *args: None,
        )
        for operation, arguments in denied.items():
            result = gateway.execute(admission.capability, _proposal(api, roots[target], operation=operation, arguments=arguments))
            assert result.code == "operation_hard_denied", (target, operation, result.code)
        assert provider.calls == local.calls == github.calls == deployment.calls == []
