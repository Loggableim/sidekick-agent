"""HTTP/SSE bridge for project-local Swarm state.

The bridge deliberately separates read-only observations from writes.  It
never interprets a WebUI ``workspace`` slug as a filesystem path: the active
Space supplies its configured project directory, which is then checked by the
established trusted-workspace resolver.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote

from cli.swarm import get_swarm_service
from swarm_core.config import load_project_config
from swarm_core.store import ProjectSwarmStore
from web.api.helpers import bad, j
from web.api.profiles import get_active_profile_name
from web.api.space_engine import resolve_active_space
from web.api.workspace import resolve_trusted_workspace


_RUNS_PREFIX = "/api/swarm/runs/"
_SSE_PATH = "/api/swarm/runs/events/stream"
_SSE_BATCH_LIMIT = 100
_SSE_POLL_SECONDS = 0.5
_SSE_HEARTBEAT_SECONDS = 15.0


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
        project_root = _resolve_project_path(parsed)
        if path == "/api/swarm/packs":
            # Packs are versioned config metadata, not runtime state.  They
            # remain readable immediately after `swarm init`, before a first
            # SQLite-backed run, while still returning 404 for no `.swarm`.
            load_project_config(project_root)
            return j(
                handler,
                {"packs": _jsonable(get_swarm_service().list_packs(project_root))},
            ) or True
        # This is intentionally the first Swarm operation for every GET.  It
        # opens an existing SQLite file with mode=ro and rejects uninitialized
        # projects without creating config, runtime state, or migrations.
        reader = ProjectSwarmStore.open_read_only(project_root)
        if path == "/api/swarm/runs":
            return j(handler, {"runs": _jsonable(reader.list_runs())}) or True
        if path == "/api/swarm/models":
            return j(
                handler,
                {"catalog": _jsonable(reader.get_model_catalog_snapshot("ollama-cloud"))},
            ) or True
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
        return j(
            handler,
            {
                "run": _jsonable(run),
                "events": _jsonable(reader.list_events(run_id)),
                "approvals": _jsonable(reader.list_approvals(run_id)),
            },
        ) or True
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
    return False


def _create_run(handler, parsed, body: Mapping[str, Any]) -> bool | None:
    try:
        project_root = _resolve_project_path(parsed, body)
        _reject_unknown_keys(body, {"project_path", "goal", "pack"})
        goal = _required_text(body, "goal")
        pack = str(body.get("pack") or "coding-team").strip()
        if not pack:
            raise ValueError("pack must be non-empty")
        summary = get_swarm_service().run(goal, project_root, pack=pack)
        return j(handler, {"run": _jsonable(summary)}, status=201) or True
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
        profile = str(get_active_profile_name() or "default").strip() or "default"
        approval = get_swarm_service().record_human_approval(
            project_root,
            run_id,
            proposal_id,
            actor_id=f"webui:{profile}",
            approved=not deny,
        )
        return j(handler, {"approval": _jsonable(approval)}) or True
    except FileNotFoundError as exc:
        return bad(handler, str(exc), status=404)
    except KeyError as exc:
        return bad(handler, str(exc), status=404)
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
        run = service.pause(project_root, run_id) if pause else service.resume(project_root, run_id)
        return j(handler, {"run": _jsonable(run)}) or True
    except FileNotFoundError as exc:
        return bad(handler, str(exc), status=404)
    except KeyError as exc:
        return bad(handler, str(exc), status=404)
    except (TypeError, ValueError) as exc:
        return bad(handler, str(exc), status=400)
    except RuntimeError as exc:
        return bad(handler, str(exc), status=409)


def _resolve_project_path(parsed, body: Mapping[str, Any] | None = None) -> Path:
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
        return Path(resolve_trusted_workspace(candidate)).resolve()
    space = resolve_active_space()
    project_dir = space.get_project_dir()
    if not project_dir:
        raise LookupError("Active Space has no configured project directory")
    return Path(resolve_trusted_workspace(project_dir)).resolve()


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
        "event: hello\n"
        f"data: {json.dumps({'cursor': cursor, 'run_id': run_id})}\n\n",
    ):
        return True
    last_heartbeat = time.monotonic()
    try:
        while True:
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
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError, ValueError):
        return False


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
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value
