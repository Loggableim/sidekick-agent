from __future__ import annotations

import importlib.util
import json
from pathlib import Path


WATCHDOG_PATH = Path(r"C:\sidekick\home\scripts\gpu_watchdog.py")


def _load_watchdog_module():
    spec = importlib.util.spec_from_file_location("gpu_watchdog_under_test", WATCHDOG_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_watchdog_clears_stale_lock_when_settings_are_disabled(tmp_path, monkeypatch):
    watchdog = _load_watchdog_module()

    home = tmp_path / "home"
    state_dir = home / "state"
    webui_state_dir = state_dir / "webui"
    webui_state_dir.mkdir(parents=True, exist_ok=True)

    settings_path = webui_state_dir / "settings.json"
    settings_path.write_text(json.dumps({"game_mode_enabled": False}), encoding="utf-8")

    lock_path = state_dir / "game_mode.lock"
    lock_path.write_text("2026-06-30T05:32:36.669468Z", encoding="utf-8")

    state_path = state_dir / "gpu_watchdog_state.json"
    state_path.write_text(
        json.dumps({"last_game_mode": True, "last_action": "blocked"}),
        encoding="utf-8",
    )

    jobs_path = home / "cron" / "jobs.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(json.dumps({"jobs": []}), encoding="utf-8")

    calls: list[str] = []
    monkeypatch.setattr(watchdog, "SIDEKICK_HOME", home)
    monkeypatch.setattr(watchdog, "NOVA_SPACE", home / "spaces" / "nova")
    monkeypatch.setattr(watchdog, "CRON_JOBS_FILE", jobs_path)
    monkeypatch.setattr(watchdog, "SETTINGS_FILES", [settings_path])
    monkeypatch.setattr(watchdog, "STATE_FILE", state_path)
    monkeypatch.setattr(watchdog, "GAME_MODE_LOCK", lock_path)
    monkeypatch.setattr(watchdog, "resume_nova_crons", lambda: calls.append("resume") or 2)
    monkeypatch.setattr(watchdog, "start_models", lambda: calls.append("start") or True)
    monkeypatch.setattr(watchdog, "kill_llama_processes", lambda: calls.append("kill_llama") or 0)
    monkeypatch.setattr(watchdog, "kill_gpu_python_processes", lambda: calls.append("kill_py") or 0)
    monkeypatch.setattr(watchdog, "pause_nova_crons", lambda: calls.append("pause") or 0)

    assert watchdog.main() == 0

    assert not lock_path.exists()
    assert json.loads(settings_path.read_text(encoding="utf-8"))["game_mode_enabled"] is False

    state_after = json.loads(state_path.read_text(encoding="utf-8"))
    assert state_after["last_game_mode"] is False
    assert state_after["last_action"] == "unblocked"
    assert calls == ["resume", "start"]


def test_watchdog_clears_active_and_legacy_locks_when_settings_are_disabled(tmp_path, monkeypatch):
    watchdog = _load_watchdog_module()

    home = tmp_path / "home"
    state_dir = home / "state"
    webui_state_dir = state_dir / "webui"
    webui_state_dir.mkdir(parents=True, exist_ok=True)

    settings_path = webui_state_dir / "settings.json"
    settings_path.write_text(json.dumps({"game_mode_enabled": False}), encoding="utf-8")

    active_lock = webui_state_dir / "game_mode.lock"
    legacy_lock = state_dir / "game_mode.lock"
    active_lock.write_text("2026-06-30T05:32:36.669468Z", encoding="utf-8")
    legacy_lock.write_text("2026-06-30T05:32:36.669468Z", encoding="utf-8")

    state_path = state_dir / "gpu_watchdog_state.json"
    state_path.write_text(
        json.dumps({"last_game_mode": True, "last_action": "blocked"}),
        encoding="utf-8",
    )

    jobs_path = home / "cron" / "jobs.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(json.dumps({"jobs": []}), encoding="utf-8")

    monkeypatch.setattr(watchdog, "SIDEKICK_HOME", home)
    monkeypatch.setattr(watchdog, "NOVA_SPACE", home / "spaces" / "nova")
    monkeypatch.setattr(watchdog, "CRON_JOBS_FILE", jobs_path)
    monkeypatch.setattr(watchdog, "SETTINGS_FILES", [settings_path])
    monkeypatch.setattr(watchdog, "STATE_FILE", state_path)
    monkeypatch.setattr(watchdog, "GAME_MODE_LOCK", legacy_lock)
    monkeypatch.setattr(watchdog, "resume_nova_crons", lambda: 0)
    monkeypatch.setattr(watchdog, "start_models", lambda: True)
    monkeypatch.setattr(watchdog, "kill_llama_processes", lambda: 0)
    monkeypatch.setattr(watchdog, "kill_gpu_python_processes", lambda: 0)
    monkeypatch.setattr(watchdog, "pause_nova_crons", lambda: 0)

    assert watchdog.main() == 0

    assert not legacy_lock.exists()
    assert not active_lock.exists()
    assert json.loads(settings_path.read_text(encoding="utf-8"))["game_mode_enabled"] is False


def test_watchdog_ignores_legacy_true_when_active_settings_are_disabled(tmp_path, monkeypatch):
    watchdog = _load_watchdog_module()

    home = tmp_path / "home"
    state_dir = home / "state"
    webui_state_dir = state_dir / "webui"
    legacy_webui_dir = home / "webui"
    webui_state_dir.mkdir(parents=True, exist_ok=True)
    legacy_webui_dir.mkdir(parents=True, exist_ok=True)

    active_settings = webui_state_dir / "settings.json"
    active_settings.write_text(json.dumps({"game_mode_enabled": False}), encoding="utf-8")
    legacy_settings = legacy_webui_dir / "settings.json"
    legacy_settings.write_text(json.dumps({"game_mode_enabled": True}), encoding="utf-8")

    lock_path = state_dir / "game_mode.lock"
    lock_path.write_text("2026-06-30T05:32:36.669468Z", encoding="utf-8")

    state_path = state_dir / "gpu_watchdog_state.json"
    state_path.write_text(
        json.dumps({"last_game_mode": True, "last_action": "blocked"}),
        encoding="utf-8",
    )

    jobs_path = home / "cron" / "jobs.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(json.dumps({"jobs": []}), encoding="utf-8")

    monkeypatch.setattr(watchdog, "SIDEKICK_HOME", home)
    monkeypatch.setattr(watchdog, "NOVA_SPACE", home / "spaces" / "nova")
    monkeypatch.setattr(watchdog, "CRON_JOBS_FILE", jobs_path)
    monkeypatch.setattr(watchdog, "ACTIVE_SETTINGS_FILE", active_settings)
    monkeypatch.setattr(watchdog, "LEGACY_SETTINGS_FILE", legacy_settings)
    monkeypatch.setattr(watchdog, "SETTINGS_FILES", [active_settings, legacy_settings])
    monkeypatch.setattr(watchdog, "STATE_FILE", state_path)
    monkeypatch.setattr(watchdog, "GAME_MODE_LOCK", lock_path)
    monkeypatch.setattr(watchdog, "resume_nova_crons", lambda: 0)
    monkeypatch.setattr(watchdog, "start_models", lambda: True)
    monkeypatch.setattr(watchdog, "kill_llama_processes", lambda: 0)
    monkeypatch.setattr(watchdog, "kill_gpu_python_processes", lambda: 0)
    monkeypatch.setattr(watchdog, "pause_nova_crons", lambda: 0)

    assert watchdog.main() == 0

    assert not lock_path.exists()
    assert json.loads(active_settings.read_text(encoding="utf-8"))["game_mode_enabled"] is False
    assert json.loads(legacy_settings.read_text(encoding="utf-8"))["game_mode_enabled"] is False


def test_pause_nova_crons_keeps_remote_safe_dream_reflection_tick_enabled(tmp_path, monkeypatch):
    watchdog = _load_watchdog_module()

    home = tmp_path / "home"
    jobs_path = home / "cron" / "jobs.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "dream-reflection",
                        "name": "Nova dream/reflection tick",
                        "enabled": True,
                        "state": "scheduled",
                        "paused_at": None,
                        "paused_reason": None,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(watchdog, "CRON_JOBS_FILE", jobs_path)

    paused = watchdog.pause_nova_crons()
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))["jobs"]

    assert paused == 0
    assert jobs[0]["enabled"] is True
    assert jobs[0]["state"] == "scheduled"
    assert jobs[0]["paused_at"] is None
    assert jobs[0]["paused_reason"] is None


def test_pause_nova_crons_pauses_explicitly_flagged_gpu_jobs(tmp_path, monkeypatch):
    watchdog = _load_watchdog_module()

    home = tmp_path / "home"
    jobs_path = home / "cron" / "jobs.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "gpu-job",
                        "name": "Nova legacy GPU tick",
                        "enabled": True,
                        "state": "scheduled",
                        "paused_at": None,
                        "paused_reason": None,
                        "game_mode_pause": True,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(watchdog, "CRON_JOBS_FILE", jobs_path)

    paused = watchdog.pause_nova_crons()
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))["jobs"]

    assert paused == 1
    assert jobs[0]["enabled"] is False
    assert jobs[0]["state"] == "paused"
    assert jobs[0]["paused_at"] is not None
    assert jobs[0]["paused_reason"] == "Game Mode block - GPU free (flagged job)"
