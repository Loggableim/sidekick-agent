"""Tests for the generic in-flight dedupe (backlog item 27).

The context poll, the streaming session-list poll and user actions could all
fire the same request concurrently. `_dedupeInFlight` keeps one promise per
key so overlapping callers share a single request.
"""
from __future__ import annotations

from pathlib import Path

SESSIONS_JS = Path(__file__).resolve().parents[1] / "web" / "static" / "sessions.js"
UI_JS = Path(__file__).resolve().parents[1] / "web" / "static" / "ui.js"


def test_dedupe_helper_exists_and_is_exported() -> None:
    source = SESSIONS_JS.read_text(encoding="utf-8")

    assert "function _dedupeInFlight(key, factory)" in source
    assert "const _inFlightByKey = new Map();" in source
    assert "window._dedupeInFlight = _dedupeInFlight;" in source


def test_dedupe_returns_the_same_promise_and_releases_it() -> None:
    source = SESSIONS_JS.read_text(encoding="utf-8")
    start = source.index("function _dedupeInFlight(key, factory)")
    body = source[start : start + 900]

    # An existing entry is handed back unchanged.
    assert "const existing = _inFlightByKey.get(key);" in body
    assert "if (existing) return existing;" in body
    # The entry is released on both settle paths.
    assert "promise.then(release, release);" in body
    # A synchronous throw must not leave a stuck entry.
    assert "Promise.reject(e)" in body


def test_release_only_clears_its_own_entry() -> None:
    source = SESSIONS_JS.read_text(encoding="utf-8")
    start = source.index("function _dedupeInFlight(key, factory)")
    body = source[start : start + 900]
    assert "_inFlightByKey.get(key) === promise" in body, (
        "a later call's entry must not be deleted by an older settle"
    )


def test_ctx_poll_uses_the_dedupe_helper() -> None:
    source = UI_JS.read_text(encoding="utf-8")
    start = source.index("function startCtxPolling()")
    body = source[start : start + 900]

    assert "_dedupeInFlight('ctx:'+sid" in body, "ctx poll is not deduped"
    # It must stay safe if sessions.js has not loaded yet.
    assert "typeof _dedupeInFlight==='function'" in body


def test_streaming_poll_uses_the_dedupe_helper() -> None:
    source = SESSIONS_JS.read_text(encoding="utf-8")
    start = source.index("function startStreamingPoll()")
    body = source[start : start + 500]

    assert "_dedupeInFlight('session-list'" in body, "streaming poll is not deduped"
