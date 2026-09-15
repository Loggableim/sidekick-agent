"""Regression tests for the MCP initial-connect retry policy.

Bug (observed 2026-09-14 in logs/errors.log, repeating every reconnect
cycle): an expired GitHub Copilot MCP credential made every connection
attempt fail with ``400 Bad Request`` (wrapped in an anyio TaskGroup
ExceptionGroup). The initial-connect loop retried the permanent client
error 3 times with backoff (~9 s wasted) before giving up, and the whole
cycle repeated on every MCP re-init. Retrying a permanent 4xx client
error is pointless - the request will fail identically.
"""

from __future__ import annotations

import sys

import pytest

httpx = pytest.importorskip("httpx")


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://mcp.example.com/mcp/")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{status}'", request=request, response=response
    )


def test_permanent_4xx_is_detected_through_exception_group():
    from tools.mcp_tool import _is_permanent_connect_error

    # anyio TaskGroup wraps the httpx error in an ExceptionGroup.
    grouped = ExceptionGroup("unhandled errors in a TaskGroup", [_status_error(400)])
    assert _is_permanent_connect_error(grouped) is True


def test_transient_and_auth_statuses_are_not_permanent():
    from tools.mcp_tool import _is_permanent_connect_error

    assert _is_permanent_connect_error(_status_error(401)) is False  # auth recovery
    assert _is_permanent_connect_error(_status_error(408)) is False  # request timeout
    assert _is_permanent_connect_error(_status_error(429)) is False  # rate limit
    assert _is_permanent_connect_error(_status_error(500)) is False  # server error
    assert _is_permanent_connect_error(_status_error(503)) is False


def test_non_http_exceptions_are_not_permanent():
    from tools.mcp_tool import _is_permanent_connect_error

    assert _is_permanent_connect_error(ConnectionError("dns blip")) is False
    assert _is_permanent_connect_error(TimeoutError("read timeout")) is False
    assert _is_permanent_connect_error(
        ExceptionGroup("mixed", [ConnectionError("x"), ValueError("y")])
    ) is False


def test_deeply_nested_group_still_detected():
    from tools.mcp_tool import _is_permanent_connect_error

    inner = ExceptionGroup("inner", [_status_error(400)])
    outer = ExceptionGroup("outer", [ConnectionError("blip"), inner])
    assert _is_permanent_connect_error(outer) is True