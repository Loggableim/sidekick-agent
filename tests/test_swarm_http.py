from __future__ import annotations

import io
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from cli.swarm_host import SidekickSwarmService
from swarm_core.models import ModelCatalogSnapshot
from swarm_core.packs import PackDefinition
from swarm_core.store import ProjectSwarmStore
from swarm_core.config import initialize_project
from web.api import swarm as swarm_api


class _Handler:
    def __init__(self, *, headers: dict[str, str] | None = None, writer=None) -> None:
        self.headers = headers or {}
        self.wfile = writer or io.BytesIO()
        self.status_code: int | None = None
        self.response_headers: list[tuple[str, str]] = []

    def send_response(self, status: int, *_args) -> None:
        self.status_code = status

    def send_header(self, name: str, value: str) -> None:
        self.response_headers.append((name, value))

    def end_headers(self) -> None:
        return None


class _DisconnectAfterEvents(io.BytesIO):
    def write(self, payload: bytes) -> int:
        result = super().write(payload)
        if b"event: events" in payload:
            raise BrokenPipeError("test stream disconnect")
        return result


def _response_json(handler: _Handler) -> dict:
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def test_http_run_start_returns_a_persisted_running_run_before_background_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches the create response waiting for the whole model workflow."""
    project = tmp_path / "project"
    project.mkdir()
    trusted_values: list[str] = []
    execution_started = threading.Event()
    execution_finished = threading.Event()
    release_execution = threading.Event()

    monkeypatch.setattr(
        swarm_api,
        "resolve_active_space",
        lambda: SimpleNamespace(get_project_dir=lambda: str(project)),
    )

    def resolve(value):
        trusted_values.append(str(value))
        return project.resolve()

    monkeypatch.setattr(swarm_api, "resolve_trusted_workspace", resolve)

    class FakeService:
        def run(self, *_args, **_kwargs):
            # The old synchronous route calls this and therefore cannot expose
            # a durable running run to the client.
            return {"run_id": "legacy-sync-run", "status": "completed"}

        def start_run(self, goal, project_root, *, pack):
            run = ProjectSwarmStore(project_root).create_run(
                run_id="run-async",
                metadata={"goal": goal, "pack": pack},
            )
            ProjectSwarmStore(project_root).append_event(
                run.run_id, "run.started", {"goal": goal, "pack": pack}
            )
            return run

        def execute_run(self, project_root, run_id, **_callbacks):
            execution_started.set()
            try:
                release_execution.wait(timeout=2)
                store = ProjectSwarmStore(project_root)
                if store.get_run(run_id).status == "running":
                    store.set_run_status(run_id, "completed")
            finally:
                execution_finished.set()

    monkeypatch.setattr(swarm_api, "get_swarm_service", lambda: FakeService())
    handler = _Handler()

    try:
        result = swarm_api.handle_swarm_post(
            handler,
            urlparse("/api/swarm/runs?workspace=marketing"),
            {"goal": "inspect", "pack": "coding-team"},
        )

        assert result is True
        assert handler.status_code == 201
        assert trusted_values == [str(project)]
        returned = _response_json(handler)["run"]
        assert returned["run_id"] == "run-async"
        assert returned["status"] == "running"
        assert returned["metadata"] == {
            "goal": "inspect",
            "pack": "coding-team",
            "autonomy": "reviewed_execution",
        }
        assert execution_started.wait(timeout=1)
        observed = ProjectSwarmStore.open_read_only(project).get_run("run-async")
        assert observed is not None
        assert observed.status == "running"
    finally:
        release_execution.set()
        if execution_started.is_set():
            assert execution_finished.wait(timeout=1)


def test_http_run_serializes_the_start_response_before_a_fast_worker_can_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches a completed worker racing ahead of the durable start response."""
    project = tmp_path / "project"
    project.mkdir()
    response_written = threading.Event()
    execution_started = threading.Event()
    execution_finished = threading.Event()
    permit_execution = threading.Event()
    execution_preceded_response: list[bool] = []
    monkeypatch.setattr(
        swarm_api, "resolve_trusted_workspace", lambda _value: project.resolve()
    )

    class ResponseSignalWriter(io.BytesIO):
        def write(self, payload: bytes) -> int:
            response_written.set()
            return super().write(payload)

    class FakeService:
        def start_run(self, goal, project_root, *, pack):
            run = ProjectSwarmStore(project_root).create_run(
                run_id="run-publication", metadata={"goal": goal, "pack": pack}
            )
            ProjectSwarmStore(project_root).append_event(
                run.run_id, "run.started", {"goal": goal, "pack": pack}
            )
            return run

        def execute_run(self, project_root, run_id, **_callbacks):
            execution_started.set()
            try:
                assert permit_execution.wait(timeout=1)
                execution_preceded_response.append(not response_written.is_set())
                store = ProjectSwarmStore(project_root)
                store.set_run_status(run_id, "completed")
            finally:
                execution_finished.set()

    original_j = swarm_api.j

    def delayed_response(handler, payload, status=200):
        # On the old ordering this releases an already-started worker before
        # the response writer runs.  With a start gate it simply times out,
        # writes the 201, then lets the worker begin.
        if execution_started.wait(timeout=0.05):
            permit_execution.set()
            assert execution_finished.wait(timeout=1)
            return original_j(handler, payload, status=status)
        result = original_j(handler, payload, status=status)
        permit_execution.set()
        return result

    monkeypatch.setattr(swarm_api, "get_swarm_service", lambda: FakeService())
    monkeypatch.setattr(swarm_api, "j", delayed_response)
    handler = _Handler(writer=ResponseSignalWriter())

    try:
        result = swarm_api.handle_swarm_post(
            handler,
            urlparse("/api/swarm/runs"),
            {"goal": "inspect", "project_path": str(project)},
        )

        assert result is True
        assert handler.status_code == 201
        assert execution_started.wait(timeout=1)
        assert execution_finished.wait(timeout=1)
        assert execution_preceded_response == [False]
        assert _response_json(handler)["run"]["status"] == "running"
    finally:
        permit_execution.set()
        if execution_started.is_set():
            assert execution_finished.wait(timeout=1)


