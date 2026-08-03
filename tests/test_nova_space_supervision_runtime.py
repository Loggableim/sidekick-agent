from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from nova.space_supervisor import (
    ManagedSpaceCapability,
    ManagedSpaceGovernance,
    ManagedSpaceSupervisor,
    SupervisorAdmission,
)
from swarm_core.models import ModelCatalogSnapshot
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


def test_heartbeat_seeds_only_enrolled_yolo_spaces_without_external_signal(
    tmp_path: Path,
) -> None:
    """A quiet heartbeat creates bounded periodic intents for current YOLO Spaces."""
    from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime

    records = {
        slug: _governance(tmp_path / slug)
        for slug in ("nova", "finanz-junkie", "aquarium-zentrum")
    }
    records["finanz-junkie"] = replace(records["finanz-junkie"], yolo=False)
    dispatched: list[tuple[Path, str]] = []
    supervisor = _supervisor(tmp_path, records)
    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=lambda root, run_id: dispatched.append((root, run_id)),
        governance_snapshots=lambda: records,
    )

    outcomes = runtime.pulse(now_epoch=100.0)
    assert [item.target_key for item in outcomes] == ["aquarium-zentrum", "nova"]
    assert [item.status for item in outcomes] == ["started", "active_limit"]
    assert len(dispatched) == 1
    statuses = {item.target_key: item for item in runtime.status()}
    assert statuses["aquarium-zentrum"].pending is False
    assert statuses["nova"].pending is True
    assert "finanz-junkie" not in statuses

    # A subsequent minute does not duplicate the seeded intent or invoke a
    # second admission while the global run remains active.
    assert runtime.pulse(now_epoch=101.0) == ()
    assert len(dispatched) == 1


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


