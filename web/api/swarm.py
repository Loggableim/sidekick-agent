"""HTTP/SSE bridge for project-local Swarm state.

The bridge deliberately separates read-only observations from writes.  It
never interprets a WebUI ``workspace`` slug as a filesystem path: the active
Space supplies its configured project directory, which is then checked by the
established trusted-workspace resolver.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote

from cli.swarm import get_swarm_service
from swarm_core.config import load_project_config
from swarm_core.store import ProjectSwarmStore
from web.api.helpers import bad, j
from web.api.space_engine import resolve_active_space
from web.api.swarm_kanban_projection import project_swarm_run_to_kanban
from web.api.workspace import (
    resolve_trusted_workspace,
    resolve_trusted_workspace_read_only,
)


_RUNS_PREFIX = "/api/swarm/runs/"
_SSE_PATH = "/api/swarm/runs/events/stream"
_SSE_BATCH_LIMIT = 100
_SSE_POLL_SECONDS = 0.5
_SSE_HEARTBEAT_SECONDS = 15.0
_BACKGROUND_RUNS_LOCK = threading.RLock()
_LOG = logging.getLogger(__name__)


@dataclass
class _BackgroundRun:
    """In-memory ownership state for one process-local worker."""

    thread: threading.Thread | None = None
    state: str = "starting"
    start_gate: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)


_BACKGROUND_RUNS: dict[tuple[str, str], _BackgroundRun] = {}


def handle_swarm_get(handler, parsed) -> bool | None:
    """Handle Swarm GET routes using the established three-valued contract."""
    path = parsed.path
    if path not in {
        "/api/swarm/runs",
        "/api/swarm/models",
        "/api/swarm/packs",
        _SSE_PATH,
    } and not path.startswith(_RUNS_PREFIX):
        return False
    try:
        project_root = _resolve_project_path(
            parsed,
            require_explicit=True,
            read_only=True,
        )
        if path == "/api/swarm/packs":
            # Packs are versioned config metadata, not runtime state.  They
            # remain readable immediately after `swarm init`, before a first
            # SQLite-backed run, while still returning 404 for no `.swarm`.
            load_project_config(project_root)
            return (
                j(
                    handler,
                    {"packs": _jsonable(get_swarm_service().list_packs(project_root))},
                )
                or True
            )
        # This is intentionally the first Swarm operation for every GET.  It
        # opens an existing SQLite file with mode=ro and rejects uninitialized
        # projects without creating config, runtime state, or migrations.
        reader = ProjectSwarmStore.open_read_only(project_root)
        if path == "/api/swarm/runs":
            return j(handler, {"runs": _jsonable(reader.list_runs())}) or True
        if path == "/api/swarm/models":
            return (
                j(
                    handler,
                    {
                        "catalog": _jsonable(
                            reader.get_model_catalog_snapshot("ollama-cloud")
                        )
                    },
                )
                or True
            )
        if path == _SSE_PATH:
            run_id = _query_text(parsed, "run_id")
            if not run_id:
                return bad(handler, "run_id is required")
            if reader.get_run(run_id) is None:
                return bad(handler, "Swarm run not found", status=404)
            return _handle_events_sse_stream(handler, parsed, reader, run_id)
        run_id = _run_id_from_path(path)
        if run_id is None:
            return False
        run = reader.get_run(run_id)
        if run is None:
            return bad(handler, "Swarm run not found", status=404)
        return (
            j(
                handler,
                {
                    "run": _jsonable(run),
                    "events": _jsonable(reader.list_events(run_id)),
                    "approvals": _jsonable(reader.list_approvals(run_id)),
                },
            )
            or True
        )
    except FileNotFoundError as exc:
        return bad(handler, str(exc), status=404)
    except LookupError as exc:
        return bad(handler, str(exc), status=404)
    except (TypeError, ValueError) as exc:
        return bad(handler, str(exc), status=400)
    except RuntimeError as exc:
        return bad(handler, str(exc), status=409)


def handle_swarm_post(handler, parsed, body) -> bool | None:
    """Handle Swarm write routes after resolving a trusted project path."""
    path = parsed.path
    if path == "/api/swarm/runs":
        return _create_run(handler, parsed, body)
    if path == "/api/swarm/models/refresh":
        return _refresh_models(handler, parsed, body)
    if not path.startswith(_RUNS_PREFIX):
        return False
    run_id, action = _run_write_target(path)
    if run_id is None or action is None:
        return False
    if action == "approve":
        return _record_human_approval(handler, parsed, body, run_id)
    if action == "pause":
        return _change_status(handler, parsed, body, run_id, pause=True)
    if action == "resume":
        return _change_status(handler, parsed, body, run_id, pause=False)
    if action == "recover":
        return _recover_execution_lease(handler, parsed, body, run_id)
    if action == "kanban-projection":
        return _project_to_kanban(handler, parsed, body, run_id)
    return False


def _create_run(handler, parsed, body: Mapping[str, Any]) -> bool | None:
    try:
        project_root = _resolve_project_path(parsed, body)
        _reject_unknown_keys(body, {"project_path", "goal", "pack"})
        goal = _required_text(body, "goal")
        pack = str(body.get("pack") or "coding-team").strip()
        if not pack:
            raise ValueError("pack must be non-empty")
        service = get_swarm_service()
        run = service.start_run(goal, project_root, pack=pack)
        execution = _launch_background_execution(service, project_root, run.run_id)
        try:
            response = j(handler, {"run": _jsonable(run)}, status=201)
        except Exception as exc:
            _cancel_unpublished_background_execution(
                project_root, run.run_id, execution, exc
            )
            raise
        # The worker is deliberately started first so a thread-start failure
        # becomes a durable paused run before returning 201.  Its gate keeps
        # the response snapshot deterministic: execution cannot change it
        # before the handler has written the response body.
        execution.start_gate.set()
        return response or True
    except FileNotFoundError as exc:
        return bad(handler, str(exc), status=404)
    except (TypeError, ValueError) as exc:
        return bad(handler, str(exc), status=400)
    except RuntimeError as exc:
        return bad(handler, str(exc), status=409)


def _refresh_models(handler, parsed, body: Mapping[str, Any]) -> bool | None:
    try:
        project_root = _resolve_project_path(parsed, body)
        _reject_unknown_keys(body, {"project_path"})
        snapshot = get_swarm_service().refresh_models(project_root)
        return j(handler, {"catalog": _jsonable(snapshot)}) or True
    except FileNotFoundError as exc:
        return bad(handler, str(exc), status=404)
    except (TypeError, ValueError) as exc:
        return bad(handler, str(exc), status=400)
    except RuntimeError as exc:
        return bad(handler, str(exc), status=409)


def _record_human_approval(
    handler,
    parsed,
    body: Mapping[str, Any],
    run_id: str,
) -> bool | None:
    try:
        project_root = _resolve_project_path(parsed, body)
        # Explicit allow-list: callers cannot turn a human decision into a
        # verifier/model approval or choose an identity/evidence family.
        _reject_unknown_keys(body, {"project_path", "proposal_id", "deny"})
        proposal_id = _required_text(body, "proposal_id")
        deny = body.get("deny", False)
        if not isinstance(deny, bool):
            raise ValueError("deny must be a bool")
        actor_id = _require_host_approval_actor(handler)
        approval = get_swarm_service().record_human_approval(
            project_root,
            run_id,
            proposal_id,
            actor_id=actor_id,
            approved=not deny,
        )
        return j(handler, {"approval": _jsonable(approval)}) or True
    except FileNotFoundError as exc:
        return bad(handler, str(exc), status=404)
    except KeyError as exc:
        return bad(handler, str(exc), status=404)
    except PermissionError as exc:
        return bad(handler, str(exc), status=403)
    except (TypeError, ValueError) as exc:
        return bad(handler, str(exc), status=400)
    except RuntimeError as exc:
        return bad(handler, str(exc), status=409)


def _change_status(
    handler,
    parsed,
    body: Mapping[str, Any],
    run_id: str,
    *,
    pause: bool,
) -> bool | None:
    try:
        project_root = _resolve_project_path(parsed, body)
        _reject_unknown_keys(body, {"project_path"})
        service = get_swarm_service()
        if pause:
            run = service.pause(project_root, run_id)
        else:
            existing = ProjectSwarmStore.open_read_only(project_root).get_run(run_id)
            if existing is None:
                raise KeyError(f"Unknown Swarm run: {run_id}")
            if existing.status == "paused":
                run, launched = _resume_paused_execution(service, project_root, run_id)
                try:
                    response = j(handler, {"run": _jsonable(run)})
                except Exception as exc:
                    if launched is not None:
                        _cancel_unpublished_background_execution(
                            project_root, run_id, launched, exc
                        )
                    raise
                # Preserve the same response-before-execution boundary used by
                # run creation.  A restart-resume must expose its durable
                # running state before the continuation can complete.
                if launched is not None:
                    launched.start_gate.set()
                return response or True
            run = service.resume(project_root, run_id)
        return j(handler, {"run": _jsonable(run)}) or True
    except FileNotFoundError as exc:
        return bad(handler, str(exc), status=404)
    except KeyError as exc:
        return bad(handler, str(exc), status=404)
    except (TypeError, ValueError) as exc:
        return bad(handler, str(exc), status=400)
    except RuntimeError as exc:
        return bad(handler, str(exc), status=409)


def _recover_execution_lease(
    handler,
    parsed,
    body: Mapping[str, Any],
    run_id: str,
) -> bool | None:
    """Audit a confirmed crashed host without resuming its workflow.

    A durable lease cannot prove whether another *process* is still alive, so
    the operator confirmation is intentionally explicit.  We can prove the
    local case, though: keep the in-process worker lock while checking and
    recovering so a live Sidekick worker is never manually taken over.
    """
    try:
        project_root = _resolve_project_path(parsed, body)
        _reject_unknown_keys(body, {"project_path"})
        actor_id = _require_host_approval_actor(handler)
        key = (str(Path(project_root).resolve()), run_id)
        service = get_swarm_service()
        with _BACKGROUND_RUNS_LOCK:
            execution = _BACKGROUND_RUNS.get(key)
            if (
                execution is not None
                and execution.thread is not None
                and execution.thread.is_alive()
            ):
                raise RuntimeError(
                    "Swarm execution is still active in this Sidekick process"
                )
            if execution is not None:
                _BACKGROUND_RUNS.pop(key, None)
            # Hold the registry lock through the short durable handoff.  A
            # concurrent local resume/create cannot launch a new worker
            # between the liveness proof and clearing the stale DB lease.
            run = service.recover_execution_lease(
                project_root,
                run_id,
                actor_id=actor_id,
            )
        return j(handler, {"run": _jsonable(run)}) or True
    except FileNotFoundError as exc:
        return bad(handler, str(exc), status=404)
    except KeyError as exc:
        return bad(handler, str(exc), status=404)
    except PermissionError as exc:
        return bad(handler, str(exc), status=403)
    except (TypeError, ValueError) as exc:
        return bad(handler, str(exc), status=400)
    except RuntimeError as exc:
        return bad(handler, str(exc), status=409)


def _launch_background_execution(
    service, project_root: Path, run_id: str
) -> _BackgroundRun:
    """Launch at most one tracked worker for one durable run.

    The worker is deliberately process-local.  A stopped Sidekick process does
    not pretend to resume a possibly costly workflow from an incomplete memory
    checkpoint; its durable run remains observable and paused/running for a
    human to inspect.
    """
    project_root = Path(project_root).resolve()
    key = (str(project_root), run_id)
    with _BACKGROUND_RUNS_LOCK:
        existing = _BACKGROUND_RUNS.get(key)
        if (
            existing is not None
            and existing.thread is not None
            and existing.thread.is_alive()
        ):
            raise RuntimeError("Swarm execution is already active for this run")
        if existing is not None:
            _BACKGROUND_RUNS.pop(key, None)

        execution = _BackgroundRun()

        def execute() -> None:
            try:
                execution.start_gate.wait()
                if execution.cancelled.is_set():
                    return
                _set_background_run_state(key, execution, "executing")
                service.execute_run(
                    project_root,
                    run_id,
                    on_pause_wait=lambda: _set_background_run_state(
                        key, execution, "waiting_for_resume"
                    ),
                    on_resume=lambda: _set_background_run_state(
                        key, execution, "executing"
                    ),
                )
            except Exception as exc:
                _record_background_execution_failure(project_root, run_id, exc)
            finally:
                with _BACKGROUND_RUNS_LOCK:
                    if _BACKGROUND_RUNS.get(key) is execution:
                        execution.state = "finished"
                        _BACKGROUND_RUNS.pop(key, None)

        thread = threading.Thread(
            target=execute,
            name=f"sidekick-swarm-{run_id[:8]}",
            daemon=True,
        )
        execution.thread = thread
        _BACKGROUND_RUNS[key] = execution
        try:
            thread.start()
        except Exception as exc:
            _BACKGROUND_RUNS.pop(key, None)
            _record_background_execution_failure(project_root, run_id, exc)
            raise RuntimeError(
                "Could not start the Swarm background execution"
            ) from exc
    return execution


def _cancel_unpublished_background_execution(
    project_root: Path,
    run_id: str,
    execution: _BackgroundRun,
    exc: Exception,
) -> None:
    """Release a pre-publication worker without leaving a durable false run."""
    execution.cancelled.set()
    _record_background_execution_failure(project_root, run_id, exc)
    execution.start_gate.set()


def _resume_paused_execution(
    service,
    project_root: Path,
    run_id: str,
) -> tuple[object, _BackgroundRun | None]:
    """Resume a paused run at a safe live boundary or relaunch it durably.

    A live worker may only be resumed while it is waiting before a model call.
    If no worker exists (for example, a provider/model pause or a Sidekick
    process restart), the durable run's staged checkpoint is resumed through a
    newly tracked worker.  We never restart an in-flight worker or silently
    create a second executor for the same run.
    """
    key = (str(Path(project_root).resolve()), run_id)
    with _BACKGROUND_RUNS_LOCK:
        execution = _BACKGROUND_RUNS.get(key)
        if (
            execution is not None
            and execution.thread is not None
            and execution.thread.is_alive()
        ):
            if execution.state == "waiting_for_resume":
                return service.resume(project_root, run_id), None
            raise RuntimeError(
                "Swarm execution is not waiting at a resumable boundary in this "
                "Sidekick process"
            )
        if execution is not None:
            _BACKGROUND_RUNS.pop(key, None)
        run = service.resume(project_root, run_id)
        return run, _launch_background_execution(service, project_root, run_id)


def _set_background_run_state(
    key: tuple[str, str],
    execution: _BackgroundRun,
    state: str,
) -> None:
    """Update worker state only while this exact worker owns the run key."""
    with _BACKGROUND_RUNS_LOCK:
        if _BACKGROUND_RUNS.get(key) is execution:
            execution.state = state


def _record_background_execution_failure(
    project_root: Path,
    run_id: str,
    exc: Exception,
) -> None:
    """Make an unexpected worker failure visible without storing error text."""
    try:
        store = ProjectSwarmStore(project_root)
        run = store.get_run(run_id)
        if run is None or run.status == "completed":
            return
        if run.status == "running":
            try:
                store.set_run_status(run_id, "paused")
            except (RuntimeError, ValueError):
                # A concurrent human pause wins the durable state transition.
                pass
        run = store.get_run(run_id)
        if run is not None and run.status != "completed":
            store.append_event(
                run_id,
                "run.execution_failed",
                {"error_type": type(exc).__name__},
            )
    except Exception as record_error:
        # Do not emit untrusted model/provider text into logs.  The type is
        # enough for an operator to see that durable failure recording failed.
        _LOG.error(
            "Could not record Swarm background failure: error_type=%s",
            type(record_error).__name__,
        )


def _project_to_kanban(
    handler,
    parsed,
    body: Mapping[str, Any],
    run_id: str,
) -> bool | None:
    """Create one human-authorized Sidekick-owned triage projection."""
    try:
        # Unlike normal Swarm writes, this cross-surface projection must carry
        # an explicit filesystem path.  The host maps it to the active Space;
        # callers cannot select a board, dispatcher, or workspace identity.
        project_root = _resolve_project_path(parsed, body, require_explicit=True)
        _reject_unknown_keys(body, {"project_path"})
        actor_id = _require_host_approval_actor(handler)
        # A Kanban task is a cross-surface write.  Record the trusted human
        # request before crossing that boundary, after a pure read confirms
        # that the run exists so a typo cannot initialize a project.
        reader = ProjectSwarmStore.open_read_only(project_root)
        if reader.get_run(run_id) is None:
            raise KeyError(f"Unknown Swarm run: {run_id}")
        ProjectSwarmStore(project_root).append_event(
            run_id,
            "sidekick.kanban_projection_requested_by_human",
            {"actor_id": actor_id},
        )
        projection = project_swarm_run_to_kanban(project_root, run_id)
        return j(handler, {"projection": _jsonable(projection)}, status=201) or True
    except FileNotFoundError as exc:
        return bad(handler, str(exc), status=404)
    except KeyError as exc:
        return bad(handler, str(exc), status=404)
    except LookupError as exc:
        return bad(handler, str(exc), status=409)
    except PermissionError as exc:
        # A Kanban projection is an explicit human-controlled cross-surface
        # write.  Missing or invalid dashboard identity is an authorization
        # failure, never an unhandled route exception.
        return bad(handler, str(exc), status=403)
    except (TypeError, ValueError) as exc:
        return bad(handler, str(exc), status=400)
    except RuntimeError as exc:
        return bad(handler, str(exc), status=409)


def _resolve_project_path(
    parsed,
    body: Mapping[str, Any] | None = None,
    *,
    require_explicit: bool = False,
    read_only: bool = False,
) -> Path:
    """Resolve an explicit project path, or the active Space's project dir.

    ``workspace`` remains a Space slug handled by the surrounding WebUI request
    context.  It is never passed to the filesystem trust resolver.
    """
    candidate: Any = None
    if body is not None and "project_path" in body:
        candidate = body.get("project_path")
    if candidate in (None, ""):
        candidate = _query_text(parsed, "project_path")
    if candidate not in (None, ""):
        if not isinstance(candidate, str):
            raise ValueError("project_path must be a string")
        resolver = (
            resolve_trusted_workspace_read_only
            if read_only
            else resolve_trusted_workspace
        )
        return Path(resolver(candidate)).resolve()
    if require_explicit:
        raise ValueError("project_path is required for Swarm GET")
    space = resolve_active_space()
    project_dir = space.get_project_dir()
    if not project_dir:
        raise LookupError("Active Space has no configured project directory")
    return Path(resolve_trusted_workspace(project_dir)).resolve()


def _require_host_approval_actor(handler) -> str:
    """Return the bridge-supplied dashboard principal or fail closed.

    Profile cookies select a local UI context but are not an authentication
    identity.  Only the FastAPI bridge may attach this value after it has
    verified the ephemeral dashboard session token.
    """
    actor_id = getattr(handler, "swarm_host_actor", None)
    if not isinstance(actor_id, str) or not actor_id.startswith("dashboard:"):
        raise PermissionError("A trusted dashboard approval principal is required")
    if not actor_id[len("dashboard:") :].strip():
        raise PermissionError("A trusted dashboard approval principal is required")
    return actor_id


def _handle_events_sse_stream(handler, parsed, reader, run_id: str) -> bool:
    cursor = _event_cursor(handler, parsed)
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()
    if not _write_sse(
        handler,
        f"event: hello\ndata: {json.dumps({'cursor': cursor, 'run_id': run_id})}\n\n",
    ):
        return True
    if _sse_writer_is_closed(handler):
        return True
    last_heartbeat = time.monotonic()
    try:
        while True:
            # FastAPI's response writer marks itself closed as soon as the
            # browser disconnects.  Poll that state independently of writes:
            # an idle SSE stream otherwise discovers a disconnect only at the
            # next heartbeat, keeping its read-only SQLite reader and route
            # thread alive unnecessarily.
            if _sse_writer_is_closed(handler):
                return True
            events = reader.list_events_after(
                run_id,
                cursor,
                limit=_SSE_BATCH_LIMIT,
            )
            if events:
                cursor = events[-1].sequence
                payload = json.dumps(
                    {"events": _jsonable(events), "cursor": cursor},
                    ensure_ascii=False,
                )
                if not _write_sse(
                    handler,
                    f"id: {cursor}\nevent: events\ndata: {payload}\n\n",
                ):
                    return True
                last_heartbeat = time.monotonic()
                continue
            if time.monotonic() - last_heartbeat >= _SSE_HEARTBEAT_SECONDS:
                if not _write_sse(handler, ": keepalive\n\n"):
                    return True
                last_heartbeat = time.monotonic()
            if _sse_writer_is_closed(handler):
                return True
            time.sleep(_SSE_POLL_SECONDS)
    except Exception:
        # A disconnect or a read-only database availability change must not
        # escape into the compatibility request handler or mutate a run.
        return True


def _write_sse(handler, payload: str) -> bool:
    try:
        handler.wfile.write(payload.encode("utf-8"))
        handler.wfile.flush()
        return True
    except (
        BrokenPipeError,
        ConnectionAbortedError,
        ConnectionResetError,
        OSError,
        ValueError,
    ):
        return False


def _sse_writer_is_closed(handler) -> bool:
    """Check optional disconnect state without changing legacy writers."""
    probe = getattr(handler.wfile, "is_closed", None)
    if not callable(probe):
        return False
    try:
        return bool(probe())
    except Exception:
        # A writer which cannot report its state is not safe to keep polling.
        return True


def _event_cursor(handler, parsed) -> int:
    raw = _query_text(parsed, "since")
    if raw is None:
        raw = handler.headers.get("Last-Event-ID")
    try:
        cursor = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        cursor = 0
    return max(cursor, 0)


def _run_id_from_path(path: str) -> str | None:
    raw = path[len(_RUNS_PREFIX) :] if path.startswith(_RUNS_PREFIX) else ""
    run_id = unquote(raw).strip("/")
    return run_id if run_id and "/" not in run_id else None


def _run_write_target(path: str) -> tuple[str | None, str | None]:
    raw = path[len(_RUNS_PREFIX) :] if path.startswith(_RUNS_PREFIX) else ""
    pieces = [unquote(piece).strip() for piece in raw.split("/")]
    if len(pieces) != 2 or not all(pieces) or any("/" in piece for piece in pieces):
        return None, None
    return pieces[0], pieces[1]


def _query_text(parsed, name: str) -> str | None:
    values = parse_qs(parsed.query or "").get(name)
    if not values:
        return None
    value = values[0]
    return value.strip() if isinstance(value, str) else None


def _required_text(body: Mapping[str, Any], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _reject_unknown_keys(body: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = set(body) - allowed
    if unknown:
        raise ValueError(f"Unsupported Swarm field(s): {', '.join(sorted(unknown))}")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        # ``asdict`` deep-copies every field before recursion.  Pack metadata
        # deliberately uses ``MappingProxyType`` for immutability, which is
        # not picklable/deep-copyable.  Walk the declared fields directly so
        # read-only API serialization preserves that immutability boundary.
        return {
            field.name: _jsonable(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value