def test_http_background_execution_failure_becomes_a_durable_paused_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches a daemon worker exception disappearing while the run stays running."""
    project = tmp_path / "project"
    project.mkdir()
    failed = threading.Event()
    monkeypatch.setattr(
        swarm_api, "resolve_trusted_workspace", lambda _value: project.resolve()
    )

    class FakeService:
        def start_run(self, goal, project_root, *, pack):
            run = ProjectSwarmStore(project_root).create_run(
                run_id="run-failure", metadata={"goal": goal, "pack": pack}
            )
            ProjectSwarmStore(project_root).append_event(
                run.run_id, "run.started", {"goal": goal, "pack": pack}
            )
            return run

        def execute_run(self, _project_root, _run_id, **_callbacks):
            failed.set()
            raise RuntimeError("provider response contained secret details")

    monkeypatch.setattr(swarm_api, "get_swarm_service", lambda: FakeService())
    handler = _Handler()

    result = swarm_api.handle_swarm_post(
        handler,
        urlparse("/api/swarm/runs"),
        {"goal": "inspect", "project_path": str(project)},
    )

    assert result is True
    assert handler.status_code == 201
    assert failed.wait(timeout=1)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        reader = ProjectSwarmStore.open_read_only(project)
        events = reader.list_events("run-failure")
        if any(event.event_type == "run.execution_failed" for event in events):
            break
        time.sleep(0.01)
    persisted = ProjectSwarmStore.open_read_only(project).get_run("run-failure")
    assert persisted is not None
    assert persisted.status == "paused"
    failure_events = [
        event
        for event in ProjectSwarmStore.open_read_only(project).list_events(
            "run-failure"
        )
        if event.event_type == "run.execution_failed"
    ]
    assert len(failure_events) == 1
    assert failure_events[0].payload == {"error_type": "RuntimeError"}


def test_http_thread_start_failure_pauses_the_durable_run_without_returning_201(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches a failed worker start being reported as a runnable 201 response."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        swarm_api, "resolve_trusted_workspace", lambda _value: project.resolve()
    )

    class FakeService:
        def start_run(self, goal, project_root, *, pack):
            run = ProjectSwarmStore(project_root).create_run(
                run_id="run-start-failure", metadata={"goal": goal, "pack": pack}
            )
            ProjectSwarmStore(project_root).append_event(
                run.run_id, "run.started", {"goal": goal, "pack": pack}
            )
            return run

    def fail_thread_start(_thread):
        raise RuntimeError("provider response contained secret details")

    monkeypatch.setattr(swarm_api, "get_swarm_service", lambda: FakeService())
    monkeypatch.setattr(swarm_api.threading.Thread, "start", fail_thread_start)
    handler = _Handler()

    result = swarm_api.handle_swarm_post(
        handler,
        urlparse("/api/swarm/runs"),
        {"goal": "inspect", "project_path": str(project)},
    )

    persisted = ProjectSwarmStore.open_read_only(project).get_run("run-start-failure")
    events = ProjectSwarmStore.open_read_only(project).list_events("run-start-failure")
    assert result is None
    assert handler.status_code == 409
    assert persisted is not None
    assert persisted.status == "paused"
    assert any(
        event.event_type == "run.execution_failed"
        and event.payload == {"error_type": "RuntimeError"}
        for event in events
    )
    assert "secret details" not in handler.wfile.getvalue().decode("utf-8")


def test_http_response_write_failure_releases_the_unpublished_worker_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches a response failure leaving a cancelled daemon in the local registry."""
    project = tmp_path / "project"
    project.mkdir()
    execution_called = threading.Event()
    key = (str(project.resolve()), "run-response-error")
    monkeypatch.setattr(
        swarm_api, "resolve_trusted_workspace", lambda _value: project.resolve()
    )

    class FakeService:
        def start_run(self, goal, project_root, *, pack):
            run = ProjectSwarmStore(project_root).create_run(
                run_id="run-response-error", metadata={"goal": goal, "pack": pack}
            )
            ProjectSwarmStore(project_root).append_event(
                run.run_id, "run.started", {"goal": goal, "pack": pack}
            )
            return run

        def execute_run(self, *_args, **_kwargs):
            execution_called.set()

    original_j = swarm_api.j

    def fail_only_the_start_response(handler, payload, status=200, **kwargs):
        if status == 201:
            raise RuntimeError("synthetic response write failure")
        return original_j(handler, payload, status=status, **kwargs)

    monkeypatch.setattr(swarm_api, "get_swarm_service", lambda: FakeService())
    monkeypatch.setattr(swarm_api, "j", fail_only_the_start_response)
    handler = _Handler()

    try:
        result = swarm_api.handle_swarm_post(
            handler,
            urlparse("/api/swarm/runs"),
            {"goal": "inspect", "project_path": str(project)},
        )

        assert result is None
        assert handler.status_code == 409
        assert not execution_called.is_set()
        persisted = ProjectSwarmStore.open_read_only(project).get_run(
            "run-response-error"
        )
        events = ProjectSwarmStore.open_read_only(project).list_events(
            "run-response-error"
        )
        assert persisted is not None
        assert persisted.status == "paused"
        assert any(
            event.event_type == "run.execution_failed"
            and event.payload == {"error_type": "RuntimeError"}
            for event in events
        )
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            with swarm_api._BACKGROUND_RUNS_LOCK:
                if key not in swarm_api._BACKGROUND_RUNS:
                    break
            time.sleep(0.01)
        with swarm_api._BACKGROUND_RUNS_LOCK:
            assert key not in swarm_api._BACKGROUND_RUNS
    finally:
        # Keep a red regression isolated if it fails before the production
        # cleanup is implemented.
        with swarm_api._BACKGROUND_RUNS_LOCK:
            lingering = swarm_api._BACKGROUND_RUNS.pop(key, None)
        if lingering is not None:
            lingering.cancelled.set()
            lingering.start_gate.set()


