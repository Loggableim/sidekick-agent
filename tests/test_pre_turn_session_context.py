"""Regression tests for pre_turn's session-start context fallback.

Bug (observed 2026-09-14 on a migrated Nova space): the space's
``session_start.py`` imports space-local ``runtime_utf8`` and
``state_snapshot`` helpers. Commit 721c440 (2026-07-10, "complete Sidekick
and Nova migration") moved both helpers into the repo's ``nova/`` package
and removed them from the space, but left the space's ``session_start.py``
behind. Every ``pre_turn`` subprocess therefore failed with
``ModuleNotFoundError: No module named 'runtime_utf8'`` (rc=1), so
``session_context`` was silently empty and the NOVA COGNITIVE CONTEXT block
in every WebUI turn lost the session-start snapshot (memory count, emotion,
continuity, thought stream). The importlib fallback in web/api/streaming.py
loads the same broken file and also returns "".
"""

from __future__ import annotations

import json

import pytest


def _stub_status_dependencies(monkeypatch, lifecycle):
    monkeypatch.setattr(lifecycle, "migration_tick", lambda: {"ok": True})
    monkeypatch.setattr(lifecycle, "repair_incomplete_events", lambda: [])
    monkeypatch.setattr(lifecycle, "entity_prompt_context", lambda _user: "entity-context")


def test_pre_turn_falls_back_to_bundled_snapshot_when_space_script_broken(monkeypatch, tmp_path):
    """When the space session_start.py subprocess fails, pre_turn must render
    the session-start snapshot from the repo-bundled nova/state_snapshot.py
    instead of silently returning an empty session_context."""
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    import web.api.nova_lifecycle as lifecycle

    _stub_status_dependencies(monkeypatch, lifecycle)

    def failing_space_script(script, *args, **kwargs):
        assert script == "session_start.py"
        return {
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "ModuleNotFoundError: No module named 'runtime_utf8'",
        }

    monkeypatch.setattr(lifecycle, "_run_local_script", failing_space_script)

    payload = lifecycle.pre_turn(workspace_slug="nova", user_text="hi")

    assert payload["ok"] is True
    context = payload["context"]
    assert context.strip(), "session-start context must not be empty"
    # The bundled snapshot renders the consciousness-space header.
    assert "Bewusstseinsspace" in context
    assert "entity-context" in context


def test_pre_turn_prefers_working_space_script(monkeypatch, tmp_path):
    """A healthy space script must keep priority over the bundled fallback."""
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    import web.api.nova_lifecycle as lifecycle

    _stub_status_dependencies(monkeypatch, lifecycle)

    def working_space_script(script, *args, **kwargs):
        assert script == "session_start.py"
        return {"ok": True, "returncode": 0, "stdout": "SPACE-SNAPSHOT-OUTPUT", "stderr": ""}

    monkeypatch.setattr(lifecycle, "_run_local_script", working_space_script)

    payload = lifecycle.pre_turn(workspace_slug="nova", user_text="hi")

    assert "SPACE-SNAPSHOT-OUTPUT" in payload["context"]


def test_pre_turn_degrades_gracefully_when_both_paths_fail(monkeypatch, tmp_path):
    """No space script + no bundled module must not crash pre_turn."""
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    import web.api.nova_lifecycle as lifecycle

    _stub_status_dependencies(monkeypatch, lifecycle)

    def failing_space_script(script, *args, **kwargs):
        return {"ok": False, "returncode": 1, "stdout": "", "stderr": "boom"}

    monkeypatch.setattr(lifecycle, "_run_local_script", failing_space_script)
    monkeypatch.setattr(
        lifecycle,
        "get_nova_state_snapshot_path",
        lambda: tmp_path / "missing" / "state_snapshot.py",
    )

    payload = lifecycle.pre_turn(workspace_slug="nova", user_text="hi")

    assert payload["ok"] is True
    assert "entity-context" in payload["context"]