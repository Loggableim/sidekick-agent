from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from nova.space_supervision_runtime import (
    NovaSpaceSupervisionRuntime,
    append_ticker_outcomes,
    ticker_event_log_path,
)
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
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


def test_model_chain_pause_is_ticker_visible_as_catalog_wait_then_resumes_once(
    tmp_path: Path,
) -> None:
    """A provider wait must not be misreported as a global-slot conflict."""
    records = {"aquarium-zentrum": _governance(tmp_path / "aquarium-zentrum")}
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=records.get,
    )
    dispatched: list[tuple[Path, str]] = []
    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=lambda root, run_id: dispatched.append((root, run_id)),
    )
    assert runtime.ingest_signal(
        "aquarium-zentrum",
        source="git",
        event_id="initial-change",
        reason_code="git_change",
    )
    started = runtime.pulse(now_epoch=0.0)[0]
    root = records["aquarium-zentrum"].canonical_root
    store = ProjectSwarmStore(root)
    store.set_run_status(started.run_id, "paused")
    store.append_event_once(
        started.run_id,
        "run.paused",
        {"reason": "model_chain_exhausted"},
        idempotency_key="provider-pause",
    )
    assert supervisor.reconcile_host_dispatch(
        root,
        started.run_id,
        failure_reason="host_execution_returned",
    ) == "paused"

    # A real heartbeat/change can arrive while the paused run occupies the
    # global slot. It must surface the provider wait instead of active_limit.
    assert runtime.ingest_signal(
        "aquarium-zentrum",
        source="ci",
        event_id="while-paused",
        reason_code="ci_change",
    )
    waiting = runtime.pulse(now_epoch=1.0)
    assert [(item.status, item.run_id) for item in waiting] == [
        ("waiting_for_catalog", started.run_id)
    ]
    append_ticker_outcomes(supervisor, waiting, observed_at=1.0)
    ticker = [
        json.loads(line)
        for line in ticker_event_log_path(supervisor).read_text(encoding="utf-8").splitlines()
    ]
    wait_event = ticker[-1]
    assert wait_event["space"] == "aquarium-zentrum"
    assert wait_event["source"] == "bridge"
    assert wait_event["reason"] == "model_chain_exhausted"
    assert wait_event["status"] == "pending"
    assert str(root) not in json.dumps(wait_event)

    store.save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=("deepseek-v4-flash",),
            healthy=True,
            source="ollama-cloud-api-live-verified",
        )
    )
    assert [item.status for item in runtime.pulse(now_epoch=2.0)] == ["auto_resumed"]
    assert dispatched == [(root, started.run_id), (root, started.run_id)]
    runtime.pulse(now_epoch=3.0)
    assert dispatched == [(root, started.run_id), (root, started.run_id)]
