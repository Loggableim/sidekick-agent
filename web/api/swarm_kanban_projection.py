"""Explicit, Sidekick-owned projection of a persisted Swarm run into Kanban.

The projection is deliberately one-way: it creates a triage task for a human
to inspect, then records the resulting Kanban identity as a Swarm event.  It
does not use any dispatcher API and no Kanban event is ever read as Swarm
state.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Any, Callable, ContextManager

from swarm_core.store import ProjectSwarmStore
from web.api.space_engine import resolve_active_space


_BOARD = "default"
_CONNECTION_LOCK = threading.RLock()
_PROJECTION_CLAIM_KEY = "sidekick-kanban-projection"
_PROJECTION_WAIT_SECONDS = 0.5


@dataclass(frozen=True)
class KanbanProjection:
    """Stable Sidekick-owned identity for a projected Swarm run."""

    task_id: str
    board: str
    space_slug: str


class ProjectionInProgressError(RuntimeError):
    """A prior durable claim may already have produced an external task."""


class SidekickKanbanProjector:
    """Create an optional Kanban triage task without changing Swarm truth."""

    def __init__(
        self,
        *,
        active_space: Callable[[], Any] = resolve_active_space,
        kanban_module: Callable[[], Any] | None = None,
        connection_for_space: Callable[[Any, str], ContextManager[Any]] | None = None,
    ) -> None:
        self._active_space = active_space
        self._kanban_module = kanban_module or _kanban_module
        self._connection_for_space = connection_for_space or _space_kanban_connection

    def project(self, project_root: Path, run_id: str) -> KanbanProjection:
        """Project one persisted run, or return its earlier recorded projection.

        A read-only open is intentional: a typo must not initialize a Swarm
        project just because the user pressed the optional projection button.
        """
        project_root = Path(project_root).resolve()
        reader = ProjectSwarmStore.open_read_only(project_root)
        run = reader.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown Swarm run: {run_id}")

        space = self._space_for_project(project_root)
        existing = _recorded_projection(reader.list_events(run_id))
        if existing is not None:
            return existing

        # Claim Swarm truth before opening a Kanban connection.  Unlike the
        # previous per-Space Python lock, this durable event is global to the
        # project/run and remains after a process crashes between the external
        # write and its confirmation event.
        store = ProjectSwarmStore(project_root)
        _, claimed = store.append_event_once(
            run_id,
            "sidekick.kanban_projection_claimed",
            {
                "board": _BOARD,
                "space_slug": str(space.slug),
                "state": "external_write_pending",
            },
            idempotency_key=_PROJECTION_CLAIM_KEY,
        )
        if not claimed:
            recorded = _await_recorded_projection(project_root, run_id)
            if recorded is not None:
                return recorded
            raise _projection_in_progress_error(project_root, run_id)

        try:
            projection = self._create_task(space, project_root, run_id, run.metadata)
            event, created = store.append_event_once(
                run_id,
                "sidekick.kanban_projection_created",
                {
                    "board": projection.board,
                    "kanban_task_id": projection.task_id,
                    "space_slug": projection.space_slug,
                },
                idempotency_key=_PROJECTION_CLAIM_KEY,
            )
            if created:
                return projection
            recorded = _recorded_projection((event,))
            assert recorded is not None
            return recorded
        except Exception as exc:
            self._record_failure(project_root, run_id, exc)
            raise RuntimeError(_error_text(exc)) from exc

    def _space_for_project(self, project_root: Path):
        space = self._active_space()
        configured = space.get_project_dir()
        if not configured:
            raise LookupError(
                "Active Sidekick Space has no configured project directory"
            )
        if Path(configured).expanduser().resolve() != project_root:
            raise LookupError("Active Sidekick Space does not match project_path")
        return space

    def _create_task(
        self,
        space,
        project_root: Path,
        run_id: str,
        metadata: dict[str, Any],
    ) -> KanbanProjection:
        goal = str(metadata.get("goal") or "").strip()
        title = f"Swarm: {goal}" if goal else f"Swarm run {run_id}"
        body = "\n".join(
            (
                "Sidekick-only projection of a project-local Swarm run.",
                f"Run: {run_id}",
                f"Project: {project_root}",
            )
        )
        kanban = self._kanban_module()
        with self._connection_for_space(space, _BOARD) as connection:
            task_id = kanban.create_task(
                connection,
                title=title,
                body=body,
                created_by="sidekick-swarm",
                triage=True,
                idempotency_key=f"swarm-kanban-projection:{run_id}",
            )
        return KanbanProjection(
            task_id=str(task_id),
            board=_BOARD,
            space_slug=str(space.slug),
        )

    @staticmethod
    def _record_failure(project_root: Path, run_id: str, exc: Exception) -> None:
        try:
            ProjectSwarmStore(project_root).append_event_once(
                run_id,
                "sidekick.kanban_projection_failed",
                {
                    "error": _error_text(exc),
                    "state": "external_write_outcome_unknown",
                },
                idempotency_key=_PROJECTION_CLAIM_KEY,
            )
        except Exception:
            # Projection status is supplementary information only.  A failure
            # to record it must never rewrite or pause the core Swarm run.
            return


def project_swarm_run_to_kanban(project_root: Path, run_id: str) -> KanbanProjection:
    """Default route-facing adapter, isolated from the Swarm core package."""
    return SidekickKanbanProjector().project(project_root, run_id)


def _recorded_projection(events) -> KanbanProjection | None:
    for event in reversed(list(events)):
        if event.event_type != "sidekick.kanban_projection_created":
            continue
        payload = event.payload
        task_id = payload.get("kanban_task_id")
        board = payload.get("board")
        space_slug = payload.get("space_slug")
        if all(
            isinstance(value, str) and value.strip()
            for value in (task_id, board, space_slug)
        ):
            return KanbanProjection(task_id=task_id, board=board, space_slug=space_slug)
    return None


def _await_recorded_projection(
    project_root: Path, run_id: str
) -> KanbanProjection | None:
    """Give the existing claim a short window to publish its confirmation."""
    deadline = time.monotonic() + _PROJECTION_WAIT_SECONDS
    while True:
        reader = ProjectSwarmStore.open_read_only(project_root)
        recorded = _recorded_projection(reader.list_events(run_id))
        if recorded is not None:
            return recorded
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def _projection_in_progress_error(
    project_root: Path, run_id: str
) -> ProjectionInProgressError:
    events = ProjectSwarmStore.open_read_only(project_root).list_events(run_id)
    for event in reversed(events):
        if event.event_type != "sidekick.kanban_projection_failed":
            continue
        error = event.payload.get("error")
        detail = str(error).strip() if error else "external write outcome is unknown"
        return ProjectionInProgressError(
            "Kanban projection requires human reconciliation: " + detail
        )
    return ProjectionInProgressError(
        "Kanban projection is already in progress; no second board will be written"
    )


def _kanban_module():
    from cli import kanban_db

    return kanban_db


@contextmanager
def _space_kanban_connection(space, board: str):
    """Open the host Space's board without retaining its thread-local override."""
    from web.api import kanban_bridge

    connection = None
    previous_home = kanban_bridge._get_ws_kanban_home()
    with _CONNECTION_LOCK:
        try:
            kanban_bridge.set_workspace_kanban(str(space.root))
            connection = kanban_bridge._conn(board=board)
        finally:
            if previous_home is None:
                kanban_bridge.clear_workspace_kanban()
            else:
                kanban_bridge.set_workspace_kanban(previous_home)
    try:
        yield connection
    finally:
        if connection is not None:
            connection.close()


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text[:500] if text else exc.__class__.__name__
