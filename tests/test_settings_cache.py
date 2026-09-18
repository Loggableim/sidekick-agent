"""Regression tests for the settings read cache (backlog item 5).

``load_settings`` runs on every API request via ``is_auth_enabled``. It is
cached by (settings.json mtime/size, resolved default workspace); these tests
pin the invalidation behaviour so a stale cache can never hide a settings
change.
"""
from __future__ import annotations

import json
import time

import pytest


def _fresh_config(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    import importlib

    from web.api import config as config_module

    importlib.reload(config_module)
    return config_module


def test_load_settings_is_cached_and_returns_copies(monkeypatch, tmp_path):
    config = _fresh_config(monkeypatch, tmp_path)

    first = config.load_settings()
    second = config.load_settings()
    assert first == second

    # A caller mutating the returned dict must not poison the cache.
    first["show_tps"] = "MUTATED"
    third = config.load_settings()
    assert third["show_tps"] != "MUTATED"


def test_load_settings_sees_save_settings_immediately(monkeypatch, tmp_path):
    config = _fresh_config(monkeypatch, tmp_path)

    config.load_settings()  # warm
    config.save_settings({"show_tps": True})
    assert config.load_settings()["show_tps"] is True

    config.save_settings({"show_tps": False})
    assert config.load_settings()["show_tps"] is False


def test_load_settings_sees_external_file_edit(monkeypatch, tmp_path):
    config = _fresh_config(monkeypatch, tmp_path)

    config.save_settings({"show_tps": False})  # creates settings.json
    config.load_settings()  # warm
    settings_file = config.SETTINGS_FILE
    payload = json.loads(settings_file.read_text(encoding="utf-8"))
    payload["show_tps"] = True
    time.sleep(1.1)  # mtime granularity
    settings_file.write_text(json.dumps(payload), encoding="utf-8")

    assert config.load_settings()["show_tps"] is True


def test_is_auth_enabled_reflects_password_changes(monkeypatch, tmp_path):
    config = _fresh_config(monkeypatch, tmp_path)
    from web.api import auth as auth_module

    assert auth_module.is_auth_enabled() is False

    config.save_settings({"_set_password": "hunter2"})
    assert auth_module.is_auth_enabled() is True

    config.save_settings({"_clear_password": True})
    assert auth_module.is_auth_enabled() is False


def test_resolve_default_workspace_revalidates_a_deleted_workspace(monkeypatch, tmp_path):
    config = _fresh_config(monkeypatch, tmp_path)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("SIDEKICK_WEBUI_DEFAULT_WORKSPACE", str(workspace))

    resolved = config.resolve_default_workspace()
    assert resolved == workspace.resolve()

    # The cached path is re-validated: a workspace deleted outside the process
    # is recreated instead of being returned as a dead path.
    workspace.rmdir()
    assert not workspace.exists()
    again = config.resolve_default_workspace()
    assert again.is_dir()
    assert again == workspace.resolve()
