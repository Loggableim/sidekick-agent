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
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Callable, Iterable, Mapping

from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from nova.feedback_adapter import LocalNovaFeedbackAdapter


_SOURCES = frozenset({"git", "kanban", "ci", "heartbeat"})
_REASON_CODES = frozenset({"git_change", "kanban_change", "ci_change", "ci_failed", "periodic_check"})
_PERIODIC_REASON = "periodic_check"
_MERGED_REASON = "multiple_changes"
_AUTONOMOUS_GOALS = {
    "git_change": (
        "Autonomous maintenance for the enrolled Space: inspect the latest code "
        "change, verify the project, and implement the smallest safe improvement."
    ),
    "kanban_change": (
        "Autonomous maintenance for the enrolled Space: review the current work "
        "queue, verify the project, and advance the highest-value safe task."
    ),
    "ci_change": (
        "Autonomous maintenance for the enrolled Space: review the CI result, "
        "verify the project, and implement the smallest safe improvement."
    ),
    "ci_failed": (
        "Autonomous maintenance for the enrolled Space: diagnose the failed CI "
        "signal, verify the project, and implement the smallest safe correction."
    ),
    "periodic_check": (
        "Autonomous maintenance for the enrolled Space: inspect current project "
        "health, run relevant verification, and implement one safe improvement if justified."
    ),
    "multiple_changes": (
        "Autonomous maintenance for the enrolled Space: reconcile recent changes, "
        "verify the project, and implement the smallest safe improvement."
    ),
}
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_TARGET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_MIN_CHECK_SECONDS = 15 * 60
_PENDING_RETRY_BACKOFF_CODES = frozenset({
    "active_limit", "admission_failed", "admission_rejected", "start_failed", "ineligible", "verifier_unavailable", "governance_unavailable"
})
_GOVERNANCE_RETRY_BACKOFF_CODES = frozenset({"governance_unavailable", "ineligible"})
_MAX_PENDING_SIGNALS = 256
_MAX_DEDUPLICATED_SIGNAL_IDENTITIES = 2_048
_SPACE_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_ACTIVE_RUNTIME_LOCK = threading.RLock()
_ACTIVE_RUNTIME: "NovaSpaceSupervisionRuntime | None" = None
_TICKER_EVENT_LOCK = threading.RLock()
_TICKER_EVENT_MAX_BYTES = 512
_TICKER_EVENT_ROTATE_BYTES = 256 * 1024
_TICKER_EVENT_KEEP_BYTES = 128 * 1024
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
    "nova_supervision_heartbeat_checkpoints",
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
        readiness_check: Callable[[Path], bool] | None = None,
        governance_snapshots: Callable[[], object] | None = None,
        feedback_adapter: LocalNovaFeedbackAdapter | None = None,
        entity_event_sink: Callable[[dict[str, object]], object] | None = None,
    ) -> None:
        if not isinstance(supervisor, ManagedSpaceSupervisor):
            raise TypeError("Nova Space supervision requires a managed supervisor")
        if not callable(dispatch_run):
            raise TypeError("Nova Space supervision requires a host dispatcher")
        self._supervisor = supervisor
        self._dispatch_run = dispatch_run
        self._readiness_check = readiness_check
        # Optional host-owned, read-only registry snapshot. It lets enrolled
        # YOLO Spaces receive their first periodic check without an external
        # Git/Kanban/CI edge; this runtime never invokes a model/provider here.
        if governance_snapshots is not None and not callable(governance_snapshots):
            raise TypeError("Nova supervision governance snapshots require a callable")
        self._governance_snapshots = governance_snapshots
        if feedback_adapter is not None and not isinstance(feedback_adapter, LocalNovaFeedbackAdapter):
            raise TypeError("Nova supervision feedback adapter is invalid")
        if entity_event_sink is not None and not callable(entity_event_sink):
            raise TypeError("Nova supervision entity event sink is invalid")
        self._feedback_adapter = feedback_adapter
        self._entity_event_sink = entity_event_sink

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
        heartbeat_bucket = _heartbeat_bucket(normalized_source, normalized_event_id, normalized_reason)
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
            if heartbeat_bucket is None:
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
            else:
                checkpoint = connection.execute(
                    "SELECT latest_bucket FROM nova_supervision_heartbeat_checkpoints WHERE target_key = ?",
                    (target,),
                ).fetchone()
                if checkpoint is not None and heartbeat_bucket <= int(checkpoint[0]):
                    return False
                connection.execute(
                    """INSERT INTO nova_supervision_heartbeat_checkpoints
                       (target_key, latest_bucket) VALUES (?, ?)
                       ON CONFLICT(target_key) DO UPDATE SET latest_bucket=excluded.latest_bucket
                       WHERE excluded.latest_bucket > nova_supervision_heartbeat_checkpoints.latest_bucket""",
                    (target, heartbeat_bucket),
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
        _append_ticker_event(
            self._supervisor,
            target_key=target,
            source=normalized_source,
            reason_code=normalized_reason,
            event_id=signal_digest,
            stage="observed",
            heartbeat_bucket=heartbeat_bucket,
            observed_at=observed_at,
            governance=governance,
        )
        return True

    def pulse(self, *, now_epoch: float | None = None) -> tuple[SupervisionPulseOutcome, ...]:
        """Perform one cheap, externally scheduled supervision pass.

        A pending verified signal runs immediately.  The host heartbeat uses
        the same durable signal path at most once per 900-second bucket, so an
        enrolled YOLO Space can keep working without a Git/Kanban/CI edge while
        preserving the global admission and exactly-once gates.
        """
        now = _epoch(now_epoch)
        # Seed only current, explicitly enrolled YOLO governance snapshots.
        # The durable row remains subject to the normal global admission and
        # Exactly-once gates below.
        self._seed_governance_snapshots(now)
        outcomes: list[SupervisionPulseOutcome] = []
        for state in self._tracked_states():
            # A failed governance lookup is itself a bounded retry boundary;
            # do not call an unhealthy registry on every one-minute heartbeat.
            if (
                state.pending_digest
                and state.last_check_code in _GOVERNANCE_RETRY_BACKOFF_CODES
                and state.last_checked_at is not None
                and now - state.last_checked_at < _MIN_CHECK_SECONDS
            ):
                continue
            # A resolver can transiently fail while a Space is being deleted,
            # moved, or its registry is being reloaded. The host ticker must
            # remain alive and must never fall through to admission without a
            # verified governance snapshot. Keep the accepted intent pending
            # behind the normal bounded retry floor instead.
            try:
                governance = self._supervisor.current_governance(state.target_key)
            except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                try:
                    self._mark_retryable_outcome(state, now, "governance_unavailable")
                except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                    pass
                outcomes.append(
                    SupervisionPulseOutcome(state.target_key, "governance_unavailable")
                )
                continue
            if (
                governance is None
                or governance.yolo is not True
                or governance.enrolled is not True
            ):
                if state.pending_digest:
                    # A revoked or temporarily ineligible Space is not an
                    # admission candidate. Persist the bounded checkpoint so
                    # the one-minute host ticker does not hammer the
                    # governance registry while the intent remains safely
                    # pending. A fresh code-owned signal clears this marker.
                    try:
                        self._mark_retryable_outcome(state, now, "ineligible")
                    except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                        pass
                    outcomes.append(SupervisionPulseOutcome(state.target_key, "ineligible"))
                continue
            if not _state_matches_governance(state, governance):
                # A stale durable scheduler row is not authority for a
                # recreated Space, moved root, or revised governance.  It is
                # retained only until a fresh, code-owned signal rebinding it
                # arrives; it may not start work or claim quiet equality.
                continue
            # A paused/active run occupies the one global Nova slot. Keep the
            # pending signal visible, but do not hammer admission every host
            # tick while the human-owned slot decision is unchanged. A fresh
            # code-owned signal clears last_checked_at in ingest_signal and
            # therefore bypasses this backoff immediately.
            if (
                state.pending_digest
                and state.last_check_code in _PENDING_RETRY_BACKOFF_CODES
                and state.last_checked_at is not None
                and now - state.last_checked_at < _MIN_CHECK_SECONDS
            ):
                # A slot collision is different from a provider/admission
                # failure: as soon as the other Space finishes, the waiting
                # employee should wake on the next host pulse instead of
                # sleeping for the full 15-minute retry floor. Keep this
                # read-only and fail closed when the ledger cannot be read;
                # ``admit`` remains the race-safe final gate below.
                if state.last_check_code == "active_limit":
                    try:
                        if self._supervisor.has_other_active_admissions(state.target_key):
                            continue
                    except (OSError, RuntimeError, sqlite3.Error):
                        continue
                else:
                    continue
            if self._readiness_check is not None:
                try:
                    ready = bool(self._readiness_check(governance.canonical_root))
                except (OSError, RuntimeError, TypeError, ValueError):
                    ready = False
                if not ready:
                    self._mark_retryable_outcome(state, now, 'verifier_unavailable')
                    outcomes.append(SupervisionPulseOutcome(state.target_key, 'verifier_unavailable'))
                    continue
            try:
                recovery_status, recovery_run_id = self._supervisor.auto_resume_recoverable_run(
                    state.target_key,
                    dispatcher=self._dispatch_run,
                )
            except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                # Recovery only has authority after a verified ledger/catalog
                # read. A transient failure must not abort the host heartbeat
                # or fall through to a fresh admission; retain the signal and
                # wait behind the existing bounded retry checkpoint instead.
                try:
                    self._mark_retryable_outcome(state, now, "admission_failed")
                except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                    pass
                outcomes.append(SupervisionPulseOutcome(state.target_key, "admission_failed"))
                continue
            if recovery_status != "none":
                if recovery_status == "auto_resumed":
                    self._mark_auto_resumed(state, now)
                outcomes.append(
                    SupervisionPulseOutcome(
                        state.target_key,
                        recovery_status,
                        recovery_run_id,
                    )
                )
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
                "goal": _AUTONOMOUS_GOALS.get(
                    reason_code, _AUTONOMOUS_GOALS[_PERIODIC_REASON]
                ),
            }
            try:
                admission = self._supervisor.admit(state.target_key, intent)
            except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                # Admission failures can be transient. Keep the event pending,
                # but persist a bounded retry checkpoint so the one-minute
                # host heartbeat does not hammer the same boundary.
                self._mark_retryable_outcome(state, now, "admission_failed")
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
                    # Host dispatch can fail after admission. Bound retries
                    # while preserving the pending intent for a later pulse.
                    self._mark_retryable_outcome(state, now, "start_failed")
                    outcomes.append(
                        SupervisionPulseOutcome(
                            state.target_key,
                            "start_failed",
                            admission.run_id,
                        )
                    )
                continue
            if admission.reason == "not_yolo_enrolled":
                self._mark_retryable_outcome(state, now, "ineligible")
                outcomes.append(SupervisionPulseOutcome(state.target_key, "ineligible"))
            elif admission.reason == "active_limit":
                self._mark_active_limit(state, now)
                outcomes.append(SupervisionPulseOutcome(state.target_key, "active_limit"))
            elif admission.status == "coalesced":
                outcomes.append(
                    SupervisionPulseOutcome(state.target_key, "coalesced", admission.run_id)
                )
            else:
                self._mark_retryable_outcome(state, now, "admission_rejected")
                outcomes.append(
                    SupervisionPulseOutcome(
                        state.target_key,
                        "admission_rejected",
                        admission.run_id,
                    )
                )
        self._publish_feedback(outcomes)
        return tuple(outcomes)

    def _publish_feedback(self, outcomes: Iterable[SupervisionPulseOutcome]) -> None:
        """Publish bounded local feedback as redacted Nova entity events."""
        adapter = self._feedback_adapter
        sink = self._entity_event_sink
        if adapter is None or sink is None:
            return
        for outcome in outcomes:
            try:
                result = adapter.send(
                    f"Space {outcome.target_key} supervision outcome: {outcome.status}",
                    timeout=5.0,
                    correlation_id=outcome.run_id or outcome.target_key,
                )
                sink({
                    "type": "nova_feedback",
                    "source": "local_feedback_adapter",
                    "payload": {
                        "target_key": outcome.target_key,
                        "run_id": outcome.run_id,
                        "status": result.status,
                        "detail": result.detail[:200],
                    },
                    "visibility": "private",
                    "correlation_id": outcome.run_id or outcome.target_key,
                })
            except Exception:
                continue

    def _seed_governance_snapshots(self, now: float) -> None:
        """Seed bounded periodic intents from a current governance snapshot.

        Discovery is advisory and fail-closed: malformed/unavailable registry
        data is ignored, while only literal ``yolo`` + ``enrolled`` records
        become a redacted periodic intent. No model/provider or Space write is
        performed by this callback.
        """
        provider = self._governance_snapshots
        if provider is None:
            return
        try:
            raw = provider()
        except Exception:
            return
        if isinstance(raw, Mapping):
            candidates = raw.items()
        elif isinstance(raw, Iterable) and not isinstance(raw, (str, bytes, bytearray)):
            candidates = ((getattr(item, "target_key", ""), item) for item in raw)
        else:
            return
        snapshots: list[tuple[str, ManagedSpaceGovernance]] = []
        try:
            for key, value in candidates:
                if len(snapshots) >= _MAX_PENDING_SIGNALS:
                    break
                if not isinstance(key, str) or not isinstance(value, ManagedSpaceGovernance):
                    continue
                try:
                    target = _target_key(key)
                except (TypeError, ValueError):
                    continue
                if value.yolo is True and value.enrolled is True:
                    snapshots.append((target, value))
        except Exception:
            return
        if not snapshots:
            return
        try:
            with self._supervisor._supervision_state_transaction(
                schema_objects=_SUPERVISION_SCHEMA_OBJECTS,
                schema_initializer=_ensure_schema,
            ) as connection:
                for target, governance in sorted(snapshots, key=lambda item: item[0]):
                    periodic_digest = _digest({
                        "target_key": target,
                        "space_id": governance.space_id,
                        "root_fingerprint": governance.root_fingerprint,
                        "governance_revision": governance.revision,
                        "reason_code": _PERIODIC_REASON,
                    })
                    current = connection.execute(
                        """SELECT target_space_id, root_fingerprint,
                                  governance_revision
                           FROM nova_supervision_space_state
                           WHERE target_key = ?""",
                        (target,),
                    ).fetchone()
                    if current is not None and _row_matches_governance(current, governance):
                        continue
                    values = (
                        target, governance.space_id, governance.root_fingerprint,
                        periodic_digest, _PERIODIC_REASON, governance.revision,
                        periodic_digest, now,
                    )
                    if current is None:
                        connection.execute(
                            """INSERT INTO nova_supervision_space_state
                               (target_key, target_space_id, root_fingerprint,
                                pending_digest, pending_reason_code, pending_count,
                                governance_revision, last_started_at,
                                current_reference_digest,
                                last_evaluated_reference_digest, last_checked_at,
                                last_check_code, last_outcome_code, updated_at)
                               VALUES (?, ?, ?, ?, ?, 1, ?, NULL, ?, '', NULL,
                                       '', '', ?)""",
                            values,
                        )
                    else:
                        # Never inherit pending work across a changed root,
                        # Space identity, or governance revision.
                        connection.execute(
                            """UPDATE nova_supervision_space_state
                               SET target_space_id = ?, root_fingerprint = ?,
                                   pending_digest = ?, pending_reason_code = ?,
                                   pending_count = 1, governance_revision = ?,
                                   last_started_at = NULL,
                                   current_reference_digest = ?,
                                   last_evaluated_reference_digest = '',
                                   last_checked_at = NULL, last_check_code = '',
                                   last_outcome_code = '', updated_at = ?
                               WHERE target_key = ?""",
                            (
                                governance.space_id, governance.root_fingerprint,
                                periodic_digest, _PERIODIC_REASON,
                                governance.revision, periodic_digest, now, target,
                            ),
                        )
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
            return

    def status(self) -> tuple[SupervisionStatus, ...]:
        """Read current pulse visibility without initializing any store."""
        with self._supervisor._supervision_state_reader() as connection:
            if connection is None:
                return ()
            try:
                rows = connection.execute(
                    """SELECT state.target_key, state.pending_digest,
                              state.last_started_at, state.last_outcome_code,
                              latest.state AS latest_admission_state
                       FROM nova_supervision_space_state AS state
                       LEFT JOIN supervisor_admissions AS latest
                         ON latest.admission_id = (
                              SELECT admission_id
                              FROM supervisor_admissions
                              WHERE target_key = state.target_key
                              ORDER BY updated_at DESC, admission_id DESC
                              LIMIT 1
                         )
                       ORDER BY state.target_key ASC"""
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
                last_outcome=_bounded_outcome(
                    _status_outcome(
                        row["last_outcome_code"], row["latest_admission_state"]
                    )
                ),
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

    def _mark_active_limit(self, state: _TrackedState, now: float) -> bool:
        """Record a slot block without clearing the still-pending signal."""
        return self._mark_retryable_outcome(state, now, "active_limit")

    def _mark_retryable_outcome(self, state: _TrackedState, now: float, outcome: str) -> bool:
        """Keep a pending signal while bounding repeated admission attempts."""
        if outcome not in _PENDING_RETRY_BACKOFF_CODES:
            return False
        with self._supervisor._supervision_state_transaction(
            schema_objects=_SUPERVISION_SCHEMA_OBJECTS,
            schema_initializer=_ensure_schema,
        ) as connection:
            cursor = connection.execute(
                """UPDATE nova_supervision_space_state
                   SET last_checked_at = ?, last_check_code = ?,
                       last_outcome_code = ?, updated_at = ?
                   WHERE target_key = ? AND target_space_id = ?
                     AND root_fingerprint = ? AND governance_revision = ?
                     AND pending_digest = ? AND last_checked_at IS ?
                     AND last_check_code = ?""",
                (
                    now,
                    outcome,
                    outcome,
                    now,
                    state.target_key,
                    state.target_space_id,
                    state.root_fingerprint,
                    state.governance_revision,
                    state.pending_digest,
                    state.last_checked_at,
                    state.last_check_code,
                ),
            )
        if cursor.rowcount not in (0, 1):
            raise RuntimeError("supervision retry state changed unexpectedly")
        return cursor.rowcount == 1

    def _mark_auto_resumed(self, state: _TrackedState, now: float) -> None:
        """Record recovery while preserving a newer pending intent."""
        with self._supervisor._supervision_state_transaction(
            schema_objects=_SUPERVISION_SCHEMA_OBJECTS,
            schema_initializer=_ensure_schema,
        ) as connection:
            cursor = connection.execute(
                """UPDATE nova_supervision_space_state
                   SET last_started_at = ?, last_evaluated_reference_digest = ?,
                       last_checked_at = NULL, last_check_code = '',
                       last_outcome_code = 'auto_resumed', updated_at = ?
                   WHERE target_key = ? AND target_space_id = ?
                     AND root_fingerprint = ? AND governance_revision = ?
                     AND pending_digest = ? AND current_reference_digest = ?
                     AND last_evaluated_reference_digest = ?
                     AND last_checked_at IS ? AND last_check_code = ?""",
                (
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
            if cursor.rowcount not in (0, 1):
                raise RuntimeError("supervision state changed during auto-resume")


    def wake_space(self, target_key: str) -> bool:
        """Clear only the active-slot backoff after an authorized human release."""
        target = _target_key(target_key)
        with self._supervisor._supervision_state_transaction(
            schema_objects=_SUPERVISION_SCHEMA_OBJECTS,
            schema_initializer=_ensure_schema,
        ) as connection:
            cursor = connection.execute(
                """UPDATE nova_supervision_space_state
                   SET last_checked_at = NULL, last_check_code = '',
                       last_outcome_code = 'human_release', updated_at = ?
                   WHERE target_key = ? AND pending_digest <> ''
                     AND last_check_code = 'active_limit'""",
                (time.time(), target),
            )
        return cursor.rowcount == 1


def install_active_runtime(runtime: "NovaSpaceSupervisionRuntime") -> None:
    """Publish the host-owned runtime for code-owned event bridges."""
    if not isinstance(runtime, NovaSpaceSupervisionRuntime):
        raise TypeError("active Nova supervision runtime is invalid")
    with _ACTIVE_RUNTIME_LOCK:
        global _ACTIVE_RUNTIME
        _ACTIVE_RUNTIME = runtime


def clear_active_runtime(runtime: "NovaSpaceSupervisionRuntime | None" = None) -> None:
    """Remove the runtime without affecting a newer host instance."""
    with _ACTIVE_RUNTIME_LOCK:
        global _ACTIVE_RUNTIME
        if runtime is None or _ACTIVE_RUNTIME is runtime:
            _ACTIVE_RUNTIME = None


def emit_code_owned_signal(
    target_key: str,
    *,
    source: str,
    event_id: str,
    reason_code: str,
) -> bool:
    """Best-effort bridge for trusted in-process Git/Kanban/CI producers."""
    with _ACTIVE_RUNTIME_LOCK:
        runtime = _ACTIVE_RUNTIME
    if runtime is None:
        # Producers such as the managed action gateway may live in a separate
        # host process from the dashboard ticker.  In that process there is no
        # in-memory runtime to publish to, but the durable supervisor ledger is
        # still the authoritative, redacted signal boundary.  Ingest only;
        # never pulse or dispatch from this fallback.
        try:
            from nova.space_supervisor import get_production_managed_space_supervisor

            supervisor = get_production_managed_space_supervisor()
            runtime = NovaSpaceSupervisionRuntime(
                supervisor=supervisor,
                dispatch_run=lambda *_args: None,
            )
        except Exception:
            return False
    try:
        return bool(runtime.ingest_signal(
            target_key,
            source=source,
            event_id=event_id,
            reason_code=reason_code,
        ))
    except Exception:
        # An observability hook must never break its originating operation.
        return False


def wake_code_owned_space(target_key: str) -> bool:
    """Wake a pending Space after a successful host-authorized slot release."""
    with _ACTIVE_RUNTIME_LOCK:
        runtime = _ACTIVE_RUNTIME
    if runtime is None:
        return False
    try:
        return bool(runtime.wake_space(target_key))
    except Exception:
        return False


def append_ticker_outcomes(
    supervisor: ManagedSpaceSupervisor,
    outcomes: object,
    *,
    event_ids: Mapping[str, str] | None = None,
    observed_at: float | None = None,
) -> None:
    """Append a redacted internal bridge notice for pulse outcomes.

    This is deliberately an in-process, append-only feed for Nova's dashboard.
    It does not send Telegram, GitHub, or arbitrary inter-space messages and it
    is only written by the host ticker after a governed pulse.
    """
    if not isinstance(supervisor, ManagedSpaceSupervisor):
        return
    timestamp = time.time() if observed_at is None else float(observed_at)
    for outcome in outcomes if isinstance(outcomes, (tuple, list)) else ():
        target = getattr(outcome, "target_key", "")
        status = getattr(outcome, "status", "")
        if not isinstance(target, str) or not isinstance(status, str):
            continue
        try:
            governance = supervisor.current_governance(target)
        except Exception:
            continue
        if governance is None or governance.yolo is not True or governance.enrolled is not True:
            continue
        outcome_status = str(status).strip().lower()
        # Keep global-slot contention as a durable, redacted reason distinct
        # from generic admission failure while preserving the pending intent.
        outcome_reason = (
            "skipped_slot_occupied"
            if outcome_status == "active_limit"
            else outcome_status
        )
        if outcome_status == "waiting_for_catalog":
            outcome_reason = "model_chain_exhausted"
        digest_id = _digest({
            "target_key": target,
            "status": outcome_status,
            "run_id": str(getattr(outcome, "run_id", "") or ""),
        })
        record_stage = "handled" if outcome_status in {
            "started", "auto_resumed", "coalesced", "unchanged", "completed"
        } else "handled"
        _append_ticker_event(
            supervisor,
            target_key=target,
            source="bridge",
            reason_code=outcome_reason,
            event_id=(digest_id if outcome_status == "waiting_for_catalog" else (event_ids or {}).get(
                target,
                _digest({"target_key": target, "status": status, "at": timestamp}),
            )),
            stage=record_stage,
            status=("pending" if outcome_status == "waiting_for_catalog" else ("handled" if outcome_status in {
                "started", "auto_resumed", "coalesced", "unchanged", "completed"
            } else "failed")),
            observed_at=timestamp,
        )


def ticker_event_log_path(supervisor: ManagedSpaceSupervisor) -> Path:
    """Return the host-owned event-log path without creating it."""
    return Path(supervisor._ledger_path).with_name("ticker_events.jsonl")


def _append_ticker_event(
    supervisor: ManagedSpaceSupervisor,
    *,
    target_key: str,
    source: str,
    reason_code: str,
    event_id: str,
    stage: str,
    status: str = "pending",
    heartbeat_bucket: int | None = None,
    observed_at: float,
    governance: ManagedSpaceGovernance | None = None,
) -> None:
    """Write only fixed, redacted fields to the append-only ticker feed."""
    try:
        target = _target_key(target_key)
        source_value = source if source in _SOURCES or source == "bridge" else ""
        reason = reason_code if len(reason_code) <= 64 and re.fullmatch(r"[a-z0-9_:-]+", reason_code or "") else ""
        if not source_value or not reason or stage not in {"observed", "handled"}:
            return
        if status not in {"pending", "handled", "failed"}:
            return
        if governance is None:
            governance = supervisor.current_governance(target)
        if governance is None or governance.yolo is not True or governance.enrolled is not True:
            return
        record = {
            "event_id": str(event_id)[:64],
            "space": target,
            "source": source_value,
            "reason": reason,
            "stage": stage,
            "status": status,
            "at": round(float(observed_at), 3),
        }
        if source_value == "heartbeat" and reason == _PERIODIC_REASON and heartbeat_bucket is not None and 0 <= int(heartbeat_bucket) <= 10**15:
            record["heartbeat_bucket"] = int(heartbeat_bucket)
        encoded = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
        if len(encoded.encode("utf-8")) > _TICKER_EVENT_MAX_BYTES:
            return
        path = ticker_event_log_path(supervisor)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _TICKER_EVENT_LOCK:
            incoming = (encoded + "\n").encode("utf-8")
            try:
                current_size = path.stat().st_size if path.exists() else 0
            except OSError:
                current_size = 0
            if current_size + len(incoming) > _TICKER_EVENT_ROTATE_BYTES:
                retained = b""
                try:
                    with path.open("rb") as source:
                        source.seek(max(0, current_size - _TICKER_EVENT_KEEP_BYTES))
                        retained = source.read()
                    if retained and current_size > _TICKER_EVENT_KEEP_BYTES:
                        first_newline = retained.find(b"\n")
                        retained = retained[first_newline + 1:] if first_newline >= 0 else b""
                except OSError:
                    retained = b""
                temp = path.with_name(path.name + ".rotate.tmp")
                try:
                    with temp.open("wb") as target:
                        target.write(retained)
                        target.write(incoming)
                    os.replace(temp, path)
                except OSError:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                with path.open("ab") as handle:
                    handle.write(incoming)
    except Exception:
        # Telemetry is strictly best effort and must never stop supervision.
        return

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
        """CREATE TABLE IF NOT EXISTS nova_supervision_heartbeat_checkpoints (
            target_key TEXT PRIMARY KEY,
            latest_bucket INTEGER NOT NULL
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


def _heartbeat_bucket(source: str, event_id: str, reason_code: str) -> int | None:
    if source != "heartbeat" or reason_code != _PERIODIC_REASON:
        return None
    match = re.fullmatch(r"heartbeat:([0-9]{1,15})", event_id)
    return int(match.group(1)) if match is not None else None


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
    # Every retry outcome written by _mark_retryable_outcome must survive a
    # ticker restart; otherwise _tracked_states drops the pending signal.
    allowed = {"", "unchanged", *_PENDING_RETRY_BACKOFF_CODES}
    if value not in allowed:
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
        "running",
        "completed",
        "cancelled",
        "abandoned",
        "paused",
        "failed",
        "ineligible",
        "active_limit",
        "coalesced",
        "start_failed",
        "admission_failed",
        "admission_rejected",
        "unchanged",
        "waiting_for_catalog",
        "governance_unavailable",
    }
    return str(value) if value in allowed else ""


def _status_outcome(last_outcome: object, admission_state: object) -> str:
    """Prefer the durable child-run terminal state for read-only status views."""
    state = str(admission_state or "").strip().lower()
    if state in {"completed", "cancelled", "abandoned", "paused", "failed"}:
        return state
    return str(last_outcome or "").strip()
