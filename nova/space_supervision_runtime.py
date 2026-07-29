"""Inert, host-owned event pulse for enrolled YOLO Space supervision.

This module deliberately owns neither a model transport nor a timer.  A future
host may inject it into the existing one-minute scheduler, but a constructed
runtime does nothing until a code-owned signal is ingested and ``pulse`` is
called.  All authority remains with :class:`ManagedSpaceSupervisor`.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Mapping

from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor


_SOURCES = frozenset({"git", "kanban", "ci"})
_REASON_CODES = frozenset({"git_change", "kanban_change", "ci_change", "ci_failed"})
_PERIODIC_REASON = "periodic_check"
_MERGED_REASON = "multiple_changes"
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_TARGET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_MIN_CHECK_SECONDS = 15 * 60
_MAX_PENDING_SIGNALS = 256
_MAX_DEDUPLICATED_SIGNAL_IDENTITIES = 2_048
_SPACE_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_SUPERVISION_STATE_COLUMNS = frozenset(
    {
        "target_space_id",
        "root_fingerprint",
        "governance_revision",
        "current_reference_digest",
        "last_evaluated_reference_digest",
        "last_checked_at",
        "last_check_code",
    }
)
_SUPERVISION_SCHEMA_OBJECTS = (
    "nova_supervision_signals",
    "nova_supervision_space_state",
    "idx_nova_supervision_signals_observed",
)

# Task 5 can render this fixed, non-model identity data without making a
# presence GET a write or a model invocation.
NOVA_SUPERVISION_IDENTITY = {
    "name": "Nova",
    "voice": "ruhig, direkt und evidenzbasiert",
    "focus": "betreut nur explizit eingeschriebene YOLO-Spaces",
}


@dataclass(frozen=True, slots=True)
class SupervisionPulseOutcome:
    """A bounded feed record; it contains no path, webhook, or model text."""

    target_key: str
    status: str
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class SupervisionStatus:
    """Read-only, redacted scheduling visibility for one tracked Space."""

    target_key: str
    pending: bool
    last_started_at: float | None
    last_outcome: str


@dataclass(frozen=True, slots=True)
class _TrackedState:
    target_key: str
    target_space_id: str
    root_fingerprint: str
    pending_digest: str
    pending_reason_code: str
    last_started_at: float | None
    governance_revision: int
    current_reference_digest: str
    last_evaluated_reference_digest: str
    last_checked_at: float | None
    last_check_code: str


class NovaSpaceSupervisionRuntime:
    """Coalesce bounded events and ask the supervisor for every admission.

    ``dispatch_run`` is a host-owned callback.  It receives only the exact
    supervisor-recorded root and run id after a child has been revalidated and
    started.  It must not be a model callback; this runtime never owns or
    invokes a transport itself.
    """

    def __init__(
        self,
        *,
        supervisor: ManagedSpaceSupervisor,
        dispatch_run: Callable[[Path, str], object],
    ) -> None:
        if not isinstance(supervisor, ManagedSpaceSupervisor):
            raise TypeError("Nova Space supervision requires a managed supervisor")
        if not callable(dispatch_run):
            raise TypeError("Nova Space supervision requires a host dispatcher")
        self._supervisor = supervisor
        self._dispatch_run = dispatch_run

    def ingest_signal(
        self,
        target_key: str,
        *,
        source: str,
        event_id: str,
        reason_code: str,
    ) -> bool:
        """Durably accept one bounded code-owned signal exactly once.

        The external event identity is hashed before storage; only its opaque
        digest and a fixed reason code remain in the central supervisor ledger.
        ``False`` means either an already-known identity or a fail-closed
        capacity rejection; neither case mutates pending work.
        """
        target = _target_key(target_key)
        normalized_source = _source(source)
        normalized_event_id = _event_id(event_id)
        normalized_reason = _reason_code(reason_code)
        governance = self._supervisor.current_governance(target)
        if (
            governance is None
            or governance.yolo is not True
            or governance.enrolled is not True
        ):
            # A signal that arrived before explicit enrollment must never be
            # retained and turned into an autonomous run if a human enables
            # the Space later.  The caller can submit a fresh bounded signal
            # after enrollment; no model or ledger write happens here.
            return False
        signal_digest = _digest(
            {
                "target_key": target,
                "source": normalized_source,
                "event_id": normalized_event_id,
                "reason_code": normalized_reason,
            }
        )
        observed_at = time.time()
        with self._supervisor._supervision_state_transaction(
            schema_objects=_SUPERVISION_SCHEMA_OBJECTS,
            schema_initializer=_ensure_schema,
        ) as connection:
            # Named tables and indexes alone cannot prove that an existing
            # scheduler table has the marker and governance-binding columns.
            # Upgrade under the same write lock before a new signal can become
            # autonomous work.
            if not _supervision_state_columns_present(connection):
                _ensure_schema(connection)
            existing = connection.execute(
                "SELECT 1 FROM nova_supervision_signals WHERE signal_digest = ?",
                (signal_digest,),
            ).fetchone()
            if existing is not None:
                return False
            stored_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM nova_supervision_signals"
                ).fetchone()[0]
            )
            if stored_count >= _MAX_DEDUPLICATED_SIGNAL_IDENTITIES:
                # Never evict an identity and thereby make an old external
                # event a new autonomous trigger. Without a trusted monotonic
                # upstream cursor, saturation must fail closed instead.
                return False
            connection.execute(
                """INSERT INTO nova_supervision_signals
                   (signal_digest, target_key, reason_code, observed_at)
                   VALUES (?, ?, ?, ?)""",
                (signal_digest, target, normalized_reason, observed_at),
            )
            current = connection.execute(
                """SELECT pending_digest, pending_reason_code, pending_count,
                          target_space_id, root_fingerprint, governance_revision
                   FROM nova_supervision_space_state WHERE target_key = ?""",
                (target,),
            ).fetchone()
            if current is None:
                pending_digest = _digest(
                    {"signal_digest": signal_digest, "reason_code": normalized_reason}
                )
                connection.execute(
                    """INSERT INTO nova_supervision_space_state
                       (target_key, target_space_id, root_fingerprint,
                        pending_digest, pending_reason_code, pending_count,
                        governance_revision, last_started_at,
                        current_reference_digest, last_evaluated_reference_digest,
                        last_checked_at, last_check_code, last_outcome_code, updated_at)
                       VALUES (?, ?, ?, ?, ?, 1, ?, NULL, ?, '', NULL, '', '', ?)""",
                    (
                        target,
                        governance.space_id,
                        governance.root_fingerprint,
                        pending_digest,
                        normalized_reason,
                        governance.revision,
                        pending_digest,
                        observed_at,
                    ),
                )
            elif not _row_matches_governance(current, governance):
                # A deleted/recreated Space, moved root, or changed governance
                # cannot inherit a prior reference or quiet checkpoint.  Keep
                # only the new event observed under the current authority.
                pending_digest = _digest(
                    {"signal_digest": signal_digest, "reason_code": normalized_reason}
                )
                connection.execute(
                    """UPDATE nova_supervision_space_state
                       SET target_space_id = ?, root_fingerprint = ?,
                           pending_digest = ?, pending_reason_code = ?, pending_count = 1,
                           governance_revision = ?, last_started_at = NULL,
                           current_reference_digest = ?, last_evaluated_reference_digest = '',
                           last_checked_at = NULL, last_check_code = '',
                           last_outcome_code = '', updated_at = ?
                       WHERE target_key = ?""",
                    (
                        governance.space_id,
                        governance.root_fingerprint,
                        pending_digest,
                        normalized_reason,
                        governance.revision,
                        pending_digest,
                        observed_at,
                        target,
                    ),
                )
            else:
                pending_digest = _digest(
                    {
                        "previous_pending_digest": current["pending_digest"],
                        "signal_digest": signal_digest,
                        "reason_code": normalized_reason,
                    }
                )
                pending_reason = _merge_reason(
                    current["pending_reason_code"], normalized_reason
                )
                pending_count = min(int(current["pending_count"]) + 1, _MAX_PENDING_SIGNALS)
                connection.execute(
                    """UPDATE nova_supervision_space_state
                       SET pending_digest = ?, pending_reason_code = ?, pending_count = ?,
                           current_reference_digest = ?, last_checked_at = NULL,
                           last_check_code = '', updated_at = ?
                       WHERE target_key = ?""",
                    (
                        pending_digest,
                        pending_reason,
                        pending_count,
                        pending_digest,
                        observed_at,
                        target,
                    ),
                )
        return True

    def pulse(self, *, now_epoch: float | None = None) -> tuple[SupervisionPulseOutcome, ...]:
        """Perform one cheap, externally scheduled supervision pass.

        A pending verified signal runs immediately.  A previously started
        tracked Space gets a periodic check only after the 900-second floor.
        Spaces without a tracked event never receive a blind cron-driven model
        check.
        """
        now = _epoch(now_epoch)
        outcomes: list[SupervisionPulseOutcome] = []
        for state in self._tracked_states():
            governance = self._supervisor.current_governance(state.target_key)
            if (
                governance is None
                or governance.yolo is not True
                or governance.enrolled is not True
            ):
                if state.pending_digest:
                    outcomes.append(SupervisionPulseOutcome(state.target_key, "ineligible"))
                continue
            if not _state_matches_governance(state, governance):
                # A stale durable scheduler row is not authority for a
                # recreated Space, moved root, or revised governance.  It is
                # retained only until a fresh, code-owned signal rebinding it
                # arrives; it may not start work or claim quiet equality.
                continue
            if state.pending_digest:
                reason_code = state.pending_reason_code
            else:
                if _quiet_check_due(state, now) and _references_are_equal(state):
                    if self._mark_unchanged(state, now):
                        outcomes.append(SupervisionPulseOutcome(state.target_key, "unchanged"))
                continue
            intent = {
                "space_id": governance.space_id,
                "governance_revision": governance.revision,
                "reason_code": reason_code,
                "pending_digest": state.pending_digest,
            }
            try:
                admission = self._supervisor.admit(state.target_key, intent)
            except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                outcomes.append(SupervisionPulseOutcome(state.target_key, "admission_failed"))
                continue
            if admission.status == "created" and admission.capability is not None:
                started = self._supervisor.start_admitted_run(
                    admission.capability,
                    dispatcher=self._dispatch_run,
                )
                if started:
                    self._mark_started(state, now)
                    outcomes.append(
                        SupervisionPulseOutcome(
                            state.target_key,
                            "started",
                            admission.run_id,
                        )
                    )
                else:
                    outcomes.append(
                        SupervisionPulseOutcome(
                            state.target_key,
                            "start_failed",
                            admission.run_id,
                        )
                    )
                continue
            if admission.reason == "not_yolo_enrolled":
                outcomes.append(SupervisionPulseOutcome(state.target_key, "ineligible"))
            elif admission.reason == "active_limit":
                outcomes.append(SupervisionPulseOutcome(state.target_key, "active_limit"))
            elif admission.status == "coalesced":
                outcomes.append(
                    SupervisionPulseOutcome(state.target_key, "coalesced", admission.run_id)
                )
            else:
                outcomes.append(
                    SupervisionPulseOutcome(
                        state.target_key,
                        "admission_rejected",
                        admission.run_id,
                    )
                )
        return tuple(outcomes)

    def status(self) -> tuple[SupervisionStatus, ...]:
        """Read current pulse visibility without initializing any store."""
        with self._supervisor._supervision_state_reader() as connection:
            if connection is None:
                return ()
            try:
                rows = connection.execute(
                    """SELECT target_key, pending_digest, last_started_at, last_outcome_code
                       FROM nova_supervision_space_state ORDER BY target_key ASC"""
                ).fetchall()
            except sqlite3.Error:
                return ()
        return tuple(
            SupervisionStatus(
                target_key=row["target_key"],
                pending=bool(row["pending_digest"]),
                last_started_at=(
                    float(row["last_started_at"])
                    if row["last_started_at"] is not None
                    else None
                ),
                last_outcome=_bounded_outcome(row["last_outcome_code"]),
            )
            for row in rows
        )

    def _tracked_states(self) -> tuple[_TrackedState, ...]:
        with self._supervisor._supervision_state_reader() as connection:
            if connection is None:
                return ()
            try:
                rows = connection.execute(
                    """SELECT target_key, target_space_id, root_fingerprint,
                               pending_digest, pending_reason_code, last_started_at,
                               governance_revision, current_reference_digest,
                               last_evaluated_reference_digest, last_checked_at,
                               last_check_code
                       FROM nova_supervision_space_state ORDER BY target_key ASC"""
                ).fetchall()
            except sqlite3.Error:
                return ()
        states: list[_TrackedState] = []
        for row in rows:
            try:
                states.append(
                    _TrackedState(
                        target_key=_target_key(row["target_key"]),
                        target_space_id=_state_space_id(row["target_space_id"]),
                        root_fingerprint=_state_root_fingerprint(row["root_fingerprint"]),
                        pending_digest=_pending_digest(row["pending_digest"]),
                        pending_reason_code=_stored_reason(row["pending_reason_code"]),
                        last_started_at=(
                            _epoch(row["last_started_at"])
                            if row["last_started_at"] is not None
                            else None
                        ),
                        governance_revision=_governance_revision(
                            row["governance_revision"]
                        ),
                        current_reference_digest=_pending_digest(
                            row["current_reference_digest"]
                        ),
                        last_evaluated_reference_digest=_pending_digest(
                            row["last_evaluated_reference_digest"]
                        ),
                        last_checked_at=(
                            _epoch(row["last_checked_at"])
                            if row["last_checked_at"] is not None
                            else None
                        ),
                        last_check_code=_check_code(row["last_check_code"]),
                    )
                )
            except (TypeError, ValueError):
                # A malformed durable row cannot become host authority.
                continue
        return tuple(states)

    def _mark_started(self, state: _TrackedState, now: float) -> None:
        """Clear only the exact dispatched signal; preserve a racing new one."""
        with self._supervisor._supervision_state_transaction(
            schema_objects=_SUPERVISION_SCHEMA_OBJECTS,
            schema_initializer=_ensure_schema,
        ) as connection:
            cursor = connection.execute(
                """UPDATE nova_supervision_space_state
                   SET pending_digest = '', pending_reason_code = ?, pending_count = 0,
                       last_started_at = ?, last_evaluated_reference_digest = ?,
                       last_checked_at = NULL, last_check_code = '',
                       last_outcome_code = 'started', updated_at = ?
                   WHERE target_key = ? AND target_space_id = ?
                     AND root_fingerprint = ? AND governance_revision = ?
                     AND pending_digest = ? AND current_reference_digest = ?
                     AND last_evaluated_reference_digest = ? AND last_checked_at IS ?
                     AND last_check_code = ?""",
                (
                    _PERIODIC_REASON,
                    now,
                    state.current_reference_digest,
                    now,
                    state.target_key,
                    state.target_space_id,
                    state.root_fingerprint,
                    state.governance_revision,
                    state.pending_digest,
                    state.current_reference_digest,
                    state.last_evaluated_reference_digest,
                    state.last_checked_at,
                    state.last_check_code,
                ),
            )
            # The run has already received a capability.  A fresh signal that
            # wins this CAS remains pending for the next pulse instead of
            # being erased by old bookkeeping.
            if cursor.rowcount not in (0, 1):
                raise RuntimeError("supervision state changed during host start")

    def _mark_unchanged(self, state: _TrackedState, now: float) -> bool:
        """Record one proven quiet comparison without model admission."""
        with self._supervisor._supervision_state_transaction(
            schema_objects=_SUPERVISION_SCHEMA_OBJECTS,
            schema_initializer=_ensure_schema,
        ) as connection:
            cursor = connection.execute(
                """UPDATE nova_supervision_space_state
                   SET last_checked_at = ?, last_check_code = 'unchanged',
                       last_outcome_code = 'unchanged', updated_at = ?
                   WHERE target_key = ? AND target_space_id = ?
                     AND root_fingerprint = ? AND governance_revision = ?
                     AND pending_digest = '' AND current_reference_digest = ?
                     AND last_evaluated_reference_digest = ? AND last_checked_at IS ?
                     AND last_check_code = ?""",
                (
                    now,
                    now,
                    state.target_key,
                    state.target_space_id,
                    state.root_fingerprint,
                    state.governance_revision,
                    state.current_reference_digest,
                    state.last_evaluated_reference_digest,
                    state.last_checked_at,
                    state.last_check_code,
                ),
            )
        if cursor.rowcount not in (0, 1):
            raise RuntimeError("supervision equality state changed unexpectedly")
        return cursor.rowcount == 1


def _ensure_schema(connection: sqlite3.Connection) -> None:
    # These opaque identity tombstones are retained up to a fixed admission
    # cap. Evicting a digest turns an old external event into a fresh autonomous
    # trigger and violates exactly-once semantics, so capacity is fail-closed.
    # Pending state below is separately bounded, preventing one Space from
    # inflating an individual intent.
    connection.execute(
        """CREATE TABLE IF NOT EXISTS nova_supervision_signals (
            signal_digest TEXT PRIMARY KEY,
            target_key TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            observed_at REAL NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS nova_supervision_space_state (
            target_key TEXT PRIMARY KEY,
            target_space_id TEXT NOT NULL DEFAULT '',
            root_fingerprint TEXT NOT NULL DEFAULT '',
            pending_digest TEXT NOT NULL,
            pending_reason_code TEXT NOT NULL,
            pending_count INTEGER NOT NULL,
            governance_revision INTEGER NOT NULL DEFAULT -1,
            last_started_at REAL,
            current_reference_digest TEXT NOT NULL DEFAULT '',
            last_evaluated_reference_digest TEXT NOT NULL DEFAULT '',
            last_checked_at REAL,
            last_check_code TEXT NOT NULL DEFAULT '',
            last_outcome_code TEXT NOT NULL,
            updated_at REAL NOT NULL
        )"""
    )
    state_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(nova_supervision_space_state)")
    }
    for column, definition in (
        ("target_space_id", "TEXT NOT NULL DEFAULT ''"),
        ("root_fingerprint", "TEXT NOT NULL DEFAULT ''"),
        ("governance_revision", "INTEGER NOT NULL DEFAULT -1"),
        ("current_reference_digest", "TEXT NOT NULL DEFAULT ''"),
        ("last_evaluated_reference_digest", "TEXT NOT NULL DEFAULT ''"),
        ("last_checked_at", "REAL"),
        ("last_check_code", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in state_columns:
            connection.execute(
                f"ALTER TABLE nova_supervision_space_state ADD COLUMN {column} {definition}"
            )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_nova_supervision_signals_observed
           ON nova_supervision_signals(observed_at)"""
    )


