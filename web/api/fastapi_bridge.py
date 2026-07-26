"""Run the transitional WebUI route module inside FastAPI.

``web.api.routes`` predates FastAPI and uses the small subset of the
``BaseHTTPRequestHandler`` interface implemented below.  Keeping that route
logic in-process lets the WebUI use one ASGI server while endpoints are moved
to native FastAPI handlers incrementally.  In particular, it removes the
random-port HTTP child process that previously proxied unmatched API routes.
"""

from __future__ import annotations

import asyncio
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

    def finish(self) -> None:
        self._chunks.put(_END)

    async def stream(self) -> AsyncIterator[bytes]:
        try:
            while True:
                item = await asyncio.to_thread(self._chunks.get)
                if item is _END:
                    break
                yield item  # type: ignore[misc]
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
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
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
        parsed = urlparse(self.handler.path)
        try:
            cookie_profile = get_profile_cookie(self.handler)
            if cookie_profile:
                set_request_profile(cookie_profile)
            _setup_workspace_from_request(self.handler, parsed)

            if not check_auth(self.handler, parsed):
                return
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
            clear_request_profile()
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
