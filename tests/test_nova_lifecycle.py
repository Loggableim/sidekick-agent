import json
from datetime import datetime, timedelta, timezone

import pytest

TestClient = pytest.importorskip("fastapi.testclient").TestClient


def _stub_nova_status_dependencies(monkeypatch, lifecycle):
    monkeypatch.setattr(lifecycle, "_game_mode_enabled", lambda: True)
    monkeypatch.setattr(lifecycle, "migration_tick", lambda: {"ok": True})
    monkeypatch.setattr(lifecycle, "ensure_background_cron_jobs", lambda: {"ok": True})
    monkeypatch.setattr(lifecycle, "_vector_memory_count", lambda: 0)
    monkeypatch.setattr(lifecycle, "_ltm_count", lambda: 0)


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

    from web.api.nova_lifecycle import autonomy_definition, guard_autonomous_action

    allowed = guard_autonomous_action({"type": "read", "target": "https://example.com"}, autonomy_level=2)
    blocked = guard_autonomous_action({"type": "post", "target": "https://example.com"}, autonomy_level=2)
    secret = guard_autonomous_action({"type": "read", "target": "C:/sidekick/home/auth.json"}, autonomy_level=4)
    autonomy = autonomy_definition(2)

    assert allowed["allowed"] is True
    assert blocked["allowed"] is False
    assert blocked["reason"] == "external_mutation_requires_level_3"
    assert secret["allowed"] is False
    assert secret["reason"] == "sensitive_target_blocked"
    assert autonomy["definition"]["name"] == "read_and_analyze"
    assert set(autonomy["levels"]) == {0, 1, 2, 3, 4}


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


