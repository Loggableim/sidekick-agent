"""Regression tests for the post_response.py worker invocation contract.

Bug (observed 2026-09-14, 66 consecutive post_turn events with
``continuity_effect_failed`` in ``.lifecycle/events.json`` since 2026-09-11):
``_run_post_response_pipeline`` invoked ``post_response.py`` with ``--digest``,
but the script only defines ``--no-digest`` (digest is the default). argparse
rejected the unknown flag with exit code 2 on every single post_turn, so the
isolated worker never ran and resonanz, emotion, and continuity were recorded
as ``worker_failed`` on every turn - permanently degrading the post-turn
pipeline.
"""

from __future__ import annotations

import json

import pytest


def _capture_worker_args(monkeypatch, lifecycle, *, worker_ok: bool = True):
    """Stub lifecycle._run_local_script and record every invocation."""
    calls: list[tuple[str, tuple, dict]] = []

    def fake_run(script, *args, **kwargs):
        calls.append((script, args, kwargs))
        if script == "post_response.py":
            # Simulate a healthy worker: single-line JSON on stdout.
            payload = {
                "ok": worker_ok,
                "steps": [
                    {"step": "resonanz", "status": "ok", "attempts": 1},
                    {"step": "emotion", "status": "ok", "attempts": 1},
                    {"step": "continuity", "status": "ok", "attempts": 1},
                ],
            }
            return {"ok": True, "stdout": json.dumps(payload) + "\n"}
        return {"ok": True, "stdout": ""}

    monkeypatch.setattr(lifecycle, "_run_local_script", fake_run)
    return calls


def test_post_response_worker_receives_only_flags_the_script_defines(monkeypatch, tmp_path):
    """The worker invocation must not pass flags that post_response.py's
    argparse does not define - argparse exits 2 on unknown arguments."""
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    import web.api.nova_lifecycle as lifecycle

    calls = _capture_worker_args(monkeypatch, lifecycle)
    result = lifecycle.post_turn(
        session_id="digest-flag",
        user_text="Ein konkreter, erinnerungswuerdiger Satz",
        assistant_text="Eine geerdete Antwort",
        workspace_slug="nova",
        blocking=True,
    )

    assert result["ok"] is True
    worker_calls = [c for c in calls if c[0] == "post_response.py"]
    assert worker_calls, "post_response.py worker was never invoked"

    script, args, _kwargs = worker_calls[0]
    assert "--digest" not in args, (
        "post_response.py does not define --digest (digest is the default); "
        "passing it makes argparse exit 2 on every post_turn"
    )

    # Cross-check against the real script's argparse: every flag the caller
    # passes must be accepted by the worker's own parser.
    import sys as _sys
    from pathlib import Path as _Path

    space_root = lifecycle.get_nova_space_root()
    script_path = space_root / script
    if script_path.exists():
        parser = _build_worker_parser(script_path)
        argv = [a for a in args if not (isinstance(a, str) and a.startswith("--") and a in {"--query", "--thinking", "--response", "--tags", "--topic", "--summary"})]
        # Just assert parse_args succeeds on the full arg list.
        try:
            parser.parse_args(list(args))
        except SystemExit as exc:  # argparse exits 2 on unknown args
            pytest.fail(f"worker invocation rejected by its own argparse: {list(args)} (exit {exc.code})")


def _build_worker_parser(script_path):
    """Execute the worker script's argparse setup without running the pipeline."""
    import argparse
    import ast

    tree = ast.parse(script_path.read_text(encoding="utf-8"))
    # Extract add_argument calls from the AST - robust against running the
    # script's side effects.
    parser = argparse.ArgumentParser()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "add_argument":
                values = []
                keywords = {}
                ok = True
                for arg in node.args:
                    if isinstance(arg, ast.Constant):
                        values.append(arg.value)
                    else:
                        ok = False
                        break
                for kw in node.keywords:
                    if isinstance(kw.value, ast.Constant):
                        keywords[kw.arg] = kw.value.value
                    else:
                        ok = False
                        break
                if ok:
                    try:
                        parser.add_argument(*values, **keywords)
                    except (TypeError, ValueError):
                        pass
    return parser


def test_post_turn_pipeline_not_degraded_when_worker_healthy(monkeypatch, tmp_path):
    """With a healthy worker invocation, the event must not be degraded."""
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    import web.api.nova_lifecycle as lifecycle

    _capture_worker_args(monkeypatch, lifecycle)
    lifecycle.post_turn(
        session_id="healthy-worker",
        user_text="Erinnerungswuerdig",
        assistant_text="Antwort",
        workspace_slug="nova",
        blocking=True,
    )
    event = lifecycle.load_events(limit=1, include_private=True)[0]
    assert event["status"] == "completed"
    assert event.get("pipeline_degraded") is False
    assert event.get("pipeline_failures") in (None, [])
    assert "continuity_done" in event["steps"]
    assert "emotion_done" in event["steps"]