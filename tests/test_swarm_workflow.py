from __future__ import annotations

from pathlib import Path
import threading
import time
from unittest.mock import patch

import pytest

from swarm_core.engine import (
    PreCompletionContext,
    PreCompletionResult,
    SwarmEngine,
)
from swarm_core.models import ModelRegistry, ModelRequest, ModelResponse
from swarm_core.router import ModelRouter
from swarm_core.store import ProjectSwarmStore
from swarm_core.transport import ModelProviderError, ModelTransport
from swarm_core.verifier import VerificationResult, VerifierAssessment
from swarm_core.workflow import ModelExecutor, RoleCall, WorkflowPaused


class WorkflowTransport(ModelTransport):
    def __init__(self, fail_models: set[str] | None = None) -> None:
        self.fail_models = fail_models or set()
        self.requests: list[ModelRequest] = []
        self._lock = threading.Lock()

    def complete(self, request: ModelRequest) -> ModelResponse:
        with self._lock:
            self.requests.append(request)
        if request.model in self.fail_models:
            raise ModelProviderError(f"{request.model} unavailable")
        data: dict[str, object] = {
            "work": f"{request.role} completed",
            "evidence": [f"{request.role}:{request.model}"],
            "decision": f"{request.role} approves",
        }
        if request.role in {"review_a", "review_b"}:
            data["approved"] = True
            data["decision"] = "approved"
        return ModelResponse(
            model=request.model,
            content=f"{request.role} completed",
            data=data,
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


def test_engine_uses_injected_read_only_verifier_and_persists_own_provenance(
    tmp_path: Path,
):
    """Catches the verifier being a synthetic copy or a cloud-model role."""

    class RecordingVerifier:
        def __init__(self) -> None:
            self.requests = []

        def verify(self, request):
            self.requests.append(request)
            marker_contents = (request.project_root / "must-not-change.txt").read_text(
                encoding="utf-8"
            )
            return VerificationResult(
                work="Read-only adapter inspected the project marker and candidate outputs.",
                evidence=("verifier:test:marker-read",),
                decision="verified",
                provenance={
                    "adapter": "test-read-only",
                    "mode": "read_only",
                    "operation": "read_project_marker",
                    "marker_contents": marker_contents,
                },
                assessments=(
                    VerifierAssessment(
                        role="builder",
                        capability="building",
                        score=0.8,
                        source_ref="verifier:test:marker-read",
                        safety_passed=True,
                    ),
                ),
            )

    marker = tmp_path / "must-not-change.txt"
    marker.write_text("unchanged", encoding="utf-8")
    transport = WorkflowTransport()
    verifier = RecordingVerifier()

    summary = SwarmEngine(transport, verifier=verifier).run(
        goal="Verify through a local adapter",
        project_root=tmp_path,
    )

    checkpoints = ProjectSwarmStore(tmp_path).get_workflow_role_checkpoints(
        summary.run_id
    )
    verifier_checkpoint = checkpoints["verifier"]
    assert summary.status == "completed"
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert len(verifier.requests) == 1
    assert list(verifier.requests[0].builder["evidence"]) == ["builder:minimax-m3"]
    assert list(verifier.requests[0].critic["evidence"]) == ["critic:minimax-m3"]
    assert "verifier" not in {request.role for request in transport.requests}
    assert verifier_checkpoint.model is None
    assert verifier_checkpoint.data["evidence"] == ["verifier:test:marker-read"]
    assert verifier_checkpoint.data["provenance"] == {
        "adapter": "test-read-only",
        "mode": "read_only",
        "operation": "read_project_marker",
        "marker_contents": "unchanged",
    }
    assert verifier_checkpoint.data["assessments"] == [
        {
            "capability": "building",
            "role": "builder",
            "safety_passed": True,
            "score": 0.8,
            "source_ref": "verifier:test:marker-read",
        }
    ]


def test_engine_pauses_when_verifier_only_relabels_untrusted_model_evidence(
    tmp_path: Path,
):
    """Catches Builder/Critic evidence being accepted as independent verification."""

    class CopyingVerifier:
        def verify(self, request):
            return VerificationResult(
                work="Copied model claims.",
                evidence=tuple(request.builder["evidence"]),
                decision="verified",
                provenance={"adapter": "copying", "mode": "read_only"},
            )

    summary = SwarmEngine(WorkflowTransport(), verifier=CopyingVerifier()).run(
        goal="Reject copied model evidence",
        project_root=tmp_path,
    )

    checkpoints = ProjectSwarmStore(tmp_path).get_workflow_role_checkpoints(
        summary.run_id
    )
    assert summary.status == "paused"
    assert summary.pause_reason == "invalid_verifier_result"
    assert "verifier" not in checkpoints


def test_resumed_run_reuses_durable_verifier_result_without_reinvoking_adapter(
    tmp_path: Path,
):
    """Catches a resumed run changing or duplicating local verification work."""

    class RecordingVerifier:
        def __init__(self) -> None:
            self.calls = 0

        def verify(self, _request):
            self.calls += 1
            return VerificationResult(
                work="Read-only verification completed.",
                evidence=("verifier:test:durable",),
                decision="verified",
                provenance={"adapter": "recording", "mode": "read_only"},
            )

    class FailingRefereeTransport(WorkflowTransport):
        def complete(self, request: ModelRequest) -> ModelResponse:
            if request.role != "referee":
                return super().complete(request)
            with self._lock:
                self.requests.append(request)
            raise ModelProviderError("referee provider unavailable")

    class FailIfReinvokedVerifier:
        def __init__(self) -> None:
            self.calls = 0

        def verify(self, _request):
            self.calls += 1
            raise AssertionError("durable verifier output must be restored")

    first_verifier = RecordingVerifier()
    first_engine = SwarmEngine(
        FailingRefereeTransport(),
        verifier=first_verifier,
    )
    run = first_engine.start_run("Preserve local verification on resume", tmp_path)
    paused = first_engine.execute_run(run.run_id, tmp_path)
    store = ProjectSwarmStore(tmp_path)
    initial_checkpoint = store.get_workflow_role_checkpoints(run.run_id)["verifier"]

    assert paused.status == "paused"
    assert first_verifier.calls == 1
    store.resume_run(run.run_id)

    resumed_verifier = FailIfReinvokedVerifier()
    resumed = SwarmEngine(
        WorkflowTransport(),
        verifier=resumed_verifier,
    ).execute_run(run.run_id, tmp_path)

    restored_checkpoint = store.get_workflow_role_checkpoints(run.run_id)["verifier"]
    assert resumed.status == "paused"
    assert resumed.pause_reason == "model_chain_exhausted"
    assert resumed_verifier.calls == 0
    assert restored_checkpoint == initial_checkpoint


def test_review_roles_require_an_explicit_boolean_approval_vote(tmp_path: Path):
    """Catches a free-form review becoming an execution quorum by accident."""

    class MissingApprovalVoteTransport(WorkflowTransport):
        def complete(self, request: ModelRequest) -> ModelResponse:
            response = super().complete(request)
            if request.role not in {"review_a", "review_b"}:
                return response
            data = dict(response.data)
            data.pop("approved", None)
            return ModelResponse(
                model=response.model,
                content=response.content,
                data=data,
            )

    transport = MissingApprovalVoteTransport()
    summary = SwarmEngine(transport).run(
        goal="Require a durable review vote",
        project_root=tmp_path,
    )

    review_requests = [
        request
        for request in transport.requests
        if request.role in {"review_a", "review_b"}
    ]
    assert summary.status == "paused"
    assert summary.pause_reason == "model_chain_exhausted"
    assert all("approved" in request.prompt for request in review_requests)
    assert not {
        "integrator",
        "referee",
    } & {request.role for request in transport.requests}


@pytest.mark.parametrize(
    ("pack", "workflow", "planner_alias", "review_alias"),
    [
        ("bug-hunt", "bug-hunt", "investigator", "verifier"),
        ("research-team", "research-team", "analyst", "reviewer"),
        ("release-audit", "release-audit", "auditor", "reviewer"),
    ],
)
def test_pack_workflow_profile_applies_declared_role_focuses(
    tmp_path: Path,
    pack: str,
    workflow: str,
    planner_alias: str,
    review_alias: str,
):
    """Catches a selected pack being reduced to an inert label in prompts."""
    project = tmp_path / pack
    project.mkdir()
    transport = WorkflowTransport()

    summary = SwarmEngine(transport).run(
        goal=f"Exercise {pack}",
        project_root=project,
        pack=pack,
    )

    planner = _request_by_role(transport.requests, "planner")
    reviewer = _request_by_role(transport.requests, "review_a")
    assert summary.status == "completed"
    assert planner.model == "deepseek-v4-pro"
    assert f"Workflow profile: {workflow}" in planner.prompt
    assert f"Role alias: {planner_alias}" in planner.prompt
    assert "Pack role focus:" in planner.prompt
    assert f"Role alias: {review_alias}" in reviewer.prompt
    assert "Pack role focus:" in reviewer.prompt


@pytest.mark.parametrize(
    "conflict_data",
    [
        {"conflict": True},
        {"decision": "needs_challenger"},
        {"evidence": [{"conflict": True, "source": "scout"}]},
    ],
)
def test_explicit_planner_conflict_runs_durable_kimi_challenger_and_arbitration(
    tmp_path: Path,
    conflict_data: dict[str, object],
):
    """Catches Kimi challenger work running without a durable conflict signal."""

    class ConflictTransport(WorkflowTransport):
        def complete(self, request: ModelRequest) -> ModelResponse:
            if request.role != "planner":
                return super().complete(request)
            with self._lock:
                self.requests.append(request)
            data: dict[str, object] = {
                "work": "planner completed",
                "evidence": ["planner:deepseek-v4-pro"],
                "decision": "planner approves",
            }
            data.update(conflict_data)
            return ModelResponse(
                model=request.model,
                content="planner completed",
                data=data,
            )

    transport = ConflictTransport()
    summary = SwarmEngine(transport).run(
        goal="Resolve a durable planning conflict",
        project_root=tmp_path,
    )

    roles = [request.role for request in transport.requests]
    checkpoints = ProjectSwarmStore(tmp_path).get_workflow_role_checkpoints(
        summary.run_id
    )
    assert summary.status == "completed"
    assert summary.call_count == 10
    assert roles.count("planner_challenger") == 1
    assert roles.count("planner_arbitrator") == 1
    assert (
        _request_by_role(transport.requests, "planner_challenger").model == "kimi-k2.6"
    )
    assert (
        _request_by_role(transport.requests, "planner_arbitrator").model
        == "deepseek-v4-pro"
    )
    assert set(_request_by_role(transport.requests, "planner_challenger").context) == {
        "goal",
        "scout",
        "plan",
    }
    assert set(_request_by_role(transport.requests, "planner_arbitrator").context) == {
        "goal",
        "scout",
        "plan",
        "planner_challenge",
    }
    assert {"planner_challenger", "planner_arbitrator"} <= set(checkpoints)


def test_engine_executes_a_precreated_run_without_a_second_start_event(tmp_path: Path):
    """Catches asynchronous hosts recreating a run after returning its id to the UI."""
    engine = SwarmEngine(WorkflowTransport())
    run = engine.start_run("Expose the durable run before execution", tmp_path)

    summary = engine.execute_run(run.run_id, tmp_path)

    events = ProjectSwarmStore(tmp_path).list_events(run.run_id)
    assert summary.run_id == run.run_id
    assert summary.status == "completed"
    assert [event.event_type for event in events].count("run.started") == 1
    assert events[0].payload == {
        "goal": "Expose the durable run before execution",
        "pack": "coding-team",
    }


def test_concurrent_engine_execution_claims_one_durable_run_lease(tmp_path: Path):
    """Catches two hosts dispatching Scout for the same active run."""

    class BlockingScoutTransport(WorkflowTransport):
        def __init__(self) -> None:
            super().__init__()
            self.scout_started = threading.Event()
            self.release_scout = threading.Event()

        def complete(self, request: ModelRequest) -> ModelResponse:
            if request.role != "scout":
                return super().complete(request)
            with self._lock:
                self.requests.append(request)
            self.scout_started.set()
            assert self.release_scout.wait(timeout=3)
            return ModelResponse(
                model=request.model,
                content="scout completed",
                data={
                    "work": "scout completed",
                    "evidence": ["scout:deepseek-v4-flash"],
                    "decision": "scout approves",
                },
            )

    transport = BlockingScoutTransport()
    first_engine = SwarmEngine(transport)
    run = first_engine.start_run("Allow only one active executor", tmp_path)
    first_summaries: list[object] = []
    first_errors: list[BaseException] = []
    second_errors: list[BaseException] = []
    second_done = threading.Event()

    def execute_first() -> None:
        try:
            first_summaries.append(first_engine.execute_run(run.run_id, tmp_path))
        except BaseException as exc:  # pragma: no cover - surfaced below
            first_errors.append(exc)

    def execute_second() -> None:
        try:
            SwarmEngine(transport).execute_run(run.run_id, tmp_path)
        except BaseException as exc:  # pragma: no cover - surfaced below
            second_errors.append(exc)
        finally:
            second_done.set()

    first = threading.Thread(target=execute_first)
    second = threading.Thread(target=execute_second)
    first.start()
    try:
        assert transport.scout_started.wait(timeout=2)
        second.start()
        assert second_done.wait(timeout=1)
        assert len(second_errors) == 1
        assert isinstance(second_errors[0], RuntimeError)
        assert "already active" in str(second_errors[0])
        with transport._lock:
            assert [request.role for request in transport.requests].count("scout") == 1
    finally:
        transport.release_scout.set()
        first.join(timeout=4)
        if second.ident is not None:
            second.join(timeout=4)

    assert not first.is_alive()
    assert not second.is_alive()
    assert first_errors == []
    assert len(first_summaries) == 1
    assert getattr(first_summaries[0], "status") == "completed"


def test_execution_lease_covers_an_in_process_human_pause_wait(tmp_path: Path):
    """Catches a second host entering while the first waits for human resume."""
    transport = WorkflowTransport()
    engine = SwarmEngine(transport)
    run = engine.start_run("Keep ownership while human-paused", tmp_path)
    store = ProjectSwarmStore(tmp_path)
    pause_entered = threading.Event()
    release_pause = threading.Event()
    first_errors: list[BaseException] = []
    first_summaries: list[object] = []
    checkpoint_calls = 0

    def checkpoint() -> None:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls != 1:
            return
        store.set_run_status(run.run_id, "paused")
        pause_entered.set()
        assert release_pause.wait(timeout=3)
        store.resume_run(run.run_id)

    def execute_first() -> None:
        try:
            first_summaries.append(
                engine.execute_run(run.run_id, tmp_path, checkpoint=checkpoint)
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            first_errors.append(exc)

    first = threading.Thread(target=execute_first)
    first.start()
    try:
        assert pause_entered.wait(timeout=2)
        with pytest.raises(RuntimeError, match="already active"):
            SwarmEngine(transport).execute_run(
                run.run_id,
                tmp_path,
                checkpoint=lambda: None,
            )
    finally:
        release_pause.set()
        first.join(timeout=4)

    assert not first.is_alive()
    assert first_errors == []
    assert len(first_summaries) == 1
    assert getattr(first_summaries[0], "status") == "completed"


def test_execution_lease_is_not_auto_stolen_after_an_unexpected_process_exit(
    tmp_path: Path,
):
    """Catches a restarted host silently taking over an unexpired durable lease."""
    transport = WorkflowTransport()
    engine = SwarmEngine(transport)
    run = engine.start_run("Fail closed for an abandoned owner", tmp_path)
    store = ProjectSwarmStore(tmp_path)

    assert store.claim_run_execution_lease(run.run_id, "crashed-owner")
    assert not store.release_run_execution_lease(run.run_id, "different-owner")
    try:
        with pytest.raises(RuntimeError, match="already active"):
            engine.execute_run(run.run_id, tmp_path)
        assert transport.requests == []
    finally:
        assert store.release_run_execution_lease(run.run_id, "crashed-owner")


def test_execution_lease_is_released_after_an_unexpected_engine_error(tmp_path: Path):
    """Catches a BaseException leaving a run permanently locked after cleanup."""

    class ProcessAbort(BaseException):
        pass

    probe_results: list[bool] = []
    run_id = ""
    store: ProjectSwarmStore

    class AbortingTransport(ModelTransport):
        def complete(self, request: ModelRequest) -> ModelResponse:
            claimed = store.claim_run_execution_lease(run_id, "probe-owner")
            probe_results.append(claimed)
            if claimed:
                assert store.release_run_execution_lease(run_id, "probe-owner")
            raise ProcessAbort("simulate process-level execution failure")

    engine = SwarmEngine(AbortingTransport())
    run = engine.start_run("Release lease on abort", tmp_path)
    run_id = run.run_id
    store = ProjectSwarmStore(tmp_path)

    with pytest.raises(ProcessAbort):
        engine.execute_run(run.run_id, tmp_path)

    assert probe_results == [False]
    assert store.claim_run_execution_lease(run.run_id, "post-abort-owner")
    assert store.release_run_execution_lease(run.run_id, "post-abort-owner")


def test_resumed_run_reuses_durable_role_checkpoints_and_run_call_budget(
    tmp_path: Path,
):
    """Catches a restart replaying confirmed Builder failure or resetting budget."""

    class PauseAtBuilderTransport(WorkflowTransport):
        def complete(self, request: ModelRequest) -> ModelResponse:
            with self._lock:
                self.requests.append(request)
            if request.role == "builder":
                raise ModelProviderError("temporary builder provider failure")
            return ModelResponse(
                model=request.model,
                content=f"{request.role} completed",
                data={
                    "work": f"{request.role} completed",
                    "evidence": [f"{request.role}:{request.model}"],
                    "decision": f"{request.role} approves",
                },
            )

    first_transport = PauseAtBuilderTransport()
    first_engine = SwarmEngine(first_transport)
    run = first_engine.start_run("Resume only unfinished roles", tmp_path)

    paused = first_engine.execute_run(run.run_id, tmp_path)

    assert paused.status == "paused"
    assert {request.role for request in first_transport.requests} == {
        "scout",
        "planner",
        "builder",
        "critic",
    }
    ProjectSwarmStore(tmp_path).resume_run(run.run_id)

    resumed_transport = WorkflowTransport()
    resumed = SwarmEngine(resumed_transport).execute_run(run.run_id, tmp_path)

    assert resumed.status == "paused"
    assert resumed.pause_reason == "model_chain_exhausted"
    # Builder's only route was already durably confirmed as failed.  A restart
    # retains the three successful role checkpoints but must not pay to retry
    # the same provider call again.
    assert resumed_transport.requests == []
    assert resumed.call_count == 4
    events = ProjectSwarmStore(tmp_path).list_events(run.run_id)
    assert [
        event.payload.get("role")
        for event in events
        if event.event_type == "work.completed"
    ].count("scout") == 1
    assert [
        event.payload.get("role")
        for event in events
        if event.event_type == "work.completed"
    ].count("planner") == 1
    assert [
        event.payload.get("role")
        for event in events
        if event.event_type == "work.completed"
    ].count("critic") == 1


def test_builder_checkpoint_survives_a_blocked_critic_before_restart(
    tmp_path: Path,
):
    """Catches a Builder result being lost while a parallel Critic is in flight."""

    class BlockedCriticTransport(ModelTransport):
        def __init__(self) -> None:
            self.builder_finished = threading.Event()
            self.release_critic = threading.Event()

        def complete(self, request: ModelRequest) -> ModelResponse:
            if request.role == "builder":
                self.builder_finished.set()
                return ModelResponse(
                    model=request.model,
                    content="builder completed",
                    data={
                        "work": "builder completed",
                        "evidence": ["builder:minimax-m3"],
                        "decision": "builder approves",
                    },
                )
            assert request.role == "critic"
            assert self.release_critic.wait(timeout=2)
            raise ModelProviderError("critic process interrupted")

    run = SwarmEngine(WorkflowTransport()).start_run(
        "Resume from the durable Builder checkpoint",
        tmp_path,
    )
    store = ProjectSwarmStore(tmp_path)
    for role, model in (
        ("scout", "deepseek-v4-flash"),
        ("planner", "deepseek-v4-pro"),
    ):
        store.record_workflow_role_checkpoint(
            run.run_id,
            role,
            model=model,
            data={
                "work": f"{role} completed",
                "evidence": [f"{role}:{model}"],
                "decision": f"{role} approves",
            },
        )

    transport = BlockedCriticTransport()
    executor = ModelExecutor(ModelRouter(ModelRegistry()), transport)
    builder_checkpointed = threading.Event()
    failures: list[BaseException] = []

    def checkpoint_success(call: RoleCall, response: ModelResponse) -> None:
        store.record_workflow_role_checkpoint(
            run.run_id,
            call.role,
            model=response.model,
            data=response.data,
        )
        if call.role == "builder":
            builder_checkpointed.set()

    def run_parallel_stage() -> None:
        try:
            executor.complete_many(
                (
                    RoleCall("builder", "build", {}),
                    RoleCall("critic", "critic", {}),
                ),
                run_id=run.run_id,
                on_success=checkpoint_success,
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.append(exc)

    worker = threading.Thread(target=run_parallel_stage)
    worker.start()
    try:
        assert transport.builder_finished.wait(timeout=1)
        # This is the crash boundary: Builder must already be in SQLite while
        # Critic remains blocked in its provider call.
        assert builder_checkpointed.wait(timeout=1)
        assert "builder" in store.get_workflow_role_checkpoints(run.run_id)
    finally:
        transport.release_critic.set()
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], WorkflowPaused)

    # Simulate the next host process resuming only the unfinished Critic and
    # all downstream stages.  It must never dispatch Builder again.
    store.set_run_status(run.run_id, "paused")
    store.resume_run(run.run_id)
    resumed_transport = WorkflowTransport()
    resumed = SwarmEngine(resumed_transport).execute_run(run.run_id, tmp_path)

    assert resumed.status == "completed"
    assert "builder" not in {request.role for request in resumed_transport.requests}
    assert "critic" in {request.role for request in resumed_transport.requests}
    assert len(store.get_workflow_role_checkpoints(run.run_id)) == 9


def test_resumed_run_skips_a_durably_confirmed_model_failure(tmp_path: Path):
    """Catches restart retrying Planner's failed first model before its fallback."""
    engine = SwarmEngine(WorkflowTransport())
    run = engine.start_run("Skip confirmed Planner failure", tmp_path)
    store = ProjectSwarmStore(tmp_path)
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
    store.append_event(
        run.run_id,
        "model.attempt_started",
        {"role": "planner", "model": "deepseek-v4-pro"},
    )
    store.append_event(
        run.run_id,
        "model.attempt_failed",
        {
            "role": "planner",
            "model": "deepseek-v4-pro",
            "reason": "call_error",
        },
    )
    store.set_run_status(run.run_id, "paused")
    store.resume_run(run.run_id)

    resumed_transport = WorkflowTransport()
    resumed = SwarmEngine(resumed_transport).execute_run(run.run_id, tmp_path)

    assert resumed.status == "completed"
    planner_requests = [
        request.model
        for request in resumed_transport.requests
        if request.role == "planner"
    ]
    assert planner_requests == ["kimi-k2.6"]


def test_resumed_run_pauses_without_retrying_an_exhausted_confirmed_chain(
    tmp_path: Path,
):
    """Catches a restart paying for a Planner chain already known to be exhausted."""
    engine = SwarmEngine(WorkflowTransport())
    run = engine.start_run("Do not replay exhausted Planner chain", tmp_path)
    store = ProjectSwarmStore(tmp_path)
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
    for model in ("deepseek-v4-pro", "kimi-k2.6"):
        store.append_event(
            run.run_id,
            "model.attempt_started",
            {"role": "planner", "model": model},
        )
        store.append_event(
            run.run_id,
            "model.attempt_failed",
            {"role": "planner", "model": model, "reason": "call_error"},
        )
    store.set_run_status(run.run_id, "paused")
    store.resume_run(run.run_id)

    resumed_transport = WorkflowTransport()
    resumed = SwarmEngine(resumed_transport).execute_run(run.run_id, tmp_path)

    assert resumed.status == "paused"
    assert resumed.pause_reason == "model_chain_exhausted"
    assert resumed_transport.requests == []


def test_resumed_run_counts_an_attempt_persisted_before_a_crash_window(
    tmp_path: Path,
):
    """Catches a restart forgetting a provider call between dispatch and response."""
    engine = SwarmEngine(WorkflowTransport(), max_calls=1)
    run = engine.start_run("Never exceed the durable one-call budget", tmp_path)
    store = ProjectSwarmStore(tmp_path)
    store.append_event(
        run.run_id,
        "model.attempt_started",
        {"role": "scout", "model": "deepseek-v4-flash"},
    )
    store.set_run_status(run.run_id, "paused")
    store.resume_run(run.run_id)

    resumed_transport = WorkflowTransport()
    resumed = SwarmEngine(resumed_transport, max_calls=1).execute_run(
        run.run_id,
        tmp_path,
    )

    assert resumed.status == "paused"
    assert resumed.pause_reason == "unmatched_model_attempt"
    assert resumed.call_count == 1
    assert resumed_transport.requests == []


def test_idempotent_transport_may_resume_an_unmatched_provider_dispatch(
    tmp_path: Path,
):
    """Catches fail-closed recovery blocking a transport with an explicit guarantee."""

    class IdempotentTransport(WorkflowTransport):
        supports_idempotent_replay = True

    initial_engine = SwarmEngine(WorkflowTransport())
    run = initial_engine.start_run("Retry only with idempotency proof", tmp_path)
    store = ProjectSwarmStore(tmp_path)
    store.append_event(
        run.run_id,
        "model.attempt_started",
        {"role": "scout", "model": "deepseek-v4-flash"},
    )
    store.set_run_status(run.run_id, "paused")
    store.resume_run(run.run_id)

    transport = IdempotentTransport()
    resumed = SwarmEngine(transport).execute_run(run.run_id, tmp_path)

    assert resumed.status == "completed"
    assert [request.role for request in transport.requests].count("scout") == 1
    assert resumed.call_count == 9


def test_human_replay_authorization_precedes_a_later_same_model_success(
    tmp_path: Path,
):
    """Catches matching a recovered attempt after its later retry completed."""
    engine = SwarmEngine(WorkflowTransport())
    run = engine.start_run(
        "Consume replay handoff before a later Scout success", tmp_path
    )
    store = ProjectSwarmStore(tmp_path)
    original_attempt = store.append_event(
        run.run_id,
        "model.attempt_started",
        {"role": "scout", "model": "deepseek-v4-flash"},
    )
    store.append_event(
        run.run_id,
        "model.attempt_replay_authorized_by_human",
        {
            "actor_id": "os:uid:4242",
            "original_attempt_sequence": original_attempt.sequence,
            "role": "scout",
            "model": "deepseek-v4-flash",
        },
    )
    store.append_event(
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
    store.set_run_status(run.run_id, "paused")
    store.resume_run(run.run_id)

    transport = WorkflowTransport()
    resumed = SwarmEngine(transport).execute_run(run.run_id, tmp_path)

    assert resumed.status == "completed"
    assert "scout" not in [request.role for request in transport.requests]


@pytest.mark.parametrize(
    "authorization_kind",
    ("malformed", "duplicate", "wrong_sequence", "wrong_model"),
)
def test_invalid_human_replay_authorization_fails_closed(
    tmp_path: Path,
    authorization_kind: str,
):
    """Catches malformed, duplicate, or forged replay handoffs dispatching a model."""
    run = SwarmEngine(WorkflowTransport()).start_run(
        "Reject an invalid replay handoff",
        tmp_path,
    )
    store = ProjectSwarmStore(tmp_path)
    original_attempt = store.append_event(
        run.run_id,
        "model.attempt_started",
        {"role": "scout", "model": "deepseek-v4-flash"},
    )
    authorization = {
        "actor_id": "os:uid:4242",
        "original_attempt_sequence": original_attempt.sequence,
        "role": "scout",
        "model": "deepseek-v4-flash",
    }
    if authorization_kind == "malformed":
        store.append_event(
            run.run_id,
            "model.attempt_replay_authorized_by_human",
            {"actor_id": "os:uid:4242", "role": "scout", "model": "deepseek-v4-flash"},
        )
    elif authorization_kind == "duplicate":
        store.append_event(
            run.run_id,
            "model.attempt_replay_authorized_by_human",
            authorization,
        )
        store.append_event(
            run.run_id,
            "model.attempt_replay_authorized_by_human",
            authorization,
        )
    elif authorization_kind == "wrong_sequence":
        store.append_event(
            run.run_id,
            "model.attempt_replay_authorized_by_human",
            {
                **authorization,
                "original_attempt_sequence": original_attempt.sequence + 1,
            },
        )
    else:
        store.append_event(
            run.run_id,
            "model.attempt_replay_authorized_by_human",
            {**authorization, "model": "deepseek-v4-pro"},
        )
    store.set_run_status(run.run_id, "paused")
    store.resume_run(run.run_id)

    transport = WorkflowTransport()
    resumed = SwarmEngine(transport).execute_run(run.run_id, tmp_path)

    assert resumed.status == "paused"
    assert resumed.pause_reason == "invalid_model_attempt_replay_authorization"
    assert transport.requests == []


def test_resumed_run_keeps_a_legacy_call_prefix_when_new_dispatch_markers_exist(
    tmp_path: Path,
):
    """Catches the first post-upgrade marker erasing earlier durable call use."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(run_id="mixed-call-accounting")
    store.append_event(
        run.run_id,
        "work.completed",
        {"role": "scout", "model": "deepseek-v4-flash", "work": "done"},
    )
    store.append_event(
        run.run_id,
        "model.attempt_failed",
        {"role": "planner", "model": "deepseek-v4-pro", "reason": "call_error"},
    )
    store.append_event(
        run.run_id,
        "model.attempt_started",
        {"role": "planner", "model": "kimi-k2.6"},
    )
    store.append_event(
        run.run_id,
        "work.completed",
        {"role": "planner", "model": "kimi-k2.6", "work": "done"},
    )

    assert SwarmEngine._prior_model_call_count(store.list_events(run.run_id)) == 3


def test_legacy_resume_fails_closed_for_an_unknown_workflow_role_triple(
    tmp_path: Path,
):
    """Catches an old Architect triple being silently skipped and replayed."""
    engine = SwarmEngine(WorkflowTransport())
    run = engine.start_run("Reject unknown legacy role", tmp_path)
    store = ProjectSwarmStore(tmp_path)
    for event_type, payload in (
        (
            "work.completed",
            {
                "role": "architect",
                "model": "deepseek-v4-pro",
                "work": "architect completed",
            },
        ),
        (
            "evidence.recorded",
            {"role": "architect", "evidence": ["architect evidence"]},
        ),
        (
            "decision.recorded",
            {"role": "architect", "decision": "architect approves"},
        ),
    ):
        store.append_event(run.run_id, event_type, payload)

    with pytest.raises(RuntimeError, match="unknown durable workflow role"):
        engine.execute_run(run.run_id, tmp_path)

    assert engine.transport.requests == []


def test_legacy_resume_requires_ordered_role_local_work_evidence_decision(
    tmp_path: Path,
):
    """Catches a reversed legacy triple being accepted as a completed Scout."""
    engine = SwarmEngine(WorkflowTransport())
    run = engine.start_run("Reject reversed legacy triple", tmp_path)
    store = ProjectSwarmStore(tmp_path)
    for event_type, payload in (
        ("decision.recorded", {"role": "scout", "decision": "done"}),
        ("evidence.recorded", {"role": "scout", "evidence": ["proof"]}),
        (
            "work.completed",
            {
                "role": "scout",
                "model": "deepseek-v4-flash",
                "work": "done",
            },
        ),
    ):
        store.append_event(run.run_id, event_type, payload)

    with pytest.raises(RuntimeError, match="out-of-order legacy workflow"):
        engine.execute_run(run.run_id, tmp_path)

    assert engine.transport.requests == []


def test_legacy_provider_role_requires_a_nonempty_model_before_replay(
    tmp_path: Path,
):
    """Catches a model-less legacy Scout lowering the resumed call budget."""
    engine = SwarmEngine(WorkflowTransport())
    run = engine.start_run("Reject model-less legacy Scout", tmp_path)
    store = ProjectSwarmStore(tmp_path)
    for event_type, payload in (
        ("work.completed", {"role": "scout", "work": "done"}),
        ("evidence.recorded", {"role": "scout", "evidence": ["proof"]}),
        ("decision.recorded", {"role": "scout", "decision": "done"}),
    ):
        store.append_event(run.run_id, event_type, payload)

    with pytest.raises(RuntimeError, match="invalid legacy model record"):
        engine.execute_run(run.run_id, tmp_path)

    assert engine.transport.requests == []


def test_engine_does_not_overwrite_a_human_pause_after_the_final_model_call(
    tmp_path: Path,
):
    """Catches a pause racing the terminal transition becoming a false completion."""

    class FinalPauseTransport(WorkflowTransport):
        def complete(self, request: ModelRequest) -> ModelResponse:
            response = super().complete(request)
            if request.role == "referee":
                store = ProjectSwarmStore(tmp_path)
                store.set_run_status(request.run_id, "paused")
                store.append_event(request.run_id, "run.paused_by_human", {})
            return response

    engine = SwarmEngine(FinalPauseTransport())
    run = engine.start_run("Honor an immediate final pause", tmp_path)

    summary = engine.execute_run(run.run_id, tmp_path)

    persisted = ProjectSwarmStore(tmp_path).get_run(run.run_id)
    events = ProjectSwarmStore(tmp_path).list_events(run.run_id)
    assert summary.status == "paused"
    assert summary.pause_reason == "human_paused"
    assert persisted is not None
    assert persisted.status == "paused"
    assert not any(event.event_type == "run.completed" for event in events)


def test_pre_completion_hook_runs_before_completed(tmp_path: Path):
    """Catches a host hook observing incomplete checkpoints or a terminal run."""
    observed: list[str] = []

    class Hook:
        hook_id = "test-hook-v1"

        def run(self, context: PreCompletionContext) -> PreCompletionResult:
            checkpoints = context.store.get_workflow_role_checkpoints(
                context.run.run_id
            )
            assert {"verifier", "review_a", "review_b"} <= set(checkpoints)
            persisted = context.store.get_run(context.run.run_id)
            assert persisted is not None
            assert persisted.status == "running"
            context.store.append_event(
                context.run.run_id, "test.pre_completion_hook", {}
            )
            observed.append(context.decision)
            return PreCompletionResult(continue_completion=True)

    engine = SwarmEngine(WorkflowTransport(), pre_completion_hook=Hook())
    run = engine.start_run(
        "verify hook order",
        tmp_path,
        host_metadata={"required_pre_completion_hook": "test-hook-v1"},
    )

    summary = engine.execute_run(run.run_id, tmp_path)

    event_types = [
        event.event_type
        for event in ProjectSwarmStore(tmp_path).list_events(run.run_id)
    ]
    assert summary.status == "completed"
    assert observed
    assert event_types.index("test.pre_completion_hook") < event_types.index(
        "run.completed"
    )


def test_pre_completion_hook_pause_resumes_from_durable_role_outputs(
    tmp_path: Path,
):
    """Catches a paused hook replaying completed model work after explicit resume."""

    class PausingHook:
        hook_id = "test-hook-v1"

        def run(self, _context: PreCompletionContext) -> PreCompletionResult:
            return PreCompletionResult(False, "awaiting_nova_approval")

    initial_transport = WorkflowTransport()
    initial_engine = SwarmEngine(
        initial_transport,
        pre_completion_hook=PausingHook(),
    )
    run = initial_engine.start_run(
        "pause for a host approval",
        tmp_path,
        host_metadata={"required_pre_completion_hook": "test-hook-v1"},
    )

    paused = initial_engine.execute_run(run.run_id, tmp_path)

    store = ProjectSwarmStore(tmp_path)
    events = store.list_events(run.run_id)
    assert paused.status == "paused"
    assert paused.pause_reason == "awaiting_nova_approval"
    assert not any(event.event_type == "run.completed" for event in events)
    assert len(initial_transport.requests) == 8

    class ContinuingHook:
        hook_id = "test-hook-v1"

        def run(self, _context: PreCompletionContext) -> PreCompletionResult:
            return PreCompletionResult(True)

    store.resume_run(run.run_id)
    resumed_transport = WorkflowTransport()
    resumed = SwarmEngine(
        resumed_transport,
        pre_completion_hook=ContinuingHook(),
    ).execute_run(run.run_id, tmp_path)

    assert resumed.status == "completed"
    assert resumed_transport.requests == []


def test_required_hook_rechecks_after_a_cooperative_pause_before_completion(
    tmp_path: Path,
):
    """Catches a checkpoint pause/resume bypassing a required completion hook."""
    hook_calls: list[str] = []

    class PausingHook:
        hook_id = "test-hook-v1"

        def run(self, _context: PreCompletionContext) -> PreCompletionResult:
            hook_calls.append("called")
            return PreCompletionResult(False, "awaiting_nova_approval")

    transport = WorkflowTransport()
    engine = SwarmEngine(transport, pre_completion_hook=PausingHook())
    run = engine.start_run(
        "recheck a required hook after cooperative resume",
        tmp_path,
        host_metadata={"required_pre_completion_hook": "test-hook-v1"},
    )
    store = ProjectSwarmStore(tmp_path)
    phase = "running"

    def checkpoint() -> None:
        nonlocal phase
        if len(transport.requests) != 8:
            return
        if phase == "running":
            store.set_run_status(run.run_id, "paused")
            phase = "paused"
        elif phase == "paused":
            store.resume_run(run.run_id)
            phase = "resumed"

    paused = engine.execute_run(run.run_id, tmp_path, checkpoint=checkpoint)

    events = store.list_events(run.run_id)
    assert phase == "resumed"
    assert hook_calls == ["called"]
    assert paused.status == "paused"
    assert paused.pause_reason == "awaiting_nova_approval"
    assert len(transport.requests) == 8
    assert not any(event.event_type == "run.completed" for event in events)

    class ContinuingHook:
        hook_id = "test-hook-v1"

        def run(self, _context: PreCompletionContext) -> PreCompletionResult:
            return PreCompletionResult(True)

    store.resume_run(run.run_id)
    resumed_transport = WorkflowTransport()
    resumed = SwarmEngine(
        resumed_transport,
        pre_completion_hook=ContinuingHook(),
    ).execute_run(run.run_id, tmp_path)

    assert resumed.status == "completed"
    assert resumed_transport.requests == []


def test_run_without_pre_completion_hook_keeps_terminal_event_sequence(tmp_path: Path):
    """Catches ordinary runs gaining a new terminal event or pause requirement."""
    run = SwarmEngine(WorkflowTransport()).start_run("normal completion", tmp_path)

    summary = SwarmEngine(WorkflowTransport()).execute_run(run.run_id, tmp_path)

    event_types = [
        event.event_type
        for event in ProjectSwarmStore(tmp_path).list_events(run.run_id)
    ]
    assert summary.status == "completed"
    assert event_types[-1] == "run.completed"
    assert "run.paused" not in event_types


def test_required_pre_completion_hook_fails_closed_when_unavailable(tmp_path: Path):
    """Catches a durable host requirement silently bypassing its completion gate."""
    transport = WorkflowTransport()
    engine = SwarmEngine(transport)
    run = engine.start_run(
        "require a host hook",
        tmp_path,
        host_metadata={"required_pre_completion_hook": "missing-hook-v1"},
    )

    summary = engine.execute_run(run.run_id, tmp_path)

    events = ProjectSwarmStore(tmp_path).list_events(run.run_id)
    assert summary.status == "paused"
    assert summary.pause_reason == "required_pre_completion_hook_unavailable"
    assert len(transport.requests) == 8
    assert not any(event.event_type == "run.completed" for event in events)


def test_required_pre_completion_hook_fails_closed_when_installed_id_mismatches(
    tmp_path: Path,
):
    """Catches an installed but differently named hook bypassing the durable gate."""

    class OtherHook:
        hook_id = "other-hook-v1"

        def run(self, _context: PreCompletionContext) -> PreCompletionResult:
            raise AssertionError("a mismatched hook must not run")

    engine = SwarmEngine(WorkflowTransport(), pre_completion_hook=OtherHook())
    run = engine.start_run(
        "require the exact hook id",
        tmp_path,
        host_metadata={"required_pre_completion_hook": "expected-hook-v1"},
    )

    summary = engine.execute_run(run.run_id, tmp_path)

    assert summary.status == "paused"
    assert summary.pause_reason == "required_pre_completion_hook_unavailable"


def test_pre_completion_hook_failure_uses_a_safe_pause_reason(tmp_path: Path):
    """Catches hook exception text being exposed in durable run state."""

    class FailingHook:
        hook_id = "test-hook-v1"

        def run(self, _context: PreCompletionContext) -> PreCompletionResult:
            raise RuntimeError("private host failure detail")

    engine = SwarmEngine(WorkflowTransport(), pre_completion_hook=FailingHook())
    run = engine.start_run(
        "contain host hook errors",
        tmp_path,
        host_metadata={"required_pre_completion_hook": "test-hook-v1"},
    )

    summary = engine.execute_run(run.run_id, tmp_path)

    events = ProjectSwarmStore(tmp_path).list_events(run.run_id)
    assert summary.status == "paused"
    assert summary.pause_reason == "pre_completion_hook_failed"
    assert "private host failure detail" not in str(events)
    assert not any(event.event_type == "run.completed" for event in events)


def test_malformed_pre_completion_hook_result_fails_closed(tmp_path: Path):
    """Catches malformed hook returns escaping as implementation exceptions."""

    class MalformedResultHook:
        hook_id = "test-hook-v1"

        def run(self, _context: PreCompletionContext) -> PreCompletionResult:
            return None  # type: ignore[return-value]

    engine = SwarmEngine(
        WorkflowTransport(),
        pre_completion_hook=MalformedResultHook(),
    )
    run = engine.start_run(
        "contain malformed hook results",
        tmp_path,
        host_metadata={"required_pre_completion_hook": "test-hook-v1"},
    )

    summary = engine.execute_run(run.run_id, tmp_path)

    events = ProjectSwarmStore(tmp_path).list_events(run.run_id)
    assert summary.status == "paused"
    assert summary.pause_reason == "pre_completion_hook_failed"
    assert not any(event.event_type == "run.completed" for event in events)


def test_pre_completion_hook_base_exception_propagates_and_releases_lease(
    tmp_path: Path,
):
    """Catches BaseException swallowing or a leaked execution lease from a hook."""

    class InterruptingHook:
        hook_id = "test-hook-v1"

        def run(self, _context: PreCompletionContext) -> PreCompletionResult:
            raise KeyboardInterrupt("stop immediately")

    engine = SwarmEngine(WorkflowTransport(), pre_completion_hook=InterruptingHook())
    run = engine.start_run(
        "preserve BaseException semantics",
        tmp_path,
        host_metadata={"required_pre_completion_hook": "test-hook-v1"},
    )

    with pytest.raises(KeyboardInterrupt, match="stop immediately"):
        engine.execute_run(run.run_id, tmp_path)

    store = ProjectSwarmStore(tmp_path)
    assert store.claim_run_execution_lease(run.run_id, "post-interrupt-owner")
    store.release_run_execution_lease(run.run_id, "post-interrupt-owner")


def test_start_run_rejects_unsafe_or_reserved_host_metadata(tmp_path: Path):
    """Catches host metadata overwriting durable Core inputs or losing JSON shape."""
    engine = SwarmEngine(WorkflowTransport())

    with pytest.raises(ValueError, match="cannot override goal"):
        engine.start_run(
            "keep Core goal authoritative",
            tmp_path,
            host_metadata={"goal": "host override"},
        )
    with pytest.raises(ValueError, match="must be JSON-safe"):
        engine.start_run(
            "persist only JSON metadata",
            tmp_path,
            host_metadata={"opaque": object()},
        )


def test_engine_preserves_a_model_pause_when_a_human_pause_wins_the_transition_race(
    tmp_path: Path,
):
    """Catches a human pause turning a normal model pause into a worker failure."""

    failure_seen = threading.Event()
    human_pause_landed = threading.Event()

    class FailingScoutTransport(WorkflowTransport):
        def complete(self, request: ModelRequest) -> ModelResponse:
            with self._lock:
                self.requests.append(request)
            if request.role == "scout":
                failure_seen.set()
                raise ModelProviderError("scout unavailable")
            raise AssertionError("the scout failure must pause the workflow")

    engine = SwarmEngine(FailingScoutTransport())
    run = engine.start_run("Preserve the modeled pause", tmp_path)
    original_get_run = ProjectSwarmStore.get_run

    def get_run_after_human_pause(store: ProjectSwarmStore, run_id: str):
        current = original_get_run(store, run_id)
        if (
            failure_seen.is_set()
            and not human_pause_landed.is_set()
            and current is not None
            and current.status == "running"
        ):
            store.set_run_status(run_id, "paused")
            store.append_event(run_id, "run.paused_by_human", {})
            human_pause_landed.set()
        return current

    with patch.object(ProjectSwarmStore, "get_run", new=get_run_after_human_pause):
        summary = engine.execute_run(run.run_id, tmp_path)

    persisted = ProjectSwarmStore(tmp_path).get_run(run.run_id)
    events = ProjectSwarmStore(tmp_path).list_events(run.run_id)
    assert human_pause_landed.is_set()
    assert persisted is not None
    assert persisted.status == "paused"
    assert summary.status == "paused"
    assert summary.pause_reason == "model_chain_exhausted"
    assert any(
        event.event_type == "run.paused"
        and event.payload["reason"] == "model_chain_exhausted"
        for event in events
    )
    assert all(event.event_type != "run.execution_failed" for event in events)


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
    verifier_evidence = verifier_events[0].payload["evidence"]
    checkpoints = ProjectSwarmStore(tmp_path).get_workflow_role_checkpoints(
        summary.run_id
    )
    assert verifier_evidence
    assert all(
        isinstance(reference, str) and reference.startswith("verifier:local:")
        for reference in verifier_evidence
    )
    assert not set(verifier_evidence) & {
        "builder:minimax-m3",
        "critic:minimax-m3",
    }
    assert checkpoints["verifier"].model is None
    assert checkpoints["verifier"].data["provenance"]["adapter"] == "default-read-only"


def test_each_shipped_pack_runs_the_reviewed_core_workflow(tmp_path: Path):
    """Catches a selectable pack being accepted by the UI but immediately paused."""
    for pack in ("bug-hunt", "research-team", "release-audit"):
        project = tmp_path / pack
        transport = WorkflowTransport()

        summary = SwarmEngine(transport).run(
            goal=f"Exercise {pack}",
            project_root=project,
            pack=pack,
        )

        assert summary.status == "completed"
        assert summary.pause_reason is None
        assert summary.call_count == 8
        assert all(f"Pack: {pack}" in request.prompt for request in transport.requests)
        started = ProjectSwarmStore(project).list_events(summary.run_id)[0]
        assert started.event_type == "run.started"
        assert started.payload["pack"] == pack


def test_engine_records_an_explicit_autonomy_level_on_the_durable_run(
    tmp_path: Path,
):
    """Catches SwarmEngine silently dropping its public autonomy argument."""
    summary = SwarmEngine(WorkflowTransport()).run(
        goal="Use execute-safe after separate policy configuration",
        project_root=tmp_path,
        autonomy="execute_safe",
    )

    run = ProjectSwarmStore(tmp_path).get_run(summary.run_id)
    assert run is not None
    assert run.metadata["autonomy"] == "execute_safe"


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
                raise ModelProviderError("critic unavailable")
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
                raise ModelProviderError(f"{request.role} unavailable")
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
                raise ModelProviderError(f"{request.role} unavailable")
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
