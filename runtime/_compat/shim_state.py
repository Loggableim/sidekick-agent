"""
Compat shim — placeholder for ``sidekick_state`` that defines the minimal
interface agent modules actually import.

Agent modules import these names from ``sidekick_state`` (found via grep):
  - SessionDB          — the main SQLite session store class
  - format_session_db_unavailable  — user-facing error formatting
  - apply_wal_with_fallback        — WAL-journal fallback helper

This shim provides stub/no-op implementations so that code can be imported
without crashing.  The actual port of the SQLite state store lives elsewhere.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal state mirroring the original module
# ---------------------------------------------------------------------------

_last_init_error: str | None = None
_last_init_error_lock = threading.Lock()

_WAL_INCOMPAT_MARKERS = (
    "locking protocol",
    "not authorized",
    "disk i/o error",
)


def _set_last_init_error(msg: str | None) -> None:
    global _last_init_error
    with _last_init_error_lock:
        _last_init_error = msg


def get_last_init_error() -> str | None:
    return _last_init_error


def format_session_db_unavailable(prefix: str = "Session database not available") -> str:
    """Format a user-facing 'session DB unavailable' message with cause.

    Mirrors the original ``sidekick_state.format_session_db_unavailable``.
    """
    cause = get_last_init_error()
    if not cause:
        return f"{prefix}."
    hint = ""
    if any(marker in cause.lower() for marker in _WAL_INCOMPAT_MARKERS):
        hint = " (state.db may be on NFS/SMB/FUSE — see https://www.sqlite.org/wal.html)"
    return f"{prefix}: {cause}{hint}."


def apply_wal_with_fallback(
    conn: Any,
    *,
    db_label: str = "state.db",
) -> str:
    """Set ``journal_mode=WAL`` on *conn*, falling back to DELETE on failure.

    This is a minimal placeholder that sets WAL directly.
    A proper port should handle the NFS/SMB fallback logic.
    """
    import sqlite3

    try:
        conn.execute("PRAGMA journal_mode=WAL")
        return "wal"
    except sqlite3.OperationalError:
        conn.execute("PRAGMA journal_mode=DELETE")
        logger.warning(
            "WAL not available on %s (filesystem may be NFS/SMB); "
            "falling back to journal_mode=DELETE",
            db_label,
        )
        return "delete"


class SessionDB:
    """SQLite-backed session storage (placeholder / stub).

    This is a minimal stand-in so agent code that conditionally imports
    ``SessionDB`` can still be loaded.  The full port of the SQLite
    state store with FTS5 search, schema management, and write-contention
    handling lives elsewhere.
    """

    def __init__(self, db_path: Path | None = None):
        import sqlite3

        from sidekick_constants import get_sidekick_home

        self.db_path = db_path or get_sidekick_home() / "state.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=1.0,
                isolation_level=None,
            )
            self._conn.row_factory = sqlite3.Row
            apply_wal_with_fallback(self._conn, db_label="state.db")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()
        except Exception as exc:
            _set_last_init_error(f"{type(exc).__name__}: {exc}")
            raise

    def _init_schema(self) -> None:
        """Create core tables if they don't exist."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                title TEXT DEFAULT 'Untitled',
                source TEXT DEFAULT 'unknown',
                model TEXT DEFAULT '',
                system_prompt TEXT DEFAULT '',
                created_at REAL DEFAULT (strftime('%s','now')),
                updated_at REAL DEFAULT (strftime('%s','now')),
                parent_session_id TEXT,
                message_count INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(session_id),
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                tool_calls TEXT,
                tool_call_id TEXT,
                model TEXT,
                timestamp REAL DEFAULT (strftime('%s','now')),
                token_count INTEGER DEFAULT 0,
                reasoning_content TEXT
            );
            """
        )

    # ------------------------------------------------------------------
    # Minimal public API — matches what agent modules call
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        cursor = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def create_session(
        self,
        session_id: str,
        *,
        title: str = "Untitled",
        source: str = "unknown",
        model: str = "",
        system_prompt: str = "",
    ) -> dict[str, Any]:
        import time

        now = time.time()
        self._conn.execute(
            """INSERT OR IGNORE INTO sessions
               (session_id, title, source, model, system_prompt, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, title, source, model, system_prompt, now, now),
        )
        return {
            "session_id": session_id,
            "title": title,
            "source": source,
            "model": model,
            "system_prompt": system_prompt,
            "created_at": now,
            "updated_at": now,
        }

    def update_title(self, session_id: str, title: str) -> None:
        import time

        self._conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ?",
            (title, time.time(), session_id),
        )

    def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        if source:
            cursor = self._conn.execute(
                "SELECT * FROM sessions WHERE source = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (source, limit, offset),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


__all__ = [
    "SessionDB",
    "apply_wal_with_fallback",
    "format_session_db_unavailable",
    "get_last_init_error",
]
