from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import threading
import time

import pytest

import cli.models as models
import cli.swarm_host as swarm_host
from cli.swarm_host import (
    OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE,
    SidekickSwarmService,
)
from swarm_core.models import ModelCatalogSnapshot, ModelRegistry
from swarm_core.router import ModelRouter
from swarm_core.store import ProjectSwarmStore
from swarm_core.transport import ModelProviderError
from swarm_core.types import ActionCapabilities


_ROUTED_MODELS = (
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "kimi-k2.6",
    "minimax-m3",
    "glm-5.2",
    "kimi-k2.7-code",
    "nemotron-3-super",
)


def _valid_response(*_args, **_kwargs):
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "work": "bounded test work",
                            "evidence": ["test:evidence"],
                            "decision": "approve",
                            "approved": True,
                        }
                    )
                }
            }
        ]
    }


def test_run_never_refreshes_an_absent_catalog_or_calls_another_provider(
    tmp_path: Path,
):
    """Catches a run implicitly discovering models or falling back outside Ollama."""
    refreshes: list[object] = []
    calls: list[dict] = []

    def unexpected_refresh():
        refreshes.append(object())
        raise AssertionError("run must not refresh the catalog")

    def unexpected_call(**kwargs):
        calls.append(kwargs)
        raise AssertionError("unhealthy/missing catalog must pause before transport")

    service = SidekickSwarmService(
        call_llm=unexpected_call,
        catalog_refresher=unexpected_refresh,
    )

    summary = service.run("inspect safely", tmp_path)

    assert summary.status == "paused"
    assert summary.pause_reason == "no_eligible_model"
    assert refreshes == []
    assert calls == []
    events = ProjectSwarmStore(tmp_path).list_events(summary.run_id)
    assert any(event.event_type == "model_catalog.unavailable" for event in events)


def test_live_refresh_never_routes_a_models_dev_only_model(
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches a supplemental picker ID being persisted as a live Swarm route."""
    picker_calls: list[object] = []

    def supplemental_picker(**_kwargs):
        picker_calls.append(object())
        return ["deepseek-v4-flash", "gemma4:31b", "qwen3.5"]

    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.com/v1")
    monkeypatch.setattr(
        models,
        "fetch_api_models",
        lambda _api_key, _base_url, *, timeout: [
            "deepseek-v4-flash",
            "gemma4:31b",
        ],
    )
    monkeypatch.setattr(
        models,
        "fetch_ollama_cloud_models",
        supplemental_picker,
    )

    snapshot = swarm_host._refresh_ollama_catalog()

    assert snapshot.healthy is True
    assert snapshot.source == OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE
    assert snapshot.models == ("deepseek-v4-flash", "gemma4:31b")
    assert "qwen3.5" not in snapshot.models
    assert picker_calls == []
    assert ModelRouter(ModelRegistry(snapshot.models)).select(
        "vision", {"vision"}
    ).models == ("gemma4:31b",)


def test_live_refresh_rejects_a_local_ollama_base_url(
    monkeypatch: pytest.MonkeyPatch,
):
    """A generic provider override must never become a Cloud routing proof."""
    api_calls: list[tuple[str, str]] = []
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(
        models,
        "fetch_api_models",
        lambda api_key, base_url, *, timeout: (
            api_calls.append((api_key, base_url)) or ["deepseek-v4-flash"]
        ),
    )

    snapshot = swarm_host._refresh_ollama_catalog()

    assert snapshot.models == ()
    assert snapshot.healthy is False
    assert snapshot.source == swarm_host.OLLAMA_CLOUD_UNAVAILABLE_CATALOG_SOURCE
    assert api_calls == []


def test_verified_catalog_pauses_when_endpoint_flips_to_local_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A catalog proof must not survive a later local endpoint override."""
    ProjectSwarmStore(tmp_path).save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=_ROUTED_MODELS,
            healthy=True,
            source=OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE,
        )
    )
    calls: list[dict] = []
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    service = SidekickSwarmService(
        call_llm=lambda **kwargs: calls.append(kwargs) or _valid_response()
    )

    summary = service.run("must not use local Ollama", tmp_path)

    assert summary.status == "paused"
    assert summary.pause_reason == "no_eligible_model"
    assert calls == []
    unavailable = next(
        event
        for event in ProjectSwarmStore(tmp_path).list_events(summary.run_id)
        if event.event_type == "model_catalog.unavailable"
    )
    assert unavailable.payload["endpoint_trusted"] is False


def test_default_sidekick_dispatch_rechecks_the_cloud_endpoint(
    monkeypatch: pytest.MonkeyPatch,
):
    """An environment flip after engine construction cannot reach a local server."""
    import runtime.auxiliary_client as auxiliary_client

    dispatched: list[dict] = []
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(
        auxiliary_client,
        "call_llm",
        lambda **kwargs: dispatched.append(kwargs) or _valid_response(),
    )

    with pytest.raises(ModelProviderError, match="canonical Ollama Cloud"):
        swarm_host._sidekick_call_llm(
            task="swarm",
            provider="ollama-cloud",
            model="deepseek-v4-flash",
            messages=[],
        )

    assert dispatched == []


