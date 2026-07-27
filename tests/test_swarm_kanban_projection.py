from __future__ import annotations

from contextlib import closing
import multiprocessing
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from swarm_core.store import ProjectSwarmStore


def _bootstrap_source_root() -> None:
    """Make spawn workers import this worktree, not the suite's ambient CWD."""
    source_root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        str(source_root),
        *[entry for entry in sys.path if Path(entry or ".").resolve() != source_root],
    ]


def _project_run_in_separate_process(
    project_path: str,
    space_root: str,
    kanban_path: str,
    start_gate,
    idempotency_read_barrier,
    results,
    strip_source_root: bool = False,
) -> None:
    """Run the real projection through an independently initialized process."""
    if strip_source_root:
        source_root = Path(__file__).resolve().parents[1]
        sys.path[:] = [
            entry for entry in sys.path if Path(entry or ".").resolve() != source_root
        ]
    _bootstrap_source_root()
    from cli import kanban_db as kb
    from web.api.swarm_kanban_projection import SidekickKanbanProjector

    project = Path(project_path)
    space = SimpleNamespace(
        slug="project-space",
        root=Path(space_root),
        get_project_dir=lambda: str(project),
    )

    def connection_for_space(_space, _board):
        connection = kb.connect(Path(kanban_path))

        def synchronize_racy_legacy_lookup(statement: str) -> None:
            normalized = " ".join(statement.split())
            if (
                normalized.startswith("SELECT id FROM tasks WHERE idempotency_key =")
                and not connection.in_transaction
            ):
                idempotency_read_barrier.wait(timeout=10)

        connection.set_trace_callback(synchronize_racy_legacy_lookup)
        return closing(connection)

    projector = SidekickKanbanProjector(
        active_space=lambda: space,
        kanban_module=lambda: kb,
        connection_for_space=connection_for_space,
    )
    try:
        start_gate.wait(timeout=10)
        projection = projector.project(project, "run-1")
        results.put(("ok", projection.task_id))
    except BaseException as exc:
        results.put(("error", f"{exc.__class__.__name__}: {exc}"))
        raise


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
            "sidekick.kanban_projection_claimed",
            {
                "board": "default",
                "space_slug": "project-space",
                "state": "external_write_pending",
            },
        ),
        (
            "sidekick.kanban_projection_created",
            {
                "board": "default",
                "kanban_task_id": first.task_id,
                "space_slug": "project-space",
            },
        ),
    ]


