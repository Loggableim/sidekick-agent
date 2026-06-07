from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from sidekick_constants import get_config_path, get_env_path, get_sidekick_home

DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "name": "Sidekick",
        "assistant_name": "Nova",
    },
    "runtime": {
        "default_surface": "cli",
    },
    "paths": {
        "workspace": None,
    },
    "webui": {
        "host": "127.0.0.1",
        "port": 8787,
    },
    "logging": {
        "level": "INFO",
        "max_size_mb": 5,
        "backup_count": 3,
    },
}


def ensure_sidekick_home() -> Path:
    home = get_sidekick_home()
    home.mkdir(parents=True, exist_ok=True)
    for subdir in ("cron", "sessions", "logs", "memories", "skills", "state"):
        (home / subdir).mkdir(parents=True, exist_ok=True)
    return home


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def read_raw_config() -> dict[str, Any]:
    config_path = get_config_path()
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{config_path} must contain a YAML mapping at the top level")
    return data


def load_config() -> dict[str, Any]:
    return _deep_merge(DEFAULT_CONFIG, read_raw_config())


def save_config(config: dict[str, Any]) -> Path:
    ensure_sidekick_home()
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(config_path.parent),
        prefix=".config-",
        suffix=".yaml.tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=True)
        os.replace(tmp_name, config_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return config_path


def _parse_scalar(value: str) -> Any:
    parsed = yaml.safe_load(value)
    return parsed


def _set_nested(config: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    cursor = config
    for part in parts[:-1]:
        current = cursor.get(part)
        if not isinstance(current, dict):
            current = {}
            cursor[part] = current
        cursor = current
    cursor[parts[-1]] = value


def get_config_value(dotted_key: str, default: Any = None) -> Any:
    cursor: Any = load_config()
    for part in dotted_key.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return default
        cursor = cursor[part]
    return cursor


def set_config_value(dotted_key: str, raw_value: str) -> tuple[Path, Any]:
    config = read_raw_config()
    value = _parse_scalar(raw_value)
    _set_nested(config, dotted_key, value)
    return save_config(config), value


def parse_env_file() -> dict[str, str]:
    env_path = get_env_path()
    if not env_path.exists():
        return {}

    result: dict[str, str] = {}
    with open(env_path, "r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            result[key] = value
    return result


def get_env_value(key: str, default: str | None = None) -> str | None:
    if key in os.environ:
        return os.environ[key]
    return parse_env_file().get(key, default)


def runtime_summary() -> dict[str, Any]:
    config = load_config()
    env = parse_env_file()
    return {
        "home": str(get_sidekick_home()),
        "config_path": str(get_config_path()),
        "env_path": str(get_env_path()),
        "config_exists": get_config_path().exists(),
        "env_exists": get_env_path().exists(),
        "config": config,
        "env_keys": sorted(env.keys()),
    }


def get_default_workspace() -> str:
    config = load_config()
    paths = config.get("paths", {})
    if isinstance(paths, dict):
        workspace = paths.get("workspace")
        if workspace:
            return str(workspace)
    return str(Path.cwd())
