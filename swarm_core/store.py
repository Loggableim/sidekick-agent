"""SQLite persistence for one project's Swarm runs and events."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from uuid import uuid4

from .types import SwarmEvent, SwarmRun


class ProjectSwarmStore:
    """Own durable runtime state beneath ``.swarm/runtime`` for one project."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.runtime_dir = self.project_root / ".swarm" / "runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.runtime_dir / "swarm.sqlite"
        self._ensure_schema()

    def create_run(
        self,
        run_id: str | None = None,
        *,
        status: str = "running",
        metadata: Mapping[str, Any] | None = None,
    ) -> SwarmRun:
        run_id = run_id or str(uuid4())
        now = _utc_now()
        metadata_json = json.dumps(dict(metadata or {}), sort_keys=True)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO runs (run_id, status, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, status, _timestamp_text(now), _timestamp_text(now), metadata_json),
            )
        return SwarmRun(run_id, status, now, now, dict(metadata or {}))

    def get_run(self, run_id: str) -> SwarmRun | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT run_id, status, created_at, updated_at, metadata_json
                FROM runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return _row_to_run(row) if row is not None else None

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        visibility: str = "project",
    ) -> SwarmEvent:
        event_id = str(uuid4())
        timestamp = _utc_now()
        payload_data = dict(payload or {})
        with self._connection() as connection:
            if connection.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone() is None:
                raise KeyError(f"Unknown Swarm run: {run_id}")
            cursor = connection.execute(
                """
                INSERT INTO events (event_id, timestamp, event_type, run_id, payload_json, visibility)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    _timestamp_text(timestamp),
                    event_type,
                    run_id,
                    json.dumps(payload_data, sort_keys=True),
                    visibility,
                ),
            )
            sequence = int(cursor.lastrowid)
        return SwarmEvent(
            event_id, sequence, timestamp, event_type, run_id, payload_data, visibility
        )

    def list_events(self, run_id: str) -> list[SwarmEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT sequence, event_id, timestamp, event_type, run_id, payload_json, visibility
                FROM events WHERE run_id = ? ORDER BY sequence ASC
                """,
                (run_id,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def set_run_status(self, run_id: str, status: str) -> SwarmRun:
        updated_at = _utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (status, _timestamp_text(updated_at), run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown Swarm run: {run_id}")
        run = self.get_run(run_id)
        assert run is not None
        return run

    def resume_run(self, run_id: str) -> SwarmRun:
        return self.set_run_status(run_id, "running")

    def _ensure_schema(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    payload_json TEXT NOT NULL,
                    visibility TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_run_sequence
                    ON events(run_id, sequence);
                """
            )

    def _connection(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return _ConnectionContext(connection)


class _ConnectionContext:
    """Commit successful operations and always close their SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self._connection

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        with closing(self._connection) as connection:
            if exc_type is None:
                connection.commit()
            else:
                connection.rollback()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp_text(timestamp: datetime) -> str:
    return timestamp.isoformat()


def _row_to_run(row: sqlite3.Row) -> SwarmRun:
    return SwarmRun(
        run_id=row["run_id"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        metadata=json.loads(row["metadata_json"]),
    )


def _row_to_event(row: sqlite3.Row) -> SwarmEvent:
    return SwarmEvent(
        event_id=row["event_id"],
        sequence=row["sequence"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        event_type=row["event_type"],
        run_id=row["run_id"],
        payload=json.loads(row["payload_json"]),
        visibility=row["visibility"],
    )
