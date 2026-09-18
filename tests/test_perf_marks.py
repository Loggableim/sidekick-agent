"""Tests for the frontend performance marks (backlog item 49).

The frontend had no performance.mark/measure calls, so boot, session load,
render and SSE reconnect timings were not measurable.
"""
from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "web" / "static"


def test_helpers_exist_and_are_exported() -> None:
    source = (STATIC / "ui.js").read_text(encoding="utf-8")
    assert "function _perfMark(name)" in source
    assert "function _perfMeasure(name,startMark)" in source
    assert "window._perfMark=_perfMark;" in source
    assert "window._perfMeasure=_perfMeasure;" in source


def test_helpers_are_guarded() -> None:
    """A missing User Timing API must never break the instrumented path."""
    source = (STATIC / "ui.js").read_text(encoding="utf-8")
    start = source.index("function _perfMark(name)")
    body = source[start : start + 700]
    assert "typeof performance.mark!=='function'" in body
    assert "try{" in body and "catch(_){}" in body


def test_boot_is_instrumented() -> None:
    source = (STATIC / "boot.js").read_text(encoding="utf-8")
    assert "_perfMark('sidekick:boot:start')" in source
    assert "_perfMeasure('sidekick:boot','sidekick:boot:start')" in source


def test_session_load_and_render_are_instrumented() -> None:
    sessions = (STATIC / "sessions.js").read_text(encoding="utf-8")
    assert "_perfMark('sidekick:session-load:start')" in sessions
    assert "_perfMeasure('sidekick:session-load','sidekick:session-load:start')" in sessions

    ui = (STATIC / "ui.js").read_text(encoding="utf-8")
    assert "_perfMark('sidekick:render:start')" in ui
    assert "_perfMeasure('sidekick:render','sidekick:render:start')" in ui


def test_sse_connect_is_instrumented() -> None:
    sessions = (STATIC / "sessions.js").read_text(encoding="utf-8")
    assert "_perfMark('sidekick:sse-connect:start')" in sessions
    assert "_perfMeasure('sidekick:sse-connect','sidekick:sse-connect:start')" in sessions


def test_render_measure_runs_in_a_finally_block() -> None:
    """A throw inside renderMessages must not lose the measure."""
    ui = (STATIC / "ui.js").read_text(encoding="utf-8")
    start = ui.index("function renderMessages(options){")
    body = ui[start : start + 400]
    assert "finally{" in body, "the render measure must run even on a throw"