def test_pre_turn_repairs_missing_personality_queue(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from web.api.nova_lifecycle import get_nova_state_paths, load_events, pre_turn

    paths = get_nova_state_paths()
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.events.write_text(
        json.dumps(
            [
                {
                    "event_id": "evt-queue-missing",
                    "type": "post_turn",
                    "status": "started",
                    "steps": ["started", "memory_done", "emotion_done", "continuity_done"],
                    "user": "hi",
                    "assistant": "hello",
                    "visibility": "private",
                }
            ]
        ),
        encoding="utf-8",
    )

    payload = pre_turn(workspace_slug="nova", user_text="hi")
    events = load_events(limit=1, include_private=True)
    queue = json.loads(paths.reflection_queue.read_text(encoding="utf-8"))

    assert payload["repaired"] == ["evt-queue-missing"]
    assert events[0]["status"] == "completed"
    assert events[0]["repair"]["reason"] == "missing_personality_queue_repaired"
    assert queue[0]["source_event_id"] == "evt-queue-missing"


def test_model_health_strategy_parses_minicpm_and_qwen(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from web.api.nova_lifecycle import _parse_model_health

    health = _parse_model_health(
        {
            "ok": True,
            "stdout": json.dumps(
                {
                    "models": {
                        "8081": {"online": True, "model": "MiniCPM5-1B-Q8_0.gguf"},
                        "8082": {"online": True, "model": "Qwen3.6-12B-IQ-Q4_K_M.gguf"},
                    }
                }
            ),
        }
    )

    assert health["models"]["8081"]["role"] == "fast_classifier"
    assert health["models"]["8081"]["sync_allowed"] is True
    assert health["models"]["8082"]["role"] == "deep_reflection"
    assert health["models"]["8082"]["requires_gpu"] is True
    assert health["models"]["8082"]["blocks_chat"] is False


def test_background_cron_jobs_are_ensured(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from web.api.nova_lifecycle import ensure_background_cron_jobs
    import runtime.cron.jobs as runtime_cron_jobs

    wrong_home = tmp_path / "wrong-home"
    monkeypatch.setattr(runtime_cron_jobs, "HERMES_DIR", wrong_home)
    monkeypatch.setattr(runtime_cron_jobs, "CRON_DIR", wrong_home / "cron")
    monkeypatch.setattr(runtime_cron_jobs, "JOBS_FILE", wrong_home / "cron" / "jobs.json")
    monkeypatch.setattr(runtime_cron_jobs, "OUTPUT_DIR", wrong_home / "cron" / "output")

    result = ensure_background_cron_jobs()
    scripts = tmp_path / "home" / "scripts"
    jobs_file = tmp_path / "home" / "cron" / "jobs.json"

    assert result["ok"] is True
    assert len(result["active"]) == 3
    assert (scripts / "nova_substrate_heartbeat.py").exists()
    assert (scripts / "nova_dream_reflection_tick.py").exists()
    assert jobs_file.exists()
    assert runtime_cron_jobs.JOBS_FILE == jobs_file
    assert not (wrong_home / "cron" / "jobs.json").exists()
    assert all(item["job_id"] for item in result["active"])
    assert {item["name"] for item in result["active"]} >= {
        "Nova substrate heartbeat",
        "Nova background tick",
        "Nova dream/reflection tick",
    }
    assert result["mode"] == "sidekick_cron_no_agent"


def test_background_cron_jobs_prune_legacy_nova_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from web.api.nova_lifecycle import ensure_background_cron_jobs

    jobs_file = tmp_path / "home" / "cron" / "jobs.json"
    jobs_file.parent.mkdir(parents=True, exist_ok=True)
    jobs_file.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "22f6a477b2f9",
                        "name": "Nova Substrate Heartbeat",
                        "schedule": {"kind": "interval", "minutes": 5, "display": "every 5m"},
                        "schedule_display": "every 5m",
                        "script": "substrate.py once",
                        "no_agent": True,
                        "deliver": "local",
                        "enabled": True,
                        "state": "scheduled",
                    },
                    {
                        "id": "1cc50ad5bfb8",
                        "name": "Nova Entity Kernel Tick",
                        "schedule": {"kind": "interval", "minutes": 15, "display": "every 15m"},
                        "schedule_display": "every 15m",
                        "script": "entity_kernel.py tick",
                        "no_agent": True,
                        "deliver": "local",
                        "enabled": True,
                        "state": "scheduled",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = ensure_background_cron_jobs()
    jobs = json.loads(jobs_file.read_text(encoding="utf-8"))["jobs"]
    names = {job["name"] for job in jobs}

    assert result["ok"] is True
    assert len(result["active"]) == 3
    assert len(jobs) == 3
    assert "Nova Substrate Heartbeat" not in names
    assert "Nova Entity Kernel Tick" not in names
    assert names == {
        "Nova substrate heartbeat",
        "Nova background tick",
        "Nova dream/reflection tick",
    }


def test_background_tick_updates_substrate_heartbeat_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    import web.api.nova_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_now", lambda: "2026-07-09T12:00:00+00:00")

    result = lifecycle.background_tick()
    substrate = json.loads((tmp_path / "home" / "spaces" / "nova" / "substrate_state.json").read_text(encoding="utf-8"))
    events = lifecycle.load_events(limit=1, include_private=True)

    assert result["ok"] is True
    assert substrate["last_heartbeat"] == "2026-07-09T12:00:00+00:00"
    assert substrate["last_lifecycle_heartbeat"] == "2026-07-09T12:00:00+00:00"
    assert substrate["lifecycle_status"] == "alive"
    assert events[0]["steps"][-1] == "substrate_done"


def test_nova_status_degrades_when_cron_storage_write_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    import runtime.cron.jobs as runtime_cron_jobs
    import web.api.nova_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_run_local_script", lambda *args, **kwargs: {"ok": False, "stdout": ""})
    monkeypatch.setattr(
        runtime_cron_jobs,
        "list_jobs",
        lambda include_disabled=False: [
            {
                "id": "nova-substrate-heartbeat",
                "name": "Nova substrate heartbeat",
                "schedule_display": "every 1m",
                "state": "scheduled",
            }
        ],
    )

    def fail_update(job_id, updates):
        raise PermissionError("jobs file locked")

    monkeypatch.setattr(runtime_cron_jobs, "update_job", fail_update)

    cron_result = lifecycle.ensure_background_cron_jobs()
    status = lifecycle.get_nova_status()

    assert cron_result["ok"] is False
    assert cron_result["reason"] == "cron_jobs_write_failed"
    assert "jobs.json" in cron_result["jobs_file"]
    assert status["ok"] is True
    assert status["cron"]["ok"] is False
    assert status["cron"]["reason"] == "cron_jobs_write_failed"


def test_dream_tick_defers_when_qwen_offline(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    import web.api.nova_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_game_mode_enabled", lambda: False)

    def fake_run(script, *args, **kwargs):
        assert script == "local_llm_bridge.py"
        return {"ok": True, "stdout": json.dumps({"models": {"8082": {"online": False}}})}

    monkeypatch.setattr(lifecycle, "_run_local_script", fake_run)

    result = lifecycle.dream_tick()
    events = lifecycle.load_events(limit=1, include_private=True)

    assert result["ok"] is True
    assert result["deferred"] is True
    assert events[0]["status"] == "deferred"
    assert events[0]["steps"][-1] == "qwen_offline_deferred"


def test_dream_tick_uses_remote_deepseek_in_game_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    import web.api.nova_lifecycle as lifecycle
    import runtime.auxiliary_client as aux
    from types import SimpleNamespace

    monkeypatch.setattr(lifecycle, "_game_mode_enabled", lambda: True)

    def fail_local_script(*args, **kwargs):
        raise AssertionError("Game Mode should not touch local LLM scripts")

    monkeypatch.setattr(lifecycle, "_run_local_script", fail_local_script)

    captured = {}

    def fake_call_llm(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="  Traum aus DeepSeek V4 Flash  ",
                        reasoning=None,
                        reasoning_content=None,
                        reasoning_details=None,
                    )
                )
            ]
        )

    monkeypatch.setattr(aux, "call_llm", fake_call_llm)

    result = lifecycle.dream_tick()
    events = lifecycle.load_events(limit=1, include_private=True)

    assert result["ok"] is True
    assert result["game_mode_enabled"] is True
    assert result["remote_provider"] == "ollama-cloud"
    assert result["remote_model"] == "deepseek-v4-flash"
    assert "DeepSeek V4 Flash" in result["narrative_preview"]
    assert captured["provider"] == "ollama-cloud"
    assert captured["model"] == "deepseek-v4-flash"
    assert events[0]["status"] == "completed"
    assert events[0]["steps"][-1] == "dream_remote_done"
    assert events[0]["remote_provider"] == "ollama-cloud"
    assert events[0]["remote_model"] == "deepseek-v4-flash"


def test_game_mode_enabled_uses_current_settings_parent(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    import web.api.config as cfg
    import web.api.nova_lifecycle as lifecycle

    monkeypatch.setattr(cfg, "is_game_mode_enabled", lambda: False)
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "home" / "state" / "webui" / "settings.json")

    settings_parent = tmp_path / "home" / "state" / "webui"
    settings_parent.mkdir(parents=True, exist_ok=True)
    lock_file = settings_parent / "game_mode.lock"
    lock_file.write_text("1", encoding="utf-8")

    real_path = lifecycle.Path

    def fake_path(value):
        if value == "C:/sidekick/home/state/game_mode.lock":
            return tmp_path / "hardcoded" / "game_mode.lock"
        if value == "C:/sidekick/home/state/gpu_watchdog_state.json":
            return tmp_path / "hardcoded" / "gpu_watchdog_state.json"
        return real_path(value)

    monkeypatch.setattr(lifecycle, "Path", fake_path)

    assert lifecycle._game_mode_enabled() is True

    lock_file.unlink()
    wd_file = settings_parent / "gpu_watchdog_state.json"
    wd_file.write_text(json.dumps({"last_game_mode": True}), encoding="utf-8")

    assert lifecycle._game_mode_enabled() is True


def test_game_mode_enabled_accepts_legacy_state_dir_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    import web.api.config as cfg
    import web.api.nova_lifecycle as lifecycle

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "home" / "state" / "webui" / "settings.json")
    monkeypatch.setattr(cfg, "load_settings", lambda: {"game_mode_enabled": False})

    legacy_lock = tmp_path / "home" / "state" / "game_mode.lock"
    legacy_lock.parent.mkdir(parents=True, exist_ok=True)
    legacy_lock.write_text("1", encoding="utf-8")

    assert cfg.is_game_mode_enabled() is True

    monkeypatch.setattr(cfg, "is_game_mode_enabled", lambda: False)
    assert lifecycle._game_mode_enabled() is True


