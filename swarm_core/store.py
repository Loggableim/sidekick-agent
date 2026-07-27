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

    def remember_memory_item(
        self,
        *,
        kind: str,
        claim_key: str,
        statement: str,
        source_refs: tuple[str, ...],
        evidence_refs: tuple[str, ...],
    ) -> tuple[dict[str, Any], bool]:
        """Persist one immutable memory statement and detect a durable conflict."""
        with self._immediate_connection() as connection:
            existing = connection.execute(
                """
                SELECT * FROM memory_items
                WHERE kind = ? AND claim_key = ? AND statement = ?
                """,
                (kind, claim_key, statement),
            ).fetchone()
            if existing is not None:
                return _row_to_memory_data(existing), False

            item_id = str(uuid4())
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO memory_items (
                    item_id, kind, claim_key, statement, source_refs_json,
                    evidence_refs_json, lifecycle, created_at, updated_at,
                    revalidated_at, lesson_opt_in, redacted_statement
                )
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL, 0, NULL)
                """,
                (
                    item_id,
                    kind,
                    claim_key,
                    statement,
                    json.dumps(list(source_refs), sort_keys=True),
                    json.dumps(list(evidence_refs), sort_keys=True),
                    _timestamp_text(now),
                    _timestamp_text(now),
                ),
            )
            row = connection.execute(
                "SELECT * FROM memory_items WHERE item_id = ?", (item_id,)
            ).fetchone()
            assert row is not None

            statements = connection.execute(
                """
                SELECT item_id FROM memory_items
                WHERE kind = ? AND claim_key = ?
                ORDER BY sequence ASC
                """,
                (kind, claim_key),
            ).fetchall()
            if len(statements) > 1:
                clarification_id = str(uuid4())
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO clarification_tasks (
                        clarification_id, kind, claim_key, status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'open', ?, ?)
                    """,
                    (
                        clarification_id,
                        kind,
                        claim_key,
                        _timestamp_text(now),
                        _timestamp_text(now),
                    ),
                )
                if cursor.rowcount == 1:
                    connection.execute(
                        """
                        INSERT INTO memory_events (
                            event_id, timestamp, event_type, payload_json
                        )
                        VALUES (?, ?, 'memory.conflict_detected', ?)
                        """,
                        (
                            str(uuid4()),
                            _timestamp_text(now),
                            json.dumps(
                                {
                                    "claim_key": claim_key,
                                    "clarification_id": clarification_id,
                                    "kind": kind,
                                    "memory_item_ids": [
                                        statement_row["item_id"]
                                        for statement_row in statements
                                    ],
                                },
                                sort_keys=True,
                            ),
                        ),
                    )
            return _row_to_memory_data(row), True

    def get_memory_item(self, item_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM memory_items WHERE item_id = ?", (item_id,)
            ).fetchone()
        return _row_to_memory_data(row) if row is not None else None

    def list_memory_items(self, *, audit: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM memory_items"
        if not audit:
            query += " WHERE lifecycle = 'active'"
        query += " ORDER BY sequence ASC"
        with self._connection() as connection:
            rows = connection.execute(query).fetchall()
        return [_row_to_memory_data(row) for row in rows]

    def update_memory_lifecycle(
        self,
        item_id: str,
        *,
        lifecycle: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_items
                SET lifecycle = ?, updated_at = ?,
                    revalidated_at = CASE
                        WHEN ? = 'active' THEN ?
                        ELSE revalidated_at
                    END
                WHERE item_id = ?
                """,
                (
                    lifecycle,
                    _timestamp_text(now),
                    lifecycle,
                    _timestamp_text(now),
                    item_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown memory item: {item_id}")
            row = connection.execute(
                "SELECT * FROM memory_items WHERE item_id = ?", (item_id,)
            ).fetchone()
        assert row is not None
        return _row_to_memory_data(row)

    def mark_memory_lesson_opt_in(
        self,
        item_id: str,
        *,
        redacted_statement: str,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_items
                SET lesson_opt_in = 1, redacted_statement = ?, updated_at = ?
                WHERE item_id = ?
                """,
                (redacted_statement, _timestamp_text(_utc_now()), item_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown memory item: {item_id}")
            row = connection.execute(
                "SELECT * FROM memory_items WHERE item_id = ?", (item_id,)
            ).fetchone()
        assert row is not None
        return _row_to_memory_data(row)

    def list_exportable_memory_lessons(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_items
                WHERE lesson_opt_in = 1
                  AND redacted_statement IS NOT NULL
                  AND TRIM(redacted_statement) != ''
                  AND redacted_statement != statement
                ORDER BY sequence ASC
                """
            ).fetchall()
        return [_row_to_memory_data(row) for row in rows]

    def list_clarification_tasks(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM clarification_tasks ORDER BY sequence ASC"
            ).fetchall()
        return [_row_to_clarification_data(row) for row in rows]

    def list_memory_events(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_events ORDER BY sequence ASC"
            ).fetchall()
        return [_row_to_memory_event_data(row) for row in rows]

    def record_reputation_result(
        self,
        *,
        role: str,
        capability: str,
        source_kind: str,
        source_ref: str,
        score: float,
    ) -> tuple[dict[str, Any], bool]:
        with self._immediate_connection() as connection:
            existing = connection.execute(
                """
                SELECT * FROM reputation_results
                WHERE role = ? AND capability = ? AND source_ref = ?
                """,
                (role, capability, source_ref),
            ).fetchone()
            if existing is not None:
                return _row_to_reputation_data(existing), False
            result_id = str(uuid4())
            created_at = _utc_now()
            connection.execute(
                """
                INSERT INTO reputation_results (
                    result_id, role, capability, source_kind, source_ref, score,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    role,
                    capability,
                    source_kind,
                    source_ref,
                    score,
                    _timestamp_text(created_at),
                ),
            )
            row = connection.execute(
                "SELECT * FROM reputation_results WHERE result_id = ?", (result_id,)
            ).fetchone()
        assert row is not None
        return _row_to_reputation_data(row), True

    def list_reputation_results(
        self,
        *,
        role: str,
        capability: str,
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reputation_results
                WHERE role = ? AND capability = ?
                ORDER BY sequence ASC
                """,
                (role, capability),
            ).fetchall()
        return [_row_to_reputation_data(row) for row in rows]

    def create_prompt_candidate(
        self,
        *,
        candidate_id: str,
        prompt_text: str,
        baseline_quality: float,
    ) -> dict[str, Any]:
        now = _utc_now()
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO prompt_candidates (
                        candidate_id, prompt_text, baseline_quality, status,
                        assessed_quality, safety_passed, assessment_refs_json,
                        human_approver_id, created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'candidate', NULL, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        candidate_id,
                        prompt_text,
                        baseline_quality,
                        _timestamp_text(now),
                        _timestamp_text(now),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM prompt_candidates WHERE candidate_id = ?",
                    (candidate_id,),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"Prompt candidate already exists: {candidate_id}"
            ) from exc
        assert row is not None
        return _row_to_prompt_candidate_data(row)

    def get_prompt_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM prompt_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        return _row_to_prompt_candidate_data(row) if row is not None else None

    def record_prompt_assessment(
        self,
        candidate_id: str,
        *,
        quality: float,
        safety_passed: bool,
        references: tuple[str, ...],
        eligible: bool,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT status FROM prompt_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(f"Unknown prompt candidate: {candidate_id}")
            if existing["status"] == "promoted":
                raise ValueError("Promoted prompt candidates cannot be reassessed")
            status = "eligible" if eligible else "candidate"
            connection.execute(
                """
                UPDATE prompt_candidates
                SET status = ?, assessed_quality = ?, safety_passed = ?,
                    assessment_refs_json = ?, updated_at = ?
                WHERE candidate_id = ?
                """,
                (
                    status,
                    quality,
                    int(safety_passed),
                    json.dumps(list(references), sort_keys=True),
                    _timestamp_text(now),
                    candidate_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM prompt_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        assert row is not None
        return _row_to_prompt_candidate_data(row)

    def approve_prompt_candidate(
        self,
        candidate_id: str,
        *,
        approver_id: str,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT human_approver_id FROM prompt_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(f"Unknown prompt candidate: {candidate_id}")
            recorded_approver = existing["human_approver_id"]
            if recorded_approver is not None and recorded_approver != approver_id:
                raise ValueError(
                    "Prompt candidate approval is already bound to another human"
                )
            connection.execute(
                """
                UPDATE prompt_candidates
                SET human_approver_id = ?, updated_at = ?
                WHERE candidate_id = ?
                """,
                (approver_id, _timestamp_text(_utc_now()), candidate_id),
            )
            row = connection.execute(
                "SELECT * FROM prompt_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        assert row is not None
        return _row_to_prompt_candidate_data(row)

    def promote_prompt_candidate(
        self, candidate_id: str
    ) -> tuple[dict[str, Any], bool]:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE prompt_candidates
                SET status = 'promoted', updated_at = ?
                WHERE candidate_id = ?
                  AND status = 'eligible'
                  AND human_approver_id IS NOT NULL
                """,
                (_timestamp_text(_utc_now()), candidate_id),
            )
            row = connection.execute(
                "SELECT * FROM prompt_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown prompt candidate: {candidate_id}")
        return _row_to_prompt_candidate_data(row), cursor.rowcount == 1

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
                CREATE TABLE IF NOT EXISTS memory_items (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    claim_key TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    source_refs_json TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    lifecycle TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revalidated_at TEXT,
                    lesson_opt_in INTEGER NOT NULL DEFAULT 0,
                    redacted_statement TEXT,
                    UNIQUE (kind, claim_key, statement)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_items_lifecycle_sequence
                    ON memory_items(lifecycle, sequence);
                CREATE INDEX IF NOT EXISTS idx_memory_items_claim
                    ON memory_items(kind, claim_key, sequence);
                CREATE TABLE IF NOT EXISTS clarification_tasks (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    clarification_id TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    claim_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (kind, claim_key)
                );
                CREATE TABLE IF NOT EXISTS memory_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reputation_results (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    result_id TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    score REAL NOT NULL CHECK (score >= 0.0 AND score <= 1.0),
                    created_at TEXT NOT NULL,
                    UNIQUE (role, capability, source_ref)
                );
                CREATE INDEX IF NOT EXISTS idx_reputation_role_capability
                    ON reputation_results(role, capability, sequence);
                CREATE TABLE IF NOT EXISTS prompt_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    prompt_text TEXT NOT NULL,
                    baseline_quality REAL NOT NULL
                        CHECK (baseline_quality >= 0.0 AND baseline_quality <= 1.0),
                    status TEXT NOT NULL,
                    assessed_quality REAL,
                    safety_passed INTEGER,
                    assessment_refs_json TEXT,
                    human_approver_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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


def _row_to_memory_data(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "item_id": row["item_id"],
        "kind": row["kind"],
        "claim_key": row["claim_key"],
        "statement": row["statement"],
        "source_refs": tuple(json.loads(row["source_refs_json"])),
        "evidence_refs": tuple(json.loads(row["evidence_refs_json"])),
        "lifecycle": row["lifecycle"],
        "created_at": datetime.fromisoformat(row["created_at"]),
        "updated_at": datetime.fromisoformat(row["updated_at"]),
        "revalidated_at": (
            datetime.fromisoformat(row["revalidated_at"])
            if row["revalidated_at"]
            else None
        ),
        "lesson_opt_in": bool(row["lesson_opt_in"]),
        "redacted_statement": row["redacted_statement"],
    }


def _row_to_clarification_data(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "clarification_id": row["clarification_id"],
        "kind": row["kind"],
        "claim_key": row["claim_key"],
        "status": row["status"],
        "created_at": datetime.fromisoformat(row["created_at"]),
        "updated_at": datetime.fromisoformat(row["updated_at"]),
    }


def _row_to_memory_event_data(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "sequence": row["sequence"],
        "timestamp": datetime.fromisoformat(row["timestamp"]),
        "event_type": row["event_type"],
        "payload": json.loads(row["payload_json"]),
    }


def _row_to_reputation_data(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "result_id": row["result_id"],
        "role": row["role"],
        "capability": row["capability"],
        "source_kind": row["source_kind"],
        "source_ref": row["source_ref"],
        "score": float(row["score"]),
        "created_at": datetime.fromisoformat(row["created_at"]),
    }


def _row_to_prompt_candidate_data(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "candidate_id": row["candidate_id"],
        "prompt_text": row["prompt_text"],
        "baseline_quality": float(row["baseline_quality"]),
        "status": row["status"],
        "assessed_quality": (
            float(row["assessed_quality"])
            if row["assessed_quality"] is not None
            else None
        ),
        "safety_passed": (
            bool(row["safety_passed"]) if row["safety_passed"] is not None else None
        ),
        "assessment_references": (
            tuple(json.loads(row["assessment_refs_json"]))
            if row["assessment_refs_json"] is not None
            else ()
        ),
        "human_approver_id": row["human_approver_id"],
        "created_at": datetime.fromisoformat(row["created_at"]),
        "updated_at": datetime.fromisoformat(row["updated_at"]),
    }
