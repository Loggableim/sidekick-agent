from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from nova.ticker_handler import consume_pending_events
from swarm_core.models import ModelCatalogSnapshot
from swarm_core.store import ProjectSwarmStore


def _governance(root: Path) -> ManagedSpaceGovernance:
    return ManagedSpaceGovernance.from_values(
        space_id=str(uuid4()),
        canonical_root=root,
        root_fingerprint="",
        yolo=True,
        enrolled=True,
        revision=1,
        policy_identity="space-governance:1",
    )


def test_consumer_resumes_provider_paused_run_without_waiting_for_new_heartbeat(
    tmp_path: Path,
) -> None:
    """A fresh verified catalog wakes the existing run exactly once."""
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
    assert runtime.ingest_signal(
        "aquarium-zentrum",
        source="heartbeat",
        event_id="heartbeat:2",
        reason_code="periodic_check",
    )
    started = consume_pending_events(supervisor=supervisor, runtime=runtime)
    run_id = started.outcomes[0].run_id
    assert run_id is not None

    store = ProjectSwarmStore(root)
    store.set_run_status(run_id, "paused")
    store.append_event_once(
        run_id,
        "run.paused",
        {"reason": "model_chain_exhausted"},
        idempotency_key="provider-paused",
    )
    assert supervisor.reconcile_host_dispatch(
        root, run_id, failure_reason="host_execution_returned"
    ) == "paused"
    # Stale heartbeats cannot create a new intent or release the occupied slot.
    assert not runtime.ingest_signal(
        "aquarium-zentrum",
        source="heartbeat",
        event_id="heartbeat:1",
        reason_code="periodic_check",
    )
    store.save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=("deepseek-v4-flash",),
            healthy=True,
            source="ollama-cloud-api-live-verified",
        )
    )

    resumed = consume_pending_events(supervisor=supervisor, runtime=runtime)

    assert resumed.pending_spaces == ()
    assert [outcome.status for outcome in resumed.outcomes] == ["auto_resumed"]
    assert dispatched == [(root, run_id), (root, run_id)]
    # With no new event, the next minute may observe state but cannot dispatch.
    assert consume_pending_events(supervisor=supervisor, runtime=runtime).outcomes == ()
    assert dispatched == [(root, run_id), (root, run_id)]
