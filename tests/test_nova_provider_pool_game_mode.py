from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


NOVA_PROVIDER_POOL_PATH = Path("C:/sidekick/home/spaces/nova/provider_pool.py")


def _load_provider_pool_module(name: str):
    if not NOVA_PROVIDER_POOL_PATH.exists():
        pytest.skip(f"missing live Nova provider pool: {NOVA_PROVIDER_POOL_PATH}")
    spec = importlib.util.spec_from_file_location(name, NOVA_PROVIDER_POOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _prepare_home(tmp_path: Path, *, game_mode_enabled: bool) -> Path:
    home = tmp_path / "home"
    settings_path = home / "state" / "webui" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"game_mode_enabled": game_mode_enabled}),
        encoding="utf-8",
    )
    return home


def _write_router_pool_config(pool_path: Path) -> None:
    pool_path.parent.mkdir(parents=True, exist_ok=True)
    pool_path.write_text(
        json.dumps(
            {
                "slots": [
                    {
                        "slot_id": "router-primary",
                        "provider": "ollama-cloud",
                        "model": "gpt:oss-20b",
                        "enabled": True,
                        "allowed_roles": ["router"],
                        "last_used": None,
                        "cooldown_until": None,
                        "consecutive_failures": 0,
                        "rate_limited_until": None,
                        "quality_weight": 1.0,
                        "last_error": None,
                        "source": "default",
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_game_mode_router_pool_prefers_deepseek_and_reports_effective_model(tmp_path, monkeypatch):
    home = _prepare_home(tmp_path, game_mode_enabled=True)
    pool_path = home / "spaces" / "nova" / "provider_pool_config.json"
    _write_router_pool_config(pool_path)
    monkeypatch.setenv("SIDEKICK_HOME", str(home))

    module = _load_provider_pool_module("nova_provider_pool_game_mode_on")
    pool = module.ProviderPool.load(pool_path)
    health = pool.health()
    router_slot = next(slot for slot in health["slots"] if slot["slot_id"] == "router-primary")
    selected = pool.select("router")

    assert router_slot["configured_model"] == "gpt:oss-20b"
    assert router_slot["model"] == "deepseek-v4-flash"
    assert router_slot["available"] is True
    assert health["router_candidates"] == ["router-primary"]
    assert selected is not None
    assert selected.provider == "ollama-cloud"
    assert selected.model == "deepseek-v4-flash"


def test_game_mode_router_pool_keeps_local_default_when_game_mode_is_off(tmp_path, monkeypatch):
    home = _prepare_home(tmp_path, game_mode_enabled=False)
    pool_path = home / "spaces" / "nova" / "provider_pool_config.json"
    _write_router_pool_config(pool_path)
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)

    module = _load_provider_pool_module("nova_provider_pool_game_mode_off")
    pool = module.ProviderPool.load(pool_path)
    health = pool.health()
    router_slot = next(slot for slot in health["slots"] if slot["slot_id"] == "router-primary")
    selected = pool.select("router")

    assert router_slot["configured_model"] == "gpt:oss-20b"
    assert router_slot["model"] == "gpt:oss-20b"
    assert router_slot["available"] is True
    assert health["router_candidates"] == ["router-primary"]
    assert selected is not None
    assert selected.provider == "ollama-cloud"
    assert selected.model == "gpt:oss-20b"
