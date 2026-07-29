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

from nova.space_supervisor import (
    DASHBOARD_ACTOR_RE,
    ManagedSpaceGovernance,
    ManagedSpaceSupervisor,
    managed_space_execution_options_for_run,
)
from cli.swarm_host import SidekickSwarmService, SwarmExecutionOptions
from swarm_core.engine import PreCompletionContext
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


def test_restart_coalesces_the_same_durable_target_intent_admission(tmp_path: Path) -> None:
    records = {"alpha": _governance(tmp_path / "alpha")}
    created = _admit(_supervisor(tmp_path, records))
    resumed = _admit(_supervisor(tmp_path, records))

    assert created.status == "created"
    assert resumed.status == "coalesced"
    assert resumed.run_id == created.run_id
    assert resumed.capability is None


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


def test_human_abandonment_holds_the_slot_until_explicit_dashboard_cancellation(tmp_path: Path) -> None:
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
    assert _admit(supervisor, "beta").reason == "active_limit"
    assert supervisor.record_completion(admission.run_id) is False
    assert supervisor.cancel(admission.admission_id, actor=_DASHBOARD_ACTOR) is True
    assert _admit(supervisor, "beta").status == "created"


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
    assert blocked.reason == "active_limit"
    assert restarted.list_active_admissions()[0]["state"] == "paused"


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
    assert _admit(restarted, "beta").reason == "active_limit"
    blocked = restarted.list_active_admissions()[0]
    assert blocked["state"] == "paused"
    run = ProjectSwarmStore(records["alpha"].canonical_root).get_run(blocked["run_id"])
    assert run is not None and run.status == "paused"


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
    assert _admit(restarted, "beta").reason == "active_limit"
    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        reason = connection.execute(
            "SELECT reason FROM supervisor_audit WHERE event_type = 'paused' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()[0]
    assert reason == "diagnostic_mismatch"