def test_host_start_pauses_when_dispatcher_explicitly_refuses_handoff(
    tmp_path: Path,
) -> None:
    """A false worker admission must not strand an admitted child as running."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = supervisor.admit("alpha", {"kind": "maintenance"})

    assert admission.capability is not None
    assert not supervisor.start_admitted_run(
        admission.capability,
        dispatcher=lambda _root, _run_id: False,
    )

    child = ProjectSwarmStore(records["alpha"].canonical_root).get_run(
        admission.run_id
    )
    assert child is not None and child.status == "paused"
    with sqlite3.connect(supervisor._ledger_path) as connection:
        state = connection.execute(
            "SELECT state FROM supervisor_admissions WHERE run_id = ?",
            (admission.run_id,),
        ).fetchone()[0]
    assert state == "paused"


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


def test_heartbeat_signal_is_bounded_and_deduplicated(tmp_path: Path) -> None:
    records = {'alpha': _governance(tmp_path / 'alpha')}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.ingest_signal(
        'alpha', source='heartbeat', event_id='heartbeat:bucket-1', reason_code='periodic_check'
    )
    assert not runtime.ingest_signal(
        'alpha', source='heartbeat', event_id='heartbeat:bucket-1', reason_code='periodic_check'
    )
    assert [item.status for item in runtime.pulse(now_epoch=0.0)] == ['started']
    assert len(dispatched) == 1



def test_host_readiness_gate_pauses_without_explicit_test_space_verifier(tmp_path: Path) -> None:
    """A controlled host run must not start when its verifier is unavailable."""
    records = {"aquarium-zentrum": _governance(tmp_path / "aquarium-zentrum")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime

    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=lambda root, run_id: dispatched.append((root, run_id)),
        readiness_check=lambda _root: False,
    )
    assert runtime.ingest_signal(
        "aquarium-zentrum", source="heartbeat", event_id="controlled-live", reason_code="periodic_check"
    )

    outcomes = runtime.pulse(now_epoch=0.0)

    assert [(item.target_key, item.status) for item in outcomes] == [
        ("aquarium-zentrum", "verifier_unavailable"),
    ]
    assert dispatched == []
    assert supervisor.list_active_admissions() == []


def test_three_space_smoke_admits_only_enrolled_yolo_space(tmp_path: Path) -> None:
    """Nova and Finanzjunkie cannot consume the global YOLO admission slot."""
    records = {
        "nova": _governance(tmp_path / "spaces" / "nova", yolo=False, enrolled=True),
        "finanzjunkie": _governance(tmp_path / "spaces" / "finanzjunkie", yolo=True, enrolled=False),
        "aquarium-zentrum": _governance(tmp_path / "spaces" / "aquarium-zentrum", yolo=True, enrolled=True),
    }
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    assert not runtime.ingest_signal("nova", source="git", event_id="nova-smoke", reason_code="git_change")
    assert not runtime.ingest_signal("finanzjunkie", source="git", event_id="finance-smoke", reason_code="git_change")
    assert runtime.ingest_signal("aquarium-zentrum", source="git", event_id="aquarium-smoke", reason_code="git_change")
    outcomes = runtime.pulse(now_epoch=0.0)
    assert [(item.target_key, item.status) for item in outcomes] == [("aquarium-zentrum", "started")]
    assert [root for root, _run_id in dispatched] == [records["aquarium-zentrum"].canonical_root]
    assert [item.target_key for item in runtime.status()] == ["aquarium-zentrum"]
    assert [item["target_space_id"] for item in supervisor.list_active_admissions()] == [records["aquarium-zentrum"].space_id]
def test_numeric_heartbeat_checkpoint_survives_restart_without_global_tombstone(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    runtime = _runtime(supervisor, [])

    assert runtime.ingest_signal(
        "alpha", source="heartbeat", event_id="heartbeat:1", reason_code="periodic_check"
    )
    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM nova_supervision_signals"
        ).fetchone()[0] == 0

    restarted = _runtime(supervisor, [])
    assert not restarted.ingest_signal(
        "alpha", source="heartbeat", event_id="heartbeat:1", reason_code="periodic_check"
    )
    assert restarted.ingest_signal(
        "alpha", source="heartbeat", event_id="heartbeat:2", reason_code="periodic_check"
    )
    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        assert connection.execute(
            "SELECT latest_bucket FROM nova_supervision_heartbeat_checkpoints WHERE target_key='alpha'"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM nova_supervision_signals"
        ).fetchone()[0] == 0


def test_ingest_writes_redacted_append_only_ticker_event(tmp_path: Path) -> None:
    from nova.space_supervision_runtime import ticker_event_log_path

    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    runtime = _runtime(supervisor, [])

    assert runtime.ingest_signal(
        "alpha", source="git", event_id="commit-1", reason_code="git_change"
    )
    path = ticker_event_log_path(supervisor)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "commit-1" not in lines[0]
    assert "git_change" in lines[0]
    assert "alpha" in lines[0]


def test_provider_blocked_run_auto_resumes_only_after_verified_catalog_refresh(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.ingest_signal(
        "alpha", source="git", event_id="catalog-block-1", reason_code="git_change"
    )
    first = runtime.pulse(now_epoch=0.0)[0]
    assert first.status == "started"
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    store.set_run_status(first.run_id, "paused")
    store.append_event_once(
        first.run_id,
        "run.paused",
        {"reason": "no_eligible_model"},
        idempotency_key="test-no-eligible-model",
    )
    assert supervisor.reconcile_host_dispatch(
        records["alpha"].canonical_root,
        first.run_id,
        failure_reason="host_execution_returned",
    ) == "paused"

    # A catalog snapshot is the durable proof that an explicit live refresh
    # happened; a generic healthy/stale cache must not wake a failed run.
    store.save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=("deepseek-v4-flash",),
            healthy=True,
            source="ollama-cloud-api-live-verified",
        )
    )

    retry = runtime.pulse(now_epoch=1.0)

    assert [item.status for item in retry] == ["auto_resumed"]
    assert len(dispatched) == 2
    assert dispatched[1] == (records["alpha"].canonical_root, first.run_id)
    assert store.get_run(first.run_id).status == "running"
    assert any(
        event.event_type == "nova.supervisor.auto_resumed_after_recovery"
        for event in store.list_events(first.run_id)
    )


def test_provider_recovery_preserves_a_signal_received_while_the_run_was_paused(
    tmp_path: Path,
) -> None:
    """A recovered old run must not acknowledge a newer external intent."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.ingest_signal(
        "alpha", source="git", event_id="before-provider-pause", reason_code="git_change"
    )
    first = runtime.pulse(now_epoch=0.0)[0]
    assert first.status == "started"
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    store.set_run_status(first.run_id, "paused")
    store.append_event_once(
        first.run_id,
        "run.paused",
        {"reason": "no_eligible_model"},
        idempotency_key="provider-pause-before-new-signal",
    )
    assert supervisor.reconcile_host_dispatch(
        records["alpha"].canonical_root,
        first.run_id,
        failure_reason="host_execution_returned",
    ) == "paused"

    # This event happened after the prior run was paused. Resuming that run
    # cannot prove the new intent was dispatched, so it must stay pending.
    assert runtime.ingest_signal(
        "alpha", source="ci", event_id="after-provider-pause", reason_code="ci_change"
    )
    store.save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=("deepseek-v4-flash",),
            healthy=True,
            source="ollama-cloud-api-live-verified",
        )
    )

    recovered = runtime.pulse(now_epoch=1.0)

    assert [item.status for item in recovered] == ["auto_resumed"]
    assert len(dispatched) == 2
    assert runtime.status()[0].pending is True
