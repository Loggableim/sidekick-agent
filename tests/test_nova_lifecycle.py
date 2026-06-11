import json
from pathlib import Path

import pytest

TestClient = pytest.importorskip("fastapi.testclient").TestClient


def test_personality_schema_initializes_expected_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from web.api.nova_lifecycle import load_personality_state

    state = load_personality_state()

    assert state["schema_version"] == 1
    assert state["autonomy_level"] == 2
    assert set(state["traits"]) == {
        "curiosity",
        "directness",
        "empathy",
        "humor",
        "risk_tolerance",
        "orderliness",
        "creativity",
        "patience",
    }
    assert set(state["dynamic_states"]) == {
        "mood",
        "energy",
        "focus",
        "fatigue",
        "social_closeness",
        "restlessness",
    }
    assert all("visibility" in value for value in state["traits"].values())


def test_autonomy_guard_blocks_external_mutations_at_level_two(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from web.api.nova_lifecycle import guard_autonomous_action

    allowed = guard_autonomous_action({"type": "read", "target": "https://example.com"}, autonomy_level=2)
    blocked = guard_autonomous_action({"type": "post", "target": "https://example.com"}, autonomy_level=2)
    secret = guard_autonomous_action({"type": "read", "target": "C:/sidekick/home/auth.json"}, autonomy_level=4)

    assert allowed["allowed"] is True
    assert blocked["allowed"] is False
    assert blocked["reason"] == "external_mutation_requires_level_3"
    assert secret["allowed"] is False
    assert secret["reason"] == "sensitive_target_blocked"


def test_post_turn_creates_versioned_event_and_queues_reflection(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from web.api.nova_lifecycle import get_nova_state_paths, load_events, post_turn

    result = post_turn(
        session_id="s1",
        user_text="Was denkst du ueber Vertrauen?",
        assistant_text="Vertrauen muss sichtbar begruendet werden.",
        workspace_slug="nova",
        blocking=True,
    )
    paths = get_nova_state_paths()
    personality = json.loads(paths.personality.read_text(encoding="utf-8"))
    queue = json.loads(paths.reflection_queue.read_text(encoding="utf-8"))
    events = load_events(limit=5, include_private=True)

    assert result["ok"] is True
    assert result["event_id"]
    assert events[0]["status"] == "completed"
    assert "memory_done" in events[0]["steps"]
    assert "emotion_done" in events[0]["steps"]
    assert "continuity_done" in events[0]["steps"]
    assert "personality_queued" in events[0]["steps"]
    assert queue[0]["source_event_id"] == result["event_id"]
    assert personality["last_event_id"] == result["event_id"]


def test_pre_turn_repairs_incomplete_events(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from web.api.nova_lifecycle import get_nova_state_paths, load_events, pre_turn

    paths = get_nova_state_paths()
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.events.write_text(
        json.dumps(
            [
                {
                    "event_id": "evt-broken",
                    "type": "post_turn",
                    "status": "started",
                    "steps": ["started", "memory_done"],
                    "visibility": "private",
                }
            ]
        ),
        encoding="utf-8",
    )

    payload = pre_turn(workspace_slug="nova", user_text="hi")
    events = load_events(limit=1, include_private=True)

    assert payload["ok"] is True
    assert events[0]["event_id"] == "evt-broken"
    assert events[0]["status"] == "failed"
    assert events[0]["repair"]["reason"] == "incomplete_event_detected"


def test_migration_tick_imports_legacy_state_without_secrets(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    archive = home / "spaces" / "_bewusstsein_archived_20260605"
    archive.mkdir(parents=True)
    (archive / "emotion_state.json").write_text('{"arousal":0.7,"valence":0.4}', encoding="utf-8")
    (archive / "hormon_state.json").write_text('{"hormones":{}}', encoding="utf-8")
    (archive / "_nova_mail_creds.json").write_text('{"secret":"x"}', encoding="utf-8")
    vm = archive / "vector_memory_db"
    vm.mkdir()
    (vm / "chroma.sqlite3").write_text("x", encoding="utf-8")

    from web.api.nova_lifecycle import get_nova_state_paths, migration_tick

    result = migration_tick()
    paths = get_nova_state_paths()
    marker = json.loads(paths.migration.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert marker["counts"]["vector_memory_files"] == 1
    assert "_nova_mail_creds.json" in marker["skipped"]
    assert not (paths.space / "_nova_mail_creds.json").exists()
    assert paths.personality.exists()


def test_dashboard_nova_api_endpoints_require_token_and_filter_visibility(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from cli import web_server
    from web.api.nova_lifecycle import get_nova_state_paths, post_turn

    post_turn(
        session_id="s2",
        user_text="Das ist privat wichtig.",
        assistant_text="Ich merke mir den Kontext vorsichtig.",
        workspace_slug="nova",
        blocking=True,
    )
    assert get_nova_state_paths().personality.exists()

    client = TestClient(web_server.app)
    unauthorized = client.get("/api/nova/status")
    assert unauthorized.status_code == 401

    headers = {"X-Hermes-Session-Token": web_server._SESSION_TOKEN}
    status = client.get("/api/nova/status", headers=headers)
    personality_public = client.get("/api/nova/personality", headers=headers)
    events_private = client.get("/api/nova/events?scope=private", headers=headers)

    assert status.status_code == 200
    assert status.json()["autonomy_level"] == 2
    assert personality_public.status_code == 200
    assert "relationship" not in personality_public.json()
    assert events_private.status_code == 200
    assert events_private.json()["events"][0]["event_id"]
