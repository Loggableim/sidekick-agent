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