def test_provider_recovery_defers_when_another_occupied_slot_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    first = supervisor.admit("alpha", {"kind": "maintenance"})
    assert first.capability is not None
    assert supervisor.start_admitted_run(
        first.capability,
        dispatcher=lambda root, run_id: dispatched.append((root, run_id)),
    )
    alpha_store = ProjectSwarmStore(records["alpha"].canonical_root)
    alpha_store.set_run_status(first.run_id, "paused")
    alpha_store.append_event_once(
        first.run_id,
        "run.paused",
        {"reason": "no_eligible_model"},
        idempotency_key="test-recovery-slot-pause",
    )
    assert supervisor.reconcile_host_dispatch(
        records["alpha"].canonical_root,
        first.run_id,
        failure_reason="host_execution_returned",
    ) == "paused"
    alpha_store.save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=("deepseek-v4-flash",),
            healthy=True,
            source="ollama-cloud-api-live-verified",
        )
    )

    # Build the interleaving that the partial unique index normally prevents:
    # free alpha temporarily, start beta, then restore alpha as paused before
    # recovery. The guard must defer without raising IntegrityError.
    with sqlite3.connect(supervisor._ledger_path) as connection:
        connection.execute("DROP INDEX idx_supervisor_one_active")
        connection.execute(
            "UPDATE supervisor_admissions SET state = 'completed' WHERE run_id = ?",
            (first.run_id,),
        )
        connection.commit()
    second = supervisor.admit("beta", {"kind": "maintenance"})
    assert second.capability is not None
    assert supervisor.start_admitted_run(
        second.capability,
        dispatcher=lambda root, run_id: dispatched.append((root, run_id)),
    )
    with sqlite3.connect(supervisor._ledger_path) as connection:
        connection.execute("DROP INDEX idx_supervisor_one_active")
        connection.execute(
            "UPDATE supervisor_admissions SET state = 'paused' WHERE run_id = ?",
            (first.run_id,),
        )
        connection.commit()
    # Slot contention must short-circuit before opening the child store.
    def fail_if_inspected(_project_root: Path):
        raise AssertionError("recovery inspected child before acquiring slot")

    monkeypatch.setattr(ProjectSwarmStore, "open_read_only", fail_if_inspected)

    result = supervisor.auto_resume_recoverable_run(
        "alpha",
        dispatcher=lambda root, run_id: dispatched.append((root, run_id)),
    )

    assert result == ("none", None)
    with sqlite3.connect(supervisor._ledger_path) as connection:
        states = dict(
            connection.execute(
                "SELECT target_key, state FROM supervisor_admissions"
            ).fetchall()
        )
    assert states["alpha"] == "paused"
    assert states["beta"] == "active"
def test_human_pause_is_never_auto_resumed_after_catalog_refresh(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    assert runtime.ingest_signal(
        "alpha", source="git", event_id="human-pause-1", reason_code="git_change"
    )
    first = runtime.pulse(now_epoch=0.0)[0]
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    store.set_run_status(first.run_id, "paused")
    store.append_event_once(
        first.run_id,
        "run.paused",
        {"reason": "human_pause"},
        idempotency_key="test-human-pause",
    )
    assert supervisor.reconcile_host_dispatch(
        records["alpha"].canonical_root,
        first.run_id,
        failure_reason="host_execution_returned",
    ) == "paused"
    store.save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=("deepseek-v4-flash",),
            healthy=True,
            source="ollama-cloud-api-live-verified",
        )
    )

    assert runtime.pulse(now_epoch=1.0) == ()
    assert len(dispatched) == 1
    assert store.get_run(first.run_id).status == "paused"


