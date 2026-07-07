"""
Compat shim — placeholder for ``sidekick_state`` that defines the minimal
interface agent modules actually import.

Agent modules import these names from ``sidekick_state`` (found via grep):
  - SessionDB                     — the main SQLite session store class
  - format_session_db_unavailable  — user-facing error formatting
  - apply_wal_with_fallback        — WAL-journal fallback helper
  - SQL_STATE_EXISTS               — sentinel indicating state table initialised

This shim provides stub/no-op implementations so that code can be imported
without crashing.  The actual port of the SQLite state store lives elsewhere.
"""

from __future__ import annotations

import logging
import json
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

# Sentinel constant used by cron/gateway to indicate the SQL state table
# structure has been initialised.  Always ``True`` for the in-memory shim.
SQL_STATE_EXISTS = True


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

            CREATE TABLE IF NOT EXISTS state_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
            """
        )
        self._repair_missing_parent_session_refs()

    def _repair_missing_parent_session_refs(self) -> int:
        """Drop parent links that point at sessions no longer present."""
        columns = self._table_columns("sessions")
        if "parent_session_id" not in columns:
            return 0
        pk_col = self._session_pk_column()
        if pk_col == "session_id":
            cursor = self._conn.execute(
                """
                UPDATE sessions
                   SET parent_session_id = NULL
                 WHERE parent_session_id IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1
                         FROM sessions parent
                        WHERE parent.session_id = sessions.parent_session_id
                   )
                """
            )
        else:
            cursor = self._conn.execute(
                """
                UPDATE sessions
                   SET parent_session_id = NULL
                 WHERE parent_session_id IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1
                         FROM sessions parent
                        WHERE parent.id = sessions.parent_session_id
                   )
                """
            )
        return int(cursor.rowcount or 0)

    # ------------------------------------------------------------------
    # Minimal public API — matches what agent modules call
    # ------------------------------------------------------------------

    def _table_columns(self, table: str) -> set[str]:
        try:
            rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        except Exception:
            return set()
        return {str(row["name"]) for row in rows}

    def _session_pk_column(self) -> str:
        columns = self._table_columns("sessions")
        if "session_id" in columns:
            return "session_id"
        if "id" in columns:
            return "id"
        return "session_id"

    def _normalize_session_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Expose a stable session_id key for legacy state.db schemas."""
        if "session_id" not in row and row.get("id"):
            row["session_id"] = row["id"]
        if "id" not in row and row.get("session_id"):
            row["id"] = row["session_id"]
        return row

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        pk_col = self._session_pk_column()
        cursor = self._conn.execute(
            f"SELECT * FROM sessions WHERE {pk_col} = ?",  # noqa: S608 - pk_col is session_id/id only.
            (session_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._normalize_session_row(dict(row))

    def resolve_session_id(self, session_id: str) -> str | None:
        if not session_id:
            return None
        pk_col = self._session_pk_column()
        row = self._conn.execute(
            f"SELECT {pk_col} FROM sessions WHERE {pk_col} = ?",  # noqa: S608 - pk_col is session_id/id only.
            (session_id,),
        ).fetchone()
        if row is not None:
            return str(row[pk_col])
        columns = self._table_columns("sessions")
        alt_col = "id" if pk_col == "session_id" and "id" in columns else None
        if alt_col:
            row = self._conn.execute(
                f"SELECT {pk_col} FROM sessions WHERE {alt_col} = ?",  # noqa: S608 - identifiers are hardcoded.
                (session_id,),
            ).fetchone()
            if row is not None:
                return str(row[pk_col])
        return None

    def create_session(
        self,
        session_id: str,
        *,
        title: str = "Untitled",
        source: str = "unknown",
        model: str = "",
        system_prompt: str = "",
        model_config: Any = None,
        user_id: str | None = None,
        parent_session_id: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        import sqlite3
        import time

        now = time.time()
        columns = self._table_columns("sessions")
        pk_col = "session_id" if "session_id" in columns else "id"
        metadata = dict(extra)
        if model_config is not None:
            metadata["model_config"] = model_config
        if user_id is not None:
            metadata["user_id"] = user_id

        effective_parent = parent_session_id
        if effective_parent and "parent_session_id" in columns:
            # Legacy DBs may enforce a self-FK on parent_session_id. If the
            # parent row is missing, INSERT OR IGNORE silently skips the child
            # and later message writes fail their session FK. Drop only the
            # broken parent link; keep the session itself.
            if self.resolve_session_id(effective_parent) is None:
                effective_parent = None

        def _insert(parent_value: str | None, title_value: str | None) -> None:
            insert_cols = [pk_col]
            values: list[Any] = [session_id]
            for col, value in (
                ("title", title_value),
                ("source", source or "unknown"),
                ("model", model),
                ("system_prompt", system_prompt),
                ("user_id", user_id),
                ("parent_session_id", parent_value),
                ("metadata", json.dumps(metadata, ensure_ascii=False) if metadata else "{}"),
                ("created_at", now),
                ("started_at", now),
                ("updated_at", now),
            ):
                if col in columns:
                    insert_cols.append(col)
                    values.append(value)
            placeholders = ", ".join("?" for _ in insert_cols)
            self._conn.execute(
                f"INSERT OR IGNORE INTO sessions ({', '.join(insert_cols)}) VALUES ({placeholders})",  # noqa: S608 - insert_cols comes from hardcoded candidates intersected with PRAGMA columns.
                values,
            )

        parent_candidates = [effective_parent]
        if effective_parent is not None:
            parent_candidates.append(None)
        title_candidates: list[str | None] = [title]
        if "title" in columns and title is not None:
            title_candidates.append(None)

        stored_row: dict[str, Any] | None = None
        stored_parent = effective_parent
        for parent_value in parent_candidates:
            for title_value in title_candidates:
                _insert(parent_value, title_value)
                stored_row = self.get_session(session_id)
                if stored_row is not None:
                    stored_parent = parent_value
                    break
            if stored_row is not None:
                break

        if stored_row is None:
            raise sqlite3.IntegrityError(f"session row was not created: {session_id}")

        return {
            "session_id": session_id,
            "title": stored_row.get("title", title),
            "source": stored_row.get("source", source),
            "model": stored_row.get("model", model),
            "system_prompt": stored_row.get("system_prompt", system_prompt),
            "user_id": stored_row.get("user_id", user_id),
            "parent_session_id": stored_row.get("parent_session_id", stored_parent),
            "metadata": metadata,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def sanitize_title(title: str) -> str:
        value = " ".join(str(title or "").replace("\x00", "").split())
        if not value:
            return ""
        return value[:120]

    def set_session_title(self, session_id: str, title: str) -> None:
        sanitized = self.sanitize_title(title)
        if not sanitized:
            raise ValueError("title is empty")
        self.update_title(session_id, sanitized)

    def update_title(self, session_id: str, title: str) -> None:
        import time

        columns = self._table_columns("sessions")
        pk_col = "session_id" if "session_id" in columns else "id"
        set_clause = "title = ?"
        values: list[Any] = [title]
        if "updated_at" in columns:
            set_clause += ", updated_at = ?"
            values.append(time.time())
        values.append(session_id)
        self._conn.execute(
            f"UPDATE sessions SET {set_clause} WHERE {pk_col} = ?",  # noqa: S608 - set_clause/pk_col are built from hardcoded schema branches.
            values,
        )

    def update_system_prompt(self, session_id: str, system_prompt: str) -> None:
        import time

        columns = self._table_columns("sessions")
        if "system_prompt" not in columns:
            return
        pk_col = "session_id" if "session_id" in columns else "id"
        set_clause = "system_prompt = ?"
        values: list[Any] = [system_prompt or ""]
        if "updated_at" in columns:
            set_clause += ", updated_at = ?"
            values.append(time.time())
        values.append(session_id)
        self._conn.execute(f"UPDATE sessions SET {set_clause} WHERE {pk_col} = ?", values)  # noqa: S608 - set_clause/pk_col are built from hardcoded schema branches.

    def end_session(self, session_id: str, status: str = "ended") -> None:
        import time

        columns = self._table_columns("sessions")
        pk_col = "session_id" if "session_id" in columns else "id"
        updates: list[str] = []
        values: list[Any] = []
        if "status" in columns:
            updates.append("status = ?")
            values.append(status)
        if "ended_at" in columns:
            updates.append("ended_at = ?")
            values.append(time.time())
        if "updated_at" in columns:
            updates.append("updated_at = ?")
            values.append(time.time())
        if not updates:
            return
        values.append(session_id)
        self._conn.execute(
            f"UPDATE sessions SET {', '.join(updates)} WHERE {pk_col} = ?",  # noqa: S608 - updates/pk_col are hardcoded schema branches.
            values,
        )

    def append_message(
        self,
        session_id: str,
        role: str,
        content: Any = "",
        *,
        tool_name: str | None = None,
        tool_calls: Any = None,
        tool_call_id: str | None = None,
        finish_reason: str | None = None,
        reasoning: Any = None,
        reasoning_content: Any = None,
        reasoning_details: Any = None,
        codex_reasoning_items: Any = None,
        codex_message_items: Any = None,
        model: str | None = None,
        token_count: int | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        import time

        now = time.time()
        if session_id and self.get_session(session_id) is None:
            self.create_session(
                session_id=session_id,
                title="Untitled",
                source=str(extra.pop("source", "unknown") or "unknown"),
                model=model or "",
            )
        columns = self._table_columns("messages")
        session_col = "session_id" if "session_id" in columns else "sid"

        def _json_or_none(value: Any) -> str | None:
            if value is None:
                return None
            if isinstance(value, str):
                return value
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)

        if content is None:
            content_text = ""
        elif isinstance(content, str):
            content_text = content
        else:
            content_text = _json_or_none(content) or ""

        candidates: dict[str, Any] = {
            session_col: session_id,
            "role": role,
            "content": content_text,
            "tool_name": tool_name,
            "tool_calls": _json_or_none(tool_calls),
            "tool_call_id": tool_call_id,
            "finish_reason": finish_reason,
            "reasoning": _json_or_none(reasoning),
            "reasoning_content": _json_or_none(reasoning_content),
            "reasoning_details": _json_or_none(reasoning_details),
            "codex_reasoning_items": _json_or_none(codex_reasoning_items),
            "codex_message_items": _json_or_none(codex_message_items),
            "model": model,
            "timestamp": now,
            "created_at": now,
            "token_count": token_count or 0,
            "metadata": _json_or_none(extra) if extra else None,
        }
        insert_cols = [col for col, value in candidates.items() if col in columns and value is not None]
        values = [candidates[col] for col in insert_cols]
        if insert_cols:
            placeholders = ", ".join("?" for _ in insert_cols)
            self._conn.execute(
                f"INSERT INTO messages ({', '.join(insert_cols)}) VALUES ({placeholders})",  # noqa: S608 - insert_cols comes from hardcoded candidates intersected with PRAGMA columns.
                values,
            )

        session_columns = self._table_columns("sessions")
        pk_col = "session_id" if "session_id" in session_columns else "id"
        updates: list[str] = []
        update_values: list[Any] = []
        if "message_count" in session_columns:
            updates.append("message_count = COALESCE(message_count, 0) + 1")
        if "updated_at" in session_columns:
            updates.append("updated_at = ?")
            update_values.append(now)
        if updates:
            update_values.append(session_id)
            self._conn.execute(
                f"UPDATE sessions SET {', '.join(updates)} WHERE {pk_col} = ?",  # noqa: S608 - updates/pk_col are hardcoded schema branches.
                update_values,
            )

        return {"session_id": session_id, "role": role, "content": content_text}

    def get_messages(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        columns = self._table_columns("messages")
        session_col = "session_id" if "session_id" in columns else "sid"
        if "timestamp" in columns:
            order_cols = "timestamp ASC, id ASC"
        elif "created_at" in columns:
            order_cols = "created_at ASC, id ASC"
        else:
            order_cols = "id ASC"
        sql = f"SELECT * FROM messages WHERE {session_col} = ? ORDER BY {order_cols}"  # noqa: S608 - session_col/order_cols are hardcoded schema branches.
        params: list[Any] = [session_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = [dict(row) for row in self._conn.execute(sql, params).fetchall()]
        for row in rows:
            if session_col != "session_id" and session_col in row:
                row["session_id"] = row[session_col]
        return rows

    def get_meta(self, key: str) -> str | None:
        if not key:
            return None
        row = self._conn.execute(
            "SELECT value FROM state_meta WHERE key = ?",
            (str(key),),
        ).fetchone()
        if row is None:
            return None
        value = row[0]
        return None if value is None else str(value)

    def set_meta(self, key: str, value: Any) -> None:
        if not key:
            return
        text = "" if value is None else str(value)
        self._conn.execute(
            "INSERT OR REPLACE INTO state_meta (key, value) VALUES (?, ?)",
            (str(key), text),
        )

    def delete_session(self, session_id: str) -> bool:
        sid = self.resolve_session_id(session_id)
        if not sid:
            return False
        session_columns = self._table_columns("sessions")
        pk_col = "session_id" if "session_id" in session_columns else "id"
        message_columns = self._table_columns("messages")
        message_session_col = "session_id" if "session_id" in message_columns else "sid"
        self._conn.execute(f"DELETE FROM messages WHERE {message_session_col} = ?", (sid,))  # noqa: S608 - message_session_col is session_id/sid only.
        cur = self._conn.execute(f"DELETE FROM sessions WHERE {pk_col} = ?", (sid,))  # noqa: S608 - pk_col is session_id/id only.
        return cur.rowcount > 0

    def get_next_title_in_lineage(self, title: str) -> str:
        base = self.sanitize_title(title) or "Session"
        return f"{base} (continued)"

    def _is_internal_title_text(self, text: str) -> bool:
        normalized = " ".join(str(text or "").strip().split()).lower()
        if not normalized:
            return True
        return (
            normalized.startswith("[important:")
            or normalized.startswith("[system:")
            or normalized.startswith("important:")
            or "you are running as a scheduled cron job" in normalized
            or "the user has invoked the" in normalized
        )

    def _derive_title_from_messages(self, session_id: str) -> str | None:
        """Best-effort fallback title from the first user message.

        Legacy Hermes/Sidekick state.db rows often have NULL session titles.
        The WebUI should still show something meaningful instead of ``Untitled``
        when message history is present.
        """
        try:
            rows = self._conn.execute(
                """
                SELECT content
                FROM messages
                WHERE session_id = ? AND role = 'user'
                ORDER BY timestamp ASC, id ASC
                LIMIT 10
                """,
                (session_id,),
            ).fetchall()
        except Exception:
            return None
        if not rows:
            return None

        for row in rows:
            content = row["content"]
            if not isinstance(content, str):
                continue
            text = content.strip()
            if text.startswith("[") or text.startswith("{"):
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
                if isinstance(parsed, list):
                    text = " ".join(
                        str(part.get("text") or "").strip()
                        for part in parsed
                        if isinstance(part, dict) and part.get("type") == "text"
                    ).strip()
                elif isinstance(parsed, dict):
                    text = str(parsed.get("text") or parsed.get("content") or "").strip()
            if text and not self._is_internal_title_text(text):
                return text[:64]
        return None

    def _fallback_title(self, row: dict[str, Any]) -> str:
        source = str(row.get("source") or "").strip().lower()
        if source == "cron":
            return "Cron session"
        if source == "cli":
            return "CLI session"
        if source:
            return source.replace("_", " ").replace("-", " ").title() + " session"
        return "Session"

    def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        columns = self._table_columns("sessions")
        if "updated_at" in columns:
            order_col = "updated_at"
        elif "last_active" in columns:
            order_col = "last_active"
        elif "started_at" in columns:
            order_col = "started_at"
        elif "created_at" in columns:
            order_col = "created_at"
        else:
            order_col = self._session_pk_column()

        if source and "source" in columns:
            cursor = self._conn.execute(
                f"SELECT * FROM sessions WHERE source = ? ORDER BY {order_col} DESC LIMIT ? OFFSET ?",  # noqa: S608 - order_col is selected from hardcoded schema branches.
                (source, limit, offset),
            )
        else:
            cursor = self._conn.execute(
                f"SELECT * FROM sessions ORDER BY {order_col} DESC LIMIT ? OFFSET ?",  # noqa: S608 - order_col is selected from hardcoded schema branches.
                (limit, offset),
            )
        rows = [self._normalize_session_row(dict(row)) for row in cursor.fetchall()]
        for row in rows:
            title = str(row.get("title") or "").strip()
            if title and title.lower() not in {"untitled", "no title", "no-title"}:
                continue
            session_id = row.get("session_id") or row.get("id")
            if not session_id:
                continue
            derived_title = self._derive_title_from_messages(str(session_id))
            if derived_title:
                row["title"] = derived_title
            else:
                row["title"] = self._fallback_title(row)
        return rows

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
    "SQL_STATE_EXISTS",
    "apply_wal_with_fallback",
    "format_session_db_unavailable",
    "get_last_init_error",
]
