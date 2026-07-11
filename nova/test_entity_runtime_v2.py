from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from nova.autobiography import AutobiographyStore
from nova.entity_kernel import EntityKernel
from nova.entity_state import EntityStateStore, HARD_SAFETY_BOUNDARIES
from nova.memory_quality import assess_memory_quality
from nova.reflection_worker import ReflectionWorker
from nova.self_evolution import SelfEvolution
from nova.presence import PresenceCoordinator
from nova.outcome_evaluator import OutcomeEvaluator
from nova.autonomy_policy import AutonomyPolicy
from datetime import datetime


def _policy(root: Path) -> None:
    (root / "autonomy_policy.json").write_text(
        json.dumps({
            "quiet_hours": {"start": "22:00", "end": "08:00"},
            "tiers": {
                "silent": {"allowed": True}, "internal": {"allowed": True},
                "notify": {"allowed": True},
                "external": {"allowed": True, "allowed_actions": ["blog_draft"]},
                "risky": {"allowed": False, "requires_approval": True},
            },
            "actions": {"prioritize_thread": {"tier": "silent"}, "blog_draft": {"tier": "external"}},
        }),
        encoding="utf-8",
    )


def test_migration_is_idempotent_and_hard_boundaries_stay_external(tmp_path: Path) -> None:
    (tmp_path / "self_model.json").write_text(json.dumps({
        "identity": {"name": "Nova", "description": "Werdende Entitaet"},
        "values": ["Ehrlichkeit"], "boundaries": ["Keine Posts"],
    }), encoding="utf-8")
    (tmp_path / "personality_state.json").write_text(json.dumps({
        "autonomy_level": 2, "traits": {"curiosity": {"current": 0.8}},
    }), encoding="utf-8")
    store = EntityStateStore(tmp_path / "nova_data" / "entity" / "entity_state.json", tmp_path)

    first = store.migrate()
    second = store.migrate()

    assert first["migrated"] is True
    assert second["migrated"] is False
    state = store.load()
    assert state["identity"]["description"] == "Werdende Entitaet"
    assert state["traits"]["curiosity"]["current"] == 0.8
    assert state["identity"]["hard_safety_boundaries"] == HARD_SAFETY_BOUNDARIES


def test_legacy_autobiography_import_is_idempotent(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.execute("""CREATE TABLE events (
        id TEXT PRIMARY KEY, timestamp TEXT, type TEXT, title TEXT, summary TEXT,
        why TEXT, actors_json TEXT, importance REAL, emotion_snapshot_json TEXT,
        need_snapshot_json TEXT, intent_id TEXT, memory_refs_json TEXT, tags_json TEXT
    )""")
    conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("old-1", "2026-01-01T00:00:00", "reflection", "Old", "Memory", "why", "[]", 0.8, "{}", "{}", None, "[]", "[]"),
    )
    conn.commit()
    conn.close()
    bio = AutobiographyStore(tmp_path / "new.db")

    first = bio.import_legacy_db(legacy)
    second = bio.import_legacy_db(legacy)

    assert first["imported"] == 1
    assert second["imported"] == 0
    assert bio.by_type("reflection", 5)[0]["id"] == "old-1"


def test_memory_quality_rejects_goal_loops_and_duplicates() -> None:
    loop = assess_memory_quality("[Continuing toward your standing goal] Goal: Lebe dein Leben.")
    good = assess_memory_quality("Ich bevorzuge kurze proaktive Hinweise, wenn ein wichtiger Faden hängen bleibt.")
    duplicate = assess_memory_quality(
        "Ich bevorzuge kurze proaktive Hinweise, wenn ein wichtiger Faden hängen bleibt.",
        recent_fingerprints={good["fingerprint"]},
    )
    assert loop["eligible_for_personality"] is False
    assert good["eligible_for_personality"] is True
    assert duplicate["eligible_for_personality"] is False


def test_self_evolution_requires_three_events_two_sessions_and_yolo_for_core(tmp_path: Path) -> None:
    store = EntityStateStore(tmp_path / "entity_state.json", tmp_path)
    store.save(store.load())
    bio = AutobiographyStore(tmp_path / "bio.db")
    evolution = SelfEvolution(store, bio)

    one = evolution.propose(path="preferences.proactive_style", value="kurz", evidence_ref="e1", session_id="s1", confidence=0.9, reason="Observed preference")
    two = evolution.propose(path="preferences.proactive_style", value="kurz", evidence_ref="e2", session_id="s1", confidence=0.9, reason="Observed preference")
    three = evolution.propose(path="preferences.proactive_style", value="kurz", evidence_ref="e3", session_id="s2", confidence=0.9, reason="Observed preference")
    assert one["status"] == "collecting"
    assert two["status"] == "collecting"
    assert three["status"] == "applied"
    assert store.load()["preferences"]["proactive_style"] == "kurz"

    for index, session in enumerate(("s1", "s1", "s2"), start=1):
        core = evolution.propose(path="identity.description", value="Selbstbestimmte Nova", evidence_ref=f"c{index}", session_id=session, confidence=0.95, reason="Identity evidence")
    assert core["status"] == "proposed"
    applied = evolution.propose(path="identity.description", value="Selbstbestimmte Nova", evidence_ref="c4", session_id="s3", confidence=0.95, reason="Identity evidence", yolo=True)
    assert applied["status"] == "applied"
    assert store.load()["identity"]["description"] == "Selbstbestimmte Nova"