def test_provider_recovery_requires_catalog_refresh_after_pause(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    assert runtime.ingest_signal("alpha", source="ci", event_id="provider-recovery", reason_code="ci_failed")
    first = runtime.pulse(now_epoch=0.0)[0]
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    store.set_run_status(first.run_id, "paused")
    store.append_event_once(first.run_id, "run.paused", {"reason": "no_eligible_model"}, idempotency_key="stale-catalog")
    assert supervisor.reconcile_host_dispatch(records["alpha"].canonical_root, first.run_id, failure_reason="host_execution_returned") == "paused"
    store.save_model_catalog_snapshot(ModelCatalogSnapshot(provider="ollama-cloud", models=("deepseek-v4-flash",), healthy=True, source="ollama-cloud-api-live-verified", refreshed_at=datetime.now(timezone.utc) - timedelta(seconds=5)))
    assert runtime.pulse(now_epoch=1.0) == ()
    store.save_model_catalog_snapshot(ModelCatalogSnapshot(provider="ollama-cloud", models=("deepseek-v4-flash",), healthy=True, source="ollama-cloud-api-live-verified"))
    assert runtime.pulse(now_epoch=2.0)[0].status == "auto_resumed"


def test_autonomous_signal_persists_actionable_goal_for_child_run(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.ingest_signal(
        "alpha", source="ci", event_id="failed-build-1", reason_code="ci_failed"
    )
    outcome = runtime.pulse(now_epoch=0.0)[0]
    assert outcome.status == "started"

    child = ProjectSwarmStore(records["alpha"].canonical_root).get_run(outcome.run_id)
    assert child is not None
    assert child.metadata["goal"] == (
        "Autonomous maintenance for the enrolled Space: diagnose the failed CI "
        "signal, verify the project, and implement the smallest safe correction."
    )

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
        # The initializer creates the current table; remove it to model a legacy ledger.
        connection.execute("DROP TABLE IF EXISTS nova_supervision_signals")
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
    assert retry == ()
    assert len(dispatched) == 1
    _complete(supervisor, tmp_path / "alpha", first[0].run_id)
    next_run = runtime.pulse(now_epoch=900.0)
    assert [(outcome.target_key, outcome.status) for outcome in next_run] == [
        ("alpha", "unchanged"),
        ("beta", "started"),
    ]
    assert len(dispatched) == 2


def test_same_space_active_limit_does_not_starve_provider_recovery(tmp_path: Path) -> None:
    """A paused own run must not look like a second Space blocking wake-up."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.ingest_signal("alpha", source="git", event_id="first", reason_code="git_change")
    first = runtime.pulse(now_epoch=0.0)[0]
    assert first.status == "started"
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    store.set_run_status(first.run_id, "paused")
    store.append_event_once(
        first.run_id,
        "run.paused",
        {"reason": "no_eligible_model"},
        idempotency_key="same-space-provider-pause",
    )
    assert supervisor.reconcile_host_dispatch(
        records["alpha"].canonical_root,
        first.run_id,
        failure_reason="host_execution_returned",
    ) == "paused"

    # The paused provider run still owns the global slot. A new signal for the
    # same Space consequently records active_limit, but must remain wakeable.
    assert runtime.ingest_signal("alpha", source="ci", event_id="second", reason_code="ci_failed")
    assert runtime.pulse(now_epoch=1.0)[0].status == "active_limit"

    store.save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=("deepseek-v4-flash",),
            healthy=True,
            source="ollama-cloud-api-live-verified",
        )
    )
    retry = runtime.pulse(now_epoch=2.0)
    assert retry[0].status == "auto_resumed"
    assert len(dispatched) == 2
    assert store.get_run(first.run_id).status == "running"
def test_slot_waiter_wakes_after_active_run_finishes_before_retry_floor(tmp_path: Path) -> None:
    """A freed global slot wakes the next Space on the next host pulse."""
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    assert runtime.ingest_signal("alpha", source="git", event_id="slot-winner", reason_code="git_change")
    assert runtime.ingest_signal("beta", source="ci", event_id="slot-waiter", reason_code="ci_change")
    first = runtime.pulse(now_epoch=0.0)
    assert [item.status for item in first] == ["started", "active_limit"]
    _complete(supervisor, tmp_path / "alpha", first[0].run_id)
    # The old 15-minute floor must not delay beta once the slot is free.
    second = runtime.pulse(now_epoch=61.0)
    assert [item.status for item in second] == ["started"]
    assert len(dispatched) == 2


def test_events_and_heartbeat_dedupe_to_one_run_then_wake_after_slot_release(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha"), "beta": _governance(tmp_path / "beta")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    assert runtime.ingest_signal("alpha", source="git", event_id="commit-1", reason_code="git_change")
    assert runtime.ingest_signal("beta", source="ci", event_id="build-1", reason_code="ci_change")
    assert runtime.ingest_signal("alpha", source="heartbeat", event_id="heartbeat:42", reason_code="periodic_check")
    assert runtime.ingest_signal("beta", source="heartbeat", event_id="heartbeat:42", reason_code="periodic_check")
    assert not runtime.ingest_signal("alpha", source="heartbeat", event_id="heartbeat:42", reason_code="periodic_check")
    assert not runtime.ingest_signal("beta", source="heartbeat", event_id="heartbeat:42", reason_code="periodic_check")
    first = runtime.pulse(now_epoch=0.0)
    assert [(x.target_key, x.status) for x in first] == [("alpha", "started"), ("beta", "active_limit")]
    assert len(dispatched) == 1
    _complete(supervisor, tmp_path / "alpha", first[0].run_id)
    second = runtime.pulse(now_epoch=120.0)
    assert [(x.target_key, x.status) for x in second] == [("beta", "started")]
    assert len(dispatched) == 2
def test_new_signal_bypasses_active_slot_backoff_without_releasing_slot(
    tmp_path: Path,
) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    assert runtime.ingest_signal("alpha", source="git", event_id="a", reason_code="git_change")
    assert runtime.ingest_signal("beta", source="ci", event_id="b", reason_code="ci_change")
    first = runtime.pulse(now_epoch=0.0)
    assert [item.status for item in first] == ["started", "active_limit"]
    assert runtime.pulse(now_epoch=60.0) == ()
    assert runtime.ingest_signal("beta", source="kanban", event_id="b2", reason_code="kanban_change")
    immediate = runtime.pulse(now_epoch=61.0)
    assert [item.status for item in immediate] == ["active_limit"]
    assert len(dispatched) == 1


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


def test_status_reconciles_completed_child_run_for_presence_and_digest(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.ingest_signal(
        "alpha", source="git", event_id="commit-1", reason_code="git_change"
    )
    outcome = runtime.pulse(now_epoch=0.0)[0]
    assert outcome.status == "started"
    _complete(supervisor, tmp_path / "alpha", outcome.run_id)

    status = runtime.status()

    assert len(status) == 1
    assert status[0].target_key == "alpha"
    assert status[0].pending is False
    assert status[0].last_outcome == "completed"


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


def test_code_owned_signal_reaches_active_runtime_once(tmp_path: Path) -> None:
    from nova.space_supervision_runtime import (
        clear_active_runtime,
        emit_code_owned_signal,
        install_active_runtime,
    )

    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    runtime = _runtime(supervisor, [])
    install_active_runtime(runtime)
    try:
        assert emit_code_owned_signal(
            "alpha", source="git", event_id="commit-1", reason_code="git_change"
        ) is True
        assert emit_code_owned_signal(
            "alpha", source="git", event_id="commit-1", reason_code="git_change"
        ) is False
        assert runtime.status()[0].pending is True
    finally:
        clear_active_runtime(runtime)


def test_admission_backoff_survives_runtime_restart_without_dropping_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    first_supervisor = _supervisor(tmp_path, records)
    first_runtime = _runtime(first_supervisor, [])

    def reject(_target: str, _intent: object) -> SupervisorAdmission:
        return SupervisorAdmission("rejected", None, None, None, "temporary_gate")

    monkeypatch.setattr(first_supervisor, "admit", reject)
    assert first_runtime.ingest_signal(
        "alpha", source="git", event_id="restart-backoff", reason_code="git_change"
    )
    assert [item.status for item in first_runtime.pulse(now_epoch=100.0)] == ["admission_rejected"]

    second_supervisor = _supervisor(tmp_path, records)
    second_runtime = _runtime(second_supervisor, [])
    calls = {"count": 0}

    def reject_after_restart(_target: str, _intent: object) -> SupervisorAdmission:
        calls["count"] += 1
        return SupervisorAdmission("rejected", None, None, None, "temporary_gate")

    monkeypatch.setattr(second_supervisor, "admit", reject_after_restart)
    assert second_runtime.pulse(now_epoch=101.0) == ()
    assert calls["count"] == 0
    assert second_runtime.status()[0].pending is True
    assert [item.status for item in second_runtime.pulse(now_epoch=1000.0)] == ["admission_rejected"]
    assert calls["count"] == 1


def test_code_owned_signal_uses_durable_fallback_without_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from nova.space_supervision_runtime import (
        NovaSpaceSupervisionRuntime,
        clear_active_runtime,
        emit_code_owned_signal,
    )

    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    monkeypatch.setattr(
        "nova.space_supervisor.get_production_managed_space_supervisor",
        lambda: supervisor,
    )
    clear_active_runtime()

    assert emit_code_owned_signal(
        "alpha", source="ci", event_id="ci-1", reason_code="ci_change"
    ) is True
    assert emit_code_owned_signal(
        "alpha", source="ci", event_id="ci-1", reason_code="ci_change"
    ) is False
    assert supervisor.list_active_admissions() == []
    status = NovaSpaceSupervisionRuntime(
        supervisor=supervisor, dispatch_run=lambda *_args: (_ for _ in ()).throw(AssertionError())
    ).status()
    assert status[0].pending is True


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


def test_admission_rejection_is_backed_off_without_dropping_pending_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    runtime = _runtime(supervisor, [])
    calls = {"count": 0}

    def reject(_target: str, _intent: object) -> SupervisorAdmission:
        calls["count"] += 1
        return SupervisorAdmission("rejected", None, None, None, "temporary_gate")

    monkeypatch.setattr(supervisor, "admit", reject)
    assert runtime.ingest_signal("alpha", source="git", event_id="a", reason_code="git_change")
    first = runtime.pulse(now_epoch=100.0)
    assert [item.status for item in first] == ["admission_rejected"]
    assert calls["count"] == 1
    # The signal remains pending, but a normal 60-second host tick must not
    # hammer the same failed admission gate.
    assert runtime.pulse(now_epoch=101.0) == ()
    assert calls["count"] == 1
    # After the bounded window it is retried, still without losing the signal.
    second = runtime.pulse(now_epoch=1000.0)
    assert [item.status for item in second] == ["admission_rejected"]
    assert calls["count"] == 2
    assert runtime.status()[0].pending is True

def test_admission_failure_backoff_prevents_heartbeat_spin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A transient admission error is retried only after the 15-minute floor."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    original_admit = supervisor.admit
    calls = {"count": 0}

    def fail_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.OperationalError("transient ledger busy")
        return original_admit(*args, **kwargs)

    monkeypatch.setattr(supervisor, "admit", fail_once)
    assert runtime.ingest_signal(
        "alpha", source="heartbeat", event_id="admission-backoff-1", reason_code="periodic_check"
    )
    assert [item.status for item in runtime.pulse(now_epoch=0.0)] == ["admission_failed"]
    assert runtime.pulse(now_epoch=1.0) == ()
    assert [item.status for item in runtime.pulse(now_epoch=899.0)] == []
    assert [item.status for item in runtime.pulse(now_epoch=900.0)] == ["started"]
    assert calls["count"] == 2
    assert len(dispatched) == 1



def test_recovery_ledger_error_fails_closed_without_crashing_the_host_pulse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A transient recovery read failure must leave pending work safely waiting."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    assert runtime.ingest_signal(
        "alpha", source="ci", event_id="recovery-ledger-busy", reason_code="ci_change"
    )

    def unavailable_recovery(*_args: object, **_kwargs: object) -> tuple[str, str | None]:
        raise sqlite3.OperationalError("temporary supervisor ledger busy")

    monkeypatch.setattr(supervisor, "auto_resume_recoverable_run", unavailable_recovery)

    first = runtime.pulse(now_epoch=0.0)

    assert [(item.target_key, item.status) for item in first] == [
        ("alpha", "admission_failed"),
    ]
    assert dispatched == []
    assert runtime.status()[0].pending is True
    # The persisted checkpoint prevents the one-minute host heartbeat from
    # retrying a known unavailable recovery boundary immediately.
    assert runtime.pulse(now_epoch=1.0) == ()


def test_recovery_failure_does_not_crash_when_the_backoff_checkpoint_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A broken recovery ledger cannot escalate into a host-tick failure."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    runtime = _runtime(supervisor, [])
    assert runtime.ingest_signal(
        "alpha", source="ci", event_id="recovery-and-checkpoint-busy", reason_code="ci_change"
    )

    def unavailable_recovery(*_args: object, **_kwargs: object) -> tuple[str, str | None]:
        raise sqlite3.OperationalError("temporary supervisor ledger busy")

    def unavailable_checkpoint(*_args: object, **_kwargs: object) -> bool:
        raise sqlite3.OperationalError("temporary supervision checkpoint busy")

    monkeypatch.setattr(supervisor, "auto_resume_recoverable_run", unavailable_recovery)
    monkeypatch.setattr(runtime, "_mark_retryable_outcome", unavailable_checkpoint)

    assert [(item.target_key, item.status) for item in runtime.pulse(now_epoch=0.0)] == [
        ("alpha", "admission_failed"),
    ]

def test_global_slot_race_writes_redacted_skipped_slot_evidence(tmp_path: Path) -> None:
    """The loser remains pending and the feed records why it was skipped."""
    from nova.space_supervision_runtime import append_ticker_outcomes

    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    assert runtime.ingest_signal("alpha", source="git", event_id="winner-1", reason_code="git_change")
    assert runtime.ingest_signal("beta", source="ci", event_id="loser-1", reason_code="ci_change")

    outcomes = runtime.pulse(now_epoch=0.0)
    append_ticker_outcomes(supervisor, outcomes, observed_at=0.0)

    assert [(item.target_key, item.status) for item in outcomes] == [
        ("alpha", "started"), ("beta", "active_limit")
    ]
    assert len(dispatched) == 1
    lines = (tmp_path / "ticker_events.jsonl").read_text(encoding="utf-8").splitlines()
    loser = [line for line in lines if '"space":"beta"' in line and '"stage":"handled"' in line]
    assert loser and '"reason":"skipped_slot_occupied"' in loser[-1]
    assert '"reason":"active_limit"' not in loser[-1]
    assert str(tmp_path) not in loser[-1]
    assert '"status":"failed"' in loser[-1]


def test_exactly_once_provider_recovery_requires_fresh_catalog_and_governance(tmp_path: Path) -> None:
    records = {
        "nova": _governance(tmp_path / "spaces" / "nova", yolo=False, enrolled=True),
        "finanzjunkie": _governance(tmp_path / "spaces" / "finanzjunkie", yolo=True, enrolled=False),
        "aquarium-zentrum": _governance(tmp_path / "spaces" / "aquarium-zentrum"),
    }
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    assert not runtime.ingest_signal("nova", source="git", event_id="nova-intent", reason_code="git_change")
    assert not runtime.ingest_signal("finanzjunkie", source="ci", event_id="finance-intent", reason_code="ci_change")
    assert runtime.ingest_signal("aquarium-zentrum", source="git", event_id="aquarium-intent", reason_code="git_change")
    first = runtime.pulse(now_epoch=0.0)[0]
    assert first.status == "started"
    store = ProjectSwarmStore(records["aquarium-zentrum"].canonical_root)
    store.set_run_status(first.run_id, "paused")
    store.append_event_once(first.run_id, "run.paused", {"reason": "model_chain_exhausted"}, idempotency_key="provider-pause-1")
    assert supervisor.reconcile_host_dispatch(records["aquarium-zentrum"].canonical_root, first.run_id, failure_reason="host_execution_returned") == "paused"
    store.save_model_catalog_snapshot(ModelCatalogSnapshot(provider="ollama-cloud", models=("deepseek-v4-flash",), healthy=True, source="ollama-cloud-api-live-verified"))
    assert [item.status for item in runtime.pulse(now_epoch=1.0)] == ["auto_resumed"]
    assert len(dispatched) == 2
    assert runtime.pulse(now_epoch=2.0) == ()
    store.set_run_status(first.run_id, "paused")
    store.append_event_once(first.run_id, "run.paused", {"reason": "model_chain_exhausted"}, idempotency_key="provider-pause-2")
    assert supervisor.reconcile_host_dispatch(records["aquarium-zentrum"].canonical_root, first.run_id, failure_reason="host_execution_returned") == "paused"
    store.save_model_catalog_snapshot(ModelCatalogSnapshot(provider="ollama-cloud", models=("deepseek-v4-flash",), healthy=True, source="ollama-cloud-api-live-verified", refreshed_at=datetime.now(timezone.utc) - timedelta(seconds=5)))
    waiting = runtime.pulse(now_epoch=3.0)
    assert len(waiting) == 1
    assert waiting[0].status == "waiting_for_catalog"
    records["aquarium-zentrum"] = replace(records["aquarium-zentrum"], enrolled=False, revision=2)
    store.save_model_catalog_snapshot(ModelCatalogSnapshot(provider="ollama-cloud", models=("deepseek-v4-flash",), healthy=True, source="ollama-cloud-api-live-verified", refreshed_at=datetime.now(timezone.utc) + timedelta(seconds=1)))
    assert runtime.pulse(now_epoch=4.0) == ()
    assert supervisor.auto_resume_recoverable_run("aquarium-zentrum", dispatcher=lambda root, run_id: dispatched.append((root, run_id))) == ("none", None)
    assert store.get_run(first.run_id).status == "paused"
    assert len(dispatched) == 2

def test_paused_space_heartbeat_stays_pending_and_never_dispatches_after_revocation(
    tmp_path: Path,
) -> None:
    """A paused worker cannot be woken by a heartbeat after enrollment is revoked."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.ingest_signal(
        "alpha", source="heartbeat", event_id="heartbeat:1", reason_code="periodic_check"
    )
    first = runtime.pulse(now_epoch=0.0)[0]
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    store.set_run_status(first.run_id, "paused")
    store.append_event_once(
        first.run_id, "run.paused", {"reason": "no_eligible_model"},
        idempotency_key="paused-before-revoke",
    )
    assert supervisor.reconcile_host_dispatch(
        records["alpha"].canonical_root, first.run_id,
        failure_reason="host_execution_returned",
    ) == "paused"
    assert runtime.ingest_signal(
        "alpha", source="heartbeat", event_id="heartbeat:2", reason_code="periodic_check"
    )
    records["alpha"] = replace(records["alpha"], enrolled=False)

    outcome = runtime.pulse(now_epoch=901.0)

    assert [item.status for item in outcome] == ["ineligible"]
    assert len(dispatched) == 1
    assert runtime.status()[0].pending is True



def test_governance_resolver_failure_keeps_ticker_alive_and_backs_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A registry outage never becomes an unguarded admission or ticker crash."""
    root = tmp_path / "alpha"
    governance = _governance(root)
    calls = {"count": 0}

    def resolve(target: str):
        assert target == "alpha"
        calls["count"] += 1
        return governance

    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=resolve,
    )
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    assert runtime.ingest_signal(
        "alpha", source="git", event_id="registry-outage", reason_code="git_change"
    )

    def unavailable(_target: str):
        calls["count"] += 1
        raise sqlite3.OperationalError("governance registry temporarily unavailable")
    monkeypatch.setattr(supervisor, "current_governance", unavailable)

    first = runtime.pulse(now_epoch=0.0)
    assert [(item.target_key, item.status) for item in first] == [
        ("alpha", "governance_unavailable")
    ]
    assert runtime.status()[0].pending is True
    assert dispatched == []

    # A normal host heartbeat must not repeatedly probe a failed registry.
    # Capture the failed lookup count so this remains an explicit regression
    # guard even if setup starts consulting the resolver in the future.
    failed_lookup_count = calls["count"]
    assert runtime.pulse(now_epoch=1.0) == ()
    assert calls["count"] == failed_lookup_count

    # Once the bounded floor expires the lookup may be retried, but the
    # still-unavailable registry remains fail-closed and never dispatches.
    assert runtime.pulse(now_epoch=901.0)[0].status == "governance_unavailable"
    assert dispatched == []

def test_ineligible_governance_lookup_is_bounded_while_pending_signal_waits(
    tmp_path: Path,
) -> None:
    """Revocation must not make the host ticker probe governance every minute."""
    root = tmp_path / "alpha"
    records = {"alpha": _governance(root)}
    calls = {"count": 0}

    def resolve(target: str):
        calls["count"] += 1
        return records.get(target)

    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=resolve,
    )
    runtime = _runtime(supervisor, [])
    assert runtime.ingest_signal(
        "alpha", source="git", event_id="revoked", reason_code="git_change"
    )
    # Revoke YOLO/enrollment before the accepted event reaches admission.
    records["alpha"] = replace(records["alpha"], yolo=False, enrolled=False)

    first = runtime.pulse(now_epoch=0.0)
    assert [item.status for item in first] == ["ineligible"]
    failed_lookup_count = calls["count"]

    # A normal one-minute heartbeat is quiet; the signal remains pending for
    # an explicit fresh signal after governance is restored.
    assert runtime.pulse(now_epoch=1.0) == ()
    assert calls["count"] == failed_lookup_count
    assert runtime.status()[0].pending is True

    # The bounded retry floor permits a later governance re-check, still
    # without dispatching while the Space is revoked.
    assert runtime.pulse(now_epoch=900.0)[0].status == "ineligible"
    assert calls["count"] == failed_lookup_count + 1


def test_cross_source_same_yolo_intent_coalesces_to_one_admission(tmp_path: Path) -> None:
    """Git and CI edges for one goal share one governed host run."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)

    assert runtime.ingest_signal("alpha", source="git", event_id="commit-42", reason_code="ci_change")
    assert runtime.ingest_signal("alpha", source="ci", event_id="build-42", reason_code="ci_change")
    outcomes = runtime.pulse(now_epoch=100.0)

    assert [(item.target_key, item.status) for item in outcomes] == [("alpha", "started")]
    assert len(dispatched) == 1
    run = ProjectSwarmStore(records["alpha"].canonical_root).get_run(dispatched[0][1])
    assert run is not None
    assert run.metadata["goal"].startswith("Autonomous maintenance for the enrolled Space")
    assert run.metadata["nova_supervisor"]["target_space_id"] == records["alpha"].space_id

def test_git_kanban_ci_events_share_one_pending_intent_across_global_slot_pause(tmp_path: Path) -> None:
    """All three edge sources coalesce, wait behind one slot, then start once."""
    records = {"alpha": _governance(tmp_path / "alpha"), "beta": _governance(tmp_path / "beta")}
    supervisor = _supervisor(tmp_path, records)
    dispatched: list[tuple[Path, str]] = []
    runtime = _runtime(supervisor, dispatched)
    assert runtime.ingest_signal("alpha", source="git", event_id="alpha-commit", reason_code="git_change")
    for source, event_id, reason in (
        ("git", "beta-commit", "git_change"),
        ("kanban", "beta-card", "kanban_change"),
        ("ci", "beta-build", "ci_failed"),
    ):
        assert runtime.ingest_signal("beta", source=source, event_id=event_id, reason_code=reason)

    first = runtime.pulse(now_epoch=100.0)
    assert [(item.target_key, item.status) for item in first] == [
        ("alpha", "started"), ("beta", "active_limit")
    ]
    assert len(dispatched) == 1
    _complete(supervisor, records["alpha"].canonical_root, first[0].run_id)

    second = runtime.pulse(now_epoch=161.0)
    assert [(item.target_key, item.status) for item in second] == [("beta", "started")]
    assert len(dispatched) == 2
    assert supervisor.list_active_admissions()[0]["run_id"] == dispatched[1][1]

def test_restart_preserves_merged_cross_source_pending_intent_for_one_resume(tmp_path: Path) -> None:
    """A restart keeps Git/Kanban intent durable and dispatches it once."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    first_supervisor = _supervisor(tmp_path, records)
    first_runtime = _runtime(first_supervisor, [])
    assert first_runtime.ingest_signal("alpha", source="git", event_id="restart-git", reason_code="git_change")
    assert first_runtime.ingest_signal("alpha", source="kanban", event_id="restart-card", reason_code="kanban_change")

    dispatched: list[tuple[Path, str]] = []
    restarted_supervisor = _supervisor(tmp_path, records)
    restarted_runtime = _runtime(restarted_supervisor, dispatched)
    outcome = restarted_runtime.pulse(now_epoch=100.0)

    assert [(item.target_key, item.status) for item in outcome] == [("alpha", "started")]
    assert len(dispatched) == 1
    assert restarted_runtime.status()[0].pending is False
    assert restarted_runtime.pulse(now_epoch=101.0) == ()
    assert len(dispatched) == 1

def test_feedback_adapter_is_published_as_redacted_entity_event(tmp_path: Path) -> None:
    from nova.feedback_adapter import LocalNovaFeedbackAdapter
    from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime

    records = {"aquarium-zentrum": _governance(tmp_path / "aquarium-zentrum")}
    supervisor = _supervisor(tmp_path, records)
    sent: list[str] = []
    events: list[dict[str, object]] = []
    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=lambda *_: None,
        governance_snapshots=lambda: records,
        feedback_adapter=LocalNovaFeedbackAdapter(lambda message: sent.append(message) or "ack"),
        entity_event_sink=events.append,
    )

    outcomes = runtime.pulse(now_epoch=100.0)

    assert outcomes and outcomes[0].status == "started"
    assert sent and "aquarium-zentrum" in sent[0]
    assert events and events[0]["type"] == "nova_feedback"
    assert events[0]["payload"]["status"] == "acked"
    assert "canonical_root" not in str(events[0])


def test_feedback_timeout_and_offline_are_entity_statuses(tmp_path: Path) -> None:
    import time
    from nova.feedback_adapter import LocalNovaFeedbackAdapter
    from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime, SupervisionPulseOutcome

    records = {"nova": _governance(tmp_path / "nova")}
    supervisor = _supervisor(tmp_path, records)
    events: list[dict[str, object]] = []
    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=lambda *_: None,
        feedback_adapter=LocalNovaFeedbackAdapter(None),
        entity_event_sink=events.append,
    )
    started = time.monotonic()
    runtime._publish_feedback((SupervisionPulseOutcome("nova", "failed"),))
    assert time.monotonic() - started < 0.1
    assert events[0]["payload"]["status"] == "offline"






def test_web_server_feedback_sink_redacts_entity_payload(monkeypatch) -> None:
    import cli.web_server as web_server
    recorded: list[object] = []

    class FakeStore:
        def record_entity_event(self, event):
            recorded.append(event)

    monkeypatch.setattr("nova.autobiography.AutobiographyStore", FakeStore)
    web_server._persist_nova_feedback_entity({
        "payload": {"target_key": "space", "run_id": "run", "status": "offline", "detail": "safe" * 100},
    })
    assert recorded
    assert len(recorded[0].payload["detail"]) <= 200
