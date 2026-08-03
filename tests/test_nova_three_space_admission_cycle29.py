"""Production-shaped three-Space capability admission regressions."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from swarm_core.store import ProjectSwarmStore


def test_three_space_admission_and_tool_boundary_revalidate_current_governance(tmp_path: Path) -> None:
    roots = {slug: tmp_path / slug for slug in ("nova", "finanzjunkie", "aquarium-zentrum")}
    for root in roots.values():
        root.mkdir(parents=True)
    governance = {
        "nova": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["nova"],
            yolo=False, enrolled=False, revision=1, policy_identity="cycle29",
        ),
        "finanzjunkie": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["finanzjunkie"],
            yolo=True, enrolled=True, revision=1, policy_identity="cycle29",
        ),
        "aquarium-zentrum": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["aquarium-zentrum"],
            yolo=True, enrolled=True, revision=1, policy_identity="cycle29",
        ),
    }
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "state" / "supervisor.sqlite",
        governance_resolver=governance.get,
    )

    # The controller Space can never enroll through the production admission
    # boundary, while the two YOLO Spaces use the one global slot.
    nova = supervisor.admit("nova", {"goal": "controller", "kind": "maintenance"})
    assert nova.status == "rejected" and nova.reason == "not_yolo_enrolled"
    finance = supervisor.admit(
        "finanzjunkie", {"goal": "finance maintenance", "kind": "maintenance"}
    )
    assert finance.status == "created" and finance.capability is not None
    aquarium = supervisor.admit(
        "aquarium-zentrum", {"goal": "aquarium maintenance", "kind": "maintenance"}
    )
    assert aquarium.status == "rejected" and aquarium.reason == "active_limit"

    # Bind the Finance child to the host execution boundary, then revoke its
    # governance generation before any tool/model action. Revalidation must
    # fail closed and pause the child; no worker/provider is involved here.
    finance_store = ProjectSwarmStore(roots["finanzjunkie"])
    assert finance_store.resume_run(finance.run_id).status == "running"
    governance["finanzjunkie"] = replace(governance["finanzjunkie"], revision=2)
    options = supervisor.execution_options_for_run(
        roots["finanzjunkie"], finance_store.get_run(finance.run_id)
    )
    assert options.blocked_reason in {"governance_changed", "governance_revoked"}
    assert finance_store.get_run(finance.run_id).status == "paused"
    assert supervisor.list_active_admissions()[0]["state"] == "paused"

    # A moved canonical root is equally invalid, even if the revision is
    # restored to a fresh value; the installed capability cannot be rebound.
    moved_root = tmp_path / "finanzjunkie-moved"
    moved_root.mkdir()
    governance["finanzjunkie"] = replace(
        governance["finanzjunkie"], canonical_root=moved_root, revision=3
    )
    moved_options = supervisor.execution_options_for_run(
        roots["finanzjunkie"], finance_store.get_run(finance.run_id)
    )
    assert moved_options.blocked_reason in {"governance_changed", "capability_invalid", "supervisor_binding_unavailable"}
    assert finance_store.get_run(finance.run_id).status == "paused"


