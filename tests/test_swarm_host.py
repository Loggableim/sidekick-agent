from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import threading
import time

import pytest

from cli.swarm_host import SidekickSwarmService
from swarm_core.models import ModelCatalogSnapshot
from swarm_core.store import ProjectSwarmStore
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
                            "decision": "continue",
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
            source="ollama-cloud-live",
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
            source="ollama-cloud-live",
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
