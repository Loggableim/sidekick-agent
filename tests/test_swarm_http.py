from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

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


def test_http_write_resolves_active_space_project_not_workspace_slug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches a Space slug reaching the filesystem trust resolver as a path."""
    project = tmp_path / "project"
    project.mkdir()
    trusted_values: list[str] = []
    created: list[tuple] = []

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
        def run(self, goal, project_root, *, pack):
            created.append((goal, project_root, pack))
            return {"run_id": "run-1", "status": "paused"}

    monkeypatch.setattr(swarm_api, "get_swarm_service", lambda: FakeService())
    handler = _Handler()

    result = swarm_api.handle_swarm_post(
        handler,
        urlparse("/api/swarm/runs?workspace=marketing"),
        {"goal": "inspect", "pack": "coding-team"},
    )

    assert result is True
    assert handler.status_code == 201
    assert trusted_values == [str(project)]
    assert created == [("inspect", project.resolve(), "coding-team")]


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
            return [{"id": "coding-team"}]

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
    assert _response_json(handler) == {"packs": [{"id": "coding-team"}]}
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
    monkeypatch.setattr(swarm_api, "get_active_profile_name", lambda: "reviewer")

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

    accepted = _Handler()
    assert (
        swarm_api.handle_swarm_post(
            accepted,
            urlparse("/api/swarm/runs/run-1/approve"),
            {"project_path": str(project), "proposal_id": "proposal-1", "deny": True},
        )
        is True
    )
    assert calls == [
        (project.resolve(), "run-1", "proposal-1", "webui:reviewer", False)
    ]


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
    handler = _Handler(
        headers={"Last-Event-ID": str(first.sequence)}, writer=writer
    )

    result = swarm_api.handle_swarm_get(
        handler,
        urlparse(f"/api/swarm/runs/events/stream?project_path={project}&run_id={run.run_id}"),
    )

    frame_text = writer.getvalue().decode("utf-8")
    assert result is True
    assert handler.status_code == 200
    assert "event: hello" in frame_text
    assert f"id: {second.sequence}" in frame_text
    assert '"event_type": "run.paused"' in frame_text
    assert ProjectSwarmStore(project).list_events(run.run_id) == [first, second]
