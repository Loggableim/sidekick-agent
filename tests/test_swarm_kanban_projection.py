from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from swarm_core.store import ProjectSwarmStore


def _mapped_space(project: Path, root: Path):
    root.mkdir()
    return SimpleNamespace(
        slug="project-space",
        root=root,
        get_project_dir=lambda: str(project),
    )


def test_projection_creates_one_triage_task_and_persists_identity_without_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches a Swarm projection dispatching work, skipping triage, or duplicating a retry."""
    from cli import kanban_db as kb
    from web.api.swarm_kanban_projection import SidekickKanbanProjector

    project = tmp_path / "project"
    project.mkdir()
    store = ProjectSwarmStore(project)
    run = store.create_run(
        run_id="run-1",
        status="paused",
        metadata={"goal": "Review a safe change"},
    )
    space = _mapped_space(project, tmp_path / "space")
    kanban_path = tmp_path / "kanban.sqlite"
    connected_spaces: list[object] = []

    def never_dispatch(*_args, **_kwargs):
        raise AssertionError("a Swarm-to-Kanban projection must not dispatch workers")

    monkeypatch.setattr(kb, "dispatch_once", never_dispatch)

    def connection_for_space(actual_space, board):
        assert board == "default"
        connected_spaces.append(actual_space)
        return closing(kb.connect(kanban_path))

    projector = SidekickKanbanProjector(
        active_space=lambda: space,
        kanban_module=lambda: kb,
        connection_for_space=connection_for_space,
    )

    first = projector.project(project, run.run_id)
    second = projector.project(project, run.run_id)

    assert first.task_id == second.task_id
    assert first.board == "default"
    assert first.space_slug == "project-space"
    assert connected_spaces == [space]
    with closing(kb.connect(kanban_path)) as conn:
        task = kb.get_task(conn, first.task_id)
        assert task is not None
        assert task.status == "triage"
        assert task.created_by == "sidekick-swarm"
        assert task.idempotency_key == "swarm-kanban-projection:run-1"
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1

    assert ProjectSwarmStore(project).get_run(run.run_id).status == "paused"
    events = ProjectSwarmStore(project).list_events(run.run_id)
    assert [(event.event_type, event.payload) for event in events] == [
        (
            "sidekick.kanban_projection_created",
            {
                "board": "default",
                "kanban_task_id": first.task_id,
                "space_slug": "project-space",
            },
        )
    ]


def test_projection_records_a_visible_failure_without_rewriting_swarm_status(
    tmp_path: Path,
):
    """Catches a Kanban outage pausing, completing, or otherwise changing Swarm truth."""
    from web.api.swarm_kanban_projection import SidekickKanbanProjector

    project = tmp_path / "project"
    project.mkdir()
    store = ProjectSwarmStore(project)
    run = store.create_run(run_id="run-1", status="paused")
    space = _mapped_space(project, tmp_path / "space")

    def unavailable(_space, _board):
        raise OSError("kanban storage is unavailable")

    projector = SidekickKanbanProjector(
        active_space=lambda: space,
        connection_for_space=unavailable,
    )

    with pytest.raises(RuntimeError, match="kanban storage is unavailable"):
        projector.project(project, run.run_id)

    assert ProjectSwarmStore(project).get_run(run.run_id).status == "paused"
    events = ProjectSwarmStore(project).list_events(run.run_id)
    assert len(events) == 1
    assert events[0].event_type == "sidekick.kanban_projection_failed"
    assert events[0].payload["error"] == "kanban storage is unavailable"


def test_projection_rejects_a_space_that_does_not_host_the_explicit_project(
    tmp_path: Path,
):
    """Catches a client-supplied project path being projected into an unrelated Space board."""
    from web.api.swarm_kanban_projection import SidekickKanbanProjector

    project = tmp_path / "project"
    unrelated = tmp_path / "unrelated"
    project.mkdir()
    unrelated.mkdir()
    store = ProjectSwarmStore(project)
    run = store.create_run(run_id="run-1")
    space = _mapped_space(unrelated, tmp_path / "space")
    connection_attempted = False

    def unexpected_connection(_space, _board):
        nonlocal connection_attempted
        connection_attempted = True
        raise AssertionError("a mismatched Space must be rejected before Kanban access")

    projector = SidekickKanbanProjector(
        active_space=lambda: space,
        connection_for_space=unexpected_connection,
    )

    with pytest.raises(LookupError, match="does not match"):
        projector.project(project, run.run_id)

    assert connection_attempted is False


def test_projection_of_an_unknown_run_does_not_initialize_swarm_state(
    tmp_path: Path,
):
    """Catches a misspelled run id creating a new .swarm project before it fails."""
    from web.api.swarm_kanban_projection import SidekickKanbanProjector

    project = tmp_path / "project"
    project.mkdir()
    space = _mapped_space(project, tmp_path / "space")
    connection_attempted = False

    def unexpected_connection(_space, _board):
        nonlocal connection_attempted
        connection_attempted = True
        raise AssertionError("an absent Swarm run must fail before Kanban access")

    projector = SidekickKanbanProjector(
        active_space=lambda: space,
        connection_for_space=unexpected_connection,
    )

    with pytest.raises(FileNotFoundError, match="Swarm project is not initialized"):
        projector.project(project, "missing")

    assert connection_attempted is False
    assert not (project / ".swarm").exists()