def test_http_resume_rejects_a_worker_that_is_not_waiting_at_a_pause_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches resume succeeding while a finishing worker has no continuation point."""
    project = tmp_path / "project"
    project.mkdir()
    execution_started = threading.Event()
    execution_finished = threading.Event()
    release_execution = threading.Event()
    resumed: list[str] = []
    monkeypatch.setattr(
        swarm_api, "resolve_trusted_workspace", lambda _value: project.resolve()
    )

    class FakeService:
        def start_run(self, goal, project_root, *, pack):
            run = ProjectSwarmStore(project_root).create_run(
                run_id="run-not-waiting", metadata={"goal": goal, "pack": pack}
            )
            ProjectSwarmStore(project_root).append_event(
                run.run_id, "run.started", {"goal": goal, "pack": pack}
            )
            return run

        def execute_run(self, _project_root, _run_id, **_callbacks):
            execution_started.set()
            try:
                release_execution.wait(timeout=2)
            finally:
                execution_finished.set()

        def pause(self, project_root, run_id):
            store = ProjectSwarmStore(project_root)
            run = store.set_run_status(run_id, "paused")
            store.append_event(run_id, "run.paused_by_human", {})
            return run

        def resume(self, project_root, run_id):
            resumed.append(run_id)
            return ProjectSwarmStore(project_root).resume_run(run_id)

    monkeypatch.setattr(swarm_api, "get_swarm_service", lambda: FakeService())

    try:
        created = _Handler()
        assert (
            swarm_api.handle_swarm_post(
                created,
                urlparse("/api/swarm/runs"),
                {"goal": "inspect", "project_path": str(project)},
            )
            is True
        )
        assert execution_started.wait(timeout=1)

        paused = _Handler()
        assert (
            swarm_api.handle_swarm_post(
                paused,
                urlparse("/api/swarm/runs/run-not-waiting/pause"),
                {"project_path": str(project)},
            )
            is True
        )

        resume = _Handler()
        assert (
            swarm_api.handle_swarm_post(
                resume,
                urlparse("/api/swarm/runs/run-not-waiting/resume"),
                {"project_path": str(project)},
            )
            is None
        )
        assert resume.status_code == 409
        assert resumed == []
        assert (
            ProjectSwarmStore.open_read_only(project).get_run("run-not-waiting").status
            == "paused"
        )
    finally:
        release_execution.set()
        if execution_started.is_set():
            assert execution_finished.wait(timeout=1)


def test_http_resume_continues_a_real_worker_waiting_at_a_human_pause_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches the safe-resume guard rejecting the worker that it was meant to wake."""
    project = tmp_path / "project"
    project.mkdir()
    first_call_started = threading.Event()
    release_first_call = threading.Event()
    calls: list[dict] = []

    def call_llm(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            first_call_started.set()
            assert release_first_call.wait(timeout=2)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "work": "bounded work",
                                "evidence": ["test:evidence"],
                                "decision": "continue",
                            }
                        )
                    }
                }
            ]
        }

    service = SidekickSwarmService(
        call_llm=call_llm,
        catalog_refresher=lambda: ModelCatalogSnapshot(
            provider="ollama-cloud",
            models=(
                "deepseek-v4-flash",
                "deepseek-v4-pro",
                "kimi-k2.6",
                "minimax-m3",
                "glm-5.2",
                "kimi-k2.7-code",
                "nemotron-3-super",
            ),
            healthy=True,
            source="test",
        ),
        pause_poll_seconds=0.005,
    )
    service.refresh_models(project)
    monkeypatch.setattr(
        swarm_api, "resolve_trusted_workspace", lambda _value: project.resolve()
    )
    monkeypatch.setattr(swarm_api, "get_swarm_service", lambda: service)

    try:
        created = _Handler()
        assert (
            swarm_api.handle_swarm_post(
                created,
                urlparse("/api/swarm/runs"),
                {"goal": "inspect", "project_path": str(project)},
            )
            is True
        )
        run_id = _response_json(created)["run"]["run_id"]
        assert first_call_started.wait(timeout=1)

        paused = _Handler()
        assert (
            swarm_api.handle_swarm_post(
                paused,
                urlparse(f"/api/swarm/runs/{run_id}/pause"),
                {"project_path": str(project)},
            )
            is True
        )
        release_first_call.set()

        deadline = time.monotonic() + 1
        resumed = None
        while time.monotonic() < deadline:
            candidate = _Handler()
            result = swarm_api.handle_swarm_post(
                candidate,
                urlparse(f"/api/swarm/runs/{run_id}/resume"),
                {"project_path": str(project)},
            )
            if result is True:
                resumed = candidate
                break
            assert candidate.status_code == 409
            time.sleep(0.01)
        assert resumed is not None
        assert _response_json(resumed)["run"]["status"] == "running"

        while time.monotonic() < deadline + 2:
            run = ProjectSwarmStore.open_read_only(project).get_run(run_id)
            if run is not None and run.status == "completed":
                break
            time.sleep(0.01)
        assert (
            ProjectSwarmStore.open_read_only(project).get_run(run_id).status
            == "completed"
        )
        assert len(calls) == 8
    finally:
        release_first_call.set()


