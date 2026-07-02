"""
Sidekick — Global Dispatcher.

Scans ALL spaces' kanban boards for ready tasks and dispatches them
to the corresponding agent within that space. Independent of the
currently "active" space — operates across the entire fleet.

Triggered by cron (every 5 min) or manually via API endpoint.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Worker pool ─────────────────────────────────────────────────────────────
_MAX_CONCURRENT_WORKERS = 4
_worker_semaphore = threading.Semaphore(_MAX_CONCURRENT_WORKERS)
_active_dispatches: dict[str, dict] = {}  # task_id -> info
_dispatches_lock = threading.Lock()

# ── Constants ───────────────────────────────────────────────────────────────
READY_STATUSES = {"todo", "ready", "backlog"}
IN_PROGRESS_STATUS = "in_progress"
DONE_STATUS = "done"


def dispatch_run_once(*, dry_run: bool = False) -> dict:
    """Scan all spaces for ready tasks and dispatch.

    Returns a report dict with counts and per-space details.
    """
    report = {
        "spaces_scanned": 0,
        "tasks_found_ready": 0,
        "tasks_dispatched": 0,
        "errors": [],
        "per_space": {},
    }

    try:
        from web.api.space_engine import get_all_spaces
    except ImportError:
        # Fallback to old workspace_isolation
        try:
            from web.api.workspace_isolation import get_all_workspaces as get_all_spaces
        except ImportError:
            report["errors"].append("space_engine not available")
            return report

    spaces = get_all_spaces()
    report["spaces_scanned"] = len(spaces)

    for space in spaces:
        space_result = _scan_space(space, dry_run=dry_run)
        report["tasks_found_ready"] += space_result["found_ready"]
        report["tasks_dispatched"] += space_result["dispatched"]
        report["per_space"][space.slug] = space_result
        if space_result.get("errors"):
            report["errors"].extend(space_result["errors"])

    return report


def _scan_space(space, *, dry_run: bool = False) -> dict:
    """Scan a single space's kanban boards for dispatchable tasks."""
    result = {
        "found_ready": 0,
        "dispatched": 0,
        "agent_count": 0,
        "boards_scanned": 0,
        "errors": [],
    }

    # Check if the space has a kanban db
    kanban_path = getattr(space, "kanban_path", None)
    if not kanban_path or not kanban_path.exists():
        return result  # No kanban = nothing to dispatch

    # Discover agents in this space
    agents = []
    try:
        agents = space.list_agents()
    except Exception:
        pass
    result["agent_count"] = len(agents)
    if not agents:
        return result  # No agents = nobody to dispatch to

    try:
        _iterate_space_boards(space, agents, result, dry_run=dry_run)
    except Exception as e:
        logger.exception("failed to scan space %s", space.slug)
        result["errors"].append(f"scan error: {e}")

    return result


def _iterate_space_boards(space, agents: list[str], result: dict, *, dry_run: bool = False) -> None:
    """Iterate boards in a space, find ready tasks, dispatch."""
    from web.api.kanban_bridge import _kb, _conn as _kb_conn

    kb = _kb()

    # Temporarily set kanban home to this space's root
    _set_space_kanban_home(str(space.root))
    try:
        # Get list of boards
        boards = ["default"]  # At minimum the default board
        try:
            with _kb_conn() as conn:
                all_boards = kb.list_boards(conn)
                if all_boards:
                    boards = [b.slug if hasattr(b, "slug") else str(b) for b in all_boards]
        except Exception:
            pass

        for board_slug in boards:
            try:
                result["boards_scanned"] += 1
                with _kb_conn(board=board_slug) as conn:
                    tasks = kb.list_tasks(conn)
                    _dispatch_ready_tasks(space, agents, conn, tasks, board_slug, result, dry_run=dry_run)
            except Exception as e:
                logger.debug("board %s/%s scan failed: %s", space.slug, board_slug, e)
    finally:
        _clear_kanban_home()


def _dispatch_ready_tasks(space, agents, conn, tasks, board_slug, result, *, dry_run: bool = False) -> None:
    """Find tasks with status in READY_STATUSES and dispatch them."""
    from web.api.kanban_bridge import _patch_task

    for task in tasks:
        status = getattr(task, "status", None) or ""
        if status not in READY_STATUSES:
            continue
        result["found_ready"] += 1

        # Determine target agent: use task.assignee or fallback to default
        assignee = getattr(task, "assignee", None) or "default"
        if assignee not in agents:
            logger.debug("space %s: task %s assigned to %r but space has agents=%s",
                         space.slug, getattr(task, "id", "?"), assignee, agents)
            continue

        if dry_run:
            result["dispatched"] += 1
            continue

        # Move task to in_progress
        task_id = str(getattr(task, "id", ""))
        if not task_id:
            continue

        try:
            _patch_task(conn, task_id, {"status": IN_PROGRESS_STATUS})
        except Exception as e:
            logger.warning("failed to set task %s to in_progress: %s", task_id, e)
            continue

        # Dispatch worker thread
        _spawn_worker(space, assignee, task_id, board_slug)
        result["dispatched"] += 1


def _spawn_worker(space, agent_slug: str, task_id: str, board_slug: str) -> None:
    """Spawn a background thread to work on a kanban task.

    The worker loads the agent's SOUL.md + space config and runs
    the task using the configured model.
    """
    worker_id = f"{space.slug}/{board_slug}/{task_id}"

    with _dispatches_lock:
        if worker_id in _active_dispatches:
            logger.debug("worker %s already active, skipping", worker_id)
            return
        _active_dispatches[worker_id] = {
            "space": space.slug,
            "board": board_slug,
            "task_id": task_id,
            "agent": agent_slug,
            "started_at": time.time(),
            "status": "spawning",
        }

    def _work():
        if not _worker_semaphore.acquire(timeout=30):
            logger.warning("worker %s: semaphore timeout, aborting", worker_id)
            return
        try:
            _execute_task(space, agent_slug, task_id, board_slug, worker_id)
        finally:
            _worker_semaphore.release()
            with _dispatches_lock:
                _active_dispatches.pop(worker_id, None)

    t = threading.Thread(target=_work, name=f"dispatch-{space.slug[:8]}-{task_id[:8]}", daemon=True)
    t.start()


