"""Trusted-root and registry-generation fail-closed checks for three Spaces."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from swarm_core.store import ProjectSwarmStore


def test_three_space_root_fingerprint_and_registry_revision_revalidate_before_execution(tmp_path: Path) -> None:
    roots = {slug: tmp_path / slug for slug in ("nova", "finanzjunkie", "aquarium-zentrum")}
    for root in roots.values(): root.mkdir(parents=True)
    governance = {
        slug: ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=root,
            yolo=slug != "nova", enrolled=slug != "nova", revision=1,
            policy_identity="cycle34",
        ) for slug, root in roots.items()
    }
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "state" / "supervisor.sqlite", governance_resolver=governance.get,
    )
    assert supervisor.admit("nova", {"goal": "controller", "kind": "maintenance"}).reason == "not_yolo_enrolled"
    finance = supervisor.admit("finanzjunkie", {"goal": "finance", "kind": "maintenance"})
    assert finance.capability is not None
    assert supervisor.admit("aquarium-zentrum", {"goal": "aquarium", "kind": "maintenance"}).reason == "active_limit"
    store = ProjectSwarmStore(roots["finanzjunkie"])
    assert store.resume_run(finance.run_id).status == "running"

    # A trusted-root move with a stale fingerprint must not inherit the
    # capability, even though the slug and Space ID remain unchanged.
    moved = tmp_path / "finanzjunkie-moved"; moved.mkdir()
    previous = governance["finanzjunkie"]
    governance["finanzjunkie"] = replace(previous, canonical_root=moved, revision=2)
    options = supervisor.execution_options_for_run(roots["finanzjunkie"], store.get_run(finance.run_id))
    assert options.blocked_reason in {"governance_changed", "governance_revoked", "capability_invalid"}
    assert store.get_run(finance.run_id).status == "paused"

    # A changed fingerprint is independently invalid; it cannot be smuggled
    # through by restoring the original path under a new registry generation.
    governance["finanzjunkie"] = replace(previous, root_fingerprint="f" * 64, revision=3)
    assert supervisor.current_governance("finanzjunkie") is None