def test_nova_status_skips_local_model_health_in_game_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    import web.api.nova_lifecycle as lifecycle

    _stub_nova_status_dependencies(monkeypatch, lifecycle)

    def fail_model_health(script, *args, **kwargs):
        if script == "local_llm_bridge.py":
            raise AssertionError("Game Mode status must not touch local LLM health")
        return {"ok": True, "stdout": "{}"}

    monkeypatch.setattr(lifecycle, "_run_local_script", fail_model_health)

    result = lifecycle.get_nova_status()

    assert result["game_mode_enabled"] is True
    assert result["qwen"] == "blocked_by_game_mode"
    assert result["minicpm"] == "blocked_by_game_mode"
    assert result["models"]["game_mode_enabled"] is True
    assert result["models"]["models"]["8082"]["blocked_by_game_mode"] is True


def test_nova_status_repairs_stale_background_event(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    import web.api.nova_lifecycle as lifecycle

    _stub_nova_status_dependencies(monkeypatch, lifecycle)
    paths = lifecycle.get_nova_state_paths()
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    stale_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    paths.events.write_text(
        json.dumps(
            [
                {
                    "event_id": "evt-stale-bg",
                    "type": "background_tick",
                    "status": "started",
                    "steps": ["started"],
                    "created_at": stale_at,
                    "updated_at": stale_at,
                    "visibility": "private",
                }
            ]
        ),
        encoding="utf-8",
    )

    status = lifecycle.get_nova_status()
    events = lifecycle.load_events(limit=1, include_private=True)

    assert status["repaired_events"] == ["evt-stale-bg"]
    assert events[0]["status"] == "failed"
    assert events[0]["repair"]["reason"] == "stale_lifecycle_event_detected"
    assert events[0]["repair"]["stale_after_seconds"] == lifecycle.NOVA_STALE_EVENT_SECONDS


def test_nova_status_preserves_fresh_background_event(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    import web.api.nova_lifecycle as lifecycle

    _stub_nova_status_dependencies(monkeypatch, lifecycle)
    paths = lifecycle.get_nova_state_paths()
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    fresh_at = datetime.now(timezone.utc).isoformat()
    paths.events.write_text(
        json.dumps(
            [
                {
                    "event_id": "evt-fresh-bg",
                    "type": "background_tick",
                    "status": "started",
                    "steps": ["started"],
                    "created_at": fresh_at,
                    "updated_at": fresh_at,
                    "visibility": "private",
                }
            ]
        ),
        encoding="utf-8",
    )

    status = lifecycle.get_nova_status()
    events = lifecycle.load_events(limit=1, include_private=True)

    assert status["repaired_events"] == []
    assert events[0]["status"] == "started"


def test_agents_md_model_names_are_repaired(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    from web.api.nova_lifecycle import get_nova_state_paths, repair_local_agents_md

    agents = get_nova_state_paths().space / "AGENTS.md"
    agents.parent.mkdir(parents=True)
    agents.write_text(
        "Ports: 8080 Dolphin 8B | 8081 3B uncensored | 8082 Qwen 9B\n"
        "Dolphin 8B:8080, 3B:8081, Qwen 9B:8082",
        encoding="utf-8",
    )

    result = repair_local_agents_md()
    text = agents.read_text(encoding="utf-8")

    assert result["updated"] is True
    assert "MiniCPM5-1B:8081" in text
    assert "Qwen3.6-12B:8082" in text
    assert "Dolphin 8B" not in text


def test_migration_conflict_repair_merges_json_and_ltm(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    archive = home / "spaces" / "_bewusstsein_archived_20260605"
    nova = home / "spaces" / "nova"
    archive.mkdir(parents=True)
    nova.mkdir(parents=True)
    (archive / "emotion_state.json").write_text('{"old_only":1,"shared":{"old":true}}', encoding="utf-8")
    (nova / "emotion_state.json").write_text('{"new_only":2,"shared":{"new":true}}', encoding="utf-8")

    import sqlite3

    for path, rows in (
        (archive / "ltm_facts.db", [("old", "legacy fact")]),
        (nova / "ltm_facts.db", [("new", "current fact")]),
    ):
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE ltm_facts (id TEXT PRIMARY KEY, fact TEXT)")
        conn.executemany("INSERT INTO ltm_facts (id, fact) VALUES (?, ?)", rows)
        conn.commit()
        conn.close()

    from web.api.nova_lifecycle import get_nova_state_paths, migration_tick

    paths = get_nova_state_paths()
    paths.state_dir.mkdir(parents=True)
    paths.migration.write_text(
        json.dumps({"conflicts": ["emotion_state.json", "ltm_facts.db"], "counts": {}, "skipped": []}),
        encoding="utf-8",
    )

    result = migration_tick()
    merged = json.loads((nova / "emotion_state.json").read_text(encoding="utf-8"))
    conn = sqlite3.connect(nova / "ltm_facts.db")
    try:
        facts = {row[0] for row in conn.execute("SELECT id FROM ltm_facts")}
    finally:
        conn.close()

    assert result["conflict_repair"]["merged_json"] == ["emotion_state.json"]
    assert result["conflict_repair"]["merged_sqlite"] == ["ltm_facts.db"]
    assert merged["old_only"] == 1
    assert merged["new_only"] == 2
    assert facts == {"old", "new"}


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
    assert status.json()["autonomy"]["definition"]["name"] == "read_and_analyze"
    assert status.json()["model_strategy"]["fast_classifier"]["port"] == 8081
    assert status.json()["cron"]["ok"] is True
    assert personality_public.status_code == 200
    assert "relationship" not in personality_public.json()
    assert events_private.status_code == 200
    assert events_private.json()["events"][0]["event_id"]
