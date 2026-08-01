"""Durable, redacted local telemetry for delegated subagents.

The store intentionally has no dependency on the WebUI.  Its database path is
resolved when a store is created, which keeps profile switches isolated via the
active ``SIDEKICK_HOME`` environment value.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional


MAX_EVENTS_PER_RUN = 200
MAX_RUNS = 1000
MAX_AGE_SECONDS = 90 * 24 * 60 * 60
LIVE_STATUSES = frozenset({"queued", "running", "waiting"})
VALID_STATUSES = frozenset(
    {"queued", "running", "waiting", "paused", "completed", "failed", "interrupted", "abandoned"}
)

_WINDOWS_PATH = re.compile(r"(?<![\w])(?:[A-Za-z]:\\(?:[^\s\"'<>|]+\\?)+[^\s\"'<>|]*)")
_POSIX_PATH = re.compile(r"(?<![\w])/(?:[^\s\"'<>]+/)+[^\s\"'<>]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret|credential)\s*([:=])\s*[^\s,;]+"
)
_KNOWN_SECRET = re.compile(r"\b(?:sk|rk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9._-]+\b", re.I)


def redact_text(value: Any, *, limit: int = 500) -> str:
    """Return a bounded summary that cannot retain common credentials or paths."""
    text = "" if value is None else str(value)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _ASSIGNMENT_SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = _KNOWN_SECRET.sub("[REDACTED]", text)
    text = _WINDOWS_PATH.sub("[PATH]", text)
    text = _POSIX_PATH.sub("[PATH]", text)
    text = " ".join(text.split())
    return text[:limit]


class SubagentStore:
    """Small SQLite run/event store, safe to use as best-effort telemetry."""

    def __init__(self, home: Optional[Path | str] = None) -> None:
        self.home = Path(home or os.environ.get("SIDEKICK_HOME") or (Path.home() / ".sidekick"))
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "subagents.db"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS subagent_runs (
                    subagent_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    space_slug TEXT,
                    goal_summary TEXT NOT NULL,
                    role TEXT,
                    model TEXT,
                    status TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    heartbeat_at REAL,
                    tool_count INTEGER NOT NULL DEFAULT 0,
                    last_step TEXT,
                    summary TEXT,
                    error_reason TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_subagent_runs_session ON subagent_runs(session_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS subagent_events (
                    subagent_id TEXT NOT NULL REFERENCES subagent_runs(subagent_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    occurred_at REAL NOT NULL,
                    kind TEXT NOT NULL,
                    detail TEXT,
                    PRIMARY KEY (subagent_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_subagent_events_run ON subagent_events(subagent_id, sequence);
                """
            )
        self.reconcile_stale_runs()
        self.prune()

    def record_run(
        self,
        *,
        subagent_id: str,
        session_id: Optional[str],
        space_slug: Optional[str],
        goal: Any,
        role: Optional[str],
        model: Optional[str],
        status: str = "queued",
        started_at: Optional[float] = None,
        heartbeat_at: Optional[float] = None,
    ) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Unsupported subagent status: {status}")
        now = time.time()
        started = float(started_at if started_at is not None else now)
        heartbeat = float(heartbeat_at if heartbeat_at is not None else started)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subagent_runs (
                    subagent_id, session_id, space_slug, goal_summary, role, model,
                    status, started_at, heartbeat_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subagent_id) DO UPDATE SET
                    session_id=excluded.session_id, space_slug=excluded.space_slug,
                    goal_summary=excluded.goal_summary, role=excluded.role, model=excluded.model,
                    status=excluded.status, heartbeat_at=excluded.heartbeat_at, updated_at=excluded.updated_at
                """,
                (
                    subagent_id, session_id, space_slug, redact_text(goal), redact_text(role, limit=80),
                    redact_text(model, limit=160), status, started, heartbeat, now, now,
                ),
            )
        self.prune()

    def update_run(
        self,
        subagent_id: str,
        *,
        status: Optional[str] = None,
        heartbeat_at: Optional[float] = None,
        tool_count: Optional[int] = None,
        last_step: Any = None,
        summary: Any = None,
        error_reason: Any = None,
        finished_at: Optional[float] = None,
    ) -> None:
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"Unsupported subagent status: {status}")
        fields: list[str] = ["updated_at=?"]
        values: list[Any] = [time.time()]
        for column, value in (("status", status), ("heartbeat_at", heartbeat_at), ("tool_count", tool_count), ("finished_at", finished_at)):
            if value is not None:
                fields.append(f"{column}=?")
                values.append(value)
        for column, value in (("last_step", last_step), ("summary", summary), ("error_reason", error_reason)):
            if value is not None:
                fields.append(f"{column}=?")
                values.append(redact_text(value))
        if status in {"completed", "failed", "interrupted", "abandoned"} and finished_at is None:
            fields.append("finished_at=?")
            values.append(time.time())
        values.append(subagent_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE subagent_runs SET {', '.join(fields)} WHERE subagent_id=?", values)

    def append_event(
        self,
        subagent_id: str,
        sequence: int,
        kind: str,
        detail: Any = "",
        *,
        occurred_at: Optional[float] = None,
    ) -> bool:
        with self._connect() as conn:
            inserted = conn.execute(
                "INSERT OR IGNORE INTO subagent_events (subagent_id, sequence, occurred_at, kind, detail) VALUES (?, ?, ?, ?, ?)",
                (subagent_id, int(sequence), float(occurred_at or time.time()), redact_text(kind, limit=80), redact_text(detail)),
            ).rowcount == 1
            if inserted:
                conn.execute(
                    """
                    DELETE FROM subagent_events
                    WHERE subagent_id=? AND sequence IN (
                      SELECT sequence FROM subagent_events WHERE subagent_id=?
                      ORDER BY sequence DESC LIMIT -1 OFFSET ?
                    )
                    """,
                    (subagent_id, subagent_id, MAX_EVENTS_PER_RUN),
                )
            return inserted

    def reconcile_stale_runs(self, max_age_seconds: float = 300) -> int:
        cutoff = time.time() - max_age_seconds
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT subagent_id FROM subagent_runs WHERE status IN ('queued', 'running', 'waiting') AND COALESCE(heartbeat_at, started_at) < ?",
                (cutoff,),
            ).fetchall()
            for row in rows:
                sid = row["subagent_id"]
                conn.execute(
                    "UPDATE subagent_runs SET status='abandoned', error_reason='server_restart', finished_at=?, updated_at=? WHERE subagent_id=?",
                    (time.time(), time.time(), sid),
                )
                next_sequence = conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM subagent_events WHERE subagent_id=?", (sid,)
                ).fetchone()[0]
                conn.execute(
                    "INSERT OR IGNORE INTO subagent_events (subagent_id, sequence, occurred_at, kind, detail) VALUES (?, ?, ?, 'abandoned', 'server_restart')",
                    (sid, next_sequence, time.time()),
                )
            return len(rows)

    def prune(self) -> None:
        cutoff = time.time() - MAX_AGE_SECONDS
        with self._connect() as conn:
            conn.execute("DELETE FROM subagent_runs WHERE started_at < ?", (cutoff,))
            conn.execute(
                """
                DELETE FROM subagent_runs WHERE subagent_id IN (
                    SELECT subagent_id FROM subagent_runs
                    ORDER BY started_at DESC, created_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (MAX_RUNS,),
            )

    def get_run(self, subagent_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM subagent_runs WHERE subagent_id=?", (subagent_id,)).fetchone()
        return dict(row) if row else None

    def list_events(self, subagent_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM subagent_events WHERE subagent_id=? ORDER BY sequence", (subagent_id,)).fetchall()
        return [dict(row) for row in rows]

    def count_runs(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM subagent_runs").fetchone()[0])


def get_subagent_store() -> SubagentStore:
    """Create a store for the profile that is active at this call site."""
    return SubagentStore()
