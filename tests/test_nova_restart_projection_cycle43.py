"""Restart/terminal-state projections preserve the one global slot."""

from pathlib import Path
from uuid import uuid4

from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from swarm_core.store import ProjectSwarmStore


def test_three_space_restart_does_not_silently_resume_and_releases_terminal_slot_once(tmp_path: Path) -> None:
    roots = {slug: tmp_path / slug for slug in ("nova", "finanzjunkie", "aquarium-zentrum")}
    for root in roots.values(): root.mkdir(parents=True)
    governance = {
        "nova": ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=roots["nova"], yolo=False, enrolled=False, revision=1, policy_identity="cycle43"),
        "finanzjunkie": ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=roots["finanzjunkie"], yolo=True, enrolled=True, revision=1, policy_identity="cycle43"),
        "aquarium-zentrum": ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=roots["aquarium-zentrum"], yolo=True, enrolled=True, revision=1, policy_identity="cycle43"),
    }
    supervisor = ManagedSpaceSupervisor(ledger_path=tmp_path / "state" / "supervisor.sqlite", governance_resolver=governance.get)
    dispatched: list[tuple[Path, str]] = []
    runtime = NovaSpaceSupervisionRuntime(supervisor=supervisor, dispatch_run=lambda root, run_id: dispatched.append((root, run_id)), governance_snapshots=lambda: governance)
    assert runtime.ingest_signal("aquarium-zentrum", source="git", event_id="aquarium-1", reason_code="git_change")
    assert runtime.ingest_signal("finanzjunkie", source="ci", event_id="finance-1", reason_code="ci_failed")
    assert [item.status for item in runtime.pulse(now_epoch=100.0)] == ["started", "active_limit"]
    assert len(dispatched) == 1

    restarted = NovaSpaceSupervisionRuntime(supervisor=supervisor, dispatch_run=lambda root, run_id: dispatched.append((root, run_id)), governance_snapshots=lambda: governance)
    assert restarted.pulse(now_epoch=101.0) == ()
    assert len(dispatched) == 1

    aquarium_run = ProjectSwarmStore(roots["aquarium-zentrum"]).get_run(dispatched[0][1])
    assert aquarium_run is not None
    ProjectSwarmStore(roots["aquarium-zentrum"]).set_run_status(aquarium_run.run_id, "completed")
    assert supervisor.record_completion(aquarium_run.run_id) is True
    assert supervisor.record_completion(aquarium_run.run_id) is False
    resumed = restarted.pulse(now_epoch=102.0)
    assert [(item.target_key, item.status) for item in resumed] == [("finanzjunkie", "started")]
    assert len(dispatched) == 2 and dispatched[1][0] == roots["finanzjunkie"]
