"""Tests for the api() default timeout (backlog item 26).

api() had no default timeout, so a request that never answered left the
caller's promise pending forever and the UI stuck on its loading state.
"""
from __future__ import annotations

from pathlib import Path

WORKSPACE_JS = Path(__file__).resolve().parents[1] / "web" / "static" / "workspace.js"


def _api_source() -> str:
    source = WORKSPACE_JS.read_text(encoding="utf-8")
    start = source.index("async function api(path,opts={})")
    end = source.index("\n// Persist/restore expanded directory state", start)
    return source[start:end]


def test_default_timeout_constant_exists() -> None:
    source = WORKSPACE_JS.read_text(encoding="utf-8")
    assert "const API_DEFAULT_TIMEOUT_MS = 30000;" in source


def test_api_applies_the_default_timeout() -> None:
    body = _api_source()

    assert "API_DEFAULT_TIMEOUT_MS" in body, "the default is not applied"
    assert "new AbortController()" in body
    assert "timeoutController.abort()" in body
    # The timer must be cleared on every exit path.
    assert "clearTimeout(timeoutTimer)" in body


def test_caller_signal_wins_over_the_default() -> None:
    body = _api_source()
    assert "!fetchOpts.signal" in body, (
        "a caller-supplied signal must not be replaced by the default timeout"
    )


def test_timeout_is_overridable_and_disableable() -> None:
    body = _api_source()
    assert "fetchOpts.timeoutMs" in body, "per-request override missing"
    assert "timeoutMs>0" in body, "timeoutMs: 0 must disable the timeout"


def test_timeout_error_is_clear_and_not_retried() -> None:
    body = _api_source()

    assert "TimeoutError" in body, "the timeout must be distinguishable"
    assert "Request timed out after" in body, "the message must name the budget"
    # A timeout must not be retried: that would triple the wait.
    assert "e.name==='AbortError'" in body
