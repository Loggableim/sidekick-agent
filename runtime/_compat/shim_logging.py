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
mimicking the behaviour in the original ``hermes_logging.py``.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

# ── Re-export from shared.logging_setup ─────────────────────────────────────
from shared.logging_setup import get_logs_dir, setup_logging

# ── Thread-local session context (matches original hermes_logging.py) ────────

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
]