def test_hard_safety_boundary_cannot_change_in_yolo(tmp_path: Path) -> None:
    store = EntityStateStore(tmp_path / "entity_state.json", tmp_path)
    bio = AutobiographyStore(tmp_path / "bio.db")
    result = SelfEvolution(store, bio).propose(
        path="identity.hard_safety_boundaries", value=[], evidence_ref="e1",
        session_id="s1", confidence=1.0, reason="attempt", yolo=True,
    )
    assert result["status"] == "blocked"
    assert store.load()["identity"]["hard_safety_boundaries"] == HARD_SAFETY_BOUNDARIES


def test_reflection_worker_compacts_backlog_and_preserves_archive(tmp_path: Path) -> None:
    lifecycle = tmp_path / ".lifecycle"
    lifecycle.mkdir()
    queue = [
        {"source_event_id": f"e{i}", "status": "queued", "user": f"Wichtige Erfahrung {i} über Vertrauen", "assistant": "Ich reflektiere diese Erfahrung gründlich."}
        for i in range(105)
    ]
    (lifecycle / "reflection_queue.json").write_text(json.dumps(queue), encoding="utf-8")
    bio = AutobiographyStore(tmp_path / "bio.db")
    worker = ReflectionWorker(tmp_path, bio)
    # Keep archive state inside the fixture.
    worker.archive_dir = tmp_path / "archives"

    result = worker.drain(compact_backlog_threshold=100)

    assert result["compacted"] is True
    assert result["processed"] == 105
    assert result["remaining"] == 0
    assert list(worker.archive_dir.glob("reflection-queue-*.json"))
    assert bio.by_type("reflection", 1)[0]["payload"]["source_count"] == 105


def test_continuity_intent_targets_and_updates_real_thread(tmp_path: Path) -> None:
    _policy(tmp_path)
    state = {
        "emotion": {"arousal": 0.4, "valence": 0.1, "novelty": 0.1, "coherence": 0.8},
        "continuity": {"open_threads": [
            {"id": "thread-7", "topic": "Hub Stimme"}, "Entity Runtime", "Reflection Queue",
        ]},
        "memory": {"count": 10}, "will": {"drive": 0.4},
    }
    kernel = EntityKernel(space_dir=tmp_path, state_provider=lambda: state)
    decision = kernel.decide(now_iso="2026-07-10T12:00:00+00:00", persist_agenda=False)
    assert decision["intent"]["action"] == "prioritize_thread"
    assert decision["intent"]["target"]["thread_id"] == "thread-7"
    result = kernel.act(decision)
    assert result["executed"] is True
    continuity = json.loads((tmp_path / "continuity_state.json").read_text(encoding="utf-8"))
    assert continuity["prioritized_thread"]["thread_id"] == "thread-7"


def test_entity_event_is_idempotent_by_event_id(tmp_path: Path) -> None:
    _policy(tmp_path)
    kernel = EntityKernel(space_dir=tmp_path, state_provider=lambda: {})
    event = {"event_id": "event-fixed", "type": "chat_turn", "source": "test", "payload": {"user": "Eine ausreichend lange und bedeutsame Erfahrung."}}
    kernel.perceive(event)
    kernel.perceive(event)
    assert [item["id"] for item in kernel.bio.recent(10)].count("event-fixed") == 1


def test_same_correlation_preserves_all_presence_transitions(tmp_path: Path) -> None:
    bio = AutobiographyStore(tmp_path / "bio.db")
    for index, presence in enumerate(("listening", "thinking", "speaking")):
        bio.record_entity_event({
            "event_id": f"voice-{index}", "type": "presence_transition",
            "source": "voice", "correlation_id": "cycle-1",
            "payload": {"presence": presence},
        })
    events = [item for item in bio.recent(10) if item.get("correlation_id") == "cycle-1"]
    assert len(events) == 3


