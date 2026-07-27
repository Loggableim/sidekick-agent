from __future__ import annotations

from pathlib import Path
import threading
import time

from swarm_core.engine import SwarmEngine
from swarm_core.models import ModelRegistry, ModelRequest, ModelResponse
from swarm_core.store import ProjectSwarmStore
from swarm_core.transport import ModelTransport


class WorkflowTransport(ModelTransport):
    def __init__(self, fail_models: set[str] | None = None) -> None:
        self.fail_models = fail_models or set()
        self.requests: list[ModelRequest] = []
        self._lock = threading.Lock()

    def complete(self, request: ModelRequest) -> ModelResponse:
        with self._lock:
            self.requests.append(request)
        if request.model in self.fail_models:
            raise RuntimeError(f"{request.model} unavailable")
        return ModelResponse(
            model=request.model,
            content=f"{request.role} completed",
            data={
                "work": f"{request.role} completed",
                "evidence": [f"{request.role}:{request.model}"],
                "decision": f"{request.role} approves",
            },
        )


def _request_by_role(requests: list[ModelRequest], role: str) -> ModelRequest:
    return next(request for request in requests if request.role == role)


def test_coding_team_runs_exact_stages_with_sharded_blackboard_context(tmp_path: Path):
    """Catches missing stages, model drift, or leaking the full blackboard to every role."""
    transport = WorkflowTransport()
    summary = SwarmEngine(transport).run(
        goal="Ship a safe change",
        project_root=tmp_path,
    )

    assert summary.status == "completed"
    assert summary.call_count == 8
    assert [request.role for request in transport.requests[:2]] == [
        "scout",
        "planner",
    ]
    assert {request.role for request in transport.requests[2:4]} == {
        "builder",
        "critic",
    }
    assert {request.role for request in transport.requests[4:6]} == {
        "review_a",
        "review_b",
    }
    assert [request.role for request in transport.requests[6:]] == [
        "integrator",
        "referee",
    ]
    assert "verifier" not in {request.role for request in transport.requests}

    assert _request_by_role(transport.requests, "scout").model == "deepseek-v4-flash"
    assert _request_by_role(transport.requests, "planner").model == "deepseek-v4-pro"
    assert _request_by_role(transport.requests, "builder").model == "minimax-m3"
    assert _request_by_role(transport.requests, "critic").model == "minimax-m3"
    assert _request_by_role(transport.requests, "review_a").model == "glm-5.2"
    assert _request_by_role(transport.requests, "review_b").model == "kimi-k2.7-code"
    assert (
        _request_by_role(transport.requests, "integrator").model == "nemotron-3-super"
    )
    assert _request_by_role(transport.requests, "referee").model == "nemotron-3-super"

    assert set(_request_by_role(transport.requests, "scout").context) == {
        "goal",
        "project_root",
    }
    assert set(_request_by_role(transport.requests, "planner").context) == {
        "goal",
        "scout",
    }
    assert set(_request_by_role(transport.requests, "builder").context) == {
        "goal",
        "plan",
    }
    assert set(_request_by_role(transport.requests, "critic").context) == {
        "goal",
        "plan",
    }
    assert set(_request_by_role(transport.requests, "review_a").context) == {
        "goal",
        "build",
        "critique",
        "verification",
    }
    assert set(_request_by_role(transport.requests, "integrator").context) == {
        "goal",
        "plan",
        "build",
        "critique",
        "verification",
        "reviews",
    }
    assert set(_request_by_role(transport.requests, "referee").context) == {
        "goal",
        "integration",
        "verification",
        "reviews",
    }


def test_engine_persists_structured_work_evidence_decision_and_verifier_events(
    tmp_path: Path,
):
    """Catches a workflow summary that exists only in memory or lacks auditable evidence."""
    summary = SwarmEngine(WorkflowTransport()).run(
        goal="Preserve evidence",
        project_root=tmp_path,
    )

    events = ProjectSwarmStore(tmp_path).list_events(summary.run_id)
    event_types = [event.event_type for event in events]
    verifier_events = [
        event
        for event in events
        if event.event_type == "evidence.recorded"
        and event.payload.get("role") == "verifier"
    ]

    assert summary.status == "completed"
    assert summary.evidence["review_a"] == ["review_a:glm-5.2"]
    assert summary.evidence["review_b"] == ["review_b:kimi-k2.7-code"]
    assert summary.decision == "referee approves"
    assert event_types[0] == "run.started"
    assert "work.completed" in event_types
    assert "evidence.recorded" in event_types
    assert "decision.recorded" in event_types
    assert event_types[-1] == "run.completed"
    assert verifier_events
    assert verifier_events[0].payload["evidence"] == [
        "builder:minimax-m3",
        "critic:minimax-m3",
    ]