def _execute_task(space, agent_slug: str, task_id: str, board_slug: str, worker_id: str) -> None:
    """Execute a dispatched task: spawn a Nova agent worker via subprocess.

    Mirrors the gateway dispatcher's ``_default_spawn`` pattern
    (``kanban_db._default_spawn``) but runs synchronously in a background
    thread — the subprocess exits on its own when the worker calls
    ``kanban_complete`` / ``kanban_block``, and we monitor its exit code
    to detect crashes.
    """
    import subprocess
    import shutil

    logger.info("dispatch worker %s starting", worker_id)

    # ── Resolve `hermes` binary ──────────────────────────────────────────
    hermes_bin = shutil.which("sidekick") or shutil.which("hermes")
    if not hermes_bin:
        hermes_bin = sys.executable
        hermes_args = ["-m", "sidekick_cli.main"]
    else:
        hermes_args = []

    # ── Build command ────────────────────────────────────────────────────
    cmd = (
        [hermes_bin] + hermes_args +
        ["-p", agent_slug,
         "--skills", "kanban-worker",
         "chat",
         "-q", f"work kanban task {task_id}"]
    )

    # ── Environment ──────────────────────────────────────────────────────
    env = dict(os.environ)
    env["SIDEKICK_KANBAN_TASK"] = task_id
    env["HERMES_KANBAN_TASK"] = task_id
    env["SIDEKICK_KANBAN_BOARD"] = board_slug
    env["HERMES_KANBAN_BOARD"] = board_slug
    env["SIDEKICK_PROFILE"] = agent_slug
    env["HERMES_PROFILE"] = agent_slug
    # Pin kanban home so the worker reads the right board
    env["SIDEKICK_KANBAN_HOME"] = str(space.root)
    env["HERMES_KANBAN_HOME"] = str(space.root)

    # ── Worker log (per-space/board/task) ──────────────────────────────
    log_dir = Path(space.root) / "kanban" / "boards" / board_slug / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{task_id}.log"
    # Rotate: keep last run
    if log_path.exists():
        rotated = log_path.with_suffix(log_path.suffix + ".1")
        try:
            if rotated.exists():
                rotated.unlink()
            log_path.rename(rotated)
        except OSError:
            pass

    logger.info(
        "dispatch %s | space=%s agent=%s task=%s | cmd=%s | log=%s",
        worker_id, space.slug, agent_slug, task_id,
        " ".join(str(p) for p in cmd), log_path,
    )

    # ── Spawn and wait ─────────────────────────────────────────────────
    try:
        with open(log_path, "ab") as log_f:
            proc = subprocess.Popen(
                cmd,
                cwd=str(space.root) if Path(space.root).is_dir() else None,
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )

        # Wait for the worker to finish (it calls kanban_complete/block itself)
        exit_code = proc.wait()
        logger.info(
            "dispatch worker %s exited (rc=%d)", worker_id, exit_code,
        )

        if exit_code != 0:
            # Worker crashed / was killed — reset task to ready
            _set_space_kanban_home(str(space.root))
            try:
                from web.api.kanban_bridge import _conn as _kb_conn, _patch_task as _patch
                with _kb_conn(board=board_slug) as conn:
                    _patch(conn, task_id, {"status": "ready"})
            except Exception as e:
                logger.warning(
                    "worker %s: failed to reset crashed task to ready: %s",
                    worker_id, e,
                )
            finally:
                _clear_kanban_home()

    except FileNotFoundError:
        logger.error(
            "dispatch worker %s: `hermes` executable not found on PATH "
            "(tried: %s); cannot spawn worker",
            worker_id, hermes_bin,
        )
        _set_space_kanban_home(str(space.root))
        try:
            from web.api.kanban_bridge import _conn as _kb_conn, _patch_task as _patch
            with _kb_conn(board=board_slug) as conn:
                _patch(conn, task_id, {"status": "blocked",
                                       "block_reason": "sidekick binary not found on PATH"})
        except Exception:
            pass
        finally:
            _clear_kanban_home()

    except Exception as e:
        logger.exception("dispatch worker %s: spawn failed: %s", worker_id, e)
        _set_space_kanban_home(str(space.root))
        try:
            from web.api.kanban_bridge import _conn as _kb_conn, _patch_task as _patch
            with _kb_conn(board=board_slug) as conn:
                _patch(conn, task_id, {"status": "ready"})
        except Exception:
            pass
        finally:
            _clear_kanban_home()

    logger.info("dispatch worker %s completed", worker_id)


# ── Kanban home helpers (isolated from request context) ─────────────────────

def _set_space_kanban_home(space_root: str) -> None:
    """Set kanban home for this thread (bypasses request-local)."""
    os.environ["SIDEKICK_KANBAN_HOME"] = space_root
    os.environ["HERMES_KANBAN_HOME"] = space_root  # backward compat


def _clear_kanban_home() -> None:
    os.environ.pop("SIDEKICK_KANBAN_HOME", None)
    os.environ.pop("HERMES_KANBAN_HOME", None)


# ── Status / Monitoring ─────────────────────────────────────────────────────

def get_active_dispatches() -> dict:
    """Return currently active dispatches (task_id → info)."""
    with _dispatches_lock:
        return dict(_active_dispatches)


# Need os for kanban home
import os
