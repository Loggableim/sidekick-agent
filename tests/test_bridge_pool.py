"""Tests for the bounded route-bridge worker pool (backlog item 8).

Every unmatched API request used to spawn its own daemon thread. Requests now
run on a bounded pool; SSE endpoints keep dedicated threads because they hold
their worker for the whole stream lifetime.
"""
from __future__ import annotations

import threading
import time

import pytest

TestClient = pytest.importorskip("fastapi.testclient").TestClient


def _bridge_threads() -> int:
    return sum(1 for t in threading.enumerate() if t.name.startswith("api-bridge"))


def test_streaming_path_lists_match_the_server() -> None:
    """The duplicated SSE list must stay in sync with cli.web_server."""
    from cli import web_server
    from web.api import fastapi_bridge

    assert fastapi_bridge._STREAMING_BRIDGE_PATHS == web_server._STREAMING_EXACT_PATHS
    assert fastapi_bridge._STREAMING_BRIDGE_PREFIXES == web_server._STREAMING_PREFIXES


def test_streaming_classifier() -> None:
    from web.api import fastapi_bridge

    for path in (
        "/api/chat/stream",
        "/api/subagents/events/stream?session_id=abc",
        "/api/agents/workspace/stream/xyz",
    ):
        assert fastapi_bridge._is_streaming_bridge_path(path), path

    for path in ("/api/status", "/api/sessions", "/api/space/config"):
        assert not fastapi_bridge._is_streaming_bridge_path(path), path


def test_concurrent_bridge_requests_use_a_bounded_pool(monkeypatch, tmp_path):
    """100 concurrent bridge calls must not create 100 threads."""
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    from cli import web_server
    from web.api import fastapi_bridge, routes

    def fake_get(handler, parsed):
        time.sleep(0.05)  # hold the worker so overlap is real
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(b'{"ok":true}')
        return True

    monkeypatch.setattr(routes, "handle_get", fake_get)
    monkeypatch.setattr(routes, "_setup_workspace_from_request", lambda *_: None)
    monkeypatch.setattr(routes, "_teardown_workspace_context", lambda: None)

    client = TestClient(web_server.app)
    headers = {web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN}
    client.get("/api/bridge-pool-test", headers=headers)  # warm

    results: list[int] = []
    lock = threading.Lock()

    def hit() -> None:
        response = client.get("/api/bridge-pool-test", headers=headers)
        with lock:
            results.append(response.status_code)

    workers = [threading.Thread(target=hit) for _ in range(60)]
    for worker in workers:
        worker.start()
    peak = 0
    while any(worker.is_alive() for worker in workers):
        peak = max(peak, _bridge_threads())
        time.sleep(0.005)
    for worker in workers:
        worker.join()

    assert results.count(200) == 60, results
    assert peak <= fastapi_bridge._BRIDGE_POOL_MAX_WORKERS, (
        f"pool grew past its bound: {peak} threads"
    )
    assert fastapi_bridge._bridge_pool_pending == 0, "pending counter leaked"


def test_backpressure_rejects_when_the_queue_is_saturated(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    from web.api import fastapi_bridge

    original = fastapi_bridge._bridge_pool_pending
    try:
        fastapi_bridge._bridge_pool_pending = fastapi_bridge._BRIDGE_POOL_QUEUE_LIMIT + 1
        assert fastapi_bridge._bridge_pool_has_capacity() is False
        fastapi_bridge._bridge_pool_pending = 0
        assert fastapi_bridge._bridge_pool_has_capacity() is True
    finally:
        fastapi_bridge._bridge_pool_pending = original
