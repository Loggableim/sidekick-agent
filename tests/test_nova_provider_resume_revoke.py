from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime
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


def test_second_provider_pause_requires_fresh_catalog_and_stays_paused_after_revoke(
    tmp_path: Path,
) -> None:
    records = {
        "nova": _governance(
            tmp_path / "spaces" / "nova", yolo=False, enrolled=True
        ),
        "finanzjunkie": _governance(
            tmp_path / "spaces" / "finanzjunkie", yolo=True, enrolled=False
        ),
        "aquarium-zentrum": _governance(tmp_path / "spaces" / "aquarium-zentrum"),
    }
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=records.get,
    )
    dispatched: list[tuple[Path, str]] = []
    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=lambda root, run_id: dispatched.append((root, run_id)),
    )

    assert not runtime.ingest_signal(
        "nova", source="git", event_id="nova-ignored", reason_code="git_change"
    )
    assert not runtime.ingest_signal(
        "finanzjunkie", source="ci", event_id="finance-ignored", reason_code="ci_change"
    )
    assert runtime.ingest_signal(
        "aquarium-zentrum",
        source="ci",
        event_id="aquarium-provider",
        reason_code="ci_failed",
    )
    first = runtime.pulse(now_epoch=0.0)[0]
    assert first.status == "started"
    root = records["aquarium-zentrum"].canonical_root
    store = ProjectSwarmStore(root)

    store.set_run_status(first.run_id, "paused")
    store.append_event_once(
        first.run_id,
        "run.paused",
        {"reason": "model_chain_exhausted"},
        idempotency_key="provider-pause-one",
    )
    assert supervisor.reconcile_host_dispatch(
        root, first.run_id, failure_reason="host_execution_returned"
    ) == "paused"
    store.save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=("deepseek-v4-flash",),
            healthy=True,
            source="ollama-cloud-api-live-verified",
        )
    )
    assert [item.status for item in runtime.pulse(now_epoch=1.0)] == ["auto_resumed"]
    assert runtime.pulse(now_epoch=2.0) == ()
    assert len(dispatched) == 2

    store.set_run_status(first.run_id, "paused")
    store.append_event_once(
        first.run_id,
        "run.paused",
        {"reason": "model_chain_exhausted"},
        idempotency_key="provider-pause-two",
    )
    assert supervisor.reconcile_host_dispatch(
        root, first.run_id, failure_reason="host_execution_returned"
    ) == "paused"
    store.save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=("deepseek-v4-flash",),
            healthy=True,
            source="ollama-cloud-api-live-verified",
            refreshed_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        )
    )
    waiting = runtime.pulse(now_epoch=3.0)
    assert len(waiting) == 1
    assert waiting[0].status == "waiting_for_catalog"
    assert waiting[0].run_id == first.run_id

    records["aquarium-zentrum"] = replace(
        records["aquarium-zentrum"], enrolled=False, revision=2
    )
    store.save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=("deepseek-v4-flash",),
            healthy=True,
            source="ollama-cloud-api-live-verified",
            refreshed_at=datetime.now(timezone.utc) + timedelta(seconds=1),
        )
    )
    assert runtime.pulse(now_epoch=4.0) == ()
    assert supervisor.auto_resume_recoverable_run(
        "aquarium-zentrum",
        dispatcher=lambda run_root, run_id: dispatched.append((run_root, run_id)),
    ) == ("none", None)
    assert store.get_run(first.run_id).status == "paused"
    assert dispatched == [(root, first.run_id), (root, first.run_id)]


