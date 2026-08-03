from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from nova.ticker_handler import consume_pending_events
from swarm_core.store import ProjectSwarmStore


def _governance(root: Path) -> ManagedSpaceGovernance:
    return ManagedSpaceGovernance.from_values(
        space_id=str(uuid4()), canonical_root=root, root_fingerprint="",
        yolo=True, enrolled=True, revision=1, policy_identity="space-governance:1",
    )


def test_fake_clock_host_ticks_need_a_new_fifteen_minute_heartbeat_for_next_run(
    monkeypatch, tmp_path: Path,
) -> None:
    """A 60-second host loop is not a 60-second autonomous model loop."""
    import nova.space_supervision_runtime as runtime_module

    now = {"value": 0.0}
    monkeypatch.setattr(runtime_module.time, "time", lambda: now["value"])
    root = tmp_path / "aquarium-zentrum"
    records = {"aquarium-zentrum": _governance(root)}
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite", governance_resolver=records.get
    )
    dispatched: list[tuple[Path, str]] = []
    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=lambda project_root, run_id: dispatched.append((project_root, run_id)),
    )

    assert runtime.ingest_signal(
        "aquarium-zentrum", source="heartbeat", event_id="heartbeat:1",
        reason_code="periodic_check",
    )
    first = consume_pending_events(supervisor=supervisor, runtime=runtime)
    first_run = first.outcomes[0].run_id
    assert first_run is not None
    ProjectSwarmStore(root).set_run_status(first_run, "completed")
    assert supervisor.record_completion(first_run)

    for tick in (60.0, 120.0, 899.0):
        now["value"] = tick
        assert consume_pending_events(supervisor=supervisor, runtime=runtime).outcomes == ()
    assert dispatched == [(root, first_run)]
    assert supervisor.list_active_admissions() == []

    now["value"] = 900.0
    assert not runtime.ingest_signal(
        "aquarium-zentrum", source="heartbeat", event_id="heartbeat:1",
        reason_code="periodic_check",
    )
    assert runtime.ingest_signal(
        "aquarium-zentrum", source="heartbeat", event_id="heartbeat:2",
        reason_code="periodic_check",
    )
    second = consume_pending_events(supervisor=supervisor, runtime=runtime)
    second_run = second.outcomes[0].run_id
    assert second_run is not None and second_run != first_run
    assert dispatched == [(root, first_run), (root, second_run)]
    assert len(supervisor.list_active_admissions()) == 1


def test_host_tick_dispatches_only_after_a_fresh_event_when_no_snapshot_provider(tmp_path: Path) -> None:
    """A quiet 60-second tick is inert; a fresh event wakes exactly one run."""
    root = tmp_path / "aquarium-zentrum"
    records = {"aquarium-zentrum": _governance(root)}
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=records.get,
    )
    dispatched: list[tuple[Path, str]] = []
    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=lambda project_root, run_id: dispatched.append((project_root, run_id)),
    )

    assert consume_pending_events(supervisor=supervisor, runtime=runtime).outcomes == ()
    assert consume_pending_events(supervisor=supervisor, runtime=runtime).outcomes == ()
    assert dispatched == []

    assert runtime.ingest_signal(
        "aquarium-zentrum", source="ci", event_id="fresh-ci-1", reason_code="ci_change"
    )
    outcome = consume_pending_events(supervisor=supervisor, runtime=runtime).outcomes
    assert [item.status for item in outcome] == ["started"]
    assert len(dispatched) == 1
    assert consume_pending_events(supervisor=supervisor, runtime=runtime).outcomes == ()
    assert len(dispatched) == 1