"""SQLite persistence for one project's Swarm runs and events."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Iterator, Mapping, TypeVar
from uuid import uuid4

from .config import (
    SwarmProjectPathError,
    SwarmProjectNotInitializedError,
    PinnedSwarmDatabase,
    initialize_project,
    load_project_config,
    pinned_swarm_database,
    resolve_swarm_path,
)
from .models import ModelCatalogSnapshot
from .types import (
    ApprovalRecord,
    IntegrationAdmission,
    IntegrationAdmissionRequest,
    SwarmEvent,
    SwarmRun,
    WorkflowRoleCheckpoint,
)


AuthorizationResult = TypeVar("AuthorizationResult")

_RUN_STATUSES = frozenset({"running", "paused", "completed"})
_ALLOWED_TRANSITIONS = {
    "running": frozenset({"paused", "completed"}),
    "paused": frozenset({"running"}),
    "completed": frozenset(),
}
_MAX_INTEGRATION_VALUE_DEPTH = 32
_MAX_INTEGRATION_VALUE_ITEMS = 10_000
_SCHEMA_MIGRATION_LOCK = threading.RLock()


class ProjectSwarmStore:
    """Own durable runtime state beneath ``.swarm/runtime`` for one project."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        initialize_project(self.project_root)
        self._refresh_runtime_paths()
        self._ensure_schema()

    @classmethod
    def open_read_only(cls, project_root: Path) -> "ReadOnlyProjectSwarmStore":
        """Open only already-persisted state without initializing or migrating.

        This is the sole store entry point intended for status pages, SSE, and
        other externally triggered read paths.  Keeping it a separate object
        makes an accidental write/migration API unavailable to those callers.
        """
        return ReadOnlyProjectSwarmStore(project_root)

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

    def admit_integration_run(
        self,
        request: IntegrationAdmissionRequest,
        *,
        now: datetime | None = None,
    ) -> IntegrationAdmission:
        """Atomically create one integration run or retain its bounded rejection."""
        if not isinstance(request, IntegrationAdmissionRequest):
            raise TypeError("Swarm integration admission requires a typed request")
        namespace = _validated_integration_namespace(request.namespace)
        idempotency_key = _validated_integration_idempotency_key(
            request.idempotency_key
        )

        with self._immediate_connection() as connection:
            existing = _get_integration_admission_from_connection(
                connection,
                namespace,
                idempotency_key,
            )
            if existing is not None:
                if existing.run is not None:
                    return IntegrationAdmission(
                        status="coalesced",
                        namespace=existing.namespace,
                        idempotency_key=existing.idempotency_key,
                        run=existing.run,
                        reason=None,
                    )
                return IntegrationAdmission(
                    status=existing.reason or "rejected",
                    namespace=existing.namespace,
                    idempotency_key=existing.idempotency_key,
                    run=None,
                    reason=existing.reason,
                )

            max_active, rolling_window_seconds, rolling_run_limit = (
                _validated_integration_admission_bounds(request)
            )
            metadata = _validated_integration_metadata(
                request.metadata,
                self.project_root,
            )
            timestamp = _validated_admission_timestamp(now)
            timestamp_text = _timestamp_text(timestamp)

            active_slots = {
                int(row["active_slot"])
                for row in connection.execute(
                    """
                    SELECT active_slot FROM integration_admissions
                    WHERE namespace = ? AND active_slot IS NOT NULL
                    """,
                    (namespace,),
                ).fetchall()
            }
            if len(active_slots) >= max_active:
                return _record_integration_rejection_in_connection(
                    connection,
                    namespace,
                    idempotency_key,
                    reason="active_limit",
                    timestamp=timestamp,
                )

            if rolling_run_limit is not None:
                cutoff = timestamp - _seconds_delta(rolling_window_seconds)
                rolling_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM integration_admissions
                        WHERE namespace = ?
                          AND state = 'admitted'
                          AND created_at >= ?
                        """,
                        (namespace, _timestamp_text(cutoff)),
                    ).fetchone()[0]
                )
                if rolling_count >= rolling_run_limit:
                    return _record_integration_rejection_in_connection(
                        connection,
                        namespace,
                        idempotency_key,
                        reason="rolling_limit",
                        timestamp=timestamp,
                    )

            active_slot = next(
                slot for slot in range(max_active) if slot not in active_slots
            )
            run_id = str(uuid4())
            metadata_json = json.dumps(metadata, sort_keys=True, allow_nan=False)
            connection.execute(
                """
                INSERT INTO runs (run_id, status, created_at, updated_at, metadata_json)
                VALUES (?, 'running', ?, ?, ?)
                """,
                (run_id, timestamp_text, timestamp_text, metadata_json),
            )
            connection.execute(
                """
                INSERT INTO integration_admissions (
                    namespace, idempotency_key, run_id, state, active_slot,
                    metadata_json, reason, created_at, updated_at
                ) VALUES (?, ?, ?, 'admitted', ?, ?, NULL, ?, ?)
                """,
                (
                    namespace,
                    idempotency_key,
                    run_id,
                    active_slot,
                    metadata_json,
                    timestamp_text,
                    timestamp_text,
                ),
            )
            connection.execute(
                """
                INSERT INTO events (
                    event_id, timestamp, event_type, run_id, payload_json, visibility
                ) VALUES (?, ?, 'run.started', ?, ?, 'project')
                """,
                (
                    str(uuid4()),
                    timestamp_text,
                    run_id,
                    json.dumps(
                        {"goal": metadata["goal"], "pack": metadata["pack"]},
                        sort_keys=True,
                        allow_nan=False,
                    ),
                ),
            )
        return IntegrationAdmission(
            status="created",
            namespace=namespace,
            idempotency_key=idempotency_key,
            run=SwarmRun(run_id, "running", timestamp, timestamp, metadata),
            reason=None,
        )

    def record_integration_rejection(
        self,
        namespace: str,
        idempotency_key: str,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> IntegrationAdmission:
        """Persist a bounded pre-run integration rejection without creating a run."""
        namespace = _validated_integration_namespace(namespace)
        idempotency_key = _validated_integration_idempotency_key(idempotency_key)
        reason = _validated_integration_reason(reason)
        timestamp = _validated_admission_timestamp(now)
        with self._immediate_connection() as connection:
            existing = _get_integration_admission_from_connection(
                connection,
                namespace,
                idempotency_key,
            )
            if existing is not None:
                return existing
            recorded = _record_integration_rejection_in_connection(
                connection,
                namespace,
                idempotency_key,
                reason=reason,
                timestamp=timestamp,
            )
            return IntegrationAdmission(
                status="rejected",
                namespace=recorded.namespace,
                idempotency_key=recorded.idempotency_key,
                run=None,
                reason=recorded.reason,
            )

    def get_integration_admission(
        self,
        namespace: str,
        idempotency_key: str,
    ) -> IntegrationAdmission | None:
        """Return one durable integration identity without creating new state."""
        namespace = _validated_integration_namespace(namespace)
        idempotency_key = _validated_integration_idempotency_key(idempotency_key)
        with self._connection() as connection:
            return _get_integration_admission_from_connection(
                connection,
                namespace,
                idempotency_key,
            )

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

    def list_runs(self) -> list[SwarmRun]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT run_id, status, created_at, updated_at, metadata_json
                FROM runs ORDER BY created_at DESC, run_id ASC
                """
            ).fetchall()
        return [_row_to_run(row) for row in rows]

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

    def append_event_once(
        self,
        run_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str,
        visibility: str = "project",
    ) -> tuple[SwarmEvent, bool]:
        """Atomically append one event for a stable integration-side key.

        The key is scoped to a run and event type.  A retry returns the
        original durable event and ``False`` instead of recording an
        indistinguishable second fact.
        """
        if not idempotency_key.strip():
            raise ValueError("event idempotency_key is required")

        with self._immediate_connection() as connection:
            existing = connection.execute(
                """
                SELECT events.sequence, events.event_id, events.timestamp,
                       events.event_type, events.run_id, events.payload_json,
                       events.visibility
                FROM event_idempotency_keys
                JOIN events ON events.event_id = event_idempotency_keys.event_id
                WHERE event_idempotency_keys.run_id = ?
                  AND event_idempotency_keys.event_type = ?
                  AND event_idempotency_keys.idempotency_key = ?
                """,
                (run_id, event_type, idempotency_key),
            ).fetchone()
            if existing is not None:
                return _row_to_event(existing), False

            if (
                connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                is None
            ):
                raise KeyError(f"Unknown Swarm run: {run_id}")

            event_id = str(uuid4())
            timestamp = _utc_now()
            payload_data = dict(payload or {})
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
            connection.execute(
                """
                INSERT INTO event_idempotency_keys (
                    run_id, event_type, idempotency_key, event_id
                ) VALUES (?, ?, ?, ?)
                """,
                (run_id, event_type, idempotency_key, event_id),
            )
            sequence = int(cursor.lastrowid)

        return (
            SwarmEvent(
                event_id,
                sequence,
                timestamp,
                event_type,
                run_id,
                payload_data,
                visibility,
            ),
            True,
        )

    def record_workflow_role_checkpoint(
        self,
        run_id: str,
        role: str,
        *,
        model: str | None,
        data: Mapping[str, Any],
    ) -> tuple[WorkflowRoleCheckpoint, bool]:
        """Atomically persist one completed role and its public evidence events.

        A continuation only treats a role as complete after this single SQLite
        transaction commits.  It therefore never replays a durable Scout,
        Planner, Builder, Critic, Reviewer, Integrator, or Verifier result
        merely because the Sidekick process exited between stages.
        """
        role = str(role).strip()
        if not role:
            raise ValueError("Workflow checkpoint role must be non-empty")
        if model is not None:
            model = str(model).strip() or None
        checkpoint_data = _validated_workflow_checkpoint_data(data)
        timestamp = _utc_now()

        with self._immediate_connection() as connection:
            existing = connection.execute(
                """
                SELECT run_id, role, model, data_json, completed_at
                FROM workflow_role_checkpoints
                WHERE run_id = ? AND role = ?
                """,
                (run_id, role),
            ).fetchone()
            if existing is not None:
                return _row_to_workflow_role_checkpoint(existing), False
            if (
                connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                is None
            ):
                raise KeyError(f"Unknown Swarm run: {run_id}")

            connection.execute(
                """
                INSERT INTO workflow_role_checkpoints (
                    run_id, role, model, data_json, completed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    role,
                    model,
                    json.dumps(checkpoint_data, sort_keys=True, allow_nan=False),
                    _timestamp_text(timestamp),
                ),
            )
            work_payload: dict[str, Any] = {
                "role": role,
                "work": checkpoint_data["work"],
            }
            if model is not None:
                work_payload["model"] = model
            for event_type, payload in (
                ("work.completed", work_payload),
                (
                    "evidence.recorded",
                    {"role": role, "evidence": checkpoint_data["evidence"]},
                ),
                (
                    "decision.recorded",
                    {"role": role, "decision": checkpoint_data["decision"]},
                ),
            ):
                connection.execute(
                    """
                    INSERT INTO events (
                        event_id, timestamp, event_type, run_id, payload_json, visibility
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        _timestamp_text(timestamp),
                        event_type,
                        run_id,
                        json.dumps(payload, sort_keys=True, allow_nan=False),
                        "project",
                    ),
                )

        return (
            WorkflowRoleCheckpoint(
                run_id=run_id,
                role=role,
                model=model,
                data=checkpoint_data,
                completed_at=timestamp,
            ),
            True,
        )

    def get_workflow_role_checkpoints(
        self,
        run_id: str,
    ) -> dict[str, WorkflowRoleCheckpoint]:
        """Return the validated durable role outputs for one resumable run."""
        with self._connection() as connection:
            return _workflow_role_checkpoints_from_connection(connection, run_id)

    def claim_run_execution_lease(self, run_id: str, owner_token: str) -> bool:
        """Atomically reserve one run for a single executing host.

        Leases deliberately have no expiry or takeover path.  A process that
        dies without its ``finally`` cleanup leaves the row behind, so a new
        host fails closed rather than replaying an action-producing workflow
        that might still be running elsewhere.
        """
        owner_token = str(owner_token).strip()
        if not owner_token:
            raise ValueError("Swarm execution lease owner_token is required")
        with self._immediate_connection() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                is None
            ):
                raise KeyError(f"Unknown Swarm run: {run_id}")
            try:
                connection.execute(
                    """
                    INSERT INTO run_execution_leases (run_id, owner_token, claimed_at)
                    VALUES (?, ?, ?)
                    """,
                    (run_id, owner_token, _timestamp_text(_utc_now())),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def release_run_execution_lease(self, run_id: str, owner_token: str) -> bool:
        """Release only the durable lease held by ``owner_token``."""
        owner_token = str(owner_token).strip()
        if not owner_token:
            raise ValueError("Swarm execution lease owner_token is required")
        with self._immediate_connection() as connection:
            cursor = connection.execute(
                """
                DELETE FROM run_execution_leases
                WHERE run_id = ? AND owner_token = ?
                """,
                (run_id, owner_token),
            )
        return cursor.rowcount == 1

    def recover_run_execution_lease(self, run_id: str, *, actor_id: str) -> SwarmRun:
        """Human-audit an abandoned lease without automatically executing it.

        This is intentionally an explicit recovery handoff, not lease expiry:
        the caller must first confirm that the prior host stopped.  The run is
        left paused so a separately authorized resume can inspect the audit
        trail and decide whether it is safe to continue.
        """
        if not isinstance(actor_id, str):
            raise TypeError("Swarm execution lease recovery actor must be a string")
        actor_id = actor_id.strip()
        if (
            not actor_id
            or len(actor_id) > 256
            or any(
                ord(character) < 32 or ord(character) == 127 for character in actor_id
            )
        ):
            raise ValueError("Swarm execution lease recovery requires an actor")
        timestamp = _utc_now()
        with self._immediate_connection() as connection:
            row = connection.execute(
                """
                SELECT run_id, status, created_at, updated_at, metadata_json
                FROM runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown Swarm run: {run_id}")
            if row["status"] == "completed":
                raise ValueError(
                    "Completed Swarm runs cannot recover an execution lease"
                )
            lease = connection.execute(
                "SELECT owner_token FROM run_execution_leases WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if lease is None:
                raise ValueError("Swarm run has no execution lease to recover")
            event_rows = connection.execute(
                """
                SELECT sequence, event_id, timestamp, event_type, run_id,
                       payload_json, visibility
                FROM events WHERE run_id = ? ORDER BY sequence ASC
                """,
                (run_id,),
            ).fetchall()
            unmatched_attempts = _unmatched_durable_model_attempts(
                [_row_to_event(event_row) for event_row in event_rows]
            )

            connection.execute(
                """
                UPDATE runs SET status = ?, updated_at = ?
                WHERE run_id = ?
                """,
                ("paused", _timestamp_text(timestamp), run_id),
            )
            connection.execute(
                "DELETE FROM run_execution_leases WHERE run_id = ?",
                (run_id,),
            )
            connection.execute(
                """
                INSERT INTO events (
                    event_id, timestamp, event_type, run_id, payload_json, visibility
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    _timestamp_text(timestamp),
                    "run.execution_lease_recovered_by_human",
                    run_id,
                    json.dumps({"actor_id": actor_id}, sort_keys=True),
                    "project",
                ),
            )
            for original_sequence, role, model in unmatched_attempts:
                connection.execute(
                    """
                    INSERT INTO events (
                        event_id, timestamp, event_type, run_id, payload_json, visibility
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        _timestamp_text(timestamp),
                        "model.attempt_replay_authorized_by_human",
                        run_id,
                        json.dumps(
                            {
                                "actor_id": actor_id,
                                "original_attempt_sequence": original_sequence,
                                "role": role,
                                "model": model,
                            },
                            sort_keys=True,
                        ),
                        "project",
                    ),
                )
            recovered = connection.execute(
                """
                SELECT run_id, status, created_at, updated_at, metadata_json
                FROM runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if recovered is None:  # pragma: no cover - protected by the transaction
            raise KeyError(f"Unknown Swarm run: {run_id}")
        return _row_to_run(recovered)

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

    def list_events_after(
        self,
        run_id: str,
        sequence: int,
        *,
        limit: int = 100,
    ) -> list[SwarmEvent]:
        return _list_events_after(self._connection, run_id, sequence, limit=limit)

    def save_model_catalog_snapshot(self, snapshot: ModelCatalogSnapshot) -> None:
        """Persist an explicitly refreshed provider catalog.

        Callers must perform health discovery before invoking this write.  No
        read/run path calls it, which prevents stale cached discovery from
        silently changing a project's routing truth.
        """
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO model_catalog_snapshots (
                    provider, models_json, healthy, source, refreshed_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    models_json = excluded.models_json,
                    healthy = excluded.healthy,
                    source = excluded.source,
                    refreshed_at = excluded.refreshed_at
                """,
                (
                    snapshot.provider,
                    json.dumps(list(snapshot.models), sort_keys=True),
                    int(snapshot.healthy),
                    snapshot.source,
                    _timestamp_text(snapshot.refreshed_at),
                ),
            )

    def get_model_catalog_snapshot(
        self,
        provider: str,
    ) -> ModelCatalogSnapshot | None:
        return _get_model_catalog_snapshot(self._connection, provider)

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
            if status == "completed":
                connection.execute(
                    """
                    UPDATE integration_admissions
                    SET active_slot = NULL, updated_at = ?
                    WHERE run_id = ? AND active_slot IS NOT NULL
                    """,
                    (_timestamp_text(updated_at), run_id),
                )
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
            [
                SwarmRun | None,
                list[ApprovalRecord],
                Mapping[str, WorkflowRoleCheckpoint],
            ],
            tuple[AuthorizationResult, bool],
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
            checkpoints = _workflow_role_checkpoints_from_connection(
                connection,
                run_id,
            )
            result, approved = authorize(
                run,
                [_row_to_approval(row) for row in approval_rows],
                checkpoints,
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
        revalidate_after: datetime | None = None,
        expires_at: datetime | None = None,
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
                existing_data = _row_to_memory_data(existing)
                merged_source_refs = _merge_references(
                    existing_data["source_refs"],
                    source_refs,
                )
                merged_evidence_refs = _merge_references(
                    existing_data["evidence_refs"],
                    evidence_refs,
                )
                merged_revalidate_after = _earliest_deadline(
                    existing_data["revalidate_after"],
                    revalidate_after,
                )
                merged_expires_at = _earliest_deadline(
                    existing_data["expires_at"],
                    expires_at,
                )
                merged_lifecycle = existing_data["lifecycle"]
                if _deadlines_require_expiry(
                    merged_revalidate_after,
                    merged_expires_at,
                ):
                    # A retry must never make a claim more usable.  Retain the
                    # contradictory evidence for audit, but fail closed rather
                    # than leaving the claim active.
                    merged_lifecycle = "expired"
                if (
                    merged_source_refs != existing_data["source_refs"]
                    or merged_evidence_refs != existing_data["evidence_refs"]
                    or merged_revalidate_after != existing_data["revalidate_after"]
                    or merged_expires_at != existing_data["expires_at"]
                    or merged_lifecycle != existing_data["lifecycle"]
                ):
                    connection.execute(
                        """
                        UPDATE memory_items
                        SET source_refs_json = ?, evidence_refs_json = ?,
                            revalidate_after = ?, expires_at = ?, lifecycle = ?,
                            updated_at = ?
                        WHERE item_id = ?
                        """,
                        (
                            json.dumps(list(merged_source_refs), sort_keys=True),
                            json.dumps(list(merged_evidence_refs), sort_keys=True),
                            _timestamp_text(merged_revalidate_after)
                            if merged_revalidate_after is not None
                            else None,
                            _timestamp_text(merged_expires_at)
                            if merged_expires_at is not None
                            else None,
                            merged_lifecycle,
                            _timestamp_text(_utc_now()),
                            existing_data["item_id"],
                        ),
                    )
                    existing = connection.execute(
                        "SELECT * FROM memory_items WHERE item_id = ?",
                        (existing_data["item_id"],),
                    ).fetchone()
                    assert existing is not None
                return _row_to_memory_data(existing), False

            item_id = str(uuid4())
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO memory_items (
                    item_id, kind, claim_key, statement, source_refs_json,
                    evidence_refs_json, lifecycle, created_at, updated_at,
                    revalidated_at, revalidate_after, expires_at, lesson_opt_in,
                    redacted_statement
                )
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL, ?, ?, 0, NULL)
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
                    (
                        _timestamp_text(revalidate_after)
                        if revalidate_after is not None
                        else None
                    ),
                    (_timestamp_text(expires_at) if expires_at is not None else None),
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

    def revalidate_memory_item(
        self,
        item_id: str,
        *,
        revalidate_after: datetime | None,
        expires_at: datetime | None,
    ) -> dict[str, Any]:
        """Explicitly restore a claim and replace its persisted deadline state."""
        now = _utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_items
                SET lifecycle = 'active', updated_at = ?, revalidated_at = ?,
                    revalidate_after = ?, expires_at = ?
                WHERE item_id = ?
                """,
                (
                    _timestamp_text(now),
                    _timestamp_text(now),
                    (
                        _timestamp_text(revalidate_after)
                        if revalidate_after is not None
                        else None
                    ),
                    (_timestamp_text(expires_at) if expires_at is not None else None),
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
                SELECT * FROM reputation_results_v2
                WHERE role = ? AND capability = ?
                  AND source_kind = ? AND source_ref = ?
                """,
                (role, capability, source_kind, source_ref),
            ).fetchone()
            if existing is not None:
                return _row_to_reputation_data(existing), False
            result_id = str(uuid4())
            created_at = _utc_now()
            connection.execute(
                """
                INSERT INTO reputation_results_v2 (
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
                "SELECT * FROM reputation_results_v2 WHERE result_id = ?",
                (result_id,),
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
                SELECT * FROM reputation_results_v2
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
                        assessment_revision, assessment_digest, human_approver_id,
                        approved_assessment_revision, approved_assessment_digest,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'candidate', NULL, NULL, NULL, 0, NULL, NULL,
                            NULL, NULL, ?, ?)
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
        assessment_digest: str,
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
            cursor = connection.execute(
                """
                UPDATE prompt_candidates
                SET status = ?, assessed_quality = ?, safety_passed = ?,
                    assessment_refs_json = ?,
                    assessment_revision = assessment_revision + 1,
                    assessment_digest = ?,
                    human_approver_id = NULL,
                    approved_assessment_revision = NULL,
                    approved_assessment_digest = NULL,
                    updated_at = ?
                WHERE candidate_id = ?
                  AND status != 'promoted'
                """,
                (
                    status,
                    quality,
                    int(safety_passed),
                    json.dumps(list(references), sort_keys=True),
                    assessment_digest,
                    _timestamp_text(now),
                    candidate_id,
                ),
            )
            if cursor.rowcount != 1:
                terminal = connection.execute(
                    "SELECT status FROM prompt_candidates WHERE candidate_id = ?",
                    (candidate_id,),
                ).fetchone()
                if terminal is None:
                    raise KeyError(f"Unknown prompt candidate: {candidate_id}")
                if terminal["status"] == "promoted":
                    raise ValueError("Promoted prompt candidates cannot be reassessed")
                raise RuntimeError("Prompt candidate changed during assessment")
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
    ) -> tuple[dict[str, Any], bool]:
        with self._immediate_connection() as connection:
            existing = connection.execute(
                "SELECT * FROM prompt_candidates WHERE candidate_id = ?",
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
                SET human_approver_id = ?,
                    approved_assessment_revision = assessment_revision,
                    approved_assessment_digest = assessment_digest,
                    updated_at = ?
                WHERE candidate_id = ?
                  AND status = 'eligible'
                  AND assessment_revision > 0
                  AND assessment_digest IS NOT NULL
                """,
                (approver_id, _timestamp_text(_utc_now()), candidate_id),
            )
            approved = connection.execute("SELECT changes()").fetchone()[0] == 1
            row = connection.execute(
                "SELECT * FROM prompt_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        assert row is not None
        return _row_to_prompt_candidate_data(row), approved

    def promote_prompt_candidate(
        self, candidate_id: str
    ) -> tuple[dict[str, Any], bool]:
        with self._immediate_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE prompt_candidates
                SET status = 'promoted', updated_at = ?
                WHERE candidate_id = ?
                  AND status = 'eligible'
                  AND human_approver_id IS NOT NULL
                  AND assessment_digest IS NOT NULL
                  AND approved_assessment_revision = assessment_revision
                  AND approved_assessment_digest = assessment_digest
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
        # The Python lock keeps same-process openers from racing their PRAGMA
        # checks.  The immediate SQLite transaction below serializes migration
        # work with other Sidekick processes using this project database.
        with _SCHEMA_MIGRATION_LOCK:
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
                CREATE TABLE IF NOT EXISTS workflow_role_checkpoints (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    role TEXT NOT NULL,
                    model TEXT,
                    data_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, role)
                );
                CREATE TABLE IF NOT EXISTS run_execution_leases (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    owner_token TEXT NOT NULL,
                    claimed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS event_idempotency_keys (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    event_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
                    PRIMARY KEY (run_id, event_type, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS integration_admissions (
                    namespace TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    run_id TEXT UNIQUE REFERENCES runs(run_id),
                    state TEXT NOT NULL,
                    active_slot INTEGER,
                    metadata_json TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, idempotency_key)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_integration_admissions_active_slot
                    ON integration_admissions(namespace, active_slot)
                    WHERE active_slot IS NOT NULL;
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
                CREATE TABLE IF NOT EXISTS model_catalog_snapshots (
                    provider TEXT PRIMARY KEY,
                    models_json TEXT NOT NULL,
                    healthy INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    refreshed_at TEXT NOT NULL
                );
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
                    revalidate_after TEXT,
                    expires_at TEXT,
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
                CREATE TABLE IF NOT EXISTS reputation_results_v2 (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    result_id TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    score REAL NOT NULL CHECK (score >= 0.0 AND score <= 1.0),
                    created_at TEXT NOT NULL,
                    UNIQUE (role, capability, source_kind, source_ref)
                );
                CREATE INDEX IF NOT EXISTS idx_reputation_v2_role_capability
                    ON reputation_results_v2(role, capability, sequence);
                CREATE TABLE IF NOT EXISTS prompt_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    prompt_text TEXT NOT NULL,
                    baseline_quality REAL NOT NULL
                        CHECK (baseline_quality >= 0.0 AND baseline_quality <= 1.0),
                    status TEXT NOT NULL,
                    assessed_quality REAL,
                    safety_passed INTEGER,
                    assessment_refs_json TEXT,
                    assessment_revision INTEGER NOT NULL DEFAULT 0,
                    assessment_digest TEXT,
                    human_approver_id TEXT,
                    approved_assessment_revision INTEGER,
                    approved_assessment_digest TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                    """
                )
            with self._immediate_connection() as connection:
                self._ensure_memory_deadline_columns(connection)
                self._ensure_prompt_assessment_columns(connection)
                self._migrate_reputation_results(connection)

    @staticmethod
    def _ensure_memory_deadline_columns(connection: sqlite3.Connection) -> None:
        _add_column_if_missing(
            connection,
            "memory_items",
            "revalidate_after",
            "ALTER TABLE memory_items ADD COLUMN revalidate_after TEXT",
        )
        _add_column_if_missing(
            connection,
            "memory_items",
            "expires_at",
            "ALTER TABLE memory_items ADD COLUMN expires_at TEXT",
        )

    @staticmethod
    def _ensure_prompt_assessment_columns(connection: sqlite3.Connection) -> None:
        _add_column_if_missing(
            connection,
            "prompt_candidates",
            "assessment_revision",
            """
            ALTER TABLE prompt_candidates
            ADD COLUMN assessment_revision INTEGER NOT NULL DEFAULT 0
            """,
        )
        _add_column_if_missing(
            connection,
            "prompt_candidates",
            "assessment_digest",
            "ALTER TABLE prompt_candidates ADD COLUMN assessment_digest TEXT",
        )
        _add_column_if_missing(
            connection,
            "prompt_candidates",
            "approved_assessment_revision",
            """
            ALTER TABLE prompt_candidates
            ADD COLUMN approved_assessment_revision INTEGER
            """,
        )
        _add_column_if_missing(
            connection,
            "prompt_candidates",
            "approved_assessment_digest",
            """
            ALTER TABLE prompt_candidates
            ADD COLUMN approved_assessment_digest TEXT
            """,
        )
        connection.execute(
            """
            UPDATE prompt_candidates
            SET human_approver_id = NULL,
                approved_assessment_revision = NULL,
                approved_assessment_digest = NULL
            WHERE assessment_digest IS NULL
            """
        )

    @staticmethod
    def _migrate_reputation_results(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO reputation_results_v2 (
                result_id, role, capability, source_kind, source_ref, score,
                created_at
            )
            SELECT result_id, role, capability, source_kind, source_ref, score,
                   created_at
            FROM reputation_results
            ORDER BY sequence ASC
            """
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._refresh_runtime_paths()
        with pinned_swarm_database(self.project_root, read_only=False) as pinned:
            connection = _open_pinned_swarm_database_connection(
                pinned,
                self.project_root,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    @contextmanager
    def _immediate_connection(self) -> Iterator[sqlite3.Connection]:
        self._refresh_runtime_paths()
        with pinned_swarm_database(self.project_root, read_only=False) as pinned:
            connection = _open_pinned_swarm_database_connection(
                pinned,
                self.project_root,
            )
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

    def _refresh_runtime_paths(self) -> None:
        self.runtime_dir = resolve_swarm_path(self.project_root, "runtime")
        self.db_path = resolve_swarm_path(self.project_root, "runtime", "swarm.sqlite")


class ReadOnlyProjectSwarmStore:
    """Read-only view of an already initialized project Swarm database.

    The constructor intentionally validates files before opening SQLite with
    ``mode=ro``.  It never calls :func:`initialize_project`, makes a runtime
    directory, applies migrations, or commits a connection.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        load_project_config(self.project_root)
        self._refresh_runtime_paths()
        try:
            with pinned_swarm_database(self.project_root, read_only=True):
                pass
        except FileNotFoundError:
            raise SwarmProjectNotInitializedError(
                f"Swarm project is not initialized: {self.project_root}"
            ) from None

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

    def list_runs(self) -> list[SwarmRun]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT run_id, status, created_at, updated_at, metadata_json
                FROM runs ORDER BY created_at DESC, run_id ASC
                """
            ).fetchall()
        return [_row_to_run(row) for row in rows]

    def list_events(self, run_id: str) -> list[SwarmEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT sequence, event_id, timestamp, event_type, run_id,
                       payload_json, visibility
                FROM events WHERE run_id = ? ORDER BY sequence ASC
                """,
                (run_id,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def list_events_after(
        self,
        run_id: str,
        sequence: int,
        *,
        limit: int = 100,
    ) -> list[SwarmEvent]:
        return _list_events_after(self._connection, run_id, sequence, limit=limit)

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

    def get_model_catalog_snapshot(
        self,
        provider: str,
    ) -> ModelCatalogSnapshot | None:
        return _get_model_catalog_snapshot(self._connection, provider)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._refresh_runtime_paths()
        with pinned_swarm_database(self.project_root, read_only=True) as pinned:
            connection = _open_pinned_swarm_database_connection(
                pinned,
                self.project_root,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                yield connection
            finally:
                connection.close()

    def _refresh_runtime_paths(self) -> None:
        self.runtime_dir = resolve_swarm_path(self.project_root, "runtime")
        self.db_path = resolve_swarm_path(self.project_root, "runtime", "swarm.sqlite")


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


class _ReadOnlyConnectionContext:
    """Close an SQLite read-only connection without committing any state."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self._connection

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._connection.close()


def _open_pinned_swarm_database_connection(
    pinned: PinnedSwarmDatabase,
    project_root: Path,
) -> sqlite3.Connection:
    """Open SQLite only while the direct runtime child retains its identity.

    POSIX stock SQLite receives a pathname rather than our already-open file
    descriptor.  The runtime directory is owner-only (the OS trust boundary),
    and these checks bracket SQLite's one pathname open so a detected stale
    same-UID child replacement cannot reach Swarm SQL or a transaction.
    """
    pinned.assert_database_identity()
    connection = sqlite3.connect(pinned.database_path, uri=pinned.uri)
    try:
        # A test hook or same-UID process may replace the child exactly while
        # SQLite opens its URI.  Check before the first SQL statement, then
        # close without exposing or mutating the substituted database.
        pinned.assert_database_identity()
        _validate_pinned_database_connection(
            connection,
            project_root,
            pinned.runtime_dir,
        )
    except BaseException:
        connection.close()
        raise
    return connection


def _validate_pinned_database_connection(
    connection: sqlite3.Connection,
    project_root: Path,
    runtime_dir: Path,
) -> None:
    """Fail closed unless SQLite opened the database in the pinned runtime dir.

    `pinned_swarm_database()` retains the runtime directory descriptor/handle
    while SQLite opens its URI.  This proof must therefore use that pinned
    parent reference, never process-global ``Path.cwd()``.
    """
    rows = connection.execute("PRAGMA database_list").fetchall()
    database_file = next((row[2] for row in rows if row[1] == "main"), None)
    if not isinstance(database_file, str) or not database_file:
        raise SwarmProjectPathError("SQLite did not expose a canonical Swarm database")
    candidate = Path(database_file)
    try:
        expected = (runtime_dir / "swarm.sqlite").resolve()
        actual = (
            candidate if candidate.is_absolute() else runtime_dir / candidate
        ).resolve()
        if actual != expected:
            raise ValueError("SQLite opened a different Swarm database")
        actual.relative_to(project_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SwarmProjectPathError(
            "SQLite database resolves outside the trusted Swarm project: "
            f"{database_file}"
        ) from exc
    if actual.name != "swarm.sqlite" or actual.parent != expected.parent:
        raise SwarmProjectPathError(
            f"SQLite database is not the pinned Swarm runtime file: {database_file}"
        )


def _list_events_after(
    connection_factory: Callable[[], Any],
    run_id: str,
    sequence: int,
    *,
    limit: int,
) -> list[SwarmEvent]:
    if not isinstance(sequence, int) or sequence < 0:
        raise ValueError("Event cursor must be a non-negative integer")
    if not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("Event limit must be between 1 and 500")
    with connection_factory() as connection:
        rows = connection.execute(
            """
            SELECT sequence, event_id, timestamp, event_type, run_id,
                   payload_json, visibility
            FROM events
            WHERE run_id = ? AND sequence > ?
            ORDER BY sequence ASC
            LIMIT ?
            """,
            (run_id, sequence, limit),
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def _get_model_catalog_snapshot(
    connection_factory: Callable[[], Any],
    provider: str,
) -> ModelCatalogSnapshot | None:
    normalized_provider = str(provider).strip().lower()
    if not normalized_provider:
        raise ValueError("Catalog provider must be non-empty")
    try:
        with connection_factory() as connection:
            row = connection.execute(
                """
                SELECT provider, models_json, healthy, source, refreshed_at
                FROM model_catalog_snapshots WHERE provider = ?
                """,
                (normalized_provider,),
            ).fetchone()
    except sqlite3.OperationalError as exc:
        # A pre-catalog project may be observed through a read-only status
        # route.  Returning no snapshot preserves read purity; migrations are
        # reserved for an explicit write path.
        if "no such table" in str(exc).lower():
            return None
        raise
    if row is None:
        return None
    return ModelCatalogSnapshot(
        provider=row["provider"],
        models=tuple(json.loads(row["models_json"])),
        healthy=bool(row["healthy"]),
        source=row["source"],
        refreshed_at=datetime.fromisoformat(row["refreshed_at"]),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp_text(timestamp: datetime) -> str:
    return timestamp.isoformat()


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table_name})")
    }


def _add_column_if_missing(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    statement: str,
) -> None:
    """Add one legacy column without concealing non-race migration failures."""
    if column_name in _table_columns(connection, table_name):
        return
    try:
        connection.execute(statement)
    except sqlite3.OperationalError as error:
        # Another process may have passed its own older PRAGMA check immediately
        # before this transaction.  Treat only a verified duplicate-column race
        # as idempotent; all other SQLite errors remain visible to callers.
        if "duplicate column name" not in str(error).lower():
            raise
        if column_name not in _table_columns(connection, table_name):
            raise


def _earliest_deadline(
    existing: datetime | None,
    incoming: datetime | None,
) -> datetime | None:
    if existing is None:
        return incoming
    if incoming is None:
        return existing
    return min(existing, incoming)


def _deadlines_require_expiry(
    revalidate_after: datetime | None,
    expires_at: datetime | None,
) -> bool:
    return (
        revalidate_after is not None
        and expires_at is not None
        and revalidate_after >= expires_at
    )


def _merge_references(
    existing: object,
    incoming: tuple[str, ...],
) -> tuple[str, ...]:
    merged: list[str] = []
    for reference in (*tuple(existing), *incoming):  # type: ignore[arg-type]
        if reference not in merged:
            merged.append(reference)
    return tuple(merged)


def _validated_workflow_checkpoint_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return the minimal structured response state safe for durable replay."""
    if not isinstance(data, Mapping):
        raise TypeError("Workflow checkpoint data must be a mapping")
    try:
        normalized = json.loads(json.dumps(dict(data), sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Workflow checkpoint data must be JSON-safe") from exc
    if not isinstance(normalized.get("work"), str):
        raise ValueError("Workflow checkpoint work must be a string")
    if not isinstance(normalized.get("evidence"), list):
        raise ValueError("Workflow checkpoint evidence must be a list")
    if not isinstance(normalized.get("decision"), str):
        raise ValueError("Workflow checkpoint decision must be a string")
    return normalized


def _workflow_role_checkpoints_from_connection(
    connection: sqlite3.Connection,
    run_id: str,
) -> dict[str, WorkflowRoleCheckpoint]:
    """Read one run's immutable role outputs within the caller's transaction."""
    rows = connection.execute(
        """
        SELECT run_id, role, model, data_json, completed_at
        FROM workflow_role_checkpoints
        WHERE run_id = ?
        ORDER BY completed_at ASC, role ASC
        """,
        (run_id,),
    ).fetchall()
    return {
        checkpoint.role: checkpoint
        for checkpoint in (_row_to_workflow_role_checkpoint(row) for row in rows)
    }


def _row_to_workflow_role_checkpoint(row: sqlite3.Row) -> WorkflowRoleCheckpoint:
    role = row["role"]
    if not isinstance(role, str) or not role.strip():
        raise RuntimeError("Stored workflow checkpoint has an invalid role")
    model = row["model"]
    if model is not None:
        if not isinstance(model, str) or not model.strip():
            raise RuntimeError("Stored workflow checkpoint has an invalid model")
        model = model.strip()
    try:
        data = _validated_workflow_checkpoint_data(json.loads(row["data_json"]))
        completed_at = datetime.fromisoformat(row["completed_at"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Stored workflow checkpoint is invalid") from exc
    return WorkflowRoleCheckpoint(
        run_id=row["run_id"],
        role=role,
        model=model,
        data=data,
        completed_at=completed_at,
    )


def _row_to_run(row: sqlite3.Row) -> SwarmRun:
    return SwarmRun(
        run_id=row["run_id"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        metadata=json.loads(row["metadata_json"]),
    )


def _get_integration_admission_from_connection(
    connection: sqlite3.Connection,
    namespace: str,
    idempotency_key: str,
) -> IntegrationAdmission | None:
    row = connection.execute(
        """
        SELECT integration_admissions.namespace,
               integration_admissions.idempotency_key,
               integration_admissions.state,
               integration_admissions.reason,
               runs.run_id AS run_id,
               runs.status AS status,
               runs.created_at AS created_at,
               runs.updated_at AS updated_at,
               runs.metadata_json AS run_metadata_json
        FROM integration_admissions
        LEFT JOIN runs ON runs.run_id = integration_admissions.run_id
        WHERE integration_admissions.namespace = ?
          AND integration_admissions.idempotency_key = ?
        """,
        (namespace, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    state = row["state"]
    if state not in {"admitted", "rejected"}:
        raise RuntimeError("Stored integration admission has an invalid state")
    if state == "admitted":
        if row["run_id"] is None or row["run_metadata_json"] is None:
            raise RuntimeError("Stored admitted integration has no run")
        run = SwarmRun(
            run_id=row["run_id"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            metadata=json.loads(row["run_metadata_json"]),
        )
        return IntegrationAdmission(
            status="admitted",
            namespace=row["namespace"],
            idempotency_key=row["idempotency_key"],
            run=run,
            reason=None,
        )
    if row["run_id"] is not None:
        raise RuntimeError("Stored rejected integration unexpectedly has a run")
    return IntegrationAdmission(
        status="rejected",
        namespace=row["namespace"],
        idempotency_key=row["idempotency_key"],
        run=None,
        reason=row["reason"],
    )


def _record_integration_rejection_in_connection(
    connection: sqlite3.Connection,
    namespace: str,
    idempotency_key: str,
    *,
    reason: str,
    timestamp: datetime,
) -> IntegrationAdmission:
    reason = _validated_integration_reason(reason)
    timestamp_text = _timestamp_text(timestamp)
    connection.execute(
        """
        INSERT INTO integration_admissions (
            namespace, idempotency_key, run_id, state, active_slot,
            metadata_json, reason, created_at, updated_at
        ) VALUES (?, ?, NULL, 'rejected', NULL, '{}', ?, ?, ?)
        """,
        (namespace, idempotency_key, reason, timestamp_text, timestamp_text),
    )
    return IntegrationAdmission(
        status=reason,
        namespace=namespace,
        idempotency_key=idempotency_key,
        run=None,
        reason=reason,
    )


def _validated_integration_namespace(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("Swarm integration namespace must be a string")
    value = value.strip()
    if (
        not value
        or len(value) > 64
        or not value.isascii()
        or any(not (character.isalnum() or character in "._-") for character in value)
    ):
        raise ValueError("Swarm integration namespace must be bounded ASCII text")
    return value


def _validated_integration_idempotency_key(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("Swarm integration idempotency_key must be a string")
    value = value.strip()
    if (
        not value
        or len(value) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("Swarm integration idempotency_key must be bounded text")
    return value


def _validated_integration_admission_bounds(
    request: IntegrationAdmissionRequest,
) -> tuple[int, int, int | None]:
    max_active = request.max_active
    rolling_window_seconds = request.rolling_window_seconds
    rolling_run_limit = request.rolling_run_limit
    if (
        isinstance(max_active, bool)
        or not isinstance(max_active, int)
        or not 1 <= max_active <= 128
    ):
        raise ValueError("Swarm integration max_active must be between 1 and 128")
    if (
        isinstance(rolling_window_seconds, bool)
        or not isinstance(rolling_window_seconds, int)
        or not 1 <= rolling_window_seconds <= 31 * 24 * 60 * 60
    ):
        raise ValueError(
            "Swarm integration rolling_window_seconds must be bounded positive seconds"
        )
    if rolling_run_limit is not None and (
        isinstance(rolling_run_limit, bool)
        or not isinstance(rolling_run_limit, int)
        or not 1 <= rolling_run_limit <= 100_000
    ):
        raise ValueError("Swarm integration rolling_run_limit must be positive or None")
    return max_active, rolling_window_seconds, rolling_run_limit


def _validated_integration_metadata(
    metadata: Mapping[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise TypeError("Swarm integration metadata must be a mapping")
    normalized = _normalized_json_value(
        metadata,
        label="Swarm integration metadata",
        depth=0,
        ancestors=set(),
        remaining_items=[_MAX_INTEGRATION_VALUE_ITEMS],
    )
    assert isinstance(normalized, dict)
    for name in ("goal", "pack", "project_root", "autonomy"):
        if name not in normalized:
            raise ValueError(f"Swarm integration metadata is missing {name}")
    for name in ("goal", "pack"):
        value = normalized[name]
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            raise ValueError(f"Swarm integration metadata has an invalid {name}")
    if normalized["project_root"] != str(project_root):
        raise ValueError("Swarm integration metadata belongs to a different project")
    if normalized["autonomy"] not in {
        "observe",
        "suggest",
        "execute_safe",
        "reviewed_execution",
        "autonomous",
    }:
        raise ValueError("Swarm integration metadata has an invalid autonomy")
    return normalized


def _normalized_json_value(
    value: Any,
    *,
    label: str,
    depth: int,
    ancestors: set[int],
    remaining_items: list[int],
) -> Any:
    remaining_items[0] -= 1
    if remaining_items[0] < 0:
        raise ValueError(f"{label} must contain bounded JSON-safe values")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{label} must contain finite JSON values")
        return value
    if depth >= _MAX_INTEGRATION_VALUE_DEPTH:
        raise ValueError(f"{label} must contain bounded JSON-safe values")
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in ancestors:
            raise ValueError(f"{label} must contain bounded JSON-safe values")
        ancestors.add(identity)
        normalized: dict[str, Any] = {}
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError(f"{label} keys must be strings")
                normalized[key] = _normalized_json_value(
                    item,
                    label=label,
                    depth=depth + 1,
                    ancestors=ancestors,
                    remaining_items=remaining_items,
                )
        finally:
            ancestors.remove(identity)
        return normalized
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in ancestors:
            raise ValueError(f"{label} must contain bounded JSON-safe values")
        ancestors.add(identity)
        try:
            return [
                _normalized_json_value(
                    item,
                    label=label,
                    depth=depth + 1,
                    ancestors=ancestors,
                    remaining_items=remaining_items,
                )
                for item in value
            ]
        finally:
            ancestors.remove(identity)
    raise TypeError(f"{label} must contain JSON-safe values")


def _validated_admission_timestamp(now: datetime | None) -> datetime:
    if now is None:
        return _utc_now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Swarm integration admission timestamp must be timezone-aware")
    return now.astimezone(timezone.utc)


def _validated_integration_reason(reason: Any) -> str:
    if not isinstance(reason, str):
        raise TypeError("Swarm integration rejection reason must be a string")
    reason = reason.strip()
    if (
        not reason
        or len(reason) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in reason)
    ):
        raise ValueError("Swarm integration rejection reason must be bounded text")
    return reason


def _seconds_delta(seconds: int) -> timedelta:
    return timedelta(seconds=seconds)


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


def _unmatched_durable_model_attempts(
    events: list[SwarmEvent],
) -> tuple[tuple[int, str, str], ...]:
    """Return exact attempts with no later terminal outcome or prior handoff.

    The event stream predates an attempt-id column, so matching is deliberately
    FIFO per ``(role, model)``.  A valid prior human replay authorization is a
    handoff for its exact original sequence and is not reissued on a later
    crash recovery; malformed/duplicate authorizations are left for Engine to
    reject fail-closed.
    """
    pending: list[tuple[int, str, str]] = []
    for event in sorted(events, key=lambda item: item.sequence):
        identity = _durable_attempt_identity(event)
        if event.event_type == "model.attempt_started":
            if identity is not None:
                pending.append((event.sequence, *identity))
            continue
        if event.event_type in {"model.attempt_failed", "work.completed"}:
            if identity is not None:
                _consume_durable_attempt(pending, *identity)
            continue
        if event.event_type == "model.attempt_replay_authorized_by_human":
            authorization = _durable_replay_authorization_identity(event)
            if authorization is None:
                continue
            original_sequence, role, model = authorization
            if event.sequence <= original_sequence:
                continue
            for index, attempt in enumerate(pending):
                if attempt == (original_sequence, role, model):
                    pending.pop(index)
                    break
    return tuple(pending)


def _durable_attempt_identity(event: SwarmEvent) -> tuple[str, str] | None:
    role = event.payload.get("role")
    model = event.payload.get("model")
    if (
        not isinstance(role, str)
        or not role
        or role != role.strip()
        or not isinstance(model, str)
        or not model
        or model != model.strip()
    ):
        return None
    return role, model


def _durable_replay_authorization_identity(
    event: SwarmEvent,
) -> tuple[int, str, str] | None:
    payload = event.payload
    if set(payload) != {
        "actor_id",
        "original_attempt_sequence",
        "role",
        "model",
    }:
        return None
    actor_id = payload.get("actor_id")
    original_sequence = payload.get("original_attempt_sequence")
    role = payload.get("role")
    model = payload.get("model")
    if (
        not isinstance(actor_id, str)
        or not actor_id.startswith(("os:", "dashboard:"))
        or not actor_id.split(":", 1)[1].strip()
        or len(actor_id) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in actor_id)
        or isinstance(original_sequence, bool)
        or not isinstance(original_sequence, int)
        or original_sequence < 1
        or not isinstance(role, str)
        or not role
        or role != role.strip()
        or not isinstance(model, str)
        or not model
        or model != model.strip()
    ):
        return None
    return original_sequence, role, model


def _consume_durable_attempt(
    pending: list[tuple[int, str, str]],
    role: str,
    model: str,
) -> None:
    for index, (_sequence, candidate_role, candidate_model) in enumerate(pending):
        if candidate_role == role and candidate_model == model:
            pending.pop(index)
            return


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
        "revalidate_after": (
            datetime.fromisoformat(row["revalidate_after"])
            if row["revalidate_after"]
            else None
        ),
        "expires_at": (
            datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
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
        "assessment_revision": int(row["assessment_revision"]),
        "assessment_digest": row["assessment_digest"],
        "human_approver_id": row["human_approver_id"],
        "approved_assessment_revision": row["approved_assessment_revision"],
        "approved_assessment_digest": row["approved_assessment_digest"],
        "created_at": datetime.fromisoformat(row["created_at"]),
        "updated_at": datetime.fromisoformat(row["updated_at"]),
    }