def test_projection_is_cross_process_idempotent_for_task_and_swarm_event(
    tmp_path: Path,
):
    """Catches two Sidekick processes creating duplicate Kanban work or evidence."""
    from cli import kanban_db as kb

    project = tmp_path / "project"
    project.mkdir()
    store = ProjectSwarmStore(project)
    store.create_run(run_id="run-1", status="paused", metadata={"goal": "Review"})
    space_root = tmp_path / "space"
    space_root.mkdir()
    kanban_path = tmp_path / "kanban.sqlite"

    context = multiprocessing.get_context("spawn")
    start_gate = context.Event()
    idempotency_read_barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_project_run_in_separate_process,
            args=(
                str(project),
                str(space_root),
                str(kanban_path),
                start_gate,
                idempotency_read_barrier,
                results,
                True,
            ),
        )
        for _ in range(2)
    ]

    try:
        for process in processes:
            process.start()
        start_gate.set()
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
        result_rows = [results.get(timeout=2) for _ in processes]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)

    assert result_rows[0][0] == result_rows[1][0] == "ok"
    assert result_rows[0][1] == result_rows[1][1]
    with closing(kb.connect(kanban_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    events = ProjectSwarmStore(project).list_events("run-1")
    assert [(event.event_type, event.payload) for event in events] == [
        (
            "sidekick.kanban_projection_claimed",
            {
                "board": "default",
                "space_slug": "project-space",
                "state": "external_write_pending",
            },
        ),
        (
            "sidekick.kanban_projection_created",
            {
                "board": "default",
                "kanban_task_id": result_rows[0][1],
                "space_slug": "project-space",
            },
        ),
    ]


def test_projection_claim_blocks_a_second_space_before_it_writes_another_board(
    tmp_path: Path,
):
    """Catches two Spaces for one project each creating a separate Kanban task."""
    from cli import kanban_db as kb
    from web.api.swarm_kanban_projection import SidekickKanbanProjector

    project = tmp_path / "project"
    project.mkdir()
    ProjectSwarmStore(project).create_run(run_id="run-1", status="paused")
    first_space = _mapped_space(project, tmp_path / "space-a")
    first_space.slug = "space-a"
    second_space = _mapped_space(project, tmp_path / "space-b")
    second_space.slug = "space-b"
    first_board = tmp_path / "kanban-a.sqlite"
    first_connection_entered = threading.Event()
    release_first_connection = threading.Event()
    first_result: dict[str, object] = {}
    second_connection_attempted = False

    def delayed_first_connection(_space, _board):
        first_connection_entered.set()
        assert release_first_connection.wait(timeout=10)
        return closing(kb.connect(first_board))

    first_projector = SidekickKanbanProjector(
        active_space=lambda: first_space,
        kanban_module=lambda: kb,
        connection_for_space=delayed_first_connection,
    )

    def create_first_projection() -> None:
        try:
            first_result["projection"] = first_projector.project(project, "run-1")
        except BaseException as exc:
            first_result["error"] = exc

    first_thread = threading.Thread(target=create_first_projection)
    first_thread.start()
    try:
        assert first_connection_entered.wait(timeout=10)

        def unexpected_second_connection(_space, _board):
            nonlocal second_connection_attempted
            second_connection_attempted = True
            raise AssertionError("a claimed projection must not open a second board")

        second_projector = SidekickKanbanProjector(
            active_space=lambda: second_space,
            kanban_module=lambda: kb,
            connection_for_space=unexpected_second_connection,
        )
        with pytest.raises(RuntimeError, match="already in progress"):
            second_projector.project(project, "run-1")
        assert second_connection_attempted is False
    finally:
        release_first_connection.set()
        first_thread.join(timeout=10)

    assert first_thread.is_alive() is False
    assert "error" not in first_result
    first_projection = first_result["projection"]
    assert first_projection.space_slug == "space-a"
    with closing(kb.connect(first_board)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    events = ProjectSwarmStore(project).list_events("run-1")
    assert [event.event_type for event in events] == [
        "sidekick.kanban_projection_claimed",
        "sidekick.kanban_projection_created",
    ]


def test_uncertain_projection_claim_never_retries_into_a_second_space_board(
    tmp_path: Path,
):
    """Catches a lost post-commit response being treated as safe to retry elsewhere."""
    from cli import kanban_db as kb
    from web.api.swarm_kanban_projection import SidekickKanbanProjector

    project = tmp_path / "project"
    project.mkdir()
    ProjectSwarmStore(project).create_run(run_id="run-1", status="paused")
    first_space = _mapped_space(project, tmp_path / "space-a")
    first_space.slug = "space-a"
    second_space = _mapped_space(project, tmp_path / "space-b")
    second_space.slug = "space-b"
    first_board = tmp_path / "kanban-a.sqlite"
    second_connection_attempted = False

    def create_then_lose_response(connection, **kwargs):
        kb.create_task(connection, **kwargs)
        raise OSError("Kanban response lost after task commit")

    first_projector = SidekickKanbanProjector(
        active_space=lambda: first_space,
        kanban_module=lambda: SimpleNamespace(create_task=create_then_lose_response),
        connection_for_space=lambda _space, _board: closing(kb.connect(first_board)),
    )
    with pytest.raises(RuntimeError, match="response lost after task commit"):
        first_projector.project(project, "run-1")

    def unexpected_second_connection(_space, _board):
        nonlocal second_connection_attempted
        second_connection_attempted = True
        raise AssertionError("an uncertain claim must block all automatic retries")

    second_projector = SidekickKanbanProjector(
        active_space=lambda: second_space,
        kanban_module=lambda: kb,
        connection_for_space=unexpected_second_connection,
    )
    with pytest.raises(RuntimeError, match="requires human reconciliation"):
        second_projector.project(project, "run-1")

    assert second_connection_attempted is False
    with closing(kb.connect(first_board)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    events = ProjectSwarmStore(project).list_events("run-1")
    assert [event.event_type for event in events] == [
        "sidekick.kanban_projection_claimed",
        "sidekick.kanban_projection_failed",
    ]
    assert events[-1].payload["state"] == "external_write_outcome_unknown"


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
    assert [event.event_type for event in events] == [
        "sidekick.kanban_projection_claimed",
        "sidekick.kanban_projection_failed",
    ]
    assert events[0].payload["state"] == "external_write_pending"
    assert events[1].payload == {
        "error": "kanban storage is unavailable",
        "state": "external_write_outcome_unknown",
    }


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