def test_http_write_rejects_untrusted_path_before_opening_swarm_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches a create/pause/approval write bypassing the trusted workspace gate."""
    project = tmp_path / "outside"
    project.mkdir()
    called = False

    def reject(_value):
        raise ValueError("Path is outside the trusted workspace list")

    monkeypatch.setattr(swarm_api, "resolve_trusted_workspace", reject)

    class FakeService:
        def run(self, *_args, **_kwargs):
            nonlocal called
            called = True

    monkeypatch.setattr(swarm_api, "get_swarm_service", lambda: FakeService())
    handler = _Handler()

    result = swarm_api.handle_swarm_post(
        handler,
        urlparse("/api/swarm/runs"),
        {"goal": "inspect", "project_path": str(project)},
    )

    assert result is None
    assert handler.status_code == 400
    assert called is False
    assert not (project / ".swarm").exists()


def test_http_read_on_missing_project_is_pure_and_returns_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches GET/status/packs creating a config, SQLite database, or catalog."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        swarm_api, "resolve_trusted_workspace", lambda _value: project.resolve()
    )
    handler = _Handler()

    result = swarm_api.handle_swarm_get(
        handler,
        urlparse(f"/api/swarm/runs?project_path={project}"),
    )

    assert result is None
    assert handler.status_code == 404
    assert _response_json(handler)["ok"] is False
    assert not (project / ".swarm").exists()


def test_swarm_get_requires_explicit_project_path_without_space_resolution(
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches a supposedly pure GET bootstrapping a Space as an implicit fallback."""
    active_space_calls: list[object] = []
    trusted_calls: list[object] = []

    def unexpected_active_space():
        active_space_calls.append(object())
        raise AssertionError("Swarm GET must not resolve an active Space")

    def unexpected_trusted_path(_value):
        trusted_calls.append(_value)
        raise AssertionError("Swarm GET without project_path must fail first")

    monkeypatch.setattr(swarm_api, "resolve_active_space", unexpected_active_space)
    monkeypatch.setattr(swarm_api, "resolve_trusted_workspace", unexpected_trusted_path)
    handler = _Handler()

    result = swarm_api.handle_swarm_get(handler, urlparse("/api/swarm/runs"))

    assert result is None
    assert handler.status_code == 400
    assert active_space_calls == []
    assert trusted_calls == []


