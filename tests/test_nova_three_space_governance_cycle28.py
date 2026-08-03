"""Three-Space E2E governance regressions without live/provider activation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from swarm_core.store import ProjectSwarmStore


def test_three_space_root_revocation_never_reuses_stale_intent_or_slot(tmp_path: Path) -> None:
    roots = {
        slug: tmp_path / slug
        for slug in ("nova", "finanzjunkie", "aquarium-zentrum")
    }
    for root in roots.values():
        root.mkdir(parents=True)
    governance = {
        "nova": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["nova"],
            yolo=False, enrolled=False, revision=1, policy_identity="cycle28",
        ),
        "finanzjunkie": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["finanzjunkie"],
            yolo=True, enrolled=True, revision=1, policy_identity="cycle28",
        ),
        "aquarium-zentrum": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["aquarium-zentrum"],
            yolo=True, enrolled=True, revision=1, policy_identity="cycle28",
        ),
    }
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "state" / "supervisor.sqlite",
        governance_resolver=governance.get,
    )
    dispatched: list[tuple[Path, str]] = []

    def fake_dispatch(root: Path, run_id: str) -> None:
        dispatched.append((root, run_id))
        # Aquarium intentionally owns the single global slot for this audit.
        if root.name != "aquarium-zentrum":
            ProjectSwarmStore(root).set_run_status(run_id, "completed")
            assert supervisor.record_completion(run_id) is True

    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor, dispatch_run=fake_dispatch,
        governance_snapshots=lambda: governance,
    )
    assert runtime.ingest_signal(
        "aquarium-zentrum", source="git", event_id="aquarium-1", reason_code="git_change"
    )
    assert runtime.ingest_signal(
        "finanzjunkie", source="ci", event_id="finance-1", reason_code="ci_failed"
    )
    first = runtime.pulse(now_epoch=100.0)
    assert [(item.target_key, item.status) for item in first] == [
        ("aquarium-zentrum", "started"), ("finanzjunkie", "active_limit")
    ]
    assert len(dispatched) == 1

    # A moved project root is a new authority, not a continuation of the old
    # pending Finance intent.  The stale row remains pending but cannot claim
    # the moved root or bypass the Aquarium global slot.
    moved_root = tmp_path / "finanzjunkie-moved"
    moved_root.mkdir()
    previous = governance["finanzjunkie"]
    governance["finanzjunkie"] = replace(previous, canonical_root=moved_root, revision=2)
    moved = runtime.pulse(now_epoch=101.0)
    assert not any(item.status == "started" for item in moved)
    assert len(dispatched) == 1
    assert all(root != moved_root for root, _run_id in dispatched)

    # Revoking YOLO/enrollment fail-closes the still-pending intent.  A
    # duplicate signal under the revoked authority is rejected and cannot
    # silently resurrect the prior run or release the global slot.
    governance["finanzjunkie"] = replace(governance["finanzjunkie"], yolo=False, enrolled=False, revision=3)
    assert runtime.ingest_signal(
        "finanzjunkie", source="ci", event_id="finance-1", reason_code="ci_failed"
    ) is False
    revoked = runtime.pulse(now_epoch=102.0)
    # A stale generation may remain pending behind the bounded retry floor,
    # but it must produce no admission/outcome and can never dispatch.
    assert revoked == ()
    assert len(dispatched) == 1


