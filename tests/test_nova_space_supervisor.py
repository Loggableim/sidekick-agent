from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import pickle
import sqlite3
import threading
from uuid import uuid4

import pytest

import nova.space_supervisor as space_supervisor_module
from nova.space_supervisor import (
    DASHBOARD_ACTOR_RE,
    ManagedSpaceGovernance,
    ManagedSpaceSupervisor,
    managed_space_execution_options_for_run,
)
from cli.swarm_host import (
    OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE,
    SidekickSwarmService,
    SwarmExecutionOptions,
)
from swarm_core.engine import PreCompletionContext
from swarm_core.models import ModelCatalogSnapshot
from swarm_core.store import ProjectSwarmStore


_DASHBOARD_ACTOR = "dashboard:" + ("a" * 64)


def _governance(root: Path, *, space_id: str | None = None, **overrides: object) -> ManagedSpaceGovernance:
    values: dict[str, object] = {
        "space_id": space_id or str(uuid4()),
        "canonical_root": root.resolve(),
        "root_fingerprint": "",
        "yolo": True,
        "enrolled": True,
        "revision": 7,
        "policy_identity": "policy:target-v1",
    }
    values.update(overrides)
    return ManagedSpaceGovernance.from_values(**values)


def _supervisor(tmp_path: Path, records: dict[str, ManagedSpaceGovernance]) -> ManagedSpaceSupervisor:
    return ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
    )


def _admit(supervisor: ManagedSpaceSupervisor, target: str = "alpha"):
    return supervisor.admit(target, {"goal": "repair flaky test", "kind": "maintenance"})


def _resume_for_completion(store: ProjectSwarmStore, run_id: str):
    """Model an explicit host resume before a worker can complete a run."""
    run = store.get_run(run_id)
    assert run is not None
    return store.resume_run(run_id) if run.status == "paused" else run


def test_live_admission_rejects_duplicate_space_identity_across_roots(tmp_path: Path) -> None:
    shared_space_id = str(uuid4())
    records = {
        "alpha": _governance(tmp_path / "alpha", space_id=shared_space_id),
        "beta": _governance(tmp_path / "beta", space_id=shared_space_id),
    }
    supervisor = _supervisor(tmp_path, records)

    first = _admit(supervisor, "alpha")
    assert first.status == "created"

    duplicate = supervisor.admit(
        "beta", {"goal": "repair a different project", "kind": "maintenance"}
    )
    assert duplicate.status == "rejected"
    assert duplicate.reason == "space_identity_conflict"
    assert duplicate.admission_id == first.admission_id
    assert len(supervisor.list_active_admissions()) == 1


def test_global_ledger_allows_only_one_active_target_across_concurrent_spaces(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    first = _supervisor(tmp_path, records)
    second = _supervisor(tmp_path, records)
    barrier = threading.Barrier(2)

    def admit(target: str):
        barrier.wait()
        return _admit(first if target == "alpha" else second, target)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(admit, ("alpha", "beta")))

    assert [result.status for result in results].count("created") == 1
    assert [result.reason for result in results].count("active_limit") == 1
    assert len(first.list_active_admissions()) == 1


def test_paused_global_slot_requires_explicit_terminalization_before_cross_space_takeover(
    tmp_path: Path,
) -> None:
    """A paused child keeps ownership until an authenticated human closes it."""
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    first = _admit(supervisor, "alpha")
    assert first.capability is not None
    assert supervisor.start_admitted_run(
        first.capability,
        dispatcher=lambda _root, _run_id: False,
    ) is False
    alpha_store = ProjectSwarmStore(records["alpha"].canonical_root)
    alpha_run = alpha_store.get_run(first.run_id)
    assert alpha_run is not None and alpha_run.status == "paused"
    assert supervisor.list_active_admissions()[0]["state"] == "paused"

    blocked = _admit(supervisor, "beta")
    assert blocked.status == "rejected"
    assert blocked.reason == "active_limit"
    assert not (records["beta"].canonical_root / ".swarm").exists()

    assert supervisor.cancel(first.admission_id, actor=_DASHBOARD_ACTOR) is True
    assert supervisor.list_active_admissions() == []
    assert _admit(supervisor, "beta").status == "created"