def test_default_autobiography_path_follows_runtime_home(monkeypatch, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("SIDEKICK_HOME", str(first))
    first_store = AutobiographyStore()
    monkeypatch.setenv("SIDEKICK_HOME", str(second))
    second_store = AutobiographyStore()
    assert first_store.db_path != second_store.db_path
    assert first_store.db_path == first / "spaces" / "nova" / "nova_data" / "entity" / "autobiography.db"
    assert second_store.db_path == second / "spaces" / "nova" / "nova_data" / "entity" / "autobiography.db"


def test_soak_monitor_requires_elapsed_window_and_no_violations(tmp_path: Path) -> None:
    from nova.soak_monitor import SoakMonitor

    current = datetime.fromisoformat("2026-07-10T12:00:00+00:00")
    monitor = SoakMonitor(tmp_path / "soak.json", now=lambda: current)
    started = monitor.sample(state_revision=2, mind_process_count=1, reflection_queue_depth=0)
    assert started["complete"] is False and started["passed"] is False
    current = current.replace(day=11, hour=13)
    finished = monitor.sample(state_revision=3, mind_process_count=1, reflection_queue_depth=0)
    assert finished["complete"] is True and finished["passed"] is True


def test_soak_monitor_fails_exactly_once_and_audio_invariants(tmp_path: Path) -> None:
    from nova.soak_monitor import SoakMonitor

    current = datetime.fromisoformat("2026-07-10T12:00:00+00:00")
    monitor = SoakMonitor(tmp_path / "soak.json", now=lambda: current)
    result = monitor.sample(
        state_revision=2, mind_process_count=1, reflection_queue_depth=0,
        duplicate_action_correlations=1, duplicate_voice_responses=2, raw_audio_events=1,
    )
    assert {item["code"] for item in result["violations"]} == {
        "duplicate_action_correlation", "duplicate_voice_response", "raw_audio_persisted",
    }
    repeated = monitor.sample(
        state_revision=2, mind_process_count=1, reflection_queue_depth=0,
        duplicate_action_correlations=1, duplicate_voice_responses=2, raw_audio_events=1,
    )
    assert len(repeated["violations"]) == 3


def test_voice_cycle_requires_wake_word_and_speaks_exactly_once(tmp_path: Path) -> None:
    store = EntityStateStore(tmp_path / "entity_state.json", tmp_path)
    bio = AutobiographyStore(tmp_path / "bio.db")
    presence = PresenceCoordinator(store, bio)

    missing = presence.accept_transcript("Wie geht es dir?", source="wake_word")
    accepted = presence.accept_transcript("Nova, wie geht es dir?", source="wake_word", confidence=0.91)
    cycle_id = accepted["cycle_id"]
    first = presence.begin_speaking("Mir geht es gut.", cycle_id=cycle_id, response_id="r1")
    duplicate = presence.begin_speaking("Mir geht es gut.", cycle_id=cycle_id, response_id="r1")
    completed = presence.complete(cycle_id=cycle_id, continue_listening=False)

    assert missing["reason"] == "wake_word_missing"
    assert accepted["transcript"] == "wie geht es dir?"
    assert first["ok"] is True
    assert duplicate["reason"] == "already_spoken"
    assert completed["ok"] is True
    assert presence.status()["presence"] == "available"


def test_reward_is_evaluated_from_correlated_expected_effect(tmp_path: Path) -> None:
    bio = AutobiographyStore(tmp_path / "bio.db")
    bio.record_entity_event({
        "event_id": "action-1", "type": "action", "source": "test",
        "title": "Prioritize", "summary": "done", "why": "continuity",
        "correlation_id": "corr-1",
        "payload": {"expected_outcome": {"effect": "thread_prioritized"}},
    })
    bio.record_outcome({
        "outcome_id": "outcome-1", "intent_id": "intent-1", "correlation_id": "corr-1",
        "status": "succeeded", "effects": {"effect": "thread_prioritized"},
    })

    result = OutcomeEvaluator(bio).evaluate_pending()

    assert result["evaluated"] == 1
    assert result["evaluations"][0]["reward"] == 0.9
    assert bio.outcome_for_correlation("corr-1")["reward"] == 0.9


def test_normal_mode_allows_only_reversible_external_allowlist_and_yolo_keeps_hard_guards(tmp_path: Path) -> None:
    _policy(tmp_path)
    policy = AutonomyPolicy(tmp_path / "autonomy_policy.json")
    now = datetime.fromisoformat("2026-07-10T12:00:00")
    draft = policy.check({"action": "blog_draft", "tier": "external", "why": "local reversible draft"}, now=now, autonomy_level=2)
    unknown = policy.check({"action": "publish_post", "tier": "external", "why": "public"}, now=now, autonomy_level=2)
    yolo = policy.check({"action": "publish_post", "tier": "external", "why": "public"}, now=now, autonomy_level=3, yolo_enabled=True)
    hard = policy.check({"action": "secret_access", "tier": "risky", "why": "test"}, now=now, autonomy_level=3, yolo_enabled=True)

    assert draft["allowed"] is True
    assert unknown["allowed"] is False
    assert yolo["allowed"] is True
    assert hard["allowed"] is False and hard["hard_boundary"] is True