def _target_key(value: object) -> str:
    if not isinstance(value, str) or _TARGET_RE.fullmatch(value) is None:
        raise ValueError("Nova supervision target key is invalid")
    return value


def _source(value: object) -> str:
    if not isinstance(value, str) or value not in _SOURCES:
        raise ValueError("Nova supervision signal source is invalid")
    return value


def _event_id(value: object) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise ValueError("Nova supervision signal identity is invalid")
    return value


def _reason_code(value: object) -> str:
    if not isinstance(value, str) or value not in _REASON_CODES:
        raise ValueError("Nova supervision signal reason is invalid")
    return value


def _stored_reason(value: object) -> str:
    if value == _PERIODIC_REASON or value == _MERGED_REASON:
        return str(value)
    return _reason_code(value)


def _merge_reason(current: object, incoming: str) -> str:
    previous = _stored_reason(current)
    if previous == _PERIODIC_REASON or previous == incoming:
        return incoming
    return _MERGED_REASON


def _pending_digest(value: object) -> str:
    if value == "":
        return ""
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Nova supervision pending digest is invalid")
    return value


def _supervision_state_columns_present(connection: sqlite3.Connection) -> bool:
    try:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(nova_supervision_space_state)")
        }
    except sqlite3.Error:
        return False
    return _SUPERVISION_STATE_COLUMNS <= columns