def test_restart_coalesces_the_same_durable_target_intent_admission(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    created = _admit(_supervisor(tmp_path, records))
    resumed = _admit(_supervisor(tmp_path, records))

    assert created.status == "created"
    assert resumed.status == "coalesced"
    assert resumed.run_id == created.run_id
    assert resumed.capability is None


def test_same_intent_after_space_rebind_is_not_coalesced_with_old_run(tmp_path: Path) -> None:
    """A root/governance revision creates a new exactly-once identity boundary."""
    space_id = str(uuid4())
    old_root = tmp_path / "alpha-old"
    new_root = tmp_path / "alpha-new"
    records = {"alpha": _governance(old_root, space_id=space_id, revision=7)}
    supervisor = _supervisor(tmp_path, records)

    first = _admit(supervisor)
    assert first.status == "created"
    assert supervisor.cancel(first.admission_id, actor=_DASHBOARD_ACTOR)

    records["alpha"] = _governance(new_root, space_id=space_id, revision=8)
    rebound = _admit(supervisor)

    assert rebound.status == "created"
    assert rebound.run_id != first.run_id
    assert rebound.capability is not None
    assert rebound.capability._canonical_root == new_root.resolve()


def test_ticker_lease_is_singleton_and_expiry_is_audited_as_orphaned(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    first = _supervisor(tmp_path, records)
    second = _supervisor(tmp_path, records)

    lease_id = first.acquire_ticker_lease("dashboard:first", now=100.0, ttl_seconds=20.0)
    assert lease_id
    assert second.acquire_ticker_lease("dashboard:second", now=110.0, ttl_seconds=20.0) is None
    assert first.heartbeat_ticker_lease(lease_id, "dashboard:first", now=110.0, ttl_seconds=20.0)

    replacement = second.acquire_ticker_lease("dashboard:second", now=131.0, ttl_seconds=20.0)
    assert replacement and replacement != lease_id
    leases = {row["lease_id"]: row for row in second.list_ticker_leases()}
    assert leases[lease_id]["state"] == "orphaned"
    assert leases[lease_id]["terminal_reason"] == "lease_expired"
    assert leases[replacement]["state"] == "active"


def test_dead_ticker_host_is_reclaimed_before_ttl_without_preempting_live_owner(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    lease_id = supervisor.acquire_ticker_lease("dashboard:999999:dead", now=100.0, ttl_seconds=120.0)
    assert lease_id

    assert supervisor.reconcile_stale_ticker_leases(lambda _owner: False, now=101.0) == (lease_id,)
    row = next(item for item in supervisor.list_ticker_leases() if item["lease_id"] == lease_id)
    assert row["state"] == "orphaned"
    assert row["terminal_reason"] == "host_restart_recovered"

    live = supervisor.acquire_ticker_lease("dashboard:123:live", now=102.0, ttl_seconds=120.0)
    assert live
    assert supervisor.reconcile_stale_ticker_leases(lambda _owner: True, now=103.0) == ()
    assert next(item for item in supervisor.list_ticker_leases() if item["lease_id"] == live)["state"] == "active"


def test_ticker_lease_does_not_resume_or_mutate_a_child_run(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    child = ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id)
    assert child is not None and child.status == "paused"

    lease_id = supervisor.acquire_ticker_lease("dashboard:first", now=100.0)
    assert lease_id
    assert ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id).status == "paused"


def test_stale_host_recovery_pauses_without_resuming_after_dead_owner(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    store.resume_run(admission.run_id)
    assert store.claim_run_execution_lease(admission.run_id, "dashboard:999999:dead")

    recovered = supervisor.reconcile_stale_host_runs(lambda _owner: False)

    assert recovered == (admission.run_id,)
    assert store.get_run(admission.run_id).status == "paused"
    assert store.get_run_execution_lease_owner(admission.run_id) is None
    assert supervisor.list_active_admissions()[0]["state"] == "paused"
    assert any(
        event.event_type == "run.execution_lease_recovered_after_host_restart"
        for event in store.list_events(admission.run_id)
    )


def test_stale_host_recovery_releases_dead_lease_from_already_paused_child(
    tmp_path: Path,
) -> None:
    """A paused child must not strand an active admission behind a dead lease."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    assert store.claim_run_execution_lease(admission.run_id, "dashboard:999999:dead")
    # Admission provisioning leaves the child paused; a host may still claim
    # its lease before a provider/policy pause is durably observed. The
    # admission remains active until host reconciliation, so this is the exact
    # crash window that otherwise blocks the global one-run slot forever.

    recovered = supervisor.reconcile_stale_host_runs(lambda _owner: False)

    assert recovered == (admission.run_id,)
    assert store.get_run(admission.run_id).status == "paused"
    assert store.get_run_execution_lease_owner(admission.run_id) is None
    assert supervisor.list_active_admissions()[0]["state"] == "paused"


def test_stale_host_recovery_pauses_active_child_when_worker_never_claimed_lease(
    tmp_path: Path,
) -> None:
    """A crash before lease claim must not strand the global run slot."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    store.resume_run(admission.run_id)
    assert store.get_run_execution_lease_owner(admission.run_id) is None

    recovered = supervisor.reconcile_stale_host_runs(lambda _owner: True)

    assert recovered == (admission.run_id,)
    assert store.get_run(admission.run_id).status == "paused"
    assert supervisor.list_active_admissions()[0]["state"] == "paused"
    assert any(
        event.event_type == "nova.supervisor.paused"
        and event.payload.get("reason") == "host_restart_recovered"
        for event in store.list_events(admission.run_id)
    )


def test_ticker_lease_race_has_one_winner_across_process_like_supervisors(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisors = [_supervisor(tmp_path, records) for _ in range(2)]

    with ThreadPoolExecutor(max_workers=2) as workers:
        leases = list(
            workers.map(
                lambda item: item[0].acquire_ticker_lease(item[1], now=100.0),
                zip(supervisors, ("dashboard:a", "dashboard:b")),
            )
        )

    assert sum(lease is not None for lease in leases) == 1



def test_failed_child_provisioning_abandons_reservation_and_releases_global_slot(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }

    def failing_store(_root: Path):
        raise OSError("child store unavailable")

    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
        child_store_factory=failing_store,
    )

    with pytest.raises(OSError, match="child store unavailable"):
        _admit(supervisor, "alpha")

    assert supervisor.list_active_admissions() == []
    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        row = connection.execute(
            "SELECT state, terminal_actor FROM supervisor_admissions"
        ).fetchone()
        audit = connection.execute(
            "SELECT event_type, actor, reason FROM supervisor_audit ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
    assert row == ("abandoned", space_supervisor_module.SYSTEM_SPACE_LIFECYCLE_ACTOR)
    assert audit == (
        "abandoned",
        space_supervisor_module.SYSTEM_SPACE_LIFECYCLE_ACTOR,
        "child_provisioning_failed",
    )

def test_capability_is_opaque_nonserializable_and_tampering_pauses_the_child(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    assert admission.capability is not None
    capability = admission.capability

    assert not hasattr(capability, "__dict__")
    with pytest.raises((AttributeError, TypeError)):
        capability._intent_digest = "0" * 64
    with pytest.raises(TypeError):
        pickle.dumps(capability)

    foreign_root = tmp_path / "foreign"
    object.__setattr__(capability, "_canonical_root", foreign_root)
    assert supervisor.revalidate_action_boundary(capability) is False
    run = ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id)
    assert run is not None and run.status == "paused"
    assert not (foreign_root / ".swarm").exists()
    assert any(event.event_type == "nova.supervisor.paused" for event in ProjectSwarmStore(records["alpha"].canonical_root).list_events(admission.run_id))


def test_revocation_blocks_resume_and_changed_root_blocks_precompletion(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    first = _admit(supervisor)
    first_run = ProjectSwarmStore(records["alpha"].canonical_root).get_run(first.run_id)
    assert first_run is not None

    records["alpha"] = replace(records["alpha"], yolo=False)
    blocked = supervisor.execution_options_for_run(records["alpha"].canonical_root, first_run)
    assert blocked.blocked_reason == "governance_revoked"
    assert ProjectSwarmStore(records["alpha"].canonical_root).get_run(first.run_id).status == "paused"

    assert supervisor.cancel(first.admission_id, actor=_DASHBOARD_ACTOR) is True
    records["alpha"] = _governance(tmp_path / "alpha", space_id=records["alpha"].space_id)
    second = supervisor.admit("alpha", {"goal": "repair a second flaky test", "kind": "maintenance"})
    records["alpha"] = _governance(tmp_path / "moved-alpha", space_id=records["alpha"].space_id)
    hook = supervisor.pre_completion_hook_for_run(second.run_id)
    child_store = ProjectSwarmStore(tmp_path / "alpha")
    child_run = child_store.get_run(second.run_id)
    assert child_run is not None
    outcome = hook.run(
        PreCompletionContext(
            run=child_run,
            project_root=tmp_path / "alpha",
            store=child_store,
            goal="repair flaky test",
            pack="coding-team",
            autonomy="autonomous",
            call_count=1,
            decision="verified",
            evidence={},
        )
    )
    assert outcome.continue_completion is False
    assert outcome.pause_reason == "root_mismatch"
    assert child_store.get_run(second.run_id).status == "paused"


def test_managed_pre_completion_rechecks_production_verifier_contract(tmp_path: Path, monkeypatch) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    run = store.get_run(admission.run_id)
    assert run is not None
    store.record_workflow_role_checkpoint(
        admission.run_id,
        "verifier",
        model=None,
        data={"work": "verified", "evidence": ["verify:old"], "decision": "verified", "provenance": {"adapter": "production-read-only", "mode": "read_only"}},
    )
    store.record_workflow_role_checkpoint(
        admission.run_id,
        "builder",
        model="minimax-m3",
        data={"work": "build", "evidence": ["build:1"], "decision": "approve"},
    )
    store.record_workflow_role_checkpoint(
        admission.run_id,
        "critic",
        model="minimax-m3",
        data={"work": "critique", "evidence": ["critic:1"], "decision": "approve"},
    )
    monkeypatch.setattr(
        "nova.production_verifier.ProductionReadOnlyVerifier.verify",
        lambda self, request: type("Result", (), {"decision": "verification_unavailable"})(),
    )
    outcome = supervisor.pre_completion_hook_for_run(admission.run_id).run(
        PreCompletionContext(
            run=run,
            project_root=records["alpha"].canonical_root,
            store=store,
            goal="maintenance",
            pack="coding-team",
            autonomy="autonomous",
            call_count=1,
            decision="verified",
            evidence={},
        )
    )
    assert outcome.continue_completion is False
    assert outcome.pause_reason == "verification_not_verified"
    assert store.get_run(admission.run_id).status == "paused"


def test_pre_completion_pauses_when_verifier_is_unavailable_or_not_positive(
    tmp_path: Path,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    run = store.get_run(admission.run_id)
    assert run is not None
    store.record_workflow_role_checkpoint(
        admission.run_id,
        "verifier",
        model=None,
        data={
            "work": "No project inspection adapter is configured",
            "evidence": ["verifier:local:test"],
            "decision": "verification_unavailable",
            "provenance": {"adapter": "default-read-only", "mode": "read_only"},
        },
    )

    outcome = supervisor.pre_completion_hook_for_run(admission.run_id).run(
        PreCompletionContext(
            run=run,
            project_root=records["alpha"].canonical_root,
            store=store,
            goal="maintenance",
            pack="coding-team",
            autonomy="autonomous",
            call_count=1,
            decision="rejected",
            evidence={},
        )
    )

    assert outcome.continue_completion is False
    assert outcome.pause_reason == "verification_not_verified"
    assert store.get_run(admission.run_id).status == "paused"


def test_non_yolo_space_performs_no_admission_or_child_store_work(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha", yolo=False, enrolled=False)}
    calls = 0

    def store_factory(root: Path):
        nonlocal calls
        calls += 1
        return ProjectSwarmStore(root)

    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
        child_store_factory=store_factory,
    )
    result = _admit(supervisor)

    assert result.status == "rejected"
    assert result.reason == "not_yolo_enrolled"
    assert calls == 0
    assert not (tmp_path / "supervisor.sqlite").exists()


def test_governance_resolver_failure_fails_closed_without_claiming_slot(tmp_path: Path) -> None:
    """A transient/corrupt governance read must not consume the global run slot."""
    child_calls = 0

    def store_factory(root: Path):
        nonlocal child_calls
        child_calls += 1
        return ProjectSwarmStore(root)

    def resolver(_target: str):
        raise OSError("governance registry unavailable")

    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=resolver,
        child_store_factory=store_factory,
    )

    result = _admit(supervisor)

    assert result.status == "rejected"
    assert result.reason == "not_yolo_enrolled"
    assert child_calls == 0
    assert not (tmp_path / "supervisor.sqlite").exists()


def test_human_cancellation_audits_actor_and_releases_the_global_slot_once(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)

    assert DASHBOARD_ACTOR_RE.fullmatch(_DASHBOARD_ACTOR)
    assert supervisor.cancel(admission.admission_id, actor=_DASHBOARD_ACTOR) is True
    assert supervisor.cancel(admission.admission_id, actor=_DASHBOARD_ACTOR) is False
    run_store = ProjectSwarmStore(records["alpha"].canonical_root)
    assert run_store.get_run(admission.run_id).status == "cancelled"
    cancelled = [event for event in run_store.list_events(admission.run_id) if event.event_type == "run.cancelled"]
    assert len(cancelled) == 1 and cancelled[0].payload["actor"] == _DASHBOARD_ACTOR
    assert _admit(supervisor, "beta").status == "created"


def test_human_abandonment_releases_the_slot_without_silent_resume(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    stale_hook = supervisor.pre_completion_hook_for_run(admission.run_id)

    assert supervisor.abandon(admission.admission_id, actor=_DASHBOARD_ACTOR) is True
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    assert store.get_run(admission.run_id).status == "abandoned"
    with pytest.raises(ValueError):
        store.resume_run(admission.run_id)
    with pytest.raises(ValueError):
        store.claim_run_execution_lease(admission.run_id, "worker")
    with pytest.raises(ValueError):
        store.recover_run_execution_lease(admission.run_id, actor_id="dashboard-operator")
    assert admission.capability is not None
    assert supervisor.revalidate_action_boundary(admission.capability) is False
    with pytest.raises(ValueError):
        supervisor.pre_completion_hook_for_run(admission.run_id)
    abandoned_run = store.get_run(admission.run_id)
    assert abandoned_run is not None
    stale_outcome = stale_hook.run(
        PreCompletionContext(
            run=abandoned_run,
            project_root=records["alpha"].canonical_root,
            store=store,
            goal="repair flaky test",
            pack="coding-team",
            autonomy="autonomous",
            call_count=1,
            decision="verified",
            evidence={},
        )
    )
    assert stale_outcome.continue_completion is False
    next_admission = _admit(supervisor, "beta")
    assert next_admission.status == "created"
    assert next_admission.run_id != admission.run_id
    assert supervisor.record_completion(admission.run_id) is False
    assert supervisor.cancel(admission.admission_id, actor=_DASHBOARD_ACTOR) is False


def test_presence_release_by_run_id_resolves_root_and_rejects_nova_owned_child(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)

    assert supervisor.abandon_run_by_id(admission.run_id, actor=_DASHBOARD_ACTOR) is True
    assert ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id).status == "abandoned"

    second = supervisor.admit("alpha", {"goal": "second release check"})
    assert second.status == "created"
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    with store._connection() as connection:
        connection.execute(
            "UPDATE runs SET metadata_json = ? WHERE run_id = ?",
            ('{"integration_namespace":"nova-space-supervisor","started_by":"nova"}', second.run_id),
        )
    with pytest.raises(PermissionError):
        supervisor.abandon_run_by_id(second.run_id, actor=_DASHBOARD_ACTOR)


def test_durable_completion_observer_releases_the_supervisor_slot(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    running = store.get_run(admission.run_id)
    assert running is not None
    options = supervisor.execution_options_for_run(records["alpha"].canonical_root, running)
    assert options.on_completed is not None
    _resume_for_completion(store, admission.run_id)
    run = store.set_run_status(admission.run_id, "completed")

    options.on_completed(records["alpha"].canonical_root, run)
    assert _admit(supervisor, "beta").status == "created"


def test_supervisor_passes_host_action_executor_into_managed_run_options(
    tmp_path: Path,
) -> None:
    class Executor:
        def execute(self, _proposal, _run):
            return True

    executor = Executor()
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
        action_executor=executor,
    )
    admission = _admit(supervisor, "alpha")
    assert admission.run_id is not None
    run = ProjectSwarmStore(tmp_path / "alpha").get_run(admission.run_id)
    assert run is not None
    options = supervisor.execution_options_for_run(tmp_path / "alpha", run)
    assert options.action_executor is executor


@pytest.mark.parametrize("terminal", ("cancel", "abandon"))
def test_human_terminal_race_reconciles_an_already_completed_child_without_overwriting_it(
    tmp_path: Path,
    terminal: str,
) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    _resume_for_completion(store, admission.run_id)
    store.set_run_status(admission.run_id, "completed")

    transition = getattr(supervisor, terminal)
    assert transition(admission.admission_id, actor=_DASHBOARD_ACTOR) is False
    assert store.get_run(admission.run_id).status == "completed"
    assert _admit(supervisor, "beta").status == "created"


def test_completion_observer_reconciles_durable_completion_after_post_hook_revocation(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    active = store.get_run(admission.run_id)
    assert active is not None
    options = supervisor.execution_options_for_run(records["alpha"].canonical_root, active)
    assert options.on_completed is not None
    _resume_for_completion(store, admission.run_id)
    completed = store.set_run_status(admission.run_id, "completed")
    records["alpha"] = replace(records["alpha"], yolo=False)

    options.on_completed(records["alpha"].canonical_root, completed)
    assert _admit(supervisor, "beta").status == "created"


def test_pause_race_reconciles_a_matching_completed_child_instead_of_stranding_slot(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    assert admission.capability is not None
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    _resume_for_completion(store, admission.run_id)
    store.set_run_status(admission.run_id, "completed")
    records["alpha"] = replace(records["alpha"], yolo=False)

    assert supervisor.revalidate_action_boundary(admission.capability) is False
    assert _admit(supervisor, "beta").status == "created"


def test_completion_after_pause_probe_is_reconciled_from_paused_on_next_admission(
    tmp_path: Path,
) -> None:
    """Catches a completed child stranded behind a raced paused ledger row."""
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    probe_finished = threading.Event()
    continue_pause = threading.Event()

    class CompletionRaceSupervisor(ManagedSpaceSupervisor):
        def _reconcile_completed_record(self, record, *, allowed_states, event_type):
            reconciled = super()._reconcile_completed_record(
                record,
                allowed_states=allowed_states,
                event_type=event_type,
            )
            if event_type == "reconciled_completed_during_pause":
                probe_finished.set()
                assert continue_pause.wait(timeout=5)
            return reconciled

    supervisor = CompletionRaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
    )
    admission = _admit(supervisor)
    assert admission.capability is not None
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    _resume_for_completion(store, admission.run_id)

    with ThreadPoolExecutor(max_workers=1) as workers:
        pausing = workers.submit(
            supervisor._pause,
            admission.capability,
            "completion_race",
        )
        assert probe_finished.wait(timeout=5)
        store.set_run_status(admission.run_id, "completed")
        continue_pause.set()
        pausing.result(timeout=5)

    assert supervisor.list_active_admissions()[0]["state"] == "paused"
    assert _admit(supervisor, "beta").status == "created"


def test_fresh_supervisor_reconciles_a_matching_completed_child_before_admission(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    original = _supervisor(tmp_path, records)
    admission = _admit(original)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    _resume_for_completion(store, admission.run_id)
    store.set_run_status(admission.run_id, "completed")

    restarted = _supervisor(tmp_path, records)
    assert _admit(restarted, "beta").status == "created"


def test_restart_keeps_slot_when_completed_child_metadata_does_not_match_ledger(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    admission = _admit(_supervisor(tmp_path, records))
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    _resume_for_completion(store, admission.run_id)
    store.set_run_status(admission.run_id, "completed")
    with store._connection() as connection:
        raw = connection.execute("SELECT metadata_json FROM runs WHERE run_id = ?", (admission.run_id,)).fetchone()[0]
        metadata = json.loads(raw)
        metadata["nova_supervisor"]["allowed_action_families"] = ["hard_denied"]
        connection.execute("UPDATE runs SET metadata_json = ? WHERE run_id = ?", (json.dumps(metadata), admission.run_id))

    restarted = _supervisor(tmp_path, records)
    blocked = _admit(restarted, "beta")
    assert blocked.reason == "active_limit"


def test_restart_keeps_slot_when_ledger_root_cannot_find_the_completed_child(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    _resume_for_completion(store, admission.run_id)
    store.set_run_status(admission.run_id, "completed")
    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        connection.execute(
            "UPDATE supervisor_admissions SET canonical_root = ? WHERE admission_id = ?",
            (str(tmp_path / "wrong-root"), admission.admission_id),
        )

    restarted = _supervisor(tmp_path, records)
    assert _admit(restarted, "beta").reason == "active_limit"


def test_allowed_action_families_are_bound_in_capability_ledger_and_child_metadata(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    assert admission.capability is not None
    run = ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id)
    assert run is not None
    allowed = run.metadata["nova_supervisor"]["allowed_action_families"]
    assert allowed == ["target_local_worktree", "github_publication", "target_deployment_worker"]
    assert "secret_access" not in allowed

    tampered_metadata = dict(run.metadata)
    tampered_supervisor = dict(tampered_metadata["nova_supervisor"])
    tampered_supervisor["allowed_action_families"] = ["hard_denied"]
    tampered_metadata["nova_supervisor"] = tampered_supervisor
    blocked = supervisor.execution_options_for_run(
        records["alpha"].canonical_root,
        replace(run, metadata=tampered_metadata),
    )
    assert blocked.blocked_reason == "capability_invalid"
    assert ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id).status == "paused"


def test_tampered_capability_action_families_fail_closed_against_the_ledger(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    assert admission.capability is not None
    object.__setattr__(admission.capability, "_allowed_action_families", ("hard_denied",))

    assert supervisor.revalidate_action_boundary(admission.capability) is False
    assert ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id).status == "paused"


def test_host_execution_options_adapter_exposes_supervisor_hooks_and_pauses_revocation(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    run = ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id)
    assert run is not None
    host = SidekickSwarmService(
        execution_options_resolver=lambda root, candidate: managed_space_execution_options_for_run(supervisor, root, candidate),
    )

    options = host._resolve_execution_options(records["alpha"].canonical_root, run)
    assert isinstance(options, SwarmExecutionOptions)
    assert options.max_calls == 128
    assert options.pre_completion_hook is not None and options.on_completed is not None

    records["alpha"] = replace(records["alpha"], yolo=False)
    blocked = host._resolve_execution_options(records["alpha"].canonical_root, run)
    assert blocked.blocked_reason == "execution_options_blocked"
    assert ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id).status == "paused"


def test_host_resume_revalidates_supervisor_governance_before_running_transition(
    tmp_path: Path,
) -> None:
    """Catches resume publishing running before managed authority is revalidated."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    records["alpha"] = replace(records["alpha"], enrolled=False)
    host = SidekickSwarmService(
        execution_options_resolver=lambda root, candidate: (
            managed_space_execution_options_for_run(supervisor, root, candidate)
        ),
    )

    with pytest.raises(RuntimeError, match="execution_options_blocked"):
        host.resume(records["alpha"].canonical_root, admission.run_id)

    run = store.get_run(admission.run_id)
    assert run is not None and run.status == "paused"
    assert not any(
        event.event_type == "run.resumed_by_human"
        for event in store.list_events(admission.run_id)
    )


def test_host_completion_executes_required_supervisor_hook_and_pauses_revocation(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    run = store.get_run(admission.run_id)
    assert run is not None
    assert run.metadata["required_pre_completion_hook"] == "nova-managed-space-supervisor-v1"
    store.save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=(
                "deepseek-v4-flash", "deepseek-v4-pro", "kimi-k2.6",
                "minimax-m3", "glm-5.2", "kimi-k2.7-code", "nemotron-3-super",
            ),
            healthy=True,
            source=OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE,
        )
    )
    assert store.resume_run(admission.run_id).status == "running"
    calls: list[dict[str, object]] = []

    def revoke_before_completion(**kwargs: object):
        calls.append(dict(kwargs))
        records["alpha"] = replace(records["alpha"], yolo=False)
        return {
            "choices": [{"message": {"content": json.dumps({
                "work": "bounded test work",
                "evidence": ["test:evidence"],
                "decision": "approve",
                "approved": True,
            })}}],
        }

    host = SidekickSwarmService(
        call_llm=revoke_before_completion,
        execution_options_resolver=lambda root, candidate: managed_space_execution_options_for_run(
            supervisor, root, candidate
        ),
    )
    summary = host.execute_run(records["alpha"].canonical_root, admission.run_id)

    assert calls
    assert summary.status == "paused"
    assert summary.pause_reason == "governance_revoked"
    assert store.get_run(admission.run_id).status == "paused"
    assert not any(event.event_type == "run.completed" for event in store.list_events(admission.run_id))


@pytest.mark.parametrize("replacement", (None, "untrusted-hook-v1"))
def test_untrusted_required_hook_marker_changes_fail_closed(tmp_path: Path, replacement: str | None) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    run = store.get_run(admission.run_id)
    assert run is not None
    metadata = dict(run.metadata)
    if replacement is None:
        metadata.pop("required_pre_completion_hook")
    else:
        metadata["required_pre_completion_hook"] = replacement

    options = supervisor.execution_options_for_run(
        records["alpha"].canonical_root,
        replace(run, metadata=metadata),
    )

    assert options.blocked_reason == "capability_invalid"
    assert store.get_run(admission.run_id).status == "paused"


def test_action_boundary_rejects_running_child_with_tampered_durable_metadata(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    assert admission.capability is not None
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    assert store.resume_run(admission.run_id).status == "running"
    with store._connection() as connection:
        raw = connection.execute("SELECT metadata_json FROM runs WHERE run_id = ?", (admission.run_id,)).fetchone()[0]
        metadata = json.loads(raw)
        metadata.pop("required_pre_completion_hook")
        connection.execute("UPDATE runs SET metadata_json = ? WHERE run_id = ?", (json.dumps(metadata), admission.run_id))

    assert supervisor.revalidate_action_boundary(admission.capability) is False
    assert store.get_run(admission.run_id).status == "paused"


@pytest.mark.parametrize("field,replacement", (("goal", "tampered goal"), ("pack", "review-team"), ("autonomy", "reviewed_execution")))
def test_workflow_input_contract_tampering_pauses_before_action_or_completion(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    assert admission.capability is not None
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    assert store.resume_run(admission.run_id).status == "running"
    with store._connection() as connection:
        raw = connection.execute("SELECT metadata_json FROM runs WHERE run_id = ?", (admission.run_id,)).fetchone()[0]
        metadata = json.loads(raw)
        metadata[field] = replacement
        connection.execute("UPDATE runs SET metadata_json = ? WHERE run_id = ?", (json.dumps(metadata), admission.run_id))

    assert supervisor.revalidate_action_boundary(admission.capability) is False
    assert store.get_run(admission.run_id).status == "paused"
    assert supervisor.record_completion(admission.run_id) is False


def test_host_rejects_tampered_workflow_contract_before_model_call(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    assert store.resume_run(admission.run_id).status == "running"
    with store._connection() as connection:
        raw = connection.execute("SELECT metadata_json FROM runs WHERE run_id = ?", (admission.run_id,)).fetchone()[0]
        metadata = json.loads(raw)
        metadata["goal"] = "untrusted replacement goal"
        connection.execute("UPDATE runs SET metadata_json = ? WHERE run_id = ?", (json.dumps(metadata), admission.run_id))
    calls: list[object] = []
    host = SidekickSwarmService(
        call_llm=lambda **_kwargs: calls.append(object()) or pytest.fail("tampered run must not call a model"),
        execution_options_resolver=lambda root, candidate: managed_space_execution_options_for_run(supervisor, root, candidate),
    )

    summary = host.execute_run(records["alpha"].canonical_root, admission.run_id)

    assert calls == []
    assert summary.status == "paused"
    assert summary.pause_reason == "execution_options_blocked"
    assert store.get_run(admission.run_id).status == "paused"


def test_host_guard_revalidates_governance_after_pause_resume_before_model_call(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    paused = threading.Event()
    result: dict[str, object] = {}
    calls: list[object] = []
    host = SidekickSwarmService(
        call_llm=lambda **_kwargs: calls.append(object()) or pytest.fail("revoked resume must not call a model"),
        execution_options_resolver=lambda root, candidate: managed_space_execution_options_for_run(supervisor, root, candidate),
        pause_poll_seconds=0.01,
    )

    def execute() -> None:
        result["summary"] = host.execute_run(
            records["alpha"].canonical_root,
            admission.run_id,
            on_pause_wait=paused.set,
        )

    worker = threading.Thread(target=execute)
    worker.start()
    assert paused.wait(timeout=5)
    records["alpha"] = replace(records["alpha"], yolo=False)
    assert store.resume_run(admission.run_id).status == "running"
    worker.join(timeout=5)
    assert not worker.is_alive()
    summary = result["summary"]

    assert calls == []
    assert summary.status == "paused"
    assert summary.pause_reason == "governance_revoked"
    assert store.get_run(admission.run_id).status == "paused"


def test_host_completion_preserves_trusted_hook_requirement_across_metadata_toctou(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    store.save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=(
                "deepseek-v4-flash", "deepseek-v4-pro", "kimi-k2.6",
                "minimax-m3", "glm-5.2", "kimi-k2.7-code", "nemotron-3-super",
            ),
            healthy=True,
            source=OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE,
        )
    )
    assert store.resume_run(admission.run_id).status == "running"

    def resolver(root: Path, candidate):
        return managed_space_execution_options_for_run(supervisor, root, candidate)

    def tamper_marker_after_first_model_call(**_kwargs: object):
        with store._connection() as connection:
            raw = connection.execute("SELECT metadata_json FROM runs WHERE run_id = ?", (admission.run_id,)).fetchone()[0]
            metadata = json.loads(raw)
            metadata.pop("required_pre_completion_hook", None)
            connection.execute("UPDATE runs SET metadata_json = ? WHERE run_id = ?", (json.dumps(metadata), admission.run_id))
        return {
            "choices": [{"message": {"content": json.dumps({
                "work": "bounded test work",
                "evidence": ["test:evidence"],
                "decision": "approve",
                "approved": True,
            })}}],
        }

    host = SidekickSwarmService(
        call_llm=tamper_marker_after_first_model_call,
        execution_options_resolver=resolver,
    )
    summary = host.execute_run(records["alpha"].canonical_root, admission.run_id)

    assert summary.status == "paused"
    assert summary.pause_reason == "capability_invalid"
    assert store.get_run(admission.run_id).status == "paused"
    assert not any(event.event_type == "run.completed" for event in store.list_events(admission.run_id))


def test_tampered_capability_cannot_directly_complete_but_verified_child_completion_reconciles(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    assert admission.capability is not None
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    _resume_for_completion(store, admission.run_id)
    store.set_run_status(admission.run_id, "completed")
    foreign_root = tmp_path / "foreign"
    object.__setattr__(admission.capability, "_canonical_root", foreign_root)

    assert supervisor.record_completion(admission.run_id) is False
    assert _admit(supervisor, "beta").status == "created"
    assert not (foreign_root / ".swarm").exists()


def test_worker_or_model_actor_cannot_cancel_or_abandon_a_supervisor_run(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)

    with pytest.raises(PermissionError):
        supervisor.cancel(admission.admission_id, actor="worker:executor")
    with pytest.raises(PermissionError):
        supervisor.abandon(admission.admission_id, actor="model:nova")
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    with pytest.raises(PermissionError):
        store.set_run_status(admission.run_id, "cancelled")
    with pytest.raises(PermissionError):
        store.set_run_status(admission.run_id, "abandoned")
    assert store.get_run(admission.run_id).status == "paused"


def test_child_metadata_is_diagnostic_only_and_cannot_reconstruct_a_capability(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    run = ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id)
    assert run is not None

    assert "capability" not in repr(run.metadata).lower()
    restarted = _supervisor(tmp_path, records)
    options = restarted.execution_options_for_run(records["alpha"].canonical_root, run)
    assert options.blocked_reason == "supervisor_binding_unavailable"


def test_dashboard_recovery_reattaches_only_a_verified_paused_child_after_restart(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    original = _supervisor(tmp_path, records)
    admission = _admit(original)
    store = ProjectSwarmStore(records["alpha"].canonical_root)

    restarted = _supervisor(tmp_path, records)
    with pytest.raises(PermissionError):
        restarted.recover_and_reattach(admission.admission_id, actor="worker:previous-host")
    capability = restarted.recover_and_reattach(admission.admission_id, actor=_DASHBOARD_ACTOR)
    assert capability is not None
    paused = store.get_run(admission.run_id)
    assert paused is not None and paused.status == "paused"
    options = restarted.execution_options_for_run(records["alpha"].canonical_root, paused)
    assert options.max_calls == 128
    assert store.resume_run(admission.run_id).status == "running"


def test_two_recoveries_advance_one_generation_and_only_the_winner_executes(
    tmp_path: Path,
) -> None:
    """Catches concurrent recovery minting two independently valid capabilities."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    admission = _admit(_supervisor(tmp_path, records))
    snapshots_ready = threading.Barrier(2)

    class SynchronizedRecoverySupervisor(ManagedSpaceSupervisor):
        def _before_recovery_transaction(
            self,
            _admission_id: str,
            _snapshot: object,
        ) -> None:
            snapshots_ready.wait(timeout=5)

    def restarted() -> SynchronizedRecoverySupervisor:
        return SynchronizedRecoverySupervisor(
            ledger_path=tmp_path / "supervisor.sqlite",
            governance_resolver=lambda target: records[target],
        )

    first = restarted()
    second = restarted()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(
            workers.map(
                lambda supervisor: supervisor.recover_and_reattach(
                    admission.admission_id,
                    actor=_DASHBOARD_ACTOR,
                ),
                (first, second),
            )
        )

    assert sum(result is not None for result in results) == 1
    run = ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id)
    assert run is not None
    options = [
        supervisor.execution_options_for_run(records["alpha"].canonical_root, run)
        for supervisor in (first, second)
    ]
    assert sum(option.blocked_reason is None for option in options) == 1
    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        generation, version = connection.execute(
            """SELECT attachment_generation, record_version
               FROM supervisor_admissions WHERE admission_id = ?""",
            (admission.admission_id,),
        ).fetchone()
    assert generation == 2
    assert version >= 3


@pytest.mark.parametrize("terminal_action", ("cancel", "abandon"))
def test_recovery_loses_to_concurrent_human_terminal_transition(
    tmp_path: Path,
    terminal_action: str,
) -> None:
    """Catches recovery reactivating a run after terminal intent is durable."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    admission = _admit(_supervisor(tmp_path, records))
    snapshot_ready = threading.Event()
    continue_recovery = threading.Event()

    class PausedRecoverySupervisor(ManagedSpaceSupervisor):
        def _before_recovery_transaction(
            self,
            _admission_id: str,
            _snapshot: object,
        ) -> None:
            snapshot_ready.set()
            assert continue_recovery.wait(timeout=5)

    recovering = PausedRecoverySupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
    )
    terminal = _supervisor(tmp_path, records)
    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(
            recovering.recover_and_reattach,
            admission.admission_id,
            actor=_DASHBOARD_ACTOR,
        )
        assert snapshot_ready.wait(timeout=5)
        assert getattr(terminal, terminal_action)(
            admission.admission_id,
            actor=_DASHBOARD_ACTOR,
        ) is True
        continue_recovery.set()
        assert future.result(timeout=5) is None

    run = ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id)
    assert run is not None and run.status == (
        "cancelled" if terminal_action == "cancel" else "abandoned"
    )
    blocked = recovering.execution_options_for_run(
        records["alpha"].canonical_root,
        run,
    )
    assert blocked.blocked_reason is not None


def test_failed_recovery_transaction_rolls_back_without_installing_a_binding(
    tmp_path: Path,
) -> None:
    """Catches a transaction loser leaving process authority behind."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    admission = _admit(_supervisor(tmp_path, records))

    class FailingRecoverySupervisor(ManagedSpaceSupervisor):
        def _before_recovery_commit(
            self,
            _admission_id: str,
            _generation: int,
        ) -> None:
            raise RuntimeError("forced recovery rollback")

    restarted = FailingRecoverySupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
    )

    with pytest.raises(RuntimeError, match="forced recovery rollback"):
        restarted.recover_and_reattach(
            admission.admission_id,
            actor=_DASHBOARD_ACTOR,
        )

    assert restarted._bindings == {}
    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        generation = connection.execute(
            """SELECT attachment_generation FROM supervisor_admissions
               WHERE admission_id = ?""",
            (admission.admission_id,),
        ).fetchone()[0]
    assert generation == 1


def test_newer_reattach_invalidates_the_old_capability_without_pausing_the_winner(
    tmp_path: Path,
) -> None:
    """Catches stale generation authority mutating or disabling its replacement."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    original = _supervisor(tmp_path, records)
    admission = _admit(original)
    assert admission.capability is not None
    old_capability = admission.capability
    restarted = _supervisor(tmp_path, records)
    current = restarted.recover_and_reattach(
        admission.admission_id,
        actor=_DASHBOARD_ACTOR,
    )
    assert current is not None
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    running = store.resume_run(admission.run_id)

    assert original.revalidate_action_boundary(old_capability) is False
    current_options = restarted.execution_options_for_run(
        records["alpha"].canonical_root,
        running,
    )
    assert current_options.blocked_reason is None
    assert store.get_run(admission.run_id).status == "running"


def test_action_boundary_requires_explicit_resume_for_admitted_and_reattached_children(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    original = _supervisor(tmp_path, records)
    admission = _admit(original)
    assert admission.capability is not None
    store = ProjectSwarmStore(records["alpha"].canonical_root)

    # Admission deliberately creates a paused child. Its capability may be
    # bound for the host, but cannot authorize an action before resume.
    assert original.revalidate_action_boundary(admission.capability) is False
    assert (restarted_state := original.list_active_admissions())
    assert restarted_state[0]["state"] == "active"
    assert store.resume_run(admission.run_id).status == "running"
    assert original.revalidate_action_boundary(admission.capability) is True

    # The same rule holds after a fresh process explicitly re-attaches the
    # paused child; re-attachment itself never starts an action.
    assert store.set_run_status(admission.run_id, "paused").status == "paused"
    restarted = _supervisor(tmp_path, records)
    recovered = restarted.recover_and_reattach(admission.admission_id, actor=_DASHBOARD_ACTOR)
    assert recovered is not None
    assert restarted.revalidate_action_boundary(recovered) is False
    assert store.resume_run(admission.run_id).status == "running"
    assert restarted.revalidate_action_boundary(recovered) is True


@pytest.mark.parametrize("defect", ("revoked", "root", "metadata", "lease"))
def test_recovery_reattach_rejects_unverified_child_and_keeps_it_paused(tmp_path: Path, defect: str) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    original = _supervisor(tmp_path, records)
    admission = _admit(original)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    if defect == "revoked":
        records["alpha"] = replace(records["alpha"], yolo=False)
    elif defect == "root":
        records["alpha"] = _governance(tmp_path / "wrong-root", space_id=records["alpha"].space_id)
    elif defect == "lease":
        assert store.claim_run_execution_lease(admission.run_id, "previous-host") is True
    else:
        with store._connection() as connection:
            raw = connection.execute("SELECT metadata_json FROM runs WHERE run_id = ?", (admission.run_id,)).fetchone()[0]
            metadata = json.loads(raw)
            metadata["nova_supervisor"]["policy_identity"] = "tampered"
            connection.execute("UPDATE runs SET metadata_json = ? WHERE run_id = ?", (json.dumps(metadata), admission.run_id))

    restarted = _supervisor(tmp_path, records)
    assert restarted.recover_and_reattach(admission.admission_id, actor=_DASHBOARD_ACTOR) is None
    run = store.get_run(admission.run_id)
    assert run is not None and run.status == "paused"
    assert restarted.list_active_admissions()[0]["state"] == "paused"
    if defect == "lease":
        assert store.has_run_execution_lease(admission.run_id) is True
    assert restarted.execution_options_for_run(records["alpha"].canonical_root, run).blocked_reason == "supervisor_binding_unavailable"


def test_missing_child_in_provisioning_state_is_recovered_to_paused_and_holds_slot(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }

    def missing_child_store(_root: Path):
        class MissingChildStore:
            def create_run(self, *_args, **_kwargs):
                raise RuntimeError("simulated process death before child create")
        return MissingChildStore()

    crashing = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
        child_store_factory=missing_child_store,
    )
    with pytest.raises(RuntimeError, match="simulated process death"):
        _admit(crashing)

    restarted = _supervisor(tmp_path, records)
    blocked = _admit(restarted, "beta")
    assert blocked.status == "created"
    active = restarted.list_active_admissions()
    assert len(active) == 1 and active[0]["state"] == "active"


def test_unstarted_child_in_provisioning_state_is_paused_and_cannot_be_bypassed(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }

    def child_then_crash(root: Path):
        store = ProjectSwarmStore(root)

        class ChildThenCrash:
            def create_run(self, *args, **kwargs):
                store.create_run(*args, **kwargs)
                raise RuntimeError("simulated process death after child create")
        return ChildThenCrash()

    crashing = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
        child_store_factory=child_then_crash,
    )
    with pytest.raises(RuntimeError, match="simulated process death"):
        _admit(crashing)

    restarted = _supervisor(tmp_path, records)
    assert _admit(restarted, "beta").status == "created"
    active = restarted.list_active_admissions()
    assert len(active) == 1 and active[0]["state"] == "active"


def test_mismatched_child_in_provisioning_state_is_paused_and_audited(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }

    def mismatched_child_then_crash(root: Path):
        store = ProjectSwarmStore(root)

        class MismatchedChildThenCrash:
            def create_run(self, run_id, *args, **kwargs):
                store.create_run(run_id, *args, **kwargs)
                with store._connection() as connection:
                    connection.execute(
                        "UPDATE runs SET metadata_json = ? WHERE run_id = ?",
                        (json.dumps({"integration_namespace": "wrong"}), run_id),
                    )
                raise RuntimeError("simulated process death after mismatched child create")
        return MismatchedChildThenCrash()

    crashing = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
        child_store_factory=mismatched_child_then_crash,
    )
    with pytest.raises(RuntimeError, match="mismatched child"):
        _admit(crashing)

    restarted = _supervisor(tmp_path, records)
    assert _admit(restarted, "beta").status == "created"
    active = restarted.list_active_admissions()
    assert len(active) == 1 and active[0]["state"] == "active"


def test_cancel_winning_before_child_creation_terminalizes_ledger_without_orphaning_child(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    reserved = threading.Event()
    continue_admission = threading.Event()

    class ReservationPausedSupervisor(ManagedSpaceSupervisor):
        def _after_provisioning_reservation(self, _admission_id: str) -> None:
            reserved.set()
            assert continue_admission.wait(timeout=5)

    supervisor = ReservationPausedSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
    )
    with ThreadPoolExecutor(max_workers=2) as workers:
        admitted = workers.submit(_admit, supervisor)
        assert reserved.wait(timeout=5)
        admission_id = supervisor.list_active_admissions()[0]["admission_id"]
        assert supervisor.cancel(admission_id, actor=_DASHBOARD_ACTOR) is True
        continue_admission.set()
        result = admitted.result(timeout=5)

    assert result.status == "rejected"
    assert result.reason == "terminal_admission"
    assert not (records["alpha"].canonical_root / ".swarm").exists()
    assert _admit(supervisor, "beta").status == "created"


def test_governance_change_at_reservation_seam_creates_no_child_and_holds_auditable_slot(
    tmp_path: Path,
) -> None:
    """Catches child creation using the stale pre-reservation governance snapshot."""
    records = {"alpha": _governance(tmp_path / "alpha")}

    class RevokedAtReservationSupervisor(ManagedSpaceSupervisor):
        def _after_provisioning_reservation(self, _admission_id: str) -> None:
            records["alpha"] = replace(
                records["alpha"],
                enrolled=False,
                revision=records["alpha"].revision + 1,
            )

    supervisor = RevokedAtReservationSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
    )

    rejected = _admit(supervisor)

    assert rejected.status == "rejected"
    assert rejected.reason == "governance_changed"
    assert rejected.capability is None
    assert not (records["alpha"].canonical_root / ".swarm").exists()
    active = supervisor.list_active_admissions()
    assert len(active) == 1 and active[0]["state"] == "paused"
    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        audit = connection.execute(
            """SELECT event_type, reason FROM supervisor_audit
               WHERE admission_id = ? ORDER BY sequence DESC LIMIT 1""",
            (rejected.admission_id,),
        ).fetchone()
    assert audit == ("paused", "governance_changed")
    assert supervisor.cancel(rejected.admission_id, actor=_DASHBOARD_ACTOR) is True
    assert not (records["alpha"].canonical_root / ".swarm").exists()


def test_cancel_waits_for_admission_that_won_ledger_lock_and_terminalizes_its_child(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    child_create_started = threading.Event()
    continue_child_create = threading.Event()

    def blocking_store(root: Path):
        store = ProjectSwarmStore(root)

        class BlockingStore:
            def create_run(self, *args, **kwargs):
                child_create_started.set()
                assert continue_child_create.wait(timeout=5)
                return store.create_run(*args, **kwargs)

            def __getattr__(self, name: str):
                return getattr(store, name)
        return BlockingStore()

    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
        child_store_factory=blocking_store,
    )
    with ThreadPoolExecutor(max_workers=2) as workers:
        admitted = workers.submit(_admit, supervisor)
        assert child_create_started.wait(timeout=5)
        admission_id = supervisor.list_active_admissions()[0]["admission_id"]
        cancelled = workers.submit(supervisor.cancel, admission_id, actor=_DASHBOARD_ACTOR)
        continue_child_create.set()
        created = admitted.result(timeout=5)
        assert cancelled.result(timeout=5) is True

    run = ProjectSwarmStore(records["alpha"].canonical_root).get_run(created.run_id)
    assert created.status == "created"
    assert run is not None and run.status == "cancelled"
    assert _admit(supervisor, "beta").status == "created"


def test_cancel_commits_nonexecutable_ledger_state_before_slow_child_terminalization(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    child_terminal_started = threading.Event()
    continue_child_terminal = threading.Event()

    def blocking_terminal_store(root: Path):
        store = ProjectSwarmStore(root)

        class BlockingTerminalStore:
            def cancel_run_by_human(self, *args, **kwargs):
                child_terminal_started.set()
                assert continue_child_terminal.wait(timeout=5)
                return store.cancel_run_by_human(*args, **kwargs)

            def __getattr__(self, name: str):
                return getattr(store, name)
        return BlockingTerminalStore()

    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
        child_store_factory=blocking_terminal_store,
    )
    admission = _admit(supervisor)
    assert admission.capability is not None
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    assert store.resume_run(admission.run_id).status == "running"
    assert supervisor.revalidate_action_boundary(admission.capability) is True

    with ThreadPoolExecutor(max_workers=1) as workers:
        cancelled = workers.submit(supervisor.cancel, admission.admission_id, actor=_DASHBOARD_ACTOR)
        assert child_terminal_started.wait(timeout=5)
        assert supervisor.revalidate_action_boundary(admission.capability) is False
        continue_child_terminal.set()
        assert cancelled.result(timeout=5) is True

    assert store.get_run(admission.run_id).status == "cancelled"


@pytest.mark.parametrize(
    "terminal_action,transitional_state,final_state",
    (
        ("cancel", "cancelling", "cancelled"),
        ("abandon", "abandoning", "abandoned"),
    ),
)
def test_human_terminalization_retries_after_child_store_write_failure(
    tmp_path: Path,
    terminal_action: str,
    transitional_state: str,
    final_state: str,
) -> None:
    """Catches a transient child write permanently stranding a global slot."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    attempts = 0

    def failing_once_store(root: Path):
        store = ProjectSwarmStore(root)

        class FailingOnceStore:
            def cancel_run_by_human(self, *args, **kwargs):
                return terminal("cancel", store.cancel_run_by_human, *args, **kwargs)

            def abandon_run_by_human(self, *args, **kwargs):
                return terminal("abandon", store.abandon_run_by_human, *args, **kwargs)

            def __getattr__(self, name: str):
                return getattr(store, name)

        return FailingOnceStore()

    def terminal(mode: str, operation, *args, **kwargs):
        nonlocal attempts
        if mode == terminal_action:
            attempts += 1
            if attempts == 1:
                raise OSError("transient child write failure")
        return operation(*args, **kwargs)

    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
        child_store_factory=failing_once_store,
    )
    admission = _admit(supervisor)
    transition = getattr(supervisor, terminal_action)

    with pytest.raises(OSError, match="transient child write failure"):
        transition(admission.admission_id, actor=_DASHBOARD_ACTOR)
    assert supervisor.list_active_admissions()[0]["state"] == transitional_state

    assert transition(admission.admission_id, actor=_DASHBOARD_ACTOR) is True
    run = ProjectSwarmStore(records["alpha"].canonical_root).get_run(admission.run_id)
    assert run is not None and run.status == final_state
    assert supervisor.list_active_admissions() == []


def test_ledger_read_connection_cannot_recreate_a_deleted_database(tmp_path: Path) -> None:
    """Catches a read-only status race recreating an empty supervisor ledger."""
    supervisor = _supervisor(tmp_path, {"alpha": _governance(tmp_path / "alpha")})
    supervisor.start()
    ledger = tmp_path / "supervisor.sqlite"
    ledger.unlink()

    with pytest.raises(sqlite3.OperationalError):
        with supervisor._read_connection():
            pass

    assert not ledger.exists()


def test_host_router_delegates_only_ledger_unknown_nonmanaged_runs(tmp_path: Path) -> None:
    """Catches child metadata or a missing ledger selecting managed authority."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    router_type = getattr(space_supervisor_module, "ManagedSpaceHostRouter", None)
    assert router_type is not None
    fallbacks: list[str] = []

    def fallback(_root: Path, run):
        fallbacks.append(run.run_id)
        return SwarmExecutionOptions(max_calls=48)

    router = router_type(supervisor, fallback)
    generic = ProjectSwarmStore(tmp_path / "generic").create_run(
        metadata={"goal": "legacy", "pack": "coding-team"}
    )
    delegated = router(tmp_path / "generic", generic)
    assert delegated is not None and delegated.max_calls == 48
    assert fallbacks == [generic.run_id]

    orphan = replace(
        generic,
        run_id=str(uuid4()),
        metadata={
            "integration_namespace": "nova-space-supervisor",
            "goal": "orphan",
            "pack": "coding-team",
            "autonomy": "autonomous",
        },
    )
    delegated = router(tmp_path / "generic", orphan)
    assert delegated is not None and delegated.max_calls == 48
    assert fallbacks == [generic.run_id, orphan.run_id]

    admission = _admit(supervisor)
    managed = ProjectSwarmStore(records["alpha"].canonical_root).get_run(
        admission.run_id
    )
    assert managed is not None
    stripped = replace(
        managed,
        metadata={
            key: value
            for key, value in managed.metadata.items()
            if key != "nova_supervisor"
        },
    )
    blocked = router(records["alpha"].canonical_root, stripped)
    assert blocked.blocked_reason == "capability_invalid"
    assert fallbacks == [generic.run_id, orphan.run_id]


def test_host_router_fails_closed_when_existing_ledger_is_unreadable(
    tmp_path: Path,
) -> None:
    """Catches an unreadable authority ledger falling through to generic execution."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    supervisor.start()
    (tmp_path / "supervisor.sqlite").write_bytes(b"not a sqlite database")
    router_type = getattr(space_supervisor_module, "ManagedSpaceHostRouter", None)
    assert router_type is not None
    fallbacks: list[str] = []
    router = router_type(
        supervisor,
        lambda _root, run: fallbacks.append(run.run_id) or None,
    )
    run = ProjectSwarmStore(tmp_path / "generic").create_run(
        metadata={"goal": "generic", "pack": "coding-team"}
    )

    blocked = router(tmp_path / "generic", run)

    assert blocked.blocked_reason == "supervisor_binding_unavailable"
    assert fallbacks == []


def test_target_key_for_bound_run_is_read_only_and_root_bound(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)

    assert supervisor.target_key_for_run(records["alpha"].canonical_root, admission.run_id) == "alpha"
    assert supervisor.target_key_for_run(tmp_path / "other", admission.run_id) is None
    assert supervisor.target_key_for_run(records["alpha"].canonical_root, "missing") is None

    assert supervisor.admission_id_for_run(
        records["alpha"].canonical_root, admission.run_id
    ) == admission.admission_id
    assert supervisor.admission_id_for_run(tmp_path / "other", admission.run_id) is None

def test_host_dispatch_reconciliation_pauses_when_worker_returns_without_completion(tmp_path: Path) -> None:
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    assert store.get_run(admission.run_id).status == "paused"

    assert supervisor.reconcile_host_dispatch(
        records["alpha"].canonical_root,
        admission.run_id,
        failure_reason="host_execution_returned",
    ) == "paused"
    assert supervisor.list_active_admissions()[0]["state"] == "paused"
    assert store.get_run(admission.run_id).status == "paused"
    assert _admit(supervisor, "beta").reason == "active_limit"


def test_host_dispatch_reconciliation_preserves_child_pause_reason(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    store.append_event_once(
        admission.run_id,
        "run.paused",
        {"reason": "no_eligible_model"},
        idempotency_key="test-child-pause",
    )

    assert supervisor.reconcile_host_dispatch(
        records["alpha"].canonical_root,
        admission.run_id,
        failure_reason="host_execution_returned",
    ) == "paused"
    audit = store.list_events(admission.run_id)
    assert any(
        event.event_type == "nova.supervisor.paused"
        and event.payload.get("reason") == "no_eligible_model"
        for event in audit
    )


@pytest.mark.parametrize(
    ("pause_reason", "expected_audit_reason"),
    (
        ("model_chain_exhausted", "catalog_refresh_required"),
        ("no_eligible_model", "catalog_refresh_required"),
    ),
)
def test_auto_resume_records_one_bounded_audit_when_recovery_proof_is_missing(
    tmp_path: Path,
    pause_reason: str,
    expected_audit_reason: str,
) -> None:
    """A paused YOLO run remains auditable without a silent recovery attempt."""
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    store.append_event_once(
        admission.run_id,
        "run.paused",
        {"reason": pause_reason},
        idempotency_key="auto-resume-wait:" + pause_reason,
    )
    assert supervisor.reconcile_host_dispatch(
        records["alpha"].canonical_root,
        admission.run_id,
        failure_reason="host_execution_returned",
    ) == "paused"
    dispatched: list[tuple[Path, str]] = []

    assert supervisor.auto_resume_recoverable_run(
        "alpha", dispatcher=lambda root, run_id: dispatched.append((root, run_id))
    ) == (("waiting_for_catalog", admission.run_id) if pause_reason == "model_chain_exhausted" else ("none", None))
    assert supervisor.auto_resume_recoverable_run(
        "alpha", dispatcher=lambda root, run_id: dispatched.append((root, run_id))
    ) == (("waiting_for_catalog", admission.run_id) if pause_reason == "model_chain_exhausted" else ("none", None))

    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        state = connection.execute(
            "SELECT state FROM supervisor_admissions WHERE admission_id = ?",
            (admission.admission_id,),
        ).fetchone()[0]
        audits = connection.execute(
            """SELECT event_type, actor, reason FROM supervisor_audit
               WHERE admission_id = ? AND event_type = 'auto_resume_waiting'
               ORDER BY sequence ASC""",
            (admission.admission_id,),
        ).fetchall()

    assert state == "paused"
    assert store.get_run(admission.run_id).status == "paused"
    assert dispatched == []
    assert audits == [
        ("auto_resume_waiting", space_supervisor_module.SYSTEM_SPACE_LIFECYCLE_ACTOR, expected_audit_reason),
    ]


def test_auto_resume_model_chain_exhaustion_after_new_verified_catalog(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    store.append_event_once(
        admission.run_id,
        "run.paused",
        {"reason": "model_chain_exhausted"},
        idempotency_key="provider-chain-exhausted",
    )
    assert supervisor.reconcile_host_dispatch(
        records["alpha"].canonical_root,
        admission.run_id,
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
    dispatched: list[tuple[Path, str]] = []
    assert supervisor.auto_resume_recoverable_run(
        "alpha", dispatcher=lambda root, run_id: dispatched.append((root, run_id))
    ) == ("auto_resumed", admission.run_id)
    assert dispatched == [(records["alpha"].canonical_root, admission.run_id)]
    assert store.get_run(admission.run_id).status == "running"
    # A restarted host must not replay the now-active intent or dispatch a second worker.
    assert supervisor.auto_resume_recoverable_run(
        "alpha", dispatcher=lambda root, run_id: dispatched.append((root, run_id))
    ) == ("none", None)

def test_restart_reconciliation_keeps_dead_running_run_paused_and_blocks_silent_resume(tmp_path: Path) -> None:
    """A dead worker lease is audited, but the run is never silently resumed."""
    records = {
        "alpha": _governance(tmp_path / "alpha"),
        "beta": _governance(tmp_path / "beta"),
    }
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor, "alpha")
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    assert store.resume_run(admission.run_id).status == "running"
    assert store.claim_run_execution_lease(admission.run_id, "dashboard:stale-host")

    assert supervisor.reconcile_stale_host_runs(lambda _owner: False) == (admission.run_id,)
    assert store.get_run(admission.run_id).status == "paused"
    assert store.get_run_execution_lease_owner(admission.run_id) is None

    # The paused restart record still owns the one global admission slot;
    # another Space cannot race it into a second run.
    blocked = _admit(supervisor, "beta")
    assert blocked.status == "rejected"
    assert blocked.reason == "active_limit"
    resumed = supervisor.auto_resume_recoverable_run(
        "alpha", dispatcher=lambda *_args: pytest.fail("restart must not auto-resume")
    )
    assert resumed == ("none", None)
    assert store.get_run(admission.run_id).status == "paused"
    assert len(supervisor.list_active_admissions()) == 1

def test_binding_rejects_cross_space_metadata_even_with_matching_root(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor, "alpha")
    assert admission.capability is not None
    assert supervisor.start_admitted_run(admission.capability, dispatcher=lambda *_: None)
    store = ProjectSwarmStore(records["alpha"].canonical_root)
    run = store.get_run(admission.run_id)
    assert run is not None
    forged = replace(run, metadata={"target_space_id": str(uuid4())})
    assert supervisor._binding_for_run(records["alpha"].canonical_root, forged) is None


def test_three_space_revocation_between_heartbeat_and_dispatch_fails_closed(tmp_path: Path) -> None:
    records = {name: _governance(tmp_path / name) for name in ("alpha", "beta", "gamma")}
    supervisor = ManagedSpaceSupervisor(ledger_path=tmp_path / "supervisor.sqlite", governance_resolver=lambda target: records[target])
    admission = _admit(supervisor, "alpha")
    assert admission.capability is not None
    dispatched: list[str] = []
    original = supervisor._before_host_dispatch
    def revoke(capability):
        records["alpha"] = replace(records["alpha"], yolo=False)
        original(capability)
    supervisor._before_host_dispatch = revoke
    assert supervisor.start_admitted_run(admission.capability, dispatcher=lambda *_: dispatched.append("alpha")) is False
    assert dispatched == []
    assert supervisor.list_active_admissions()[0]["state"] == "paused"
    assert supervisor.admit("beta", {"goal": "next", "kind": "maintenance"}).reason == "active_limit"


def test_three_space_status_and_revision_isolation(tmp_path: Path) -> None:
    records = {name: _governance(tmp_path / name) for name in ("nova", "finanz-junkie", "aquarium-zentrum")}
    supervisor = _supervisor(tmp_path, records)
    admission = _admit(supervisor, "nova")
    assert admission.status == "created"
    active = supervisor.list_active_admissions()
    assert len(active) == 1 and active[0]["target_space_id"] == records["nova"].space_id
    original = {name: supervisor.current_governance(name).revision for name in records}
    records["finanz-junkie"] = replace(records["finanz-junkie"], revision=original["finanz-junkie"] + 1)
    assert supervisor.current_governance("nova").revision == original["nova"]
    assert supervisor.current_governance("aquarium-zentrum").revision == original["aquarium-zentrum"]
    assert supervisor.current_governance("finanz-junkie").revision == original["finanz-junkie"] + 1



def test_legacy_global_yolo_cannot_admit_non_enrolled_spaces(tmp_path: Path, monkeypatch) -> None:
    records = {
        "nova": _governance(tmp_path / "nova"),
        "finanz-junkie": _governance(tmp_path / "finanz-junkie", yolo=False),
        "aquarium-zentrum": _governance(tmp_path / "aquarium-zentrum", enrolled=False),
    }
    supervisor = _supervisor(tmp_path, records)
    monkeypatch.setenv("NOVA_YOLO", "1")
    assert _admit(supervisor, "nova").status == "created"
    for slug in ("finanz-junkie", "aquarium-zentrum"):
        result = supervisor.admit(slug, {"goal": "must not run", "kind": "maintenance"})
        assert result.status == "rejected"
        assert result.reason == "not_yolo_enrolled"
