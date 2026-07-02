from __future__ import annotations

import pytest


def _reset_spaces(monkeypatch, tmp_path):
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(space_engine, "_SPACE_CACHE", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_TS", 0.0)
    space_engine.set_active_space(None)
    return space_engine, spaces_root


def test_space_memory_path_defaults_to_space_memory_dir(monkeypatch, tmp_path):
    space_engine, spaces_root = _reset_spaces(monkeypatch, tmp_path)

    space = space_engine.get_or_create_space("color", "Color")
    space.save_config({"name": "Color"})

    assert space.memory_dir == spaces_root / "color" / "memory"


def test_space_memory_path_accepts_relative_path(monkeypatch, tmp_path):
    space_engine, spaces_root = _reset_spaces(monkeypatch, tmp_path)

    space = space_engine.get_or_create_space("nova", "Nova")
    space.save_config({"name": "Nova", "memory_path": "./memory"})

    assert space.memory_dir == spaces_root / "nova" / "memory"


def test_space_memory_path_accepts_absolute_path(monkeypatch, tmp_path):
    space_engine, _ = _reset_spaces(monkeypatch, tmp_path)
    custom = tmp_path / "custom-memory"

    space = space_engine.get_or_create_space("finance", "Finance")
    space.save_config({"name": "Finance", "memory_path": str(custom)})

    assert space.memory_dir == custom


def test_space_memory_path_rejects_traversal(monkeypatch, tmp_path):
    space_engine, spaces_root = _reset_spaces(monkeypatch, tmp_path)

    space = space_engine.get_or_create_space("unsafe", "Unsafe")
    space.save_config({"name": "Unsafe", "memory_path": "../shared"})

    with pytest.raises(ValueError):
        _ = space.memory_dir


def test_memory_tool_uses_active_space_memory_path(monkeypatch, tmp_path):
    space_engine, spaces_root = _reset_spaces(monkeypatch, tmp_path)
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))

    space = space_engine.get_or_create_space("nova", "Nova")
    space.save_config({"name": "Nova", "memory_path": "./memory"})
    space_engine.set_active_space("nova")

    import tools.memory_tool as memory_tool

    assert memory_tool.get_memory_dir() == spaces_root / "nova" / "memory"


def test_memory_tool_falls_back_to_global_when_no_active_space(monkeypatch, tmp_path):
    space_engine, _ = _reset_spaces(monkeypatch, tmp_path)
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    space_engine.set_active_space(None)

    import tools.memory_tool as memory_tool

    assert memory_tool.get_memory_dir() == tmp_path / "home" / "memories"
