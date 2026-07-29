from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from nova.space_supervisor import (
    ManagedSpaceCapability,
    ManagedSpaceGovernance,
    ManagedSpaceSupervisor,
)
from swarm_core.store import ProjectSwarmStore


def _governance(root: Path, **overrides: object) -> ManagedSpaceGovernance:
    values: dict[str, object] = {
        "space_id": str(uuid4()),
        "canonical_root": root,
        "root_fingerprint": "",
        "yolo": True,
        "enrolled": True,
        "revision": 1,
        "policy_identity": "space-governance:1",
    }
    values.update(overrides)
    return ManagedSpaceGovernance.from_values(**values)


def _supervisor(
    tmp_path: Path,
    records: dict[str, ManagedSpaceGovernance],
) -> ManagedSpaceSupervisor:
    return ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
    )


def _runtime(
    supervisor: ManagedSpaceSupervisor,
    dispatched: list[tuple[Path, str]],
):
    from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime

    return NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=lambda root, run_id: dispatched.append((root, run_id)),
    )


def _complete(supervisor: ManagedSpaceSupervisor, root: Path, run_id: str) -> None:
    ProjectSwarmStore(root).set_run_status(run_id, "completed")
    assert supervisor.record_completion(run_id) is True


def test_host_start_of_admitted_run_uses_bound_root_without_human_resume_marker(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = supervisor.admit("alpha", {"kind": "maintenance"})
    dispatched: list[tuple[Path, str]] = []

    assert admission.capability is not None
    assert supervisor.start_admitted_run(
        admission.capability,
        dispatcher=lambda root, run_id: dispatched.append((root, run_id)),
    )

    child = ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id)
    assert child is not None and child.status == "running"
    assert dispatched == [(records["alpha"].canonical_root, admission.run_id)]
    events = ProjectSwarmStore(records["alpha"].canonical_root).list_events(
        admission.run_id
    )
    assert any(event.event_type == "nova.supervisor.host_started" for event in events)
    assert not any(event.event_type == "run.resumed_by_human" for event in events)


def test_host_start_rejects_an_uninitialized_capability_without_dispatching(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    forged = object.__new__(ManagedSpaceCapability)
    dispatched: list[tuple[Path, str]] = []

    assert not supervisor.start_admitted_run(
        forged,
        dispatcher=lambda root, run_id: dispatched.append((root, run_id)),
    )

    assert dispatched == []


def test_host_start_revalidates_revocation_before_dispatch_and_pauses_fail_closed(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}

    class RevokingSupervisor(ManagedSpaceSupervisor):
        def _before_host_start(self, capability):
            del capability
            records["alpha"] = replace(records["alpha"], enrolled=False)

    supervisor = RevokingSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
    )
    admission = supervisor.admit("alpha", {"kind": "maintenance"})
    dispatched: list[tuple[Path, str]] = []

    assert admission.capability is not None
    assert not supervisor.start_admitted_run(
        admission.capability,
        dispatcher=lambda root, run_id: dispatched.append((root, run_id)),
    )

    child = ProjectSwarmStore(tmp_path / "alpha").get_run(admission.run_id)
    assert child is not None and child.status == "paused"
    assert dispatched == []


def test_host_start_does_not_dispatch_if_the_bound_child_is_paused_during_handoff(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}

    class PausingStore:
        def __init__(self, root: Path) -> None:
            self._store = ProjectSwarmStore(root)

        def __getattr__(self, name: str):
            return getattr(self._store, name)

        def append_event_once(self, run_id: str, *args, **kwargs):
            result = self._store.append_event_once(run_id, *args, **kwargs)
            self._store.set_run_status(run_id, "paused")
            return result

    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
        child_store_factory=PausingStore,
    )
    admission = supervisor.admit("alpha", {"kind": "maintenance"})
    dispatched: list[tuple[Path, str]] = []

    assert admission.capability is not None
    assert not supervisor.start_admitted_run(
        admission.capability,
        dispatcher=lambda root, run_id: dispatched.append((root, run_id)),
    )

    child = ProjectSwarmStore(tmp_path / "alpha").get_run(admission.run_id)
    assert child is not None and child.status == "paused"
    assert dispatched == []


