"""Run the transitional WebUI route module inside FastAPI.

``web.api.routes`` predates FastAPI and uses the small subset of the
``BaseHTTPRequestHandler`` interface implemented below.  Keeping that route
logic in-process lets the WebUI use one ASGI server while endpoints are moved
to native FastAPI handlers incrementally.  In particular, it removes the
random-port HTTP child process that previously proxied unmatched API routes.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import io
import queue
import threading
import traceback
from email.message import Message
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.requests import ClientDisconnect


_END = object()
_HEADER_WAIT_SECONDS = 20.0
_RUNTIME_INIT_LOCK = threading.Lock()
_RUNTIME_STATE_DIR: str | None = None

# Bounded worker pool for the legacy route bridge.
#
# Every unmatched API request used to spawn its own daemon thread, so a burst
# of requests created an unbounded number of threads (each one running the
# legacy route module). Requests now run on a bounded pool.
#
# SSE endpoints are excluded: a stream handler holds its thread for the whole
# lifetime of the connection (it only returns after ``wfile.finish()``), so
# putting them on a shared pool would let a handful of open streams starve
# every normal request. They keep a dedicated thread each.
_BRIDGE_POOL_MAX_WORKERS = 16
# Generous queue: the pool absorbs bursts, and 503 only fires when the backlog
# is far beyond anything a single dashboard produces.
_BRIDGE_POOL_QUEUE_LIMIT = 256
_bridge_pool: "concurrent.futures.ThreadPoolExecutor | None" = None
_bridge_pool_lock = threading.Lock()
_bridge_pool_pending = 0

# Mirrors ``cli.web_server._STREAMING_EXACT_PATHS`` / ``_STREAMING_PREFIXES``.
# Duplicated on purpose: ``cli.web_server`` imports this module at import time,
# so importing it back would be circular. A test asserts the two lists agree.
_STREAMING_BRIDGE_PATHS: frozenset[str] = frozenset({
    "/api/chat/stream",
    "/api/terminal/stream",
    "/api/sessions/gateway/stream",
    "/api/approval/stream",
    "/api/clarify/stream",
    "/api/browser/events",
    "/api/nova/events",
    "/api/gmail/ai/summary/stream",
    "/api/kanban/events/stream",
    "/api/swarm/runs/events/stream",
    "/api/subagents/events/stream",
})
_STREAMING_BRIDGE_PREFIXES: tuple[str, ...] = (
    "/api/agents/workspace/stream/",
)


def _is_streaming_bridge_path(path: str) -> bool:
    """True for long-lived SSE paths that must not occupy a pool worker."""
    clean = str(path or "").split("?", 1)[0].rstrip("/")
    return clean in _STREAMING_BRIDGE_PATHS or any(
        clean.startswith(prefix) for prefix in _STREAMING_BRIDGE_PREFIXES
    )


def _get_bridge_pool() -> "concurrent.futures.ThreadPoolExecutor":
    global _bridge_pool
    if _bridge_pool is None:
        with _bridge_pool_lock:
            if _bridge_pool is None:
                _bridge_pool = concurrent.futures.ThreadPoolExecutor(
                    max_workers=_BRIDGE_POOL_MAX_WORKERS,
                    thread_name_prefix="api-bridge",
                )
    return _bridge_pool


def _bridge_pool_has_capacity() -> bool:
    """Backpressure check: refuse work when the pool queue is saturated."""
    return _bridge_pool_pending < _BRIDGE_POOL_QUEUE_LIMIT


def _is_pure_swarm_get(method: str, path: str) -> bool:
    """True for Swarm reads that must not initialize a WebUI Space."""
    return method.upper() == "GET" and path.startswith("/api/swarm/")


def _is_swarm_human_actor_post(method: str, path: str) -> bool:
    """True when a Swarm write requires the trusted dashboard principal."""
    return (
        method.upper() == "POST"
        and path.startswith("/api/swarm/runs/")
        and path.endswith(("/approve", "/recover", "/kanban-projection"))
    )


def _requires_dashboard_actor_post(method: str, path: str) -> bool:
    """Return whether a legacy write needs the trusted dashboard principal."""
    return _is_swarm_human_actor_post(method, path) or (
        method.upper() == "POST"
        and path in {"/api/space/config", "/api/space/delete"}
    )


def _prepare_webui_runtime() -> None:
    """Initialize file-backed route state for the current Sidekick home.

    The removed HTTP server performed this work before it accepted requests.
    Keeping it beside the in-process adapter gives FastAPI and test clients the
    same lifecycle, including when a test switches ``SIDEKICK_HOME``.
    """
    global _RUNTIME_STATE_DIR
    from web.api import config as config_mod

    config_mod.refresh_runtime_paths_from_env()
    state_dir = str(config_mod.STATE_DIR.resolve())
    if state_dir == _RUNTIME_STATE_DIR:
        return

    with _RUNTIME_INIT_LOCK:
        if state_dir == _RUNTIME_STATE_DIR:
            return
        from web.api import agents, models, profiles, routes

        profiles.refresh_profile_base_home_from_env()
        routes.STATE_DIR = config_mod.STATE_DIR
        routes.SESSION_DIR = config_mod.SESSION_DIR
        models._SESSION_LIST_CACHE.clear()
        models._SESSION_LIST_CACHE_AT.clear()
        agents.init_agents_db(config_mod.STATE_DIR, config_mod.SESSION_DIR)
        _RUNTIME_STATE_DIR = state_dir


class _ResponseWriter:
    """Thread-safe byte stream that becomes an ASGI response body."""

    def __init__(self) -> None:
        self._chunks: queue.Queue[bytes | object] = queue.Queue()
        self._closed = threading.Event()

    def write(self, payload: bytes | bytearray | memoryview) -> int:
        if self._closed.is_set():
            raise BrokenPipeError("client disconnected")
        data = bytes(payload)
        if data:
            self._chunks.put(data)
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self._closed.set()

    def is_closed(self) -> bool:
        """Expose ASGI disconnect state to long-lived compatibility routes."""
        return self._closed.is_set()

    def finish(self) -> None:
        self._chunks.put(_END)

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield queued chunks, draining whatever is already buffered.

        Each ``asyncio.to_thread`` call occupies a thread from anyio's default
        limiter (40 threads), so one call per chunk lets a busy stream consume
        the whole limiter. After the first blocking ``get`` we drain the queue
        without blocking and yield the batch as one item, so a burst of chunks
        costs a single thread hop.
        """
        try:
            while True:
                item = await asyncio.to_thread(self._chunks.get)
                if item is _END:
                    break
                batch = bytearray(item)
                while True:
                    try:
                        nxt = self._chunks.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is _END:
                        self._chunks.put(_END)  # keep the sentinel for the next loop
                        break
                    batch.extend(nxt)
                yield bytes(batch)
        finally:
            self.close()