def test_role_context_shards_remain_exact_after_reloading_durable_events(
    tmp_path: Path,
):
    """Catches context shards existing only in transient transport requests."""
    summary = SwarmEngine(WorkflowTransport()).run(
        goal="Persist exact shards",
        project_root=tmp_path,
    )

    reloaded = ProjectSwarmStore(tmp_path).list_events(summary.run_id)
    started_by_role = {
        event.payload["role"]: event.payload
        for event in reloaded
        if event.event_type == "work.started"
    }

    assert set(started_by_role["scout"]["context"]) == {"goal", "project_root"}
    assert set(started_by_role["planner"]["context"]) == {"goal", "scout"}
    assert set(started_by_role["builder"]["context"]) == {"goal", "plan"}
    assert set(started_by_role["critic"]["context"]) == {"goal", "plan"}
    assert set(started_by_role["verifier"]["context"]) == {
        "goal",
        "build",
        "critique",
    }
    assert set(started_by_role["review_a"]["context"]) == {
        "goal",
        "build",
        "critique",
        "verification",
    }
    assert (
        started_by_role["review_a"]["context"] == started_by_role["review_b"]["context"]
    )
    assert set(started_by_role["integrator"]["context"]) == {
        "goal",
        "plan",
        "build",
        "critique",
        "verification",
        "reviews",
    }
    assert set(started_by_role["referee"]["context"]) == {
        "goal",
        "integration",
        "verification",
        "reviews",
    }


def test_engine_pauses_and_persists_reason_after_role_chain_exhaustion(
    tmp_path: Path,
):
    """Catches exhausted cloud routing completing or failing without a resumable pause."""
    transport = WorkflowTransport(fail_models={"deepseek-v4-pro", "kimi-k2.6"})
    summary = SwarmEngine(transport).run(
        goal="Pause safely",
        project_root=tmp_path,
    )

    persisted = ProjectSwarmStore(tmp_path).get_run(summary.run_id)
    events = ProjectSwarmStore(tmp_path).list_events(summary.run_id)

    assert summary.status == "paused"
    assert summary.call_count == 3
    assert summary.pause_reason == "model_chain_exhausted"
    assert persisted is not None
    assert persisted.status == "paused"
    assert events[-1].event_type == "run.paused"
    assert events[-1].payload == {
        "attempted_models": ["deepseek-v4-pro", "kimi-k2.6"],
        "reason": "model_chain_exhausted",
        "role": "planner",
    }
    assert [request.model for request in transport.requests] == [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "kimi-k2.6",
    ]


def test_catalog_exhaustion_becomes_a_durable_pause_instead_of_a_running_leak(
    tmp_path: Path,
):
    """Catches router lookup exhaustion escaping while the run remains running."""
    summary = SwarmEngine(
        WorkflowTransport(),
        registry=ModelRegistry(catalog={"gemma4:31b"}),
    ).run(
        goal="Pause when no scout is eligible",
        project_root=tmp_path,
    )

    persisted = ProjectSwarmStore(tmp_path).get_run(summary.run_id)
    events = ProjectSwarmStore(tmp_path).list_events(summary.run_id)

    assert summary.status == "paused"
    assert summary.pause_reason == "no_eligible_model"
    assert summary.call_count == 0
    assert persisted is not None
    assert persisted.status == "paused"
    assert events[-1].event_type == "run.paused"
    assert events[-1].payload == {
        "attempted_models": [],
        "reason": "no_eligible_model",
        "role": "scout",
    }