def test_direct_router_swarm_get_skips_workspace_setup(
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches the legacy/direct dispatcher reintroducing Space initialization."""
    from web.api import routes

    def unexpected_setup(*_args):
        raise AssertionError("Swarm GET must not initialize a workspace")

    def fake_swarm_get(handler, parsed):
        assert parsed.path == "/api/swarm/runs"
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(b'{"ok":true}')
        return True

    monkeypatch.setattr(routes, "_setup_workspace_from_request", unexpected_setup)
    monkeypatch.setattr(routes, "_teardown_workspace_context", unexpected_setup)
    monkeypatch.setattr(swarm_api, "handle_swarm_get", fake_swarm_get)
    handler = _Handler()

    handled = routes.handle_get(
        handler,
        urlparse("/api/swarm/runs?project_path=C%3A%2Ftrusted"),
    )

    assert handled is True
    assert handler.status_code == 200
    assert _response_json(handler) == {"ok": True}


def test_http_packs_reads_initialized_config_without_creating_runtime_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches pack metadata needing a mutating store open after `swarm init`."""
    project = tmp_path / "project"
    project.mkdir()
    initialize_project(project)
    monkeypatch.setattr(
        swarm_api, "resolve_trusted_workspace", lambda _value: project.resolve()
    )

    class FakeService:
        def list_packs(self, project_root):
            assert project_root == project.resolve()
            return [
                PackDefinition(
                    pack_id="coding-team",
                    description="Coding workflow",
                    workflow="scout -> builder",
                    roles={"scout": "discover"},
                )
            ]

    monkeypatch.setattr(swarm_api, "get_swarm_service", lambda: FakeService())
    handler = _Handler()

    assert (
        swarm_api.handle_swarm_get(
            handler,
            urlparse(f"/api/swarm/packs?project_path={project}"),
        )
        is True
    )
    assert handler.status_code == 200
    assert _response_json(handler) == {
        "packs": [
            {
                "pack_id": "coding-team",
                "description": "Coding workflow",
                "workflow": "scout -> builder",
                "roles": {"scout": "discover"},
            }
        ]
    }
    assert not (project / ".swarm" / "runtime" / "swarm.sqlite").exists()


def test_http_approval_rejects_forged_model_fields_and_derives_human_actor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches WebUI callers creating model/verifier rows or choosing an approver."""
    project = tmp_path / "project"
    project.mkdir()
    calls: list[tuple] = []
    monkeypatch.setattr(
        swarm_api, "resolve_trusted_workspace", lambda _value: project.resolve()
    )

    class FakeService:
        def record_human_approval(
            self, project_root, run_id, proposal_id, *, actor_id, approved
        ):
            calls.append((project_root, run_id, proposal_id, actor_id, approved))
            return {"approval_type": "human", "approved": approved}

    monkeypatch.setattr(swarm_api, "get_swarm_service", lambda: FakeService())

    forged = _Handler()
    assert (
        swarm_api.handle_swarm_post(
            forged,
            urlparse("/api/swarm/runs/run-1/approve"),
            {
                "project_path": str(project),
                "proposal_id": "proposal-1",
                "approval_type": "model",
                "approver_id": "forged",
                "model_family": "glm",
                "evidence_refs": ["forged"],
            },
        )
        is None
    )
    assert forged.status_code == 400
    assert calls == []

    missing_actor = _Handler()
    assert (
        swarm_api.handle_swarm_post(
            missing_actor,
            urlparse("/api/swarm/runs/run-1/approve"),
            {"project_path": str(project), "proposal_id": "proposal-1"},
        )
        is None
    )
    assert missing_actor.status_code == 403
    assert calls == []

    accepted = _Handler()
    accepted.swarm_host_actor = "dashboard:trusted-test-principal"
    assert (
        swarm_api.handle_swarm_post(
            accepted,
            urlparse("/api/swarm/runs/run-1/approve"),
            {"project_path": str(project), "proposal_id": "proposal-1", "deny": True},
        )
        is True
    )
    assert calls == [
        (
            project.resolve(),
            "run-1",
            "proposal-1",
            "dashboard:trusted-test-principal",
            False,
        )
    ]


def test_http_existing_run_write_does_not_initialize_an_absent_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches an approval typo creating project state before it is rejected."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        swarm_api, "resolve_trusted_workspace", lambda _value: project.resolve()
    )
    handler = _Handler()
    handler.swarm_host_actor = "dashboard:trusted-test-principal"

    assert (
        swarm_api.handle_swarm_post(
            handler,
            urlparse("/api/swarm/runs/missing/approve"),
            {"project_path": str(project), "proposal_id": "proposal-1"},
        )
        is None
    )
    assert handler.status_code == 404
    assert not (project / ".swarm").exists()


def test_http_kanban_projection_accepts_only_an_explicit_trusted_project_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches the optional Kanban write accepting a client board, Space slug, or untrusted path."""
    project = tmp_path / "project"
    project.mkdir()
    trusted_values: list[str] = []
    calls: list[tuple[Path, str]] = []

    def resolve(value):
        trusted_values.append(str(value))
        if value != str(project):
            raise ValueError("Path is outside the trusted workspace list")
        return project.resolve()

    monkeypatch.setattr(swarm_api, "resolve_trusted_workspace", resolve)
    monkeypatch.setattr(
        swarm_api,
        "project_swarm_run_to_kanban",
        lambda project_root, run_id: (
            calls.append((project_root, run_id))
            or {
                "task_id": "task-1",
                "board": "default",
                "space_slug": "project-space",
            }
        ),
    )

    forged = _Handler()
    assert (
        swarm_api.handle_swarm_post(
            forged,
            urlparse("/api/swarm/runs/run-1/kanban-projection"),
            {"project_path": str(project), "board": "attacker-board"},
        )
        is None
    )
    assert forged.status_code == 400
    assert calls == []

    missing_path = _Handler()
    assert (
        swarm_api.handle_swarm_post(
            missing_path,
            urlparse("/api/swarm/runs/run-1/kanban-projection"),
            {},
        )
        is None
    )
    assert missing_path.status_code == 400
    assert calls == []

    accepted = _Handler()
    assert (
        swarm_api.handle_swarm_post(
            accepted,
            urlparse("/api/swarm/runs/run-1/kanban-projection"),
            {"project_path": str(project)},
        )
        is True
    )
    assert accepted.status_code == 201
    assert trusted_values == [str(project), str(project)]
    assert calls == [(project.resolve(), "run-1")]
    assert _response_json(accepted) == {
        "projection": {
            "task_id": "task-1",
            "board": "default",
            "space_slug": "project-space",
        }
    }


def test_sse_reads_ordered_cursor_tail_without_mutating_a_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches EventSource reconnects replaying all events or resuming work."""
    project = tmp_path / "project"
    project.mkdir()
    store = ProjectSwarmStore(project)
    run = store.create_run(run_id="stream-run")
    first = store.append_event(run.run_id, "run.started", {"goal": "inspect"})
    second = store.append_event(run.run_id, "run.paused", {"reason": "catalog"})
    monkeypatch.setattr(
        swarm_api, "resolve_trusted_workspace", lambda _value: project.resolve()
    )
    writer = _DisconnectAfterEvents()
    handler = _Handler(headers={"Last-Event-ID": str(first.sequence)}, writer=writer)

    result = swarm_api.handle_swarm_get(
        handler,
        urlparse(
            f"/api/swarm/runs/events/stream?project_path={project}&run_id={run.run_id}"
        ),
    )

    frame_text = writer.getvalue().decode("utf-8")
    assert result is True
    assert handler.status_code == 200
    assert "event: hello" in frame_text
    assert f"id: {second.sequence}" in frame_text
    assert '"event_type": "run.paused"' in frame_text
    assert ProjectSwarmStore(project).list_events(run.run_id) == [first, second]
