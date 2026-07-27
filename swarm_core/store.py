"""SQLite persistence for one project's Swarm runs and events."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator, Mapping, TypeVar
from uuid import uuid4

from .config import initialize_project
from .types import ApprovalRecord, SwarmEvent, SwarmRun


AuthorizationResult = TypeVar("AuthorizationResult")

_RUN_STATUSES = frozenset({"running", "paused", "completed"})
_ALLOWED_TRANSITIONS = {
    "running": frozenset({"paused", "completed"}),
    "paused": frozenset({"running"}),
    "completed": frozenset(),
}


class ProjectSwarmStore:
    """Own durable runtime state beneath ``.swarm/runtime`` for one project."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        initialize_project(self.project_root)
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
        if status not in _RUN_STATUSES:
            raise ValueError(f"Unsupported Swarm run status: {status}")
        run_id = run_id or str(uuid4())
        now = _utc_now()
        metadata_data = dict(metadata or {})
        metadata_data.setdefault(
            "autonomy",
            initialize_project(self.project_root).default_autonomy,
        )
        metadata_json = json.dumps(metadata_data, sort_keys=True)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO runs (run_id, status, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    status,
                    _timestamp_text(now),
                    _timestamp_text(now),
                    metadata_json,
                ),
            )
        return SwarmRun(run_id, status, now, now, metadata_data)

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
            if (
                connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                is None
            ):
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
        if status not in _RUN_STATUSES:
            raise ValueError(f"Unsupported Swarm run status: {status}")
        updated_at = _utc_now()
        with self._immediate_connection() as connection:
            row = connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown Swarm run: {run_id}")
            current_status = row["status"]
            if status not in _ALLOWED_TRANSITIONS.get(current_status, frozenset()):
                if status == "running" and current_status != "paused":
                    raise ValueError("Only paused Swarm runs can transition to running")
                raise ValueError(
                    f"Illegal Swarm run transition: {current_status} -> {status}"
                )
            cursor = connection.execute(
                """
                UPDATE runs SET status = ?, updated_at = ?
                WHERE run_id = ? AND status = ?
                """,
                (status, _timestamp_text(updated_at), run_id, current_status),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Swarm run status changed during transition")
        run = self.get_run(run_id)
        assert run is not None
        return run

    def resume_run(self, run_id: str) -> SwarmRun:
        return self.set_run_status(run_id, "running")

    def record_approval(
        self,
        run_id: str,
        proposal_id: str,
        proposal_digest: str,
        approval_type: str,
        approver_id: str,
        *,
        approved: bool = True,
        model_family: str | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> ApprovalRecord:
        approval_id = str(uuid4())
        created_at = _utc_now()
        with self._connection() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                is None
            ):
                raise KeyError(f"Unknown Swarm run: {run_id}")
            cursor = connection.execute(
                """
                INSERT INTO approvals (
                    approval_id, run_id, proposal_id, proposal_digest,
                    approval_type, approver_id, approved, model_family,
                    evidence_refs_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    run_id,
                    proposal_id,
                    proposal_digest,
                    approval_type,
                    approver_id,
                    int(approved),
                    model_family,
                    json.dumps(list(evidence_refs), sort_keys=True),
                    _timestamp_text(created_at),
                ),
            )
            sequence = int(cursor.lastrowid)
        return ApprovalRecord(
            approval_id=approval_id,
            sequence=sequence,
            run_id=run_id,
            proposal_id=proposal_id,
            proposal_digest=proposal_digest,
            approval_type=approval_type,
            approver_id=approver_id,
            approved=approved,
            model_family=model_family,
            evidence_refs=evidence_refs,
            created_at=created_at,
        )

    def list_approvals(
        self,
        run_id: str,
        *,
        proposal_id: str | None = None,
    ) -> list[ApprovalRecord]:
        query = """
            SELECT sequence, approval_id, run_id, proposal_id, proposal_digest,
                   approval_type, approver_id, approved, model_family,
                   evidence_refs_json, created_at
            FROM approvals WHERE run_id = ?
        """
        parameters: tuple[str, ...] = (run_id,)
        if proposal_id is not None:
            query += " AND proposal_id = ?"
            parameters = (run_id, proposal_id)
        query += " ORDER BY sequence ASC"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_row_to_approval(row) for row in rows]

    def claim_execution(
        self,
        run_id: str,
        proposal_id: str,
        proposal_digest: str,
    ) -> bool:
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO action_executions (
                        run_id, proposal_id, proposal_digest, claimed_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        proposal_id,
                        proposal_digest,
                        _timestamp_text(_utc_now()),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def authorize_and_claim(
        self,
        run_id: str,
        proposal_id: str,
        proposal_digest: str,
        authorize: Callable[
            [SwarmRun | None, list[ApprovalRecord]], tuple[AuthorizationResult, bool]
        ],
    ) -> tuple[AuthorizationResult, bool]:
        """Read policy inputs and claim execution under one SQLite write lock."""
        with self._immediate_connection() as connection:
            run_row = connection.execute(
                """
                SELECT run_id, status, created_at, updated_at, metadata_json
                FROM runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            run = _row_to_run(run_row) if run_row is not None else None
            approval_rows = connection.execute(
                """
                SELECT sequence, approval_id, run_id, proposal_id, proposal_digest,
                       approval_type, approver_id, approved, model_family,
                       evidence_refs_json, created_at
                FROM approvals WHERE run_id = ? AND proposal_id = ?
                ORDER BY sequence ASC
                """,
                (run_id, proposal_id),
            ).fetchall()
            result, approved = authorize(
                run,
                [_row_to_approval(row) for row in approval_rows],
            )
            if not approved:
                return result, False
            try:
                connection.execute(
                    """
                    INSERT INTO action_executions (
                        run_id, proposal_id, proposal_digest, claimed_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        proposal_id,
                        proposal_digest,
                        _timestamp_text(_utc_now()),
                    ),
                )
            except sqlite3.IntegrityError:
                return result, False
            return result, True

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
                CREATE TABLE IF NOT EXISTS approvals (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    approval_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    proposal_id TEXT NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    approval_type TEXT NOT NULL,
                    approver_id TEXT NOT NULL,
                    approved INTEGER NOT NULL,
                    model_family TEXT,
                    evidence_refs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_approvals_run_proposal
                    ON approvals(run_id, proposal_id, sequence);
                CREATE TABLE IF NOT EXISTS action_executions (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    proposal_id TEXT NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, proposal_digest)
                );
                """
            )

    def _connection(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return _ConnectionContext(connection)

    @contextmanager
    def _immediate_connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


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


def _row_to_approval(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=row["approval_id"],
        sequence=row["sequence"],
        run_id=row["run_id"],
        proposal_id=row["proposal_id"],
        proposal_digest=row["proposal_digest"],
        approval_type=row["approval_type"],
        approver_id=row["approver_id"],
        approved=bool(row["approved"]),
        model_family=row["model_family"],
        evidence_refs=tuple(json.loads(row["evidence_refs_json"])),
        created_at=datetime.fromisoformat(row["created_at"]),
    )
