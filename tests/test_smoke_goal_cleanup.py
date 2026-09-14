"""Regression tests: smoke-test goal cleanup + orphaned goal hygiene.

Bug (observed 2026-09-14, live nova goals.db): the WebUI smoke test's
``_check_goal_reload_resume_autostarts`` set an active goal via ``/api/goal``
but its ``finally`` cleanup only cancelled the stream and deleted the session —
it never called ``/api/goal clear``. ``/api/session/delete`` does not remove the
``goal:{session_id}`` row from the space's ``goals.db`` either, so every smoke
run leaked an orphaned **active** goal: 81 stale ``Smoke reload continuation``
goals accumulated in the live nova goals.db (96 rows total, 83% garbage).

Two guards:
1. The smoke script must clear the goal it created before deleting the session.
2. The goals API must report a cleared goal as ``None`` (the contract the
   cleanup relies on) — pinned here against the space-scoped store.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "browser_webui_smoke.py"


def test_smoke_goal_check_clears_goal_in_cleanup() -> None:
    """The reload-resume check's finally block must clear the goal it set."""
    source = SMOKE_SCRIPT.read_text(encoding="utf-8")
    start = source.find("def _check_goal_reload_resume_autostarts")
    assert start != -1, "goal reload-resume check not found"
    end = source.find("\ndef ", start + 1)
    body = source[start:end if end != -1 else len(source)]

    assert '"args": goal_text' in body, "goal check no longer sets a goal — update this guard"
    # The cleanup (finally block) must clear the goal for the created session.
    finally_idx = body.find("finally:")
    assert finally_idx != -1, "goal check lost its finally cleanup"
    cleanup = body[finally_idx:]
    assert "/api/goal" in cleanup and '"clear"' in cleanup, (
        "smoke goal check deletes the session without clearing its goal — "
        "session deletion does not remove the goal:{session_id} row from the "
        "space goals.db, so every run leaks an orphaned active goal"
    )
    # The clear must happen before the session delete for deterministic ordering.
    assert cleanup.find('"clear"') < cleanup.find('"/api/session/delete"'), (
        "goal clear should run before the session delete"
    )


def test_goal_clear_removes_state_from_space_store(monkeypatch, tmp_path):
    """Clearing a goal must make goal_state_for_session return None (space store)."""
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from runtime._compat.shim_state import SessionDB
    from web.api.goals import goal_command_payload, goal_state_for_session

    space_root = tmp_path / "home" / "spaces" / "color"
    space_root.mkdir(parents=True)
    db = SessionDB(db_path=space_root / "goals.db")
    db.set_meta("goal:smoke-session", json.dumps({"goal": "orphaned", "status": "active", "turns_used": 0, "max_turns": 20, "created_at": 0.0, "last_turn_at": 0.0}))

    # The store has an orphaned active goal for a session that no longer exists.
    assert goal_state_for_session("smoke-session", space_slug="color") is not None

    payload = goal_command_payload("smoke-session", "clear", space_slug="color")
    assert payload["ok"] is True
    assert payload["action"] == "clear"

    # After the clear, the goal must be gone from every read surface.
    assert goal_state_for_session("smoke-session", space_slug="color") is None
    status = goal_command_payload("smoke-session", "status", space_slug="color")
    assert status["goal"] is None
    assert status["message_key"] == "goal_status_none"