def test_legacy_live_snapshot_pauses_until_an_explicit_verified_refresh(
    tmp_path: Path,
):
    """Catches an old merged snapshot making an unproven route executable."""
    transport_calls: list[dict] = []
    ProjectSwarmStore(tmp_path).save_model_catalog_snapshot(
        ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=("deepseek-v4-flash",),
            healthy=True,
            source="ollama-cloud-live",
        )
    )
    service = SidekickSwarmService(
        call_llm=lambda **kwargs: transport_calls.append(kwargs) or _valid_response()
    )

    summary = service.run("must wait for a verified catalog", tmp_path)

    assert summary.status == "paused"
    assert summary.pause_reason == "no_eligible_model"
    assert transport_calls == []
    events = ProjectSwarmStore(tmp_path).list_events(summary.run_id)
    unavailable = next(
        event for event in events if event.event_type == "model_catalog.unavailable"
    )
    assert unavailable.payload["verified"] is False


def test_explicit_refresh_persists_live_catalog_and_host_transport_is_slot_bound(
    tmp_path: Path,
):
    """Catches hidden catalog writes or a transport escaping the Ollama slot/provider."""
    calls: list[dict] = []
    slots: list[tuple[str, str]] = []

    @contextmanager
    def provider_slot(run_id: str, provider: str):
        slots.append((run_id, provider))
        yield

    def call_llm(**kwargs):
        calls.append(kwargs)
        return _valid_response()

    service = SidekickSwarmService(
        call_llm=call_llm,
        catalog_refresher=lambda: ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=_ROUTED_MODELS,
            healthy=True,
            source=OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE,
        ),
        provider_slot=provider_slot,
    )

    snapshot = service.refresh_models(tmp_path)
    summary = service.run("produce a structured review", tmp_path)

    assert snapshot.healthy is True
    assert summary.status == "completed"
    assert calls
    assert len(slots) == len(calls)
    assert all(provider == "ollama-cloud" for _run_id, provider in slots)
    assert all(call["provider"] == "ollama-cloud" for call in calls)
    assert all(call["model"] in _ROUTED_MODELS for call in calls)
    assert all("gpt-oss" not in call["model"] for call in calls)
    restored = ProjectSwarmStore.open_read_only(tmp_path).get_model_catalog_snapshot(
        "ollama-cloud"
    )
    assert restored is not None
    assert restored.models == _ROUTED_MODELS


def test_started_run_waits_for_human_resume_at_a_model_boundary(tmp_path: Path):
    """Catches a paused background run completing after its in-flight call returns."""
    first_call_started = threading.Event()
    release_first_call = threading.Event()
    calls: list[dict] = []

    def call_llm(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            first_call_started.set()
            assert release_first_call.wait(timeout=2)
        return _valid_response()

    service = SidekickSwarmService(
        call_llm=call_llm,
        catalog_refresher=lambda: ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=_ROUTED_MODELS,
            healthy=True,
            source=OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE,
        ),
    )
    service.refresh_models(tmp_path)
    run = service.start_run("pause at a safe boundary", tmp_path)
    result: dict[str, object] = {}

    def execute() -> None:
        try:
            result["summary"] = service.execute_run(tmp_path, run.run_id)
        except Exception as exc:  # pragma: no cover - assertion below exposes it
            result["error"] = exc

    worker = threading.Thread(target=execute)
    worker.start()
    assert first_call_started.wait(timeout=1)

    paused = service.pause(tmp_path, run.run_id)
    assert paused.status == "paused"
    release_first_call.set()

    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        events = ProjectSwarmStore.open_read_only(tmp_path).list_events(run.run_id)
        if any(
            event.event_type == "work.completed"
            and event.payload.get("role") == "scout"
            for event in events
        ):
            break
        time.sleep(0.01)
    assert (
        ProjectSwarmStore.open_read_only(tmp_path).get_run(run.run_id).status
        == "paused"
    )
    assert len(calls) == 1
    assert worker.is_alive()

    resumed = service.resume(tmp_path, run.run_id)
    assert resumed.status == "running"
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert "error" not in result
    assert result["summary"].status == "completed"
    assert len(calls) == 8


