from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor


def test_admit_rejects_explicit_empty_goal_before_ledger_write() -> None:
    root = Path(".").resolve()
    governance = ManagedSpaceGovernance.from_values(
        space_id=str(uuid4()),
        canonical_root=root,
        yolo=True,
        enrolled=True,
        revision=1,
        policy_identity="policy:test",
    )
    supervisor = ManagedSpaceSupervisor(
        ledger_path=Path("unused-supervisor.sqlite"),
        governance_resolver=lambda _target: governance,
    )

    result = supervisor.admit("alpha", {"goal": "   ", "kind": "maintenance"})

    assert result.status == "rejected"
    assert result.reason == "invalid_intent"
    assert supervisor.list_active_admissions() == []