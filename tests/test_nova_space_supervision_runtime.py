from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3
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


def _ledger_schema_snapshot(path: Path) -> tuple[int, tuple[tuple[object, ...], ...]]:
    with sqlite3.connect(path) as connection:
        schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
        objects = tuple(
            connection.execute(
                """SELECT type, name, tbl_name, COALESCE(sql, '')
                   FROM sqlite_master
                   ORDER BY type, name"""
            )
        )
    return schema_version, objects


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


def test_host_start_revalidates_again_after_status_probe_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = supervisor.admit("alpha", {"kind": "maintenance"})
    dispatched: list[tuple[Path, str]] = []
    original_status = supervisor._read_action_boundary_child_status

    def status_then_revoke(capability):
        status = original_status(capability)
        records["alpha"] = replace(records["alpha"], yolo=False)
        return status

    monkeypatch.setattr(supervisor, "_read_action_boundary_child_status", status_then_revoke)
    assert admission.capability is not None

    assert not supervisor.start_admitted_run(
        admission.capability,
        dispatcher=lambda root, run_id: dispatched.append((root, run_id)),
    )

    child = ProjectSwarmStore(tmp_path / "alpha").get_run(admission.run_id)
    assert child is not None and child.status == "paused"
    assert dispatched == []


def test_host_start_revalidates_at_final_dispatch_handoff(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}

    class RevokingAtDispatchSupervisor(ManagedSpaceSupervisor):
        def _before_host_dispatch(self, capability):
            del capability
            records["alpha"] = replace(records["alpha"], enrolled=False)

    supervisor = RevokingAtDispatchSupervisor(
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


def test_restart_and_concurrent_pulses_dispatch_one_pending_signal_once(
    tmp_path: Path,
) -> None:
    """A persisted event may survive restart, but never become duplicate work."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    first_supervisor = _supervisor(tmp_path, records)
    second_supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    first_runtime = _runtime(first_supervisor, dispatched)
    second_runtime = _runtime(second_supervisor, dispatched)

    assert first_runtime.ingest_signal(
        "alpha", source="ci", event_id="restart-build-1", reason_code="ci_change"
    )

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(
            workers.map(
                lambda runtime: runtime.pulse(now_epoch=0.0),
                (first_runtime, second_runtime),
            )
        )

    started = [outcome for batch in outcomes for outcome in batch if outcome.status == "started"]
    assert len(started) == 1
    run_id = started[0].run_id
    assert run_id is not None
    assert dispatched == [(records["alpha"].canonical_root, run_id)]

    ProjectSwarmStore(records["alpha"].canonical_root).set_run_status(run_id, "completed")
    assert first_supervisor.record_completion(run_id) or second_supervisor.record_completion(run_id)

    restarted_runtime = _runtime(_supervisor(tmp_path, records), dispatched)
    assert restarted_runtime.pulse(now_epoch=1.0) == ()
    assert dispatched == [(records["alpha"].canonical_root, run_id)]


def test_equal_reference_at_fifteen_minutes_records_unchanged_without_model_work(
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(
        supervisor,
        "admit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a quiet equality check must not admit a model run")
        ),
    )
    assert [(item.target_key, item.status) for item in runtime.pulse(now_epoch=900.0)] == [
        ("alpha", "unchanged")
    ]
    assert dispatched == [(records["alpha"].canonical_root, first[0].run_id)]
    assert runtime.pulse(now_epoch=901.0) == ()


def test_fresh_signal_after_an_unchanged_check_bypasses_the_quiet_floor(
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
    _complete(supervisor, tmp_path / "alpha", first[0].run_id)
    assert [outcome.status for outcome in runtime.pulse(now_epoch=900.0)] == ["unchanged"]

    assert runtime.ingest_signal(
        "alpha", source="kanban", event_id="card-2", reason_code="kanban_change"
    )
    immediate = runtime.pulse(now_epoch=901.0)
    assert immediate[0].status == "started"
    assert len(dispatched) == 2


@pytest.mark.parametrize("legacy_reference", ("", "malformed-reference"))
def test_legacy_reference_row_does_not_admit_periodic_model_work(
    tmp_path: Path, legacy_reference: str
) -> None:
    """Blank or invalid markers are unknown, never a periodic model trigger."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.ingest_signal(
        "alpha", source="ci", event_id="build-1", reason_code="ci_change"
    )
    first = runtime.pulse(now_epoch=0.0)
    _complete(supervisor, tmp_path / "alpha", first[0].run_id)

    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(nova_supervision_space_state)")
        }
        for definition in (
            "current_reference_digest TEXT NOT NULL DEFAULT ''",
            "last_evaluated_reference_digest TEXT NOT NULL DEFAULT ''",
            "last_checked_at REAL",
            "last_check_code TEXT NOT NULL DEFAULT ''",
        ):
            if definition.split()[0] not in columns:
                connection.execute(
                    f"ALTER TABLE nova_supervision_space_state ADD COLUMN {definition}"
                )
        connection.execute(
            """UPDATE nova_supervision_space_state
               SET current_reference_digest = ?,
                   last_evaluated_reference_digest = ?,
                   last_checked_at = NULL, last_check_code = ''
               WHERE target_key = 'alpha'""",
            (legacy_reference, legacy_reference),
        )

    assert runtime.pulse(now_epoch=900.0) == ()
    assert dispatched == [(records["alpha"].canonical_root, first[0].run_id)]