def _state_space_id(value: object) -> str:
    if not isinstance(value, str) or _SPACE_ID_RE.fullmatch(value) is None:
        raise ValueError("Nova supervision Space binding is invalid")
    return value


def _state_root_fingerprint(value: object) -> str:
    digest = _pending_digest(value)
    if not digest:
        raise ValueError("Nova supervision root binding is invalid")
    return digest


def _governance_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Nova supervision governance revision is invalid")
    return value


def _check_code(value: object) -> str:
    if value not in ("", "unchanged"):
        raise ValueError("Nova supervision checkpoint code is invalid")
    return str(value)


def _row_matches_governance(
    row: Mapping[str, object], governance: ManagedSpaceGovernance
) -> bool:
    try:
        return (
            _state_space_id(row["target_space_id"]) == governance.space_id
            and _state_root_fingerprint(row["root_fingerprint"])
            == governance.root_fingerprint
            and _governance_revision(row["governance_revision"]) == governance.revision
        )
    except (KeyError, TypeError, ValueError):
        return False


def _state_matches_governance(
    state: _TrackedState, governance: ManagedSpaceGovernance
) -> bool:
    return (
        state.target_space_id == governance.space_id
        and state.root_fingerprint == governance.root_fingerprint
        and state.governance_revision == governance.revision
    )


def _quiet_check_due(state: _TrackedState, now: float) -> bool:
    anchors = [
        value for value in (state.last_started_at, state.last_checked_at) if value is not None
    ]
    return bool(anchors) and now - max(anchors) >= _MIN_CHECK_SECONDS


def _references_are_equal(state: _TrackedState) -> bool:
    return (
        bool(state.current_reference_digest)
        and state.current_reference_digest == state.last_evaluated_reference_digest
    )


def _epoch(value: object) -> float:
    if value is None:
        return time.time()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Nova supervision epoch must be numeric")
    epoch = float(value)
    if not math.isfinite(epoch) or epoch < 0:
        raise ValueError("Nova supervision epoch is invalid")
    return epoch


def _digest(value: Mapping[str, object]) -> str:
    return sha256(
        json.dumps(
            dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()


def _bounded_outcome(value: object) -> str:
    allowed = {
        "",
        "started",
        "ineligible",
        "active_limit",
        "coalesced",
        "start_failed",
        "admission_failed",
        "admission_rejected",
        "unchanged",
    }
    return str(value) if value in allowed else ""