def test_human_approval_is_proposal_bound_and_cannot_execute_an_action(
    tmp_path: Path,
):
    """Catches host approval fabricating model evidence or invoking an adapter."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(run_id="approval-run")
    store.append_event(
        run.run_id,
        "swarm.action_proposed",
        {
            "proposal_id": "proposal-1",
            "requested_action": {
                "name": "write_project_file",
                "arguments": {"path": "report.txt"},
                "use_worktree": True,
            },
            "evidence_refs": ["evidence:verified"],
        },
    )
    service = SidekickSwarmService(
        action_classifier=lambda _action: ActionCapabilities(
            category="project",
            reversible=True,
            external=False,
            cost_increasing=False,
        )
    )

    approval = service.record_human_approval(
        tmp_path,
        run.run_id,
        "proposal-1",
        actor_id="cli:alice",
        approved=False,
    )

    assert approval.approval_type == "human"
    assert approval.approver_id == "cli:alice"
    assert approval.approved is False
    assert approval.model_family is None
    assert approval.evidence_refs == ()
    assert ProjectSwarmStore(tmp_path).list_approvals(run.run_id) == [approval]

    with pytest.raises(ValueError, match="proposal"):
        service.record_human_approval(
            tmp_path,
            run.run_id,
            "unknown-proposal",
            actor_id="cli:alice",
        )


def test_existing_run_controls_do_not_initialize_an_absent_project(tmp_path: Path):
    """Catches pause/resume/approval typos creating a new empty .swarm tree."""
    project = tmp_path / "uninitialized"
    project.mkdir()
    service = SidekickSwarmService()

    with pytest.raises(FileNotFoundError, match="not initialized"):
        service.pause(project, "unknown-run")

    assert not (project / ".swarm").exists()


def test_execution_failure_returns_a_running_run_to_a_sanitized_pause(
    tmp_path: Path,
):
    """A synchronous host failure must leave the durable run resumable."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(run_id="cli-failure")
    service = SidekickSwarmService()

    paused = service.record_execution_failure(
        tmp_path,
        run.run_id,
        error_type="RuntimeError",
    )

    assert paused.status == "paused"
    assert ProjectSwarmStore(tmp_path).get_run(run.run_id).status == "paused"
    assert ProjectSwarmStore(tmp_path).list_events(run.run_id)[-1].payload == {
        "error_type": "RuntimeError"
    }


def test_execution_lease_recovery_requires_a_bounded_host_actor(tmp_path: Path):
    """Catches a caller forging an arbitrary recovery-audit principal."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(run_id="recover-host-actor")
    assert store.claim_run_execution_lease(run.run_id, "abandoned-owner")
    service = SidekickSwarmService()

    with pytest.raises(ValueError, match="host actor"):
        service.recover_execution_lease(
            tmp_path,
            run.run_id,
            actor_id="manual:alice",
        )
    with pytest.raises(ValueError, match="host actor"):
        service.recover_execution_lease(
            tmp_path,
            run.run_id,
            actor_id="dashboard:   ",
        )

    recovered = service.recover_execution_lease(
        tmp_path,
        run.run_id,
        actor_id="dashboard:trusted-test-principal",
    )

    assert recovered.status == "paused"
    assert ProjectSwarmStore(tmp_path).list_events(run.run_id)[-1].payload == {
        "actor_id": "dashboard:trusted-test-principal"
    }


def test_execution_lease_recovery_authorizes_only_the_fifo_unresolved_attempt(
    tmp_path: Path,
):
    """Catches recovery authorizing a completed/failed attempt instead of its successor."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(run_id="recover-fifo-attempt")
    first_attempt = store.append_event(
        run.run_id,
        "model.attempt_started",
        {"role": "scout", "model": "deepseek-v4-flash"},
    )
    second_attempt = store.append_event(
        run.run_id,
        "model.attempt_started",
        {"role": "scout", "model": "deepseek-v4-flash"},
    )
    store.append_event(
        run.run_id,
        "model.attempt_failed",
        {
            "role": "scout",
            "model": "deepseek-v4-flash",
            "reason": "call_error",
        },
    )
    unresolved_attempt = store.append_event(
        run.run_id,
        "model.attempt_started",
        {"role": "scout", "model": "deepseek-v4-flash"},
    )
    store.record_workflow_role_checkpoint(
        run.run_id,
        "scout",
        model="deepseek-v4-flash",
        data={
            "work": "scout completed",
            "evidence": ["scout:deepseek-v4-flash"],
            "decision": "scout approves",
        },
    )
    assert first_attempt.sequence < second_attempt.sequence < unresolved_attempt.sequence
    assert store.claim_run_execution_lease(run.run_id, "abandoned-owner")

    recovered = SidekickSwarmService().recover_execution_lease(
        tmp_path,
        run.run_id,
        actor_id="dashboard:trusted-test-principal",
    )

    # A later host can itself die before it starts the authorized retry.  Its
    # recovery must retain the original handoff rather than writing a duplicate
    # authorization that the Engine will correctly reject.
    assert store.claim_run_execution_lease(run.run_id, "second-abandoned-owner")
    recovered_again = SidekickSwarmService().recover_execution_lease(
        tmp_path,
        run.run_id,
        actor_id="dashboard:trusted-test-principal",
    )

    assert recovered.status == "paused"
    assert recovered_again.status == "paused"
    assert [
        event.payload
        for event in ProjectSwarmStore(tmp_path).list_events(run.run_id)
        if event.event_type == "model.attempt_replay_authorized_by_human"
    ] == [
        {
            "actor_id": "dashboard:trusted-test-principal",
            "original_attempt_sequence": unresolved_attempt.sequence,
            "role": "scout",
            "model": "deepseek-v4-flash",
        }
    ]