def test_object_complete_legacy_schema_migrates_marker_and_binding_columns_before_signal_write(
    tmp_path: Path,
) -> None:
    """An existing table/index set cannot suppress a required column migration."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    supervisor.start()
    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        connection.execute(
            """CREATE TABLE nova_supervision_signals (
                signal_digest TEXT PRIMARY KEY,
                target_key TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                observed_at REAL NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE nova_supervision_space_state (
                target_key TEXT PRIMARY KEY,
                pending_digest TEXT NOT NULL,
                pending_reason_code TEXT NOT NULL,
                pending_count INTEGER NOT NULL,
                last_started_at REAL,
                last_outcome_code TEXT NOT NULL,
                updated_at REAL NOT NULL
            )"""
        )
        connection.execute(
            """CREATE INDEX idx_nova_supervision_signals_observed
               ON nova_supervision_signals(observed_at)"""
        )

    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.ingest_signal(
        "alpha", source="ci", event_id="legacy-build-1", reason_code="ci_change"
    )
    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(nova_supervision_space_state)")
        }

    assert {
        "target_space_id",
        "root_fingerprint",
        "governance_revision",
        "current_reference_digest",
        "last_evaluated_reference_digest",
        "last_checked_at",
        "last_check_code",
    } <= columns
    assert [outcome.status for outcome in runtime.pulse(now_epoch=0.0)] == ["started"]


def test_rebind_clears_old_reference_markers_before_admitting_a_fresh_signal(
    tmp_path: Path,
) -> None:
    """A recreated Space cannot inherit a quiet marker from its predecessor."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.ingest_signal(
        "alpha", source="ci", event_id="build-1", reason_code="ci_change"
    )
    first = runtime.pulse(now_epoch=0.0)
    _complete(supervisor, tmp_path / "alpha", first[0].run_id)
    assert [outcome.status for outcome in runtime.pulse(now_epoch=900.0)] == ["unchanged"]

    records["alpha"] = _governance(tmp_path / "alpha-rebound", revision=2)
    assert runtime.ingest_signal(
        "alpha", source="git", event_id="rebound-1", reason_code="git_change"
    )

    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        row = connection.execute(
            """SELECT target_space_id, root_fingerprint, governance_revision,
                      current_reference_digest, last_evaluated_reference_digest,
                      last_checked_at, last_check_code
               FROM nova_supervision_space_state WHERE target_key = 'alpha'"""
        ).fetchone()

    assert row is not None
    assert row[0] == records["alpha"].space_id
    assert row[1] == records["alpha"].root_fingerprint
    assert row[2] == records["alpha"].revision
    assert isinstance(row[3], str) and len(row[3]) == 64
    assert row[4:] == ("", None, "")
    assert [outcome.status for outcome in runtime.pulse(now_epoch=901.0)] == ["started"]


