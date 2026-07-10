"""
Compat shim — bridges old ``sidekick_logging`` imports to the new
``shared.logging_setup`` layout.

Agent modules expect:
  - setup_logging(*, force=False) -> Path
  - get_logs_dir() -> Path
  - set_session_context(session_id: str) -> None
  - clear_session_context() -> None

The first two are re-exported from ``shared.logging_setup``.
The session-context helpers are implemented here via thread-local storage,
mimicking the behaviour in the original ``sidekick_logging.py``.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

# ── Re-export from shared.logging_setup ─────────────────────────────────────
from shared.logging_setup import get_logs_dir
from shared.logging_setup import setup_logging as _real_setup_logging


def setup_logging(force: bool = False, sidekick_home: Optional[str] = None) -> Path:
    """Wrapper that accepts deprecated ``sidekick_home`` kwarg (silently ignored)."""
    return _real_setup_logging(force=force)

# ── Verbose logging (for AIAgent --verbose mode) ────────────────────────────
# Matches the original sidekick_logging.setup_verbose_logging()
_LOG_FORMAT_VERBOSE = "%(asctime)s - %(name)s - %(levelname)s%(session_tag)s - %(message)s"

_NOISY_LOGGERS = (
    "openai", "openai._base_client", "httpx", "httpcore",
    "asyncio", "hpack", "hpack.hpack", "grpc", "modal",
    "urllib3", "urllib3.connectionpool",
)


def setup_verbose_logging() -> None:
    """Enable DEBUG-level console logging for ``--verbose`` / ``-v`` mode."""
    from runtime.redact import RedactingFormatter

    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler):
            if getattr(h, "_sidekick_verbose", False):
                return

    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(RedactingFormatter(_LOG_FORMAT_VERBOSE, datefmt="%H:%M:%S"))
    handler._sidekick_verbose = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    if root.level > logging.DEBUG:
        root.setLevel(logging.DEBUG)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


# ── Thread-local session context (matches original sidekick_logging.py) ────────

_session_context = threading.local()


def set_session_context(session_id: str) -> None:
    """Set the session ID for the current thread.

    All subsequent log records on this thread will include ``[session_id]``
    in the formatted output when the record factory is installed.
    """
    _session_context.session_id = session_id


def clear_session_context() -> None:
    """Clear the session ID for the current thread."""
    _session_context.session_id = None


def _install_session_record_factory() -> None:
    """Replace the global LogRecord factory to inject ``session_tag``.

    Idempotent — safe to call multiple times.
    """
    current_factory = logging.getLogRecordFactory()
    if getattr(current_factory, "_sidekick_session_injector", False):
        return

    def _session_record_factory(*args, **kwargs):
        record = current_factory(*args, **kwargs)
        sid = getattr(_session_context, "session_id", None)
        record.session_tag = f" [{sid}]" if sid else ""  # type: ignore[attr-defined]
        return record

    _session_record_factory._sidekick_session_injector = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(_session_record_factory)


# Install the record factory on import so session_tag is always available.
_install_session_record_factory()

__all__ = [
    "clear_session_context",
    "get_logs_dir",
    "set_session_context",
    "setup_logging",
    "setup_verbose_logging",
]
