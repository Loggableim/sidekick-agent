"""
Sidekick — Structured Error Logger.

Catches, stores, and exposes frontend and backend errors via a SQLite database.
Sidekick can query errors via API to debug WebUI issues without needing the browser's
DevTools console.

Schema
------
- id:          INTEGER PRIMARY KEY AUTOINCREMENT
- timestamp:   ISO-8601 timestamp (UTC)
- type:        'js_error' | 'unhandled_promise' | 'api_error' | 'console_error'
               | 'caught_exception'
- message:     Error message text
- stack:       Stack trace (if available)
- url:         Source URL / filename where the error occurred
- line:        Line number (int)
- col:         Column number (int)
- user_agent:  Browser user-agent string
- path:        The window.location.pathname when the error happened
- method:      HTTP method for API errors
- status:      HTTP status for API errors
- body:        Response body snippet for API errors
- meta:        JSON blob for additional structured data
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

import web.api.config as _cfg

logger = logging.getLogger(__name__)

# ── Database path ──────────────────────────────────────────────────────────────
def _state_dir() -> Path:
    return Path(_cfg.STATE_DIR).expanduser().resolve()


def _db_dir() -> Path:
    return _state_dir() / "logs"


def _db_path() -> Path:
    return _db_dir() / "errors.db"


DB_DIR = _db_dir()
DB_PATH = _db_path()

# ── Connection management (thread-safe) ───────────────────────────────────────
_local = threading.local()

def _get_conn() -> sqlite3.Connection:
    """Get a thread-local database connection."""
    global DB_DIR, DB_PATH
    current_path = _db_path()
    cached_path = getattr(_local, "conn_path", None)
    if not hasattr(_local, "conn") or _local.conn is None or cached_path != str(current_path):
        old_conn = getattr(_local, "conn", None)
        if old_conn is not None:
            try:
                old_conn.close()
            except Exception:
                pass
        current_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(current_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _init_schema(conn)
        _local.conn = conn
        _local.conn_path = str(current_path)
        DB_DIR = current_path.parent
        DB_PATH = current_path
    return _local.conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS webui_errors (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            type        TEXT NOT NULL DEFAULT 'js_error',
            message     TEXT NOT NULL DEFAULT '',
            stack       TEXT NOT NULL DEFAULT '',
            url         TEXT NOT NULL DEFAULT '',
            line        INTEGER NOT NULL DEFAULT 0,
            col         INTEGER NOT NULL DEFAULT 0,
            user_agent  TEXT NOT NULL DEFAULT '',
            path        TEXT NOT NULL DEFAULT '',
            method      TEXT NOT NULL DEFAULT '',
            status      INTEGER NOT NULL DEFAULT 0,
            body        TEXT NOT NULL DEFAULT '',
            meta        TEXT NOT NULL DEFAULT '{}'
        )
    """)
    # Index on timestamp for efficient time-range queries
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_webui_errors_ts
        ON webui_errors(timestamp)
    """)
    # Index on error type for filtering
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_webui_errors_type
        ON webui_errors(type)
    """)
    conn.commit()


# ── Public API ────────────────────────────────────────────────────────────────

def log_error(
    *,
    type: str = "js_error",
    message: str = "",
    stack: str = "",
    url: str = "",
    line: int = 0,
    col: int = 0,
    user_agent: str = "",
    path: str = "",
    method: str = "",
    status: int = 0,
    body: str = "",
    meta: dict = None,
) -> int:
    """Insert an error record. Returns the new row ID.

    All string params are truncated to 4096 chars to keep the DB lean.
    ``meta`` is JSON-serialized automatically.
    """
    try:
        conn = _get_conn()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        meta_json = json.dumps(meta or {}, ensure_ascii=False)[:4096]

        cur = conn.execute(
            """INSERT INTO webui_errors
               (timestamp, type, message, stack, url, line, col,
                user_agent, path, method, status, body, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now,
                str(type)[:64],
                str(message)[:4096],
                str(stack)[:4096],
                str(url)[:1024],
                int(line or 0),
                int(col or 0),
                str(user_agent)[:512],
                str(path)[:1024],
                str(method)[:16],
                int(status or 0),
                str(body)[:4096],
                meta_json,
            ),
        )
        conn.commit()
        row_id = cur.lastrowid
        logger.debug("Error logged: #%s [%s] %s", row_id, type, message[:80])
        return row_id
    except Exception as exc:
        logger.exception("Failed to log error to DB: %s", exc)
        return 0


def get_errors(
    limit: int = 50,
    offset: int = 0,
    type_filter: str = None,
    since: str = None,
    until: str = None,
) -> list[dict]:
    """Fetch error records with optional filtering and pagination.

    Returns a list of dicts ordered by newest-first.
    """
    try:
        conn = _get_conn()
        type_filter = type_filter or None
        since = since or None
        until = until or None

        rows = conn.execute(
            """SELECT * FROM webui_errors
               WHERE (? IS NULL OR type = ?)
                 AND (? IS NULL OR timestamp >= ?)
                 AND (? IS NULL OR timestamp <= ?)
               ORDER BY id DESC
               LIMIT ? OFFSET ?""",
            (
                type_filter,
                type_filter,
                since,
                since,
                until,
                until,
                int(limit),
                int(offset),
            ),
        ).fetchall()

        return [dict(r) for r in rows]
    except Exception as exc:
        logger.exception("Failed to fetch errors: %s", exc)
        return []


def get_error_stats() -> dict:
    """Return aggregate error statistics."""
    try:
        conn = _get_conn()

        total = conn.execute("SELECT COUNT(*) as c FROM webui_errors").fetchone()["c"]

        by_type = {}
        for row in conn.execute(
            "SELECT type, COUNT(*) as c FROM webui_errors GROUP BY type ORDER BY c DESC"
        ).fetchall():
            by_type[row["type"]] = row["c"]

        # Last 24h
        yesterday = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400)
        )
        last_24h = conn.execute(
            "SELECT COUNT(*) as c FROM webui_errors WHERE timestamp >= ?",
            (yesterday,),
        ).fetchone()["c"]

        # Most recent error timestamp
        latest_row = conn.execute(
            "SELECT timestamp FROM webui_errors ORDER BY id DESC LIMIT 1"
        ).fetchone()
        latest = latest_row["timestamp"] if latest_row else None

        return {
            "total": total,
            "by_type": by_type,
            "last_24h": last_24h,
            "latest_timestamp": latest,
        }
    except Exception as exc:
        logger.exception("Failed to get error stats: %s", exc)
        return {"total": 0, "by_type": {}, "last_24h": 0, "latest_timestamp": None}


def clear_errors(before: str = None) -> int:
    """Delete errors. If *before* is given, deletes only entries older than that
    ISO timestamp. Returns the number of deleted rows. Pass no args to delete all.
    """
    try:
        conn = _get_conn()
        if before:
            cur = conn.execute(
                "DELETE FROM webui_errors WHERE timestamp < ?", (before,)
            )
        else:
            cur = conn.execute("DELETE FROM webui_errors")
        conn.commit()
        deleted = cur.rowcount
        logger.info("Cleared %s error records", deleted)
        return deleted
    except Exception as exc:
        logger.exception("Failed to clear errors: %s", exc)
        return 0


def get_db_path() -> str:
    """Return the absolute path to the error database for diagnostic use."""
    return str(_db_path().resolve())