def test_host_refreshes_catalog_for_paused_recoverable_space(tmp_path: Path, monkeypatch) -> None:
    from cli.web_server import _refresh_recoverable_provider_catalogs
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
    assert runtime.ingest_signal("aquarium-zentrum", source="ci", event_id="provider-refresh", reason_code="ci_failed")
    first = runtime.pulse(now_epoch=0.0)[0]
    root = records["aquarium-zentrum"].canonical_root
    store = ProjectSwarmStore(root)
    store.set_run_status(first.run_id, "paused")
    store.append_event_once(first.run_id, "run.paused", {"reason": "model_chain_exhausted"}, idempotency_key="host-refresh")
    assert supervisor.reconcile_host_dispatch(root, first.run_id, failure_reason="host_execution_returned") == "paused"
    calls: list[Path] = []
    class FakeService:
        def refresh_models(self, project_root: Path):
            calls.append(project_root)
            snapshot = ModelCatalogSnapshot(provider="ollama-cloud", models=("deepseek-v4-flash",), healthy=True, source="ollama-cloud-api-live-verified")
            store.save_model_catalog_snapshot(snapshot)
            return snapshot
    monkeypatch.setattr("cli.swarm.get_swarm_service", lambda: FakeService())
    assert _refresh_recoverable_provider_catalogs(supervisor) == 1
    assert _refresh_recoverable_provider_catalogs(supervisor) == 0
    assert calls == [root]
    calls.clear()
    assert _refresh_recoverable_provider_catalogs(supervisor, now=float("nan")) == 0
    assert _refresh_recoverable_provider_catalogs(supervisor, now=float("inf")) == 0
    assert calls == []
    assert [item.status for item in runtime.pulse(now_epoch=1.0)] == ["auto_resumed"]
    assert len(dispatched) == 2

def test_provider_refresh_attempts_prune_stale_run_ids() -> None:
    import cli.web_server as web_server
    attempts = web_server._PROVIDER_REFRESH_ATTEMPTS
    previous = dict(attempts)
    try:
        attempts.clear()
        attempts.update({f"stale-{index}": 0.0 for index in range(1025)})
        class EmptySupervisor:
            def list_active_admissions(self):
                return []
        assert web_server._refresh_recoverable_provider_catalogs(EmptySupervisor(), now=200000.0) == 0
        assert len(attempts) <= web_server._PROVIDER_REFRESH_MAX_ATTEMPTS
    finally:
        attempts.clear()
        attempts.update(previous)

def test_provider_auto_resume_revocation_before_dispatch_is_fail_closed(
    tmp_path: Path,
) -> None:
    """A governance revoke at the host handoff must not dispatch the child."""

    target = "aquarium-zentrum"
    records = {target: _governance(tmp_path / target)}

    class RevokingSupervisor(ManagedSpaceSupervisor):
        armed = False

        def _before_host_dispatch(self, capability) -> None:  # type: ignore[no-untyped-def]
            del capability
            if self.armed:
                records[target] = replace(records[target], enrolled=False, revision=2)

    supervisor = RevokingSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=records.get,
    )
    dispatched: list[tuple[Path, str]] = []
    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=lambda root, run_id: dispatched.append((root, run_id)),
    )
    assert runtime.ingest_signal(
        target, source="ci", event_id="provider-revoke-race", reason_code="ci_failed"
    )
    first = runtime.pulse(now_epoch=0.0)[0]
    supervisor.armed = True
    dispatched.clear()
    root = records[target].canonical_root
    store = ProjectSwarmStore(root)
    store.set_run_status(first.run_id, "paused")
    store.append_event_once(
        first.run_id,
        "run.paused",
        {"reason": "model_chain_exhausted"},
        idempotency_key="provider-revoke-race-pause",
    )
    assert supervisor.reconcile_host_dispatch(
        root, first.run_id, failure_reason="host_execution_returned"
    ) == "paused"
    store.save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=("deepseek-v4-flash",),
            healthy=True,
            source="ollama-cloud-api-live-verified",
            refreshed_at=datetime.now(timezone.utc) + timedelta(seconds=1),
        )
    )

    status, run_id = supervisor.auto_resume_recoverable_run(
        target,
        dispatcher=lambda run_root, child_run_id: dispatched.append(
            (run_root, child_run_id)
        ),
    )
    assert status == "start_failed"
    assert run_id == first.run_id
    assert dispatched == []
    assert store.get_run(first.run_id).status == "paused"
    admission = supervisor.list_active_admissions()
    assert admission and admission[0]["state"] == "paused"