def test_duplicate_signals_coalesce_and_event_dispatches_once_without_model_runtime(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.ingest_signal(
        "alpha", source="git", event_id="commit-1", reason_code="git_change"
    )
    assert not runtime.ingest_signal(
        "alpha", source="git", event_id="commit-1", reason_code="git_change"
    )

    outcomes = runtime.pulse(now_epoch=0.0)

    assert [outcome.status for outcome in outcomes] == ["started"]
    assert len(dispatched) == 1
    assert runtime.pulse(now_epoch=1.0) == ()
    assert len(dispatched) == 1


def test_concurrent_duplicate_signal_identity_coalesces_to_one_pending_dispatch(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    def ingest() -> bool:
        return runtime.ingest_signal(
            "alpha", source="git", event_id="commit-concurrent", reason_code="git_change"
        )

    with ThreadPoolExecutor(max_workers=4) as workers:
        accepted = list(workers.map(lambda _index: ingest(), range(4)))

    assert accepted.count(True) == 1
    assert accepted.count(False) == 3
    assert [outcome.status for outcome in runtime.pulse(now_epoch=0.0)] == ["started"]
    assert len(dispatched) == 1


def test_periodic_check_waits_fifteen_minutes_but_pending_event_bypasses_floor(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    assert runtime.ingest_signal(
        "alpha", source="ci", event_id="build-1", reason_code="ci_change"
    )
    first = runtime.pulse(now_epoch=0.0)
    assert first[0].status == "started"
    _complete(supervisor, tmp_path / "alpha", first[0].run_id)

    assert runtime.pulse(now_epoch=899.0) == ()
    at_floor = runtime.pulse(now_epoch=900.0)
    assert at_floor[0].status == "started"
    _complete(supervisor, tmp_path / "alpha", at_floor[0].run_id)

    assert runtime.ingest_signal(
        "alpha", source="kanban", event_id="card-2", reason_code="kanban_change"
    )
    immediate = runtime.pulse(now_epoch=901.0)
    assert immediate[0].status == "started"
    assert len(dispatched) == 3


def test_ineligible_spaces_never_create_a_child_dispatch_or_consult_global_nova_yolo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha", yolo=False, enrolled=False)}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    import nova.entity_kernel as entity_kernel

    monkeypatch.setattr(
        entity_kernel.EntityKernel,
        "is_yolo_enabled",
        lambda _self: (_ for _ in ()).throw(AssertionError("global YOLO consulted")),
    )
    assert runtime.ingest_signal(
        "alpha", source="git", event_id="commit-1", reason_code="git_change"
    )

    outcomes = runtime.pulse(now_epoch=0.0)

    assert [outcome.status for outcome in outcomes] == ["ineligible"]
    assert dispatched == []
    assert not (tmp_path / "alpha" / ".swarm").exists()


def test_one_global_active_run_leaves_other_space_pending_without_duplicate_dispatch(
    tmp_path: Path,
) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    assert runtime.ingest_signal(
        "alpha", source="git", event_id="commit-1", reason_code="git_change"
    )
    assert runtime.ingest_signal(
        "beta", source="ci", event_id="build-1", reason_code="ci_change"
    )

    first = runtime.pulse(now_epoch=0.0)

    assert [(outcome.target_key, outcome.status) for outcome in first] == [
        ("alpha", "started"),
        ("beta", "active_limit"),
    ]
    assert len(dispatched) == 1
    retry = runtime.pulse(now_epoch=1.0)
    assert [(outcome.target_key, outcome.status) for outcome in retry] == [
        ("beta", "active_limit"),
    ]
    assert len(dispatched) == 1
    _complete(supervisor, tmp_path / "alpha", first[0].run_id)
    next_run = runtime.pulse(now_epoch=2.0)
    assert [(outcome.target_key, outcome.status) for outcome in next_run] == [
        ("beta", "started"),
    ]
    assert len(dispatched) == 2


def test_read_only_status_does_not_initialize_the_supervisor_ledger_or_dispatch(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.status() == ()

    assert not (tmp_path / "supervisor.sqlite").exists()
    assert dispatched == []


def test_inert_pulse_without_signal_does_not_create_a_ledger_or_schedule_work(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.pulse(now_epoch=900.0) == ()

    assert not (tmp_path / "supervisor.sqlite").exists()
    assert dispatched == []
