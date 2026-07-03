from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from sidekick_constants import get_sidekick_home
from shared.config import ensure_sidekick_home, load_config

_INITIALIZED = False


def get_logs_dir() -> Path:
    return get_sidekick_home() / "logs"


def _read_logging_config() -> tuple[str, int, int]:
    config = load_config()
    if not isinstance(config, dict):
        return ("INFO", 5, 3)
    logging_cfg = config.get("logging", {})
    if not isinstance(logging_cfg, dict):
        return ("INFO", 5, 3)
    level = str(logging_cfg.get("level", "INFO")).upper()
    try:
        max_size_mb = int(logging_cfg.get("max_size_mb", 5))
    except (TypeError, ValueError):
        max_size_mb = 5
    try:
        backup_count = int(logging_cfg.get("backup_count", 3))
    except (TypeError, ValueError):
        backup_count = 3
    return (level, max_size_mb, backup_count)


def setup_logging(force: bool = False) -> Path:
    global _INITIALIZED
    logs_dir = get_logs_dir()
    ensure_sidekick_home()
    logs_dir.mkdir(parents=True, exist_ok=True)

    if _INITIALIZED and not force:
        return logs_dir

    level_name, max_size_mb, backup_count = _read_logging_config()
    level = getattr(logging, level_name, logging.INFO)
    max_bytes = max_size_mb * 1024 * 1024

    root = logging.getLogger()
    if force:
        for handler in list(root.handlers):
            if getattr(handler, "_sidekick_managed", False):
                root.removeHandler(handler)
                handler.close()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    agent_handler = RotatingFileHandler(
        logs_dir / "agent.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    agent_handler._sidekick_managed = True  # type: ignore[attr-defined]
    agent_handler.setLevel(level)
    agent_handler.setFormatter(formatter)
    root.addHandler(agent_handler)

    errors_handler = RotatingFileHandler(
        logs_dir / "errors.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    errors_handler._sidekick_managed = True  # type: ignore[attr-defined]
    errors_handler.setLevel(logging.WARNING)
    errors_handler.setFormatter(formatter)
    root.addHandler(errors_handler)

    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)

    _INITIALIZED = True
    return logs_dir