def test_equality_race_preserves_a_fresh_signal_for_the_next_pulse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stale equality write loses to a newly accepted bounded signal."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.ingest_signal(
        "alpha", source="ci", event_id="build-1", reason_code="ci_change"
    )
    first = runtime.pulse(now_epoch=0.0)
    _complete(supervisor, tmp_path / "alpha", first[0].run_id)
    original_mark_unchanged = runtime._mark_unchanged
    injected = False

    def mark_unchanged_after_fresh_signal(state: object, now: float) -> bool:
        nonlocal injected
        if not injected:
            injected = True
            assert runtime.ingest_signal(
                "alpha", source="git", event_id="racing-commit", reason_code="git_change"
            )
        return original_mark_unchanged(state, now)

    monkeypatch.setattr(runtime, "_mark_unchanged", mark_unchanged_after_fresh_signal)

    assert runtime.pulse(now_epoch=900.0) == ()
    assert [outcome.status for outcome in runtime.pulse(now_epoch=901.0)] == ["started"]
    assert len(dispatched) == 2


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
    assert not runtime.ingest_signal(
        "alpha", source="git", event_id="commit-1", reason_code="git_change"
    )

    outcomes = runtime.pulse(now_epoch=0.0)

    assert outcomes == ()
    assert dispatched == []
    assert not (tmp_path / "alpha" / ".swarm").exists()


def test_ineligible_signal_is_not_replayed_after_later_yolo_enrollment(
    tmp_path: Path,
) -> None:
    """Catches an old non-YOLO event becoming a new autonomous trigger later."""
    records = {"alpha": _governance(tmp_path / "alpha", yolo=False, enrolled=False)}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert not runtime.ingest_signal(
        "alpha", source="ci", event_id="build-before-enrollment", reason_code="ci_change"
    )
    records["alpha"] = replace(records["alpha"], yolo=True, enrolled=True)

    assert runtime.pulse(now_epoch=0.0) == ()
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


def test_concurrent_supervisor_start_is_schema_idempotent_after_initialization(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    supervisor.start()
    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        index_names = {
            row[0]
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type = 'index' AND name IN (?, ?)""",
                (
                    "idx_supervisor_admissions_target_updated",
                    "idx_supervisor_audit_admission_sequence",
                ),
            )
        }
    assert index_names == {
        "idx_supervisor_admissions_target_updated",
        "idx_supervisor_audit_admission_sequence",
    }
    before = _ledger_schema_snapshot(tmp_path / "supervisor.sqlite")

    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda _index: supervisor.start(), range(4)))

    assert _ledger_schema_snapshot(tmp_path / "supervisor.sqlite") == before


def test_signal_identity_capacity_rejects_new_events_without_evicting_replay_tombstones(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    for index in range(2_048):
        assert runtime.ingest_signal(
            "alpha",
            source="git",
            event_id=f"capacity-{index}",
            reason_code="git_change",
        )

    schema_before_rejections = _ledger_schema_snapshot(tmp_path / "supervisor.sqlite")
    assert not runtime.ingest_signal(
        "alpha",
        source="git",
        event_id="capacity-new",
        reason_code="git_change",
    )
    assert not runtime.ingest_signal(
        "alpha",
        source="git",
        event_id="capacity-0",
        reason_code="git_change",
    )
    with ThreadPoolExecutor(max_workers=4) as workers:
        rejected = list(
            workers.map(
                lambda index: runtime.ingest_signal(
                    "alpha",
                    source="git",
                    event_id=("capacity-0" if index == 0 else f"capacity-race-{index}"),
                    reason_code="git_change",
                ),
                range(4),
            )
        )
    assert rejected == [False, False, False, False]

    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        stored_count = connection.execute(
            "SELECT COUNT(*) FROM nova_supervision_signals"
        ).fetchone()[0]
    assert stored_count == 2_048
    assert _ledger_schema_snapshot(tmp_path / "supervisor.sqlite") == schema_before_rejections
    assert dispatched == []