class _RouteHandler:
    """Minimal request/response adapter consumed by ``web.api.routes``."""

    def __init__(self, request: Request, body: bytes) -> None:
        headers = Message()
        for name, value in request.headers.items():
            headers.add_header(name, value)
        if body:
            if "Content-Length" in headers:
                headers.replace_header("Content-Length", str(len(body)))
            else:
                headers.add_header("Content-Length", str(len(body)))

        self.headers = headers
        self.rfile = io.BytesIO(body)
        self.wfile = _ResponseWriter()
        self.command = request.method
        self.path = request.url.path
        if request.url.query:
            self.path += "?" + request.url.query
        client = request.client
        self.client_address = (client.host, client.port) if client else ("127.0.0.1", 0)
        # ``web.api.auth`` only probes this object for ``getpeercert``.
        self.request = object()
        self.status_code: int | None = None
        self.response_headers: list[tuple[str, str]] = []
        self.headers_ready = threading.Event()

    def send_response(self, status: int, _message: str | None = None) -> None:
        self.status_code = int(status)

    def send_header(self, name: str, value: Any) -> None:
        self.response_headers.append((str(name), str(value)))

    def end_headers(self) -> None:
        self.headers_ready.set()


class _RouteExecution:
    def __init__(self, request: Request, body: bytes) -> None:
        self.request = request
        self.handler = _RouteHandler(request, body)
        self.completed = threading.Event()
        self.thread: threading.Thread | None = None
        self._pool_future: concurrent.futures.Future | None = None

    def start(self) -> None:
        """Run the handler on the bounded pool, or a dedicated thread for SSE.

        SSE handlers hold their worker until the stream ends, so they must not
        occupy a shared pool slot; they get their own daemon thread as before.
        """
        global _bridge_pool_pending
        if _is_streaming_bridge_path(self.handler.path):
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            return

        pool = _get_bridge_pool()
        _bridge_pool_pending += 1
        try:
            self._pool_future = pool.submit(self._run)
        except RuntimeError:
            # Pool shut down (interpreter teardown): fall back to a thread.
            _bridge_pool_pending -= 1
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            return

        def _release(_future: concurrent.futures.Future) -> None:
            global _bridge_pool_pending
            _bridge_pool_pending -= 1

        self._pool_future.add_done_callback(_release)

    def _run(self) -> None:
        # A Swarm read has its own project-local, read-only initialization
        # contract.  Classify it before the legacy WebUI bootstrap, which
        # initializes global Agent/Space state and can create files.
        parsed = urlparse(self.handler.path)
        pure_swarm_get = _is_pure_swarm_get(self.handler.command, parsed.path)
        if not pure_swarm_get:
            _prepare_webui_runtime()
        from web.api.auth import check_auth
        from web.api.helpers import get_profile_cookie, j
        from web.api.profiles import clear_request_profile, set_request_profile
        from web.api.routes import (
            _setup_workspace_from_request,
            _teardown_workspace_context,
            handle_delete,
            handle_get,
            handle_patch,
            handle_post,
        )

        route_for_method: dict[str, Callable[[Any, Any], bool]] = {
            "GET": handle_get,
            "POST": handle_post,
            "PATCH": handle_patch,
            "DELETE": handle_delete,
        }
        workspace_context_attempted = False
        profile_token = None
        try:
            # Swarm GET/SSE skips the legacy workspace/bootstrap path, but its
            # project trust resolver is still profile-aware.  Setting only
            # this thread-local context is pure: it neither creates profile
            # state nor changes the process-wide active profile.
            cookie_profile = get_profile_cookie(self.handler)
            if cookie_profile:
                profile_token = set_request_profile(cookie_profile)
            if not pure_swarm_get:
                # Preserve normal route cleanup even if setup raises midway.
                workspace_context_attempted = True
                _setup_workspace_from_request(self.handler, parsed)

            if not check_auth(self.handler, parsed, read_only=pure_swarm_get):
                return
            if _requires_dashboard_actor_post(self.handler.command, parsed.path):
                from cli.web_server import dashboard_session_principal

                actor_id = dashboard_session_principal(self.request)
                if actor_id is not None:
                    self.handler.dashboard_host_actor = actor_id
                    if _is_swarm_human_actor_post(self.handler.command, parsed.path):
                        self.handler.swarm_host_actor = actor_id
            route = route_for_method.get(self.handler.command)
            if route is None:
                j(self.handler, {"error": "method not allowed"}, status=405)
                return
            if route(self.handler, parsed) is False:
                j(self.handler, {"error": "not found"}, status=404)
        except Exception:
            # Keep the existing API error contract while retaining the traceback
            # in the server logs for diagnosis.
            traceback.print_exc()
            if not self.handler.headers_ready.is_set():
                try:
                    j(self.handler, {"error": "Internal server error"}, status=500)
                except Exception:
                    pass
        finally:
            clear_request_profile(profile_token)
            if workspace_context_attempted:
                _teardown_workspace_context()
            if not self.handler.headers_ready.is_set():
                self.handler.send_response(204)
                self.handler.end_headers()
            self.handler.wfile.finish()
            self.completed.set()

    def response(self) -> StreamingResponse:
        raw_headers = [
            (
                name.encode("latin-1", errors="replace").lower(),
                value.encode("latin-1", errors="replace"),
            )
            for name, value in self.handler.response_headers
        ]
        response = StreamingResponse(
            self.handler.wfile.stream(),
            status_code=self.handler.status_code or 200,
            media_type=None,
        )
        response.raw_headers = raw_headers
        return response


async def dispatch_route(request: Request) -> Response:
    """Dispatch a non-native API request through the in-process route bridge."""
    try:
        body = await request.body()
    except ClientDisconnect:
        return Response(content=b"", status_code=499)

    # Backpressure: refuse new non-streaming work when the bounded pool queue
    # is saturated, instead of growing the queue without bound.
    if not _is_streaming_bridge_path(request.url.path) and not _bridge_pool_has_capacity():
        return JSONResponse(
            {"error": "route bridge overloaded, retry shortly"},
            status_code=503,
            headers={"Retry-After": "1"},
        )

    execution = _RouteExecution(request, body)
    execution.start()
    headers_ready = await asyncio.to_thread(
        execution.handler.headers_ready.wait, _HEADER_WAIT_SECONDS
    )
    if not headers_ready:
        execution.handler.wfile.close()
        return JSONResponse(
            {"error": "route handler did not start a response in time"},
            status_code=504,
        )
    return execution.response()