def test_successful_parallel_sibling_is_persisted_before_other_sibling_pauses(
    tmp_path: Path,
):
    """Catches successful parallel work being discarded when its sibling exhausts."""

    class CriticFailureTransport(WorkflowTransport):
        def complete(self, request: ModelRequest) -> ModelResponse:
            if request.role == "critic":
                with self._lock:
                    self.requests.append(request)
                raise RuntimeError("critic unavailable")
            return super().complete(request)

    summary = SwarmEngine(CriticFailureTransport()).run(
        goal="Retain successful builder evidence",
        project_root=tmp_path,
    )

    events = ProjectSwarmStore(tmp_path).list_events(summary.run_id)
    builder_events = [
        event
        for event in events
        if event.payload.get("role") == "builder"
        and event.event_type
        in {"work.completed", "evidence.recorded", "decision.recorded"}
    ]
    critic_completed = [
        event
        for event in events
        if event.payload.get("role") == "critic"
        and event.event_type == "work.completed"
    ]

    assert summary.status == "paused"
    assert summary.evidence["builder"] == ["builder:minimax-m3"]
    assert [event.event_type for event in builder_events] == [
        "work.completed",
        "evidence.recorded",
        "decision.recorded",
    ]
    assert builder_events[0].payload["work"] == "builder completed"
    assert builder_events[1].payload["evidence"] == ["builder:minimax-m3"]
    assert builder_events[2].payload["decision"] == "builder approves"
    assert critic_completed == []
    assert events[-1].event_type == "run.paused"


def test_failed_model_attempt_is_durable_when_role_fallback_later_succeeds(
    tmp_path: Path,
):
    """Catches fallback success erasing the failed model and failure reason."""
    summary = SwarmEngine(WorkflowTransport(fail_models={"deepseek-v4-pro"})).run(
        goal="Record planner fallback",
        project_root=tmp_path,
    )

    events = ProjectSwarmStore(tmp_path).list_events(summary.run_id)
    failures = [event for event in events if event.event_type == "model.attempt_failed"]

    assert summary.status == "completed"
    assert [event.payload for event in failures] == [
        {
            "model": "deepseek-v4-pro",
            "reason": "call_error",
            "role": "planner",
        }
    ]
    assert any(
        event.event_type == "work.completed"
        and event.payload.get("role") == "planner"
        and event.payload.get("model") == "kimi-k2.6"
        for event in events
    )


def test_parallel_pause_uses_role_order_when_both_siblings_fail_inverted(
    tmp_path: Path,
):
    """Catches as-completed timing choosing Critic over earlier-role Builder."""

    class InvertedFailureTransport(WorkflowTransport):
        def complete(self, request: ModelRequest) -> ModelResponse:
            if request.role in {"builder", "critic"}:
                with self._lock:
                    self.requests.append(request)
                if request.role == "builder":
                    time.sleep(0.04)
                raise RuntimeError(f"{request.role} unavailable")
            return super().complete(request)

    summary = SwarmEngine(InvertedFailureTransport()).run(
        goal="Choose deterministic parallel pause",
        project_root=tmp_path,
    )

    events = ProjectSwarmStore(tmp_path).list_events(summary.run_id)
    failures = [
        event.payload
        for event in events
        if event.event_type == "model.attempt_failed"
        and event.payload["role"] in {"builder", "critic"}
    ]

    assert summary.status == "paused"
    assert failures == [
        {
            "model": "minimax-m3",
            "reason": "call_error",
            "role": "builder",
        },
        {
            "model": "minimax-m3",
            "reason": "call_error",
            "role": "critic",
        },
    ]
    assert events[-1].event_type == "run.paused"
    assert events[-1].payload["role"] == "builder"


def test_parallel_pause_stays_builder_first_when_builder_fails_immediately(
    tmp_path: Path,
):
    """Catches deterministic selection reversing under the complementary timing."""

    class ComplementaryFailureTransport(WorkflowTransport):
        def complete(self, request: ModelRequest) -> ModelResponse:
            if request.role in {"builder", "critic"}:
                with self._lock:
                    self.requests.append(request)
                if request.role == "critic":
                    time.sleep(0.04)
                raise RuntimeError(f"{request.role} unavailable")
            return super().complete(request)

    summary = SwarmEngine(ComplementaryFailureTransport()).run(
        goal="Keep Builder first under complementary timing",
        project_root=tmp_path,
    )

    events = ProjectSwarmStore(tmp_path).list_events(summary.run_id)
    failures = [
        event.payload
        for event in events
        if event.event_type == "model.attempt_failed"
        and event.payload["role"] in {"builder", "critic"}
    ]

    assert summary.status == "paused"
    assert failures == [
        {
            "model": "minimax-m3",
            "reason": "call_error",
            "role": "builder",
        },
        {
            "model": "minimax-m3",
            "reason": "call_error",
            "role": "critic",
        },
    ]
    assert events[-1].event_type == "run.paused"
    assert events[-1].payload["role"] == "builder"
