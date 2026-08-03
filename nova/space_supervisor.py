"""Global, fail-closed admission for Nova supervision of enrolled Spaces.

This module is deliberately separate from :mod:`nova.swarm_runtime_bridge`.
The bridge remains a same-project boundary; this supervisor owns the one
cross-Space admission slot and keeps its authority in process only.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Callable, Iterator, Mapping
from uuid import UUID, uuid4

from swarm_core.engine import PreCompletionContext, PreCompletionResult
from swarm_core.store import ProjectSwarmStore
from swarm_core.types import SwarmRun


DASHBOARD_ACTOR_RE = re.compile(r"dashboard:[0-9a-f]{64}\Z")
_INTENT_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_RUN_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_CHILD_PAUSE_REASON_RE = re.compile(r"[A-Za-z0-9_.:-]{1,96}\Z")
SYSTEM_SPACE_LIFECYCLE_ACTOR = "system:space-lifecycle"
_CAPABILITY_TOKEN = object()
_ACTION_CONTEXT_TOKEN = object()
_OCCUPIED_STATES = frozenset(
    {
        "provisioning",
        "active",
        "paused",
        "cancelling",
        "abandoning",
    }
)
_EXECUTABLE_STATES = frozenset({"active"})
_ALLOWED_ACTION_FAMILIES = (
    "target_local_worktree",
    "github_publication",
    "target_deployment_worker",
)
_SPACE_CHANGE_PAUSE_REASONS = frozenset(
    {"governance_changed", "root_changed", "space_deleted"}
)
_RECOVERABLE_PROVIDER_PAUSE_REASONS = frozenset(
    {
        "no_eligible_model",
        "model_chain_exhausted",
        "model_provider_unavailable",
        "model_catalog_unavailable",
        "provider_unavailable",
    }
)
_OLLAMA_CLOUD_PROVIDER = "ollama-cloud"
_OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE = "ollama-cloud-api-live-verified"
_AUTO_RESUME_WAIT_AUDIT_EVENT = "auto_resume_waiting"
_AUTO_RESUME_WAIT_REASONS = frozenset(
    {"model_chain_exhausted", "pause_not_recoverable", "catalog_refresh_required"}
)
_SUPERVISOR_ADMISSION_COLUMNS = frozenset(
    {
        "admission_id",
        "target_key",
        "target_space_id",
        "intent_digest",
        "canonical_root",
        "root_fingerprint",
        "governance_revision",
        "policy_identity",
        "allowed_action_families_json",
        "workflow_contract_digest",
        "run_id",
        "state",
        "attachment_generation",
        "record_version",
        "created_at",
        "updated_at",
        "terminal_actor",
    }
)
_SUPERVISOR_REQUIRED_INDEX_SQL = {
    "idx_supervisor_target_intent": (
        "createuniqueindexidx_supervisor_target_intent"
        "onsupervisor_admissions(target_key,target_space_id,intent_digest,canonical_root,root_fingerprint,governance_revision)"
    ),
    "idx_supervisor_one_active": (
        "createuniqueindexidx_supervisor_one_active"
        "onsupervisor_admissions((1))wherestatein("
        "'provisioning','active','paused','cancelling','abandoning')"
    ),
    "idx_supervisor_admissions_target_updated": (
        "createindexidx_supervisor_admissions_target_updated"
        "onsupervisor_admissions(target_key,updated_atdesc)"
    ),
    "idx_supervisor_audit_admission_sequence": (
        "createindexidx_supervisor_audit_admission_sequence"
        "onsupervisor_audit(admission_id,sequencedesc)"
    ),
}


@dataclass(frozen=True, slots=True)
class ManagedSpaceGovernance:
    """Independently resolved, typed governance required for one target."""

    space_id: str
    canonical_root: Path
    root_fingerprint: str
    yolo: bool
    enrolled: bool
    revision: int
    policy_identity: str

    @classmethod
    def from_values(
        cls,
        *,
        space_id: object,
        canonical_root: object,
        root_fingerprint: object = "",
        yolo: object,
        enrolled: object,
        revision: object,
        policy_identity: object,
    ) -> "ManagedSpaceGovernance":
        try:
            normalized_id = UUID(str(space_id)).hex
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("managed Space requires a valid space_id") from exc
        root = Path(canonical_root).expanduser().resolve()
        fingerprint = _root_fingerprint(root)
        if root_fingerprint not in ("", fingerprint):
            raise ValueError("managed Space root fingerprint mismatch")
        if type(yolo) is not bool or type(enrolled) is not bool:
            raise ValueError("managed Space governance booleans must be literal")
        if type(revision) is not int or revision < 0:
            raise ValueError("managed Space governance revision is invalid")
        if not isinstance(policy_identity, str) or not policy_identity.strip():
            raise ValueError("managed Space policy identity is required")
        return cls(
            normalized_id,
            root,
            fingerprint,
            yolo,
            enrolled,
            revision,
            policy_identity.strip(),
        )


@dataclass(frozen=True, slots=True, init=False)
class ManagedSpaceCapability:
    """Host-owned authority; it is neither reconstructible nor serializable."""

    _admission_id: str
    _target_key: str
    _target_space_id: str
    _canonical_root: Path
    _root_fingerprint: str
    _governance_revision: int
    _policy_identity: str
    _run_id: str
    _intent_digest: str
    _allowed_action_families: tuple[str, ...]
    _allowed_action_families_digest: str
    _attachment_generation: int

    def __init__(self, *args: object, _token: object | None = None) -> None:
        if _token is not _CAPABILITY_TOKEN:
            raise TypeError("ManagedSpaceCapability is host-owned")
        (
            admission_id,
            target_key,
            target_space_id,
            canonical_root,
            root_fingerprint,
            governance_revision,
            policy_identity,
            run_id,
            intent_digest,
            allowed_action_families,
            allowed_action_families_digest,
            attachment_generation,
        ) = args
        object.__setattr__(self, "_admission_id", admission_id)
        object.__setattr__(self, "_target_key", target_key)
        object.__setattr__(self, "_target_space_id", target_space_id)
        object.__setattr__(self, "_canonical_root", canonical_root)
        object.__setattr__(self, "_root_fingerprint", root_fingerprint)
        object.__setattr__(self, "_governance_revision", governance_revision)
        object.__setattr__(self, "_policy_identity", policy_identity)
        object.__setattr__(self, "_run_id", run_id)
        object.__setattr__(self, "_intent_digest", intent_digest)
        object.__setattr__(self, "_allowed_action_families", allowed_action_families)
        object.__setattr__(
            self, "_allowed_action_families_digest", allowed_action_families_digest
        )
        object.__setattr__(self, "_attachment_generation", attachment_generation)

    def __reduce__(self):
        raise TypeError("ManagedSpaceCapability cannot be serialized")

    def __reduce_ex__(self, protocol: int):
        raise TypeError("ManagedSpaceCapability cannot be serialized")

    def __repr__(self) -> str:
        return "<ManagedSpaceCapability>"


@dataclass(frozen=True, slots=True, init=False)
class ManagedSpaceActionContext:
    """Frozen authority reconstructed from the current binding and ledger."""

    run_id: str
    canonical_root: Path
    attachment_generation: int
    allowed_action_families: tuple[str, ...]
    target_space_id: str

    def __init__(self, *args: object, _token: object | None = None) -> None:
        if _token is not _ACTION_CONTEXT_TOKEN:
            raise TypeError("ManagedSpaceActionContext is supervisor-owned")
        (
            run_id,
            canonical_root,
            attachment_generation,
            allowed_action_families,
            target_space_id,
        ) = args
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "canonical_root", canonical_root)
        object.__setattr__(self, "attachment_generation", attachment_generation)
        object.__setattr__(
            self,
            "allowed_action_families",
            allowed_action_families,
        )
        object.__setattr__(self, "target_space_id", target_space_id)

    def __reduce__(self):
        raise TypeError("ManagedSpaceActionContext cannot be serialized")

    def __reduce_ex__(self, protocol: int):
        raise TypeError("ManagedSpaceActionContext cannot be serialized")


@dataclass(frozen=True, slots=True)
class SupervisorAdmission:
    status: str
    admission_id: str | None
    run_id: str | None
    capability: ManagedSpaceCapability | None
    reason: str | None


class ManagedSpacePreCompletionHook:
    hook_id = "nova-managed-space-supervisor-v1"
    verifier_contract = "production-read-only-v1"

    def __init__(
        self, supervisor: "ManagedSpaceSupervisor", capability: ManagedSpaceCapability
    ) -> None:
        self._supervisor = supervisor
        self._capability = capability

    def run(self, context: PreCompletionContext) -> PreCompletionResult:
        capability = self._capability
        if (
            context.run.run_id != capability._run_id
            or Path(context.project_root).resolve() != capability._canonical_root
            or not _diagnostic_metadata_matches_capability(
                context.run.metadata, capability
            )
        ):
            self._supervisor._pause(capability, "capability_invalid")
            return PreCompletionResult(False, "capability_invalid")
        reason = self._supervisor._revalidate(capability)
        if reason is not None:
            self._supervisor._pause(capability, reason)
            return PreCompletionResult(False, reason)
        try:
            verifier_checkpoint = context.store.get_workflow_role_checkpoints(
                context.run.run_id
            ).get("verifier")
            verifier_decision = (
                verifier_checkpoint.data.get("decision")
                if verifier_checkpoint is not None
                and isinstance(verifier_checkpoint.data, Mapping)
                else None
            )
        except (AttributeError, KeyError, TypeError, ValueError, sqlite3.Error):
            verifier_decision = None
        if verifier_decision != "verified":
            self._supervisor._pause(capability, "verification_not_verified")
            return PreCompletionResult(False, "verification_not_verified")
        # Re-attest the current worktree at the final gate. A positive
        # checkpoint from before a builder change to .swarm/verify.json is
        # stale evidence and must not unlock completion.
        if context.run.metadata.get("production_verifier") == self.verifier_contract:
            try:
                from nova.production_verifier import ProductionReadOnlyVerifier
                from swarm_core.verifier import VerificationRequest

                checkpoints = context.store.get_workflow_role_checkpoints(
                    context.run.run_id
                )
                builder = checkpoints.get("builder")
                critic = checkpoints.get("critic")
                if builder is None or critic is None:
                    raise ValueError("missing builder or critic checkpoint")
                final_result = ProductionReadOnlyVerifier().verify(
                    VerificationRequest(
                        run_id=context.run.run_id,
                        goal=context.goal,
                        project_root=context.project_root,
                        builder=builder.data,
                        critic=critic.data,
                    )
                )
            except Exception:
                final_result = None
            if final_result is None or final_result.decision != "verified":
                self._supervisor._pause(capability, "verification_not_verified")
                return PreCompletionResult(False, "verification_not_verified")
        return PreCompletionResult(continue_completion=True)


class ManagedSpaceSupervisor:
    """The write-capable owner of a single global supervisor admission slot."""

    def __init__(
        self,
        *,
        ledger_path: Path,
        governance_resolver: Callable[[str], ManagedSpaceGovernance],
        child_store_factory: Callable[[Path], ProjectSwarmStore] = ProjectSwarmStore,
        action_executor: Any | None = None,
    ) -> None:
        if not callable(governance_resolver) or not callable(child_store_factory):
            raise TypeError("managed Space supervisor requires callable dependencies")
        if action_executor is not None and not callable(getattr(action_executor, "execute", None)):
            raise TypeError("managed Space action executor requires an execute method")
        self._ledger_path = Path(ledger_path)
        self._governance_resolver = governance_resolver
        self._child_store_factory = child_store_factory
        self._action_executor = action_executor
        self._bindings: dict[str, ManagedSpaceCapability] = {}
        self._bindings_lock = threading.RLock()

    def start(self) -> None:
        """Initialize the central ledger only on a write-capable start path."""
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self._ledger_path, timeout=30, isolation_level=None
        )
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            if not _supervisor_ledger_schema_ready(connection):
                self._initialize_ledger_schema(connection)
            else:
                _ensure_ticker_lease_schema(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_ledger_schema(self, connection: sqlite3.Connection) -> None:
        """Create/migrate the ledger exactly once while its write lock is held."""
        connection.execute(
            """CREATE TABLE IF NOT EXISTS supervisor_admissions (
                admission_id TEXT PRIMARY KEY, target_key TEXT NOT NULL,
                target_space_id TEXT NOT NULL, intent_digest TEXT NOT NULL,
                canonical_root TEXT NOT NULL, root_fingerprint TEXT NOT NULL,
                governance_revision INTEGER NOT NULL, policy_identity TEXT NOT NULL,
                allowed_action_families_json TEXT NOT NULL DEFAULT '[]',
                workflow_contract_digest TEXT NOT NULL DEFAULT '',
                run_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
                attachment_generation INTEGER NOT NULL DEFAULT 0,
                record_version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                terminal_actor TEXT
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS supervisor_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                admission_id TEXT NOT NULL REFERENCES supervisor_admissions(admission_id),
                event_type TEXT NOT NULL, actor TEXT, reason TEXT, created_at TEXT NOT NULL
            )"""
        )
        # The intent identity is bound to the complete Space capability. A
        # root move or governance revision must not collide with a historical
        # run carrying the same intent digest; migrate the old two-column
        # index before creating the capability-bound index.
        connection.execute("DROP INDEX IF EXISTS idx_supervisor_target_intent")
        connection.execute(
            """CREATE UNIQUE INDEX idx_supervisor_target_intent
               ON supervisor_admissions(
                   target_key, target_space_id, intent_digest, canonical_root,
                   root_fingerprint, governance_revision
               )"""
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(supervisor_admissions)")
        }
        if "allowed_action_families_json" not in columns:
            connection.execute(
                "ALTER TABLE supervisor_admissions ADD COLUMN allowed_action_families_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "workflow_contract_digest" not in columns:
            connection.execute(
                "ALTER TABLE supervisor_admissions ADD COLUMN workflow_contract_digest TEXT NOT NULL DEFAULT ''"
            )
        if "attachment_generation" not in columns:
            connection.execute(
                """ALTER TABLE supervisor_admissions
                   ADD COLUMN attachment_generation INTEGER NOT NULL DEFAULT 0"""
            )
        if "record_version" not in columns:
            connection.execute(
                """ALTER TABLE supervisor_admissions
                   ADD COLUMN record_version INTEGER NOT NULL DEFAULT 0"""
            )
        connection.execute("DROP INDEX IF EXISTS idx_supervisor_one_active")
        connection.execute(
            """CREATE UNIQUE INDEX idx_supervisor_one_active
               ON supervisor_admissions((1))
               WHERE state IN (
                   'provisioning', 'active', 'paused',
                   'cancelling', 'abandoning'
               )"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_supervisor_admissions_target_updated
               ON supervisor_admissions(target_key, updated_at DESC)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_supervisor_audit_admission_sequence
               ON supervisor_audit(admission_id, sequence DESC)"""
        )
        _ensure_ticker_lease_schema(connection)

    def acquire_ticker_lease(
        self,
        owner_id: str,
        *,
        now: float | None = None,
        ttl_seconds: float = 120.0,
    ) -> str | None:
        """Acquire the one durable host-ticker lease, or return ``None``.

        An expired lease is retained as ``orphaned`` for auditability.  This
        lease guards only the host ticker; it never resumes a child run and
        therefore cannot bypass the run ledger's Exactly-once admission.
        """
        owner_id = str(owner_id or "").strip()
        if not owner_id or ttl_seconds <= 0:
            raise ValueError("ticker lease owner and positive TTL are required")
        now = float(time.time() if now is None else now)
        self.start()
        lease_id = str(uuid4())
        try:
            with self._immediate_connection() as connection:
                active = connection.execute(
                    """SELECT lease_id, owner_id, expires_at
                       FROM supervisor_ticker_leases
                       WHERE state = 'active'
                       ORDER BY started_at DESC LIMIT 1"""
                ).fetchone()
                if active is not None:
                    if float(active["expires_at"]) > now:
                        return None
                    connection.execute(
                        """UPDATE supervisor_ticker_leases
                           SET state = 'orphaned', terminal_reason = ?, updated_at = ?
                           WHERE lease_id = ? AND state = 'active'""",
                        ("lease_expired", now, active["lease_id"]),
                    )
                connection.execute(
                    """INSERT INTO supervisor_ticker_leases
                       (lease_id, owner_id, state, started_at, expires_at, updated_at, terminal_reason)
                       VALUES (?, ?, 'active', ?, ?, ?, NULL)""",
                    (lease_id, owner_id, now, now + float(ttl_seconds), now),
                )
        except sqlite3.IntegrityError:
            # Another process won the unique active-lease race.
            return None
        return lease_id

    def reconcile_stale_ticker_leases(
        self, owner_alive: Callable[[str], bool], *, now: float | None = None
    ) -> tuple[str, ...]:
        """Audit-orphan ticker leases whose host is definitively gone.

        A live owner is never pre-empted.  The callback must therefore fail
        closed for unknown/malformed owners; only an explicit ``False`` may
        reclaim a lease before its TTL.  This keeps dashboard restarts quick
        without weakening the single-ticker invariant.
        """
        if not callable(owner_alive):
            raise TypeError("ticker owner liveness requires a callable")
        now_value = float(time.time() if now is None else now)
        self.start()
        orphaned: list[str] = []
        with self._immediate_connection() as connection:
            rows = connection.execute(
                """SELECT lease_id, owner_id, expires_at
                   FROM supervisor_ticker_leases WHERE state = 'active'"""
            ).fetchall()
            for row in rows:
                lease_id = str(row["lease_id"])
                expired = float(row["expires_at"]) <= now_value
                dead = False
                if not expired:
                    try:
                        dead = owner_alive(str(row["owner_id"])) is False
                    except Exception:
                        dead = False
                if not expired and not dead:
                    continue
                reason = "lease_expired" if expired else "host_restart_recovered"
                cursor = connection.execute(
                    """UPDATE supervisor_ticker_leases
                       SET state = 'orphaned', terminal_reason = ?, updated_at = ?
                       WHERE lease_id = ? AND state = 'active'""",
                    (reason, now_value, lease_id),
                )
                if cursor.rowcount:
                    orphaned.append(lease_id)
        return tuple(orphaned)

    def heartbeat_ticker_lease(
        self,
        lease_id: str,
        owner_id: str,
        *,
        now: float | None = None,
        ttl_seconds: float = 120.0,
    ) -> bool:
        if not lease_id or not owner_id or ttl_seconds <= 0:
            return False
        now = float(time.time() if now is None else now)
        self.start()
        with self._immediate_connection() as connection:
            cursor = connection.execute(
                """UPDATE supervisor_ticker_leases
                   SET expires_at = ?, updated_at = ?
                   WHERE lease_id = ? AND owner_id = ? AND state = 'active'
                     AND expires_at > ?""",
                (now + float(ttl_seconds), now, lease_id, owner_id, now),
            )
        return cursor.rowcount == 1

    def release_ticker_lease(
        self,
        lease_id: str,
        owner_id: str,
        *,
        reason: str = "released",
        now: float | None = None,
    ) -> bool:
        if not lease_id or not owner_id:
            return False
        now = float(time.time() if now is None else now)
        self.start()
        with self._immediate_connection() as connection:
            cursor = connection.execute(
                """UPDATE supervisor_ticker_leases
                   SET state = 'released', terminal_reason = ?, updated_at = ?
                   WHERE lease_id = ? AND owner_id = ? AND state = 'active'""",
                (str(reason or "released")[:96], now, lease_id, owner_id),
            )
        return cursor.rowcount == 1

    def list_ticker_leases(self) -> list[dict[str, Any]]:
        if not self._ledger_path.exists():
            return []
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT lease_id, owner_id, state, started_at, expires_at,
                          updated_at, terminal_reason
                   FROM supervisor_ticker_leases ORDER BY started_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def has_other_active_admissions(self, target_key: str) -> bool:
        """Return whether another Space currently occupies the global slot."""
        target = _target_key(target_key)
        if not self._ledger_path.exists():
            return False
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT target_key FROM supervisor_admissions
                   WHERE state IN (
                       'provisioning', 'active', 'paused',
                       'cancelling', 'abandoning'
                   )"""
            ).fetchall()
        # One same-target row is the expected self-owned admission. Any other
        # row (including a duplicate same-target row in a legacy/corrupt
        # ledger) is treated as contention so recovery remains fail-closed.
        return len(rows) > 1 or any(row["target_key"] != target for row in rows)

    def admit(self, target_key: str, intent: Mapping[str, Any]) -> SupervisorAdmission:
        target_key = _target_key(target_key)
        # An explicitly supplied empty goal is never actionable. Reject it
        # before governance resolution or any ledger write so malformed
        # ticker/bridge payloads cannot consume the global YOLO slot. Legacy
        # kind-only callers remain valid while they migrate to a goal.
        if "goal" in intent:
            goal = intent.get("goal")
            if not isinstance(goal, str) or not goal.strip():
                return SupervisorAdmission(
                    "rejected", None, None, None, "invalid_intent"
                )
        governance = _resolved_governance(self._governance_resolver, target_key)
        if (
            governance is None
            or governance.yolo is not True
            or governance.enrolled is not True
        ):
            return SupervisorAdmission(
                "rejected", None, None, None, "not_yolo_enrolled"
            )
        intent_digest = _intent_digest(intent)
        self.start()
        admission_id = str(uuid4())
        run_id = str(uuid4())
        capability = _capability(
            admission_id,
            target_key,
            governance,
            run_id,
            intent_digest,
            attachment_generation=1,
        )
        metadata = _diagnostic_metadata(capability, intent)
        workflow_contract_digest = _workflow_contract_digest(metadata)
        now = _timestamp()
        with self._immediate_connection() as connection:
            self._reconcile_completed_admissions(connection)
            # A live ledger row is also an identity claim. Never let a second
            # target key (or moved root) reuse the same Space ID while the
            # original run still occupies the global slot. Comparing against
            # the immutable ledger snapshot keeps duplicate slug/root claims
            # fail-closed even if resolver entries race.
            identity_rows = connection.execute(
                """SELECT admission_id, run_id, target_key, target_space_id,
                          canonical_root, root_fingerprint, state
                   FROM supervisor_admissions
                   WHERE state IN (
                       'provisioning', 'active', 'paused',
                       'cancelling', 'abandoning'
                   ) AND (target_space_id = ? OR target_key = ?)
                   ORDER BY updated_at DESC""",
                (governance.space_id, target_key),
            ).fetchall()
            for identity in identity_rows:
                if (
                    identity["target_space_id"] != governance.space_id
                    or identity["target_key"] != target_key
                    or identity["canonical_root"] != str(governance.canonical_root)
                    or identity["root_fingerprint"] != governance.root_fingerprint
                ):
                    return SupervisorAdmission(
                        "rejected",
                        identity["admission_id"],
                        identity["run_id"],
                        None,
                        "space_identity_conflict",
                    )
            existing = connection.execute(
                """SELECT admission_id, run_id, state FROM supervisor_admissions
                   WHERE target_key = ? AND target_space_id = ?
                     AND intent_digest = ? AND canonical_root = ?
                     AND root_fingerprint = ? AND governance_revision = ?""",
                (
                    target_key,
                    governance.space_id,
                    intent_digest,
                    str(governance.canonical_root),
                    governance.root_fingerprint,
                    governance.revision,
                ),
            ).fetchone()
            if existing is not None:
                if existing["state"] in _OCCUPIED_STATES | {"completed"}:
                    return SupervisorAdmission(
                        "coalesced",
                        existing["admission_id"],
                        existing["run_id"],
                        None,
                        None,
                    )
                return SupervisorAdmission(
                    "rejected",
                    existing["admission_id"],
                    existing["run_id"],
                    None,
                    "terminal_admission",
                )
            active = connection.execute(
                """SELECT 1 FROM supervisor_admissions
                   WHERE state IN (
                       'provisioning', 'active', 'paused',
                       'cancelling', 'abandoning'
                   )"""
            ).fetchone()
            if active is not None:
                return SupervisorAdmission("rejected", None, None, None, "active_limit")
            connection.execute(
                """INSERT INTO supervisor_admissions (
                    admission_id, target_key, target_space_id, intent_digest, canonical_root,
                    root_fingerprint, governance_revision, policy_identity, allowed_action_families_json, workflow_contract_digest, run_id, state,
                    attachment_generation, record_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'provisioning', 0, 1, ?, ?)""",
                (
                    admission_id,
                    target_key,
                    governance.space_id,
                    intent_digest,
                    str(governance.canonical_root),
                    governance.root_fingerprint,
                    governance.revision,
                    governance.policy_identity,
                    _allowed_action_families_json(),
                    workflow_contract_digest,
                    run_id,
                    now,
                    now,
                ),
            )
            _audit(connection, admission_id, "provisioning", None, None, now)
        self._after_provisioning_reservation(admission_id)
        # Serialize child creation and ledger activation against human
        # terminal transitions.  A cancellation that wins before this lock
        # is acquired terminalizes the reservation and prevents any child
        # filesystem write; an admission that wins creates and activates
        # while the terminal path is blocked on this ledger lock.
        with self._immediate_connection() as connection:
            record = connection.execute(
                "SELECT * FROM supervisor_admissions WHERE admission_id = ?",
                (admission_id,),
            ).fetchone()
            if record is None or record["state"] != "provisioning":
                return SupervisorAdmission(
                    "rejected", admission_id, run_id, None, "terminal_admission"
                )
            current_governance = _resolved_governance(
                self._governance_resolver,
                target_key,
            )
            if not _governance_matches_record(current_governance, record):
                cursor = connection.execute(
                    """UPDATE supervisor_admissions
                       SET state = 'paused',
                           attachment_generation = attachment_generation + 1,
                           record_version = record_version + 1,
                           updated_at = ?
                       WHERE admission_id = ? AND state = 'provisioning'
                         AND attachment_generation = ? AND record_version = ?""",
                    (
                        _timestamp(),
                        admission_id,
                        record["attachment_generation"],
                        record["record_version"],
                    ),
                )
                if cursor.rowcount:
                    _audit(
                        connection,
                        admission_id,
                        "paused",
                        None,
                        "governance_changed",
                        _timestamp(),
                    )
                return SupervisorAdmission(
                    "rejected",
                    admission_id,
                    run_id,
                    None,
                    "governance_changed",
                )
            assert current_governance is not None
            try:
                store = self._child_store_factory(current_governance.canonical_root)
                store.create_run(run_id, status="paused", metadata=metadata)
                if not self._activate_provisioning(connection, record):
                    raise RuntimeError(
                        "managed Space child provisioning could not be activated"
                    )
            except BaseException:
                # Child-store creation is outside the ledger transaction. Never
                # strand the global admission slot in ``provisioning`` when it
                # fails; preserve an audit-able terminal lifecycle instead.
                cursor = connection.execute(
                    """UPDATE supervisor_admissions
                       SET state = 'abandoned', terminal_actor = ?,
                           attachment_generation = attachment_generation + 1,
                           record_version = record_version + 1, updated_at = ?
                       WHERE admission_id = ? AND state = 'provisioning'
                         AND attachment_generation = ? AND record_version = ?""",
                    (
                        SYSTEM_SPACE_LIFECYCLE_ACTOR,
                        _timestamp(),
                        admission_id,
                        record["attachment_generation"],
                        record["record_version"],
                    ),
                )
                if cursor.rowcount:
                    _audit(
                        connection,
                        admission_id,
                        "abandoned",
                        SYSTEM_SPACE_LIFECYCLE_ACTOR,
                        "child_provisioning_failed",
                        _timestamp(),
                    )
                    # The surrounding ledger context would otherwise roll this
                    # terminal transition back when the original failure is
                    # re-raised to the caller.
                    connection.commit()
                raise
        self._install_binding(capability)
        return SupervisorAdmission("created", admission_id, run_id, capability, None)

    def _after_provisioning_reservation(self, admission_id: str) -> None:
        """Test seam between durable reservation and serialized child create."""
        del admission_id

    def list_active_admissions(self) -> list[dict[str, str]]:
        if not self._ledger_path.exists():
            return []
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT admission_id, target_space_id, run_id, state
                   FROM supervisor_admissions
                   WHERE state IN (
                       'provisioning', 'active', 'paused',
                       'cancelling', 'abandoning'
                   )"""
            ).fetchall()
        return [dict(row) for row in rows]

    def reconcile_stale_host_runs(
        self, host_alive: Callable[[str], bool]
    ) -> tuple[str, ...]:
        """Pause runs whose dashboard execution owner is definitively dead."""
        if not callable(host_alive):
            raise TypeError("stale host reconciliation requires a callable")
        self.start()
        recovered: list[str] = []
        for admission in self.list_active_admissions():
            try:
                record = self._record(admission["admission_id"])
                if record is None or record["state"] != "active":
                    continue
                child_store = self._child_store_factory(Path(record["canonical_root"]))
                child = child_store.get_run(record["run_id"])
                owner = child_store.get_run_execution_lease_owner(record["run_id"])
                if child is None:
                    continue
                # A host can die after admission activation but before the
                # worker claims its execution lease. Without an owner token,
                # retaining an active slot is unsafe and can deadlock the
                # global exactly-once boundary. Pause this ambiguous handoff
                # so a later host must explicitly recover it.
                owner_missing = not owner
                if owner_missing and child.status not in {"running", "paused"}:
                    continue
                try:
                    owner_alive = host_alive(owner) if owner else False
                except Exception:
                    # An uncertain owner must never be preempted.
                    owner_alive = True
                # A provider/policy pause can race the host crash after the
                # lease is claimed. Recover that dead lease too; otherwise the
                # active admission permanently occupies the global slot while
                # the child is already paused and cannot execute.
                recover_paused = child.status == "paused" and owner_alive is False
                recover_running = child.status == "running" and owner_alive is False
                if not (recover_paused or recover_running):
                    continue
                if owner:
                    child_store.recover_run_execution_lease_after_host_restart(record["run_id"])
                if owner_missing:
                    self._pause_record(
                        record,
                        "host_restart_recovered",
                        actor=SYSTEM_SPACE_LIFECYCLE_ACTOR,
                    )
                    current = self._record(record["admission_id"])
                    if current is None or current["state"] != "paused":
                        continue
                    recovered.append(record["run_id"])
                    continue
                with self._immediate_connection() as connection:
                    cursor = connection.execute(
                        """UPDATE supervisor_admissions
                           SET state = 'paused', updated_at = ?, record_version = record_version + 1
                           WHERE admission_id = ? AND state = 'active'""",
                        (_timestamp(), record["admission_id"]),
                    )
                    if cursor.rowcount:
                        _audit(
                            connection,
                            record["admission_id"],
                            "paused",
                            SYSTEM_SPACE_LIFECYCLE_ACTOR,
                            "host_restart_recovered",
                            _timestamp(),
                        )
                        recovered.append(record["run_id"])
                self._remove_binding_for_record(record)
            except (OSError, RuntimeError, ValueError, sqlite3.Error):
                continue
        return tuple(recovered)

    def _reconcile_completed_admissions(self, connection: sqlite3.Connection) -> None:
        """Release only active slots whose trusted child run is durably complete.

        This runs only from an admission write transaction, never from a
        status/read API.  It deliberately has no governance lookup: recovery
        validates the immutable ledger and child diagnostic contract instead.
        """
        records = connection.execute(
            """SELECT * FROM supervisor_admissions
               WHERE state IN ('active', 'paused')"""
        ).fetchall()
        for record in records:
            try:
                store = ProjectSwarmStore.open_read_only(Path(record["canonical_root"]))
                run = store.get_run(record["run_id"])
            except (OSError, RuntimeError, ValueError, sqlite3.Error):
                run = None
            if (
                run is not None
                and run.status == "completed"
                and _diagnostic_metadata_matches_record(run.metadata, record)
            ):
                cursor = connection.execute(
                    """UPDATE supervisor_admissions
                       SET state = 'completed',
                           attachment_generation = attachment_generation + 1,
                           record_version = record_version + 1,
                           updated_at = ?
                       WHERE admission_id = ? AND state = ?
                          AND attachment_generation = ? AND record_version = ?""",
                    (
                        _timestamp(),
                        record["admission_id"],
                        record["state"],
                        record["attachment_generation"],
                        record["record_version"],
                    ),
                )
                if cursor.rowcount:
                    _audit(
                        connection,
                        record["admission_id"],
                        "reconciled_completed",
                        None,
                        None,
                        _timestamp(),
                    )
            elif run is not None and run.status == "completed":
                _audit(
                    connection,
                    record["admission_id"],
                    "reconciliation_failed",
                    None,
                    "diagnostic_mismatch",
                    _timestamp(),
                )

    def _activate_provisioning(
        self, connection: sqlite3.Connection, record: Mapping[str, Any]
    ) -> bool:
        """Activate a verified child using the caller's held ledger lock."""
        if record["state"] != "provisioning":
            return False
        try:
            reader = ProjectSwarmStore.open_read_only(Path(record["canonical_root"]))
            run = reader.get_run(record["run_id"])
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return False
        if (
            run is None
            or run.status != "paused"
            or not _diagnostic_metadata_matches_record(run.metadata, record)
        ):
            return False
        cursor = connection.execute(
            """UPDATE supervisor_admissions
               SET state = 'active', attachment_generation = 1,
                   record_version = record_version + 1, updated_at = ?
               WHERE admission_id = ? AND state = 'provisioning'
                 AND attachment_generation = 0 AND record_version = ?""",
            (_timestamp(), record["admission_id"], record["record_version"]),
        )
        if cursor.rowcount:
            _audit(
                connection, record["admission_id"], "admitted", None, None, _timestamp()
            )
        return cursor.rowcount == 1

    def execution_options_for_run(self, project_root: Path, run: SwarmRun):
        """Return host-compatible options only for a live, active binding."""
        from cli.swarm_host import SwarmExecutionOptions

        with self._bindings_lock:
            capability = self._bindings.get(run.run_id)
        if capability is None or Path(project_root).resolve() != capability._canonical_root:
            return SwarmExecutionOptions(
                blocked_reason="supervisor_binding_unavailable"
            )
        if not _diagnostic_metadata_matches_capability(run.metadata, capability):
            self._pause(capability, "capability_invalid")
            return SwarmExecutionOptions(blocked_reason="capability_invalid")
        reason = self._revalidate(capability)
        if reason is not None:
            self._pause(capability, reason)
            return SwarmExecutionOptions(blocked_reason=reason)
        from nova.production_verifier import ProductionReadOnlyVerifier
        return SwarmExecutionOptions(
            max_calls=128,
            max_concurrent=3,
            verifier=ProductionReadOnlyVerifier(),
            pre_completion_hook=ManagedSpacePreCompletionHook(self, capability),
            required_pre_completion_hook_id=ManagedSpacePreCompletionHook.hook_id,
            execution_guard=self._execution_guard_for(capability),
            action_executor=self._action_executor,
            on_completed=self.completion_observer_for_run(run.run_id),
        )

    def start_admitted_run(
        self,
        capability: ManagedSpaceCapability,
        *,
        dispatcher: Callable[[Path, str], object],
    ) -> bool:
        """Host-start one already admitted child without a human-resume marker.

        Admission deliberately leaves the child paused so that a host can bind
        its execution machinery before any model boundary is reachable.  This
        is the only non-human transition for that paused child.  The caller
        receives only the ledger-rooted ``Path`` and durable run id; the
        capability never crosses the dispatch boundary.
        """
        if not isinstance(capability, ManagedSpaceCapability):
            return False
        try:
            run_id = capability._run_id
        except AttributeError:
            # ``object.__new__`` can manufacture an uninitialized instance in
            # Python.  It is not an authority and must not turn a host pulse
            # into an exception path.
            return False
        if not callable(dispatcher):
            self._pause(capability, "host_dispatch_failed")
            return False
        if not self._has_current_binding(capability, run_id=run_id):
            return False
        reason = self._revalidate(capability)
        if reason is not None:
            self._pause(capability, reason)
            return False
        self._before_host_start(capability)
        # A governance/root change can race a host wake-up.  Revalidate after
        # the deterministic handoff point and again after the child transition
        # before invoking the dispatcher.
        reason = self._revalidate(capability)
        if reason is not None:
            self._pause(capability, reason)
            return False
        record = self._record(capability._admission_id)
        if (
            record is None
            or record["state"] not in _EXECUTABLE_STATES
            or not _capability_matches_record(capability, record)
        ):
            self._pause(capability, "capability_invalid")
            return False
        try:
            root = Path(record["canonical_root"]).resolve()
            reader = ProjectSwarmStore.open_read_only(root)
            child = reader.get_run(record["run_id"])
        except (OSError, RuntimeError, ValueError, KeyError, sqlite3.Error):
            child = None
        if (
            child is None
            or child.status != "paused"
            or not _diagnostic_metadata_matches_record(child.metadata, record)
        ):
            self._pause(capability, "host_start_invalid_child")
            return False
        try:
            store = self._child_store_factory(root)
            store.set_run_status(record["run_id"], "running")
            store.append_event_once(
                record["run_id"],
                "nova.supervisor.host_started",
                {"source": "space_supervision"},
                idempotency_key="supervisor-host-start",
            )
        except BaseException:
            self._pause(capability, "host_start_failed")
            return False
        reason = self._revalidate(capability)
        if reason is not None:
            self._pause(capability, reason)
            return False
        if self._read_action_boundary_child_status(capability) != "running":
            # A human pause/cancellation may race the child transition.  A
            # generic host dispatcher must never be invoked for a non-running
            # child even though the ledger binding itself still looks active.
            self._pause(capability, "host_start_interrupted")
            return False
        # The status probe itself is a read boundary. Revalidate its exact
        # in-process binding plus immutable ledger/root/governance facts before
        # *and* at the final dispatch handoff, so a revocation cannot slip
        # between a successful probe and an arbitrary host callback.
        if not self._has_current_binding(capability, run_id=run_id):
            return False
        reason = self._revalidate(capability)
        if reason is not None:
            self._pause(capability, reason)
            return False
        try:
            self._before_host_dispatch(capability)
        except BaseException:
            self._pause(capability, "host_dispatch_failed")
            return False
        if not self._has_current_binding(capability, run_id=run_id):
            return False
        reason = self._revalidate(capability)
        if reason is not None:
            self._pause(capability, reason)
            return False
        try:
            dispatch_result = dispatcher(root, record["run_id"])
        except BaseException:
            self._pause(capability, "host_dispatch_failed")
            return False
        # Dispatchers may be asynchronous, but an explicit ``False`` is a
        # host-level refusal (for example, a saturated worker pool). Treat
        # that signal as a failed handoff instead of leaving the child in
        # ``running`` forever with no worker behind it. ``None`` remains the
        # success value used by existing fire-and-forget dispatchers.
        if dispatch_result is False:
            self._pause(capability, "host_dispatch_failed")
            return False
        return True

    def _before_host_start(self, capability: ManagedSpaceCapability) -> None:
        """Deterministic test seam between admission and host start revalidation."""
        del capability

    def _before_host_dispatch(self, capability: ManagedSpaceCapability) -> None:
        """Deterministic seam immediately before final host dispatch validation."""
        del capability

    def _has_current_binding(
        self,
        capability: ManagedSpaceCapability,
        *,
        run_id: str | None = None,
    ) -> bool:
        """Require the exact process-local capability object, not its values."""
        try:
            bound_run_id = capability._run_id if run_id is None else run_id
        except AttributeError:
            return False
        with self._bindings_lock:
            return self._bindings.get(bound_run_id) is capability

    def current_governance(self, target_key: str) -> ManagedSpaceGovernance | None:
        """Resolve target governance read-only for the host supervision pulse."""
        return _resolved_governance(self._governance_resolver, _target_key(target_key))

    def pause_for_space_change(
        self,
        target_key: str,
        *,
        reason: str,
        actor: str | None = None,
    ) -> bool:
        """Fail closed before a managed Space's governance/root is changed.

        This host-only lifecycle transition always obtains the target root and
        child run from the durable ledger.  It intentionally does not resolve
        current governance: the caller is about to replace or remove that
        evidence, so a successful fresh lookup would be the wrong authority.
        ``False`` means the caller must not persist the Space change.
        """
        try:
            target = _target_key(target_key)
        except (TypeError, ValueError):
            return False
        if reason not in _SPACE_CHANGE_PAUSE_REASONS:
            return False
        if actor == SYSTEM_SPACE_LIFECYCLE_ACTOR:
            lifecycle_actor = actor
        else:
            try:
                _dashboard_actor(actor)
            except PermissionError:
                return False
            lifecycle_actor = actor
        if not self._ledger_path.exists():
            return True
        try:
            with self._read_connection() as connection:
                record = connection.execute(
                    """SELECT * FROM supervisor_admissions
                       WHERE target_key = ?
                       ORDER BY updated_at DESC
                       LIMIT 1""",
                    (target,),
                ).fetchone()
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return False
        if record is None:
            return True
        terminal_states = {"completed", "cancelled", "abandoned"}
        if record["state"] in terminal_states:
            return True
        if record["state"] == "paused":
            # A prior fail-closed ledger transition may have raced its child
            # store write.  Do not use the stale ledger label alone as proof
            # that the child is stopped before replacing its Space authority.
            return self._pause_existing_child(record, reason)
        if record["state"] in {"cancelling", "abandoning"}:
            return False
        if record["state"] not in {"provisioning", "active"}:
            return False
        try:
            with self._immediate_connection() as connection:
                current = connection.execute(
                    "SELECT * FROM supervisor_admissions WHERE admission_id = ?",
                    (record["admission_id"],),
                ).fetchone()
                if current is None:
                    return True
                if current["state"] in terminal_states:
                    return True
                if current["state"] == "paused":
                    # A concurrent pause changed the generation after our
                    # read snapshot.  Do not touch a child store while the
                    # ledger transaction is held; reject this lifecycle
                    # write so its caller retries from a fresh snapshot.
                    return False
                if (
                    current["target_key"] != target
                    or not _same_record_generation(current, record)
                    or current["state"] not in {"provisioning", "active"}
                ):
                    return False
                changed = self._pause_record_in_connection(
                    connection,
                    current,
                    reason,
                    actor=lifecycle_actor,
                )
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return False
        if not changed:
            return False
        self._remove_binding_for_record(record)
        return self._pause_existing_child(record, reason)

    def _ledger_schema_ready_read_only(self) -> bool:
        """Check core ledger readiness without opening a write-capable database."""
        if not self._ledger_path.exists():
            return False
        try:
            with self._read_connection() as connection:
                return _supervisor_ledger_schema_ready(connection)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return False

    @contextmanager
    def _supervision_state_transaction(
        self,
        *,
        schema_objects: tuple[str, ...],
        schema_initializer: Callable[[sqlite3.Connection], None],
    ) -> Iterator[sqlite3.Connection]:
        """Lock runtime state, initializing missing schema only under that lock.

        Steady-state callers do only read probes and DML.  This keeps duplicate
        or capacity-rejected autonomous signals from executing SQLite DDL.
        """
        if not schema_objects or not callable(schema_initializer):
            raise TypeError("supervision state schema contract is invalid")
        if not self._ledger_schema_ready_read_only():
            self.start()
        with self._immediate_connection() as connection:
            if not _supervisor_ledger_schema_ready(connection):
                self._initialize_ledger_schema(connection)
            if not _sqlite_schema_objects_present(connection, schema_objects):
                schema_initializer(connection)
            yield connection

    @contextmanager
    def _supervision_state_reader(self) -> Iterator[sqlite3.Connection | None]:
        """Read supervision state without creating or migrating the ledger."""
        if not self._ledger_path.exists():
            yield None
            return
        try:
            connection = sqlite3.connect(
                self._ledger_path.resolve().as_uri() + "?mode=ro",
                uri=True,
            )
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            yield None
            return
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _execution_guard_for(self, capability: ManagedSpaceCapability):
        def guard(_project_root: Path, current_run: SwarmRun | None) -> str | None:
            if current_run is None or not _diagnostic_metadata_matches_capability(
                current_run.metadata, capability
            ):
                self._pause(capability, "capability_invalid")
                return "capability_invalid"
            reason = self._revalidate(capability)
            if reason is not None:
                self._pause(capability, reason)
            return reason

        return guard

    def pre_completion_hook_for_run(self, run_id: str) -> ManagedSpacePreCompletionHook:
        with self._bindings_lock:
            capability = self._bindings.get(run_id)
        if capability is None:
            raise ValueError("managed Space supervisor binding is unavailable")
        return ManagedSpacePreCompletionHook(self, capability)

    def recover_and_reattach(
        self,
        admission_id: str,
        *,
        actor: str,
    ) -> ManagedSpaceCapability | None:
        """Explicit dashboard-only remint of a paused child-run capability.

        This only restores an in-memory binding; it neither resumes nor
        dispatches the child. A host must still perform its normal explicit
        resume after this audited handoff.
        """
        _dashboard_actor(actor)
        self.start()
        snapshot = self._record(admission_id)
        if snapshot is None or snapshot["state"] not in {"active", "paused"}:
            return None
        self._before_recovery_transaction(admission_id, snapshot)
        capability: ManagedSpaceCapability | None = None
        invalid_record: sqlite3.Row | None = None
        with self._immediate_connection() as connection:
            record = connection.execute(
                "SELECT * FROM supervisor_admissions WHERE admission_id = ?",
                (admission_id,),
            ).fetchone()
            if (
                record is None
                or record["state"] not in {"active", "paused"}
                or not _same_recovery_snapshot(record, snapshot)
            ):
                return None
            try:
                reader = ProjectSwarmStore.open_read_only(
                    Path(record["canonical_root"])
                )
                run = reader.get_run(record["run_id"])
                lease_held = reader.has_run_execution_lease(record["run_id"])
                governance = _resolved_governance(
                    self._governance_resolver,
                    record["target_key"],
                )
            except (OSError, RuntimeError, ValueError, sqlite3.Error):
                run = None
                lease_held = True
                governance = None
            if (
                run is None
                or run.status != "paused"
                or lease_held
                or not _diagnostic_metadata_matches_record(run.metadata, record)
                or not _governance_matches_record(governance, record)
            ):
                self._pause_record_in_connection(
                    connection,
                    record,
                    "recovery_validation_failed",
                    actor=actor,
                )
                invalid_record = record
            else:
                next_generation = record["attachment_generation"] + 1
                capability = _capability_from_record(
                    record,
                    attachment_generation=next_generation,
                )
                if capability is None:
                    self._pause_record_in_connection(
                        connection,
                        record,
                        "recovery_validation_failed",
                        actor=actor,
                    )
                    invalid_record = record
                else:
                    cursor = connection.execute(
                        """UPDATE supervisor_admissions
                           SET state = 'active', attachment_generation = ?,
                               record_version = record_version + 1, updated_at = ?
                           WHERE admission_id = ? AND state = ?
                             AND attachment_generation = ? AND record_version = ?""",
                        (
                            next_generation,
                            _timestamp(),
                            admission_id,
                            record["state"],
                            record["attachment_generation"],
                            record["record_version"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        return None
                    _audit(
                        connection,
                        admission_id,
                        "recovery_reattached",
                        actor,
                        None,
                        _timestamp(),
                    )
                    self._before_recovery_commit(
                        admission_id,
                        next_generation,
                    )
        if invalid_record is not None:
            self._remove_binding_for_record(invalid_record)
            self._pause_existing_child(
                invalid_record,
                "recovery_validation_failed",
            )
            return None
        if capability is None:
            return None
        self._install_binding(capability)
        return capability

    def _before_recovery_transaction(
        self,
        admission_id: str,
        snapshot: Mapping[str, Any],
    ) -> None:
        """Deterministic test seam after the immutable recovery snapshot."""
        del admission_id, snapshot

    def _before_recovery_commit(
        self,
        admission_id: str,
        generation: int,
    ) -> None:
        """Deterministic test seam inside the recovery transaction."""
        del admission_id, generation

    def revalidate_action_boundary(self, capability: ManagedSpaceCapability) -> bool:
        return self.resolve_action_context(capability) is not None

    def resolve_action_context(
        self,
        capability: ManagedSpaceCapability,
    ) -> ManagedSpaceActionContext | None:
        """Return authority only for this process's exact current attachment.

        A capability reconstructed from ledger values is intentionally not
        current authority. Reject it before any ledger/child mutation so a
        forged or stale object cannot pause or remove the installed binding.
        """
        if not isinstance(capability, ManagedSpaceCapability):
            return None
        with self._bindings_lock:
            if self._bindings.get(capability._run_id) is not capability:
                return None
        reason = self._revalidate(capability)
        if reason is not None:
            self._pause(capability, reason)
            return None
        status = self._read_action_boundary_child_status(capability)
        if status != "running":
            if status == "metadata_invalid":
                self._pause(capability, "capability_invalid")
            # A paused child is already safe. Preserve its active ledger
            # record so an authenticated host can explicitly resume it.
            elif status != "paused":
                self._pause_ledger_only(capability, "child_not_running")
            return None
        record = self._record(capability._admission_id)
        if (
            record is None
            or record["state"] not in _EXECUTABLE_STATES
            or not _capability_matches_record(capability, record)
        ):
            return None
        try:
            families = tuple(json.loads(record["allowed_action_families_json"]))
            if families != _ALLOWED_ACTION_FAMILIES:
                return None
            context = ManagedSpaceActionContext(
                record["run_id"],
                Path(record["canonical_root"]).resolve(),
                record["attachment_generation"],
                families,
                record["target_space_id"],
                _token=_ACTION_CONTEXT_TOKEN,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        with self._bindings_lock:
            if self._bindings.get(context.run_id) is not capability:
                return None
        return context

    def pause_action_context(
        self,
        capability: ManagedSpaceCapability,
        context: ManagedSpaceActionContext,
        reason: str,
    ) -> bool:
        """Pause only the exact still-current, ledger-derived action context."""
        if not isinstance(context, ManagedSpaceActionContext):
            return False
        current = self.resolve_action_context(capability)
        if current != context:
            return False
        self._pause(capability, reason)
        return True

    def _read_action_boundary_child_status(
        self, capability: ManagedSpaceCapability
    ) -> str | None:
        """Return the ledger-rooted child status without creating or writing a store."""
        record = self._record(capability._admission_id)
        if (
            record is None
            or record["state"] not in _EXECUTABLE_STATES
            or not _capability_matches_record(capability, record)
        ):
            return None
        try:
            reader = ProjectSwarmStore.open_read_only(Path(record["canonical_root"]))
            run = reader.get_run(record["run_id"])
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return None
        if run is None:
            return None
        if not _diagnostic_metadata_matches_capability(run.metadata, capability):
            return "metadata_invalid"
        return run.status

    def _pause_ledger_only(
        self, capability: ManagedSpaceCapability, reason: str
    ) -> None:
        """Fail closed from an action gate without writing through capability data."""
        record = self._record(capability._admission_id)
        if (
            record is None
            or record["state"] not in _EXECUTABLE_STATES
            or not _capability_targets_record_generation(capability, record)
        ):
            return
        if self._reconcile_completed_record(
            record,
            allowed_states=("active",),
            event_type="reconciled_completed_during_action_gate",
        ):
            return
        with self._immediate_connection() as connection:
            changed = self._pause_record_in_connection(
                connection,
                record,
                reason,
            )
        if changed:
            self._remove_binding(capability)

    def _pause_record(
        self,
        record: Mapping[str, Any],
        reason: str,
        *,
        actor: str | None = None,
    ) -> None:
        """Pause only the ledger-recorded child root/run, never caller input."""
        with self._immediate_connection() as connection:
            current = connection.execute(
                "SELECT * FROM supervisor_admissions WHERE admission_id = ?",
                (record["admission_id"],),
            ).fetchone()
            if current is None or not _same_record_generation(current, record):
                return
            changed = self._pause_record_in_connection(
                connection,
                current,
                reason,
                actor=actor,
            )
        if changed:
            self._remove_binding_for_record(record)
            self._pause_existing_child(record, reason)

    def _pause_record_in_connection(
        self,
        connection: sqlite3.Connection,
        record: Mapping[str, Any],
        reason: str,
        *,
        actor: str | None = None,
    ) -> bool:
        """CAS one live generation to paused while the ledger lock is held."""
        if record["state"] not in {"active", "paused", "provisioning"}:
            return False
        cursor = connection.execute(
            """UPDATE supervisor_admissions
               SET state = 'paused',
                   attachment_generation = attachment_generation + 1,
                   record_version = record_version + 1,
                   updated_at = ?
               WHERE admission_id = ? AND state = ?
                 AND attachment_generation = ? AND record_version = ?""",
            (
                _timestamp(),
                record["admission_id"],
                record["state"],
                record["attachment_generation"],
                record["record_version"],
            ),
        )
        if cursor.rowcount:
            _audit(
                connection,
                record["admission_id"],
                "paused",
                actor,
                reason,
                _timestamp(),
            )
        return cursor.rowcount == 1

    def _pause_existing_child(
        self,
        record: Mapping[str, Any],
        reason: str,
    ) -> bool:
        """Stop and confirm the ledger-rooted child without creating a store.

        ``False`` deliberately leaves the ledger paused but tells lifecycle
        callers not to replace/delete the Space evidence while a running child
        cannot be proved stopped.  Other fail-closed paths may ignore the
        result because their only safe action is the ledger pause itself.
        """
        try:
            reader = ProjectSwarmStore.open_read_only(Path(record["canonical_root"]))
            run = reader.get_run(record["run_id"])
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return record["state"] == "provisioning"
        if run is None:
            return record["state"] == "provisioning"
        if run.status in {"completed", "cancelled", "abandoned"}:
            return True
        if run.status not in {"running", "paused"}:
            return False
        try:
            store = self._child_store_factory(Path(record["canonical_root"]))
            if run.status == "running":
                store.set_run_status(record["run_id"], "paused")
            store.append_event_once(
                record["run_id"],
                "nova.supervisor.paused",
                {"reason": reason},
                idempotency_key="supervisor-pause:" + reason,
            )
        except (OSError, RuntimeError, ValueError, KeyError, sqlite3.Error):
            # A status write can succeed before the optional child event
            # append fails.  Re-open below instead of trusting the exception.
            pass
        try:
            confirmed = ProjectSwarmStore.open_read_only(
                Path(record["canonical_root"])
            ).get_run(record["run_id"])
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return False
        return confirmed is not None and confirmed.status in {
            "paused",
            "completed",
            "cancelled",
            "abandoned",
        }

    def record_completion(self, run_id: str) -> bool:
        with self._bindings_lock:
            capability = self._bindings.get(run_id)
        if capability is None:
            return False
        record = self._record(capability._admission_id)
        if record is None or not _capability_matches_record(capability, record):
            self._pause(capability, "capability_invalid")
            return False
        return self._reconcile_completed_record(
            record,
            allowed_states=("active",),
            event_type="completed",
        )

    def reconcile_host_dispatch(
        self,
        project_root: Path,
        run_id: str,
        *,
        failure_reason: str = "host_dispatch_failed",
    ) -> str:
        """Release or pause a host-started run after its worker returns.

        ``on_completed`` covers the normal completed path, but a cloud
        catalog outage, a schema pause, or an unexpected worker exception can
        return from ``SwarmHost.execute_run`` without invoking that observer.
        Leaving the supervisor admission active in those cases would consume
        the global one-run slot forever. This method is capability- and
        ledger-bound; it never trusts a caller-provided root to create a store
        or to transition a different run.
        """
        if not isinstance(project_root, Path) or not isinstance(run_id, str):
            return "invalid"
        with self._bindings_lock:
            capability = self._bindings.get(run_id)
        if capability is None:
            return "unbound"
        record = self._record(capability._admission_id)
        if (
            record is None
            or not _capability_matches_record(capability, record)
            or Path(project_root).resolve() != Path(record["canonical_root"])
        ):
            self._pause(capability, "capability_invalid")
            return "paused"
        try:
            child = ProjectSwarmStore.open_read_only(
                Path(record["canonical_root"])
            ).get_run(record["run_id"])
        except (OSError, RuntimeError, ValueError, KeyError, sqlite3.Error):
            child = None
        if child is not None and child.status == "completed":
            if self._reconcile_completed_record(
                record,
                allowed_states=("active", "paused"),
                event_type="completed_after_host_dispatch",
            ):
                return "completed"
        if child is not None and child.status == "paused" and failure_reason == "host_execution_returned":
            # Swarm may have paused fail-closed at a model/policy boundary
            # before the host callback returns. Preserve that bounded reason
            # in the supervisor audit instead of replacing it with a generic
            # transport outcome such as ``host_execution_returned``.
            try:
                for event in reversed(
                    ProjectSwarmStore.open_read_only(
                        Path(record["canonical_root"])
                    ).list_events(record["run_id"])
                ):
                    if event.event_type != "run.paused":
                        continue
                    candidate = event.payload.get("reason") if isinstance(event.payload, Mapping) else None
                    if isinstance(candidate, str) and _CHILD_PAUSE_REASON_RE.fullmatch(candidate):
                        failure_reason = candidate
                    break
            except (OSError, RuntimeError, ValueError, KeyError, sqlite3.Error):
                pass
        # A returned worker with a paused/missing/unknown child is fail-closed:
        # stop the ledger binding and let the existing human resume path decide
        # whether another attempt is appropriate.
        self._pause(capability, failure_reason)
        return "paused"

    def auto_resume_recoverable_run(
        self,
        target_key: str,
        *,
        dispatcher: Callable[[Path, str], object],
    ) -> tuple[str, str | None]:
        """Resume one provider-blocked child after an explicit catalog proof.

        This is intentionally narrower than the dashboard recovery path.  It
        only wakes a still-valid enrolled YOLO Space whose child recorded one
        of the bounded provider/model pause reasons and whose project-local
        catalog now contains an explicitly live-verified Ollama Cloud
        snapshot.  Human, governance, root, policy, and schema pauses never
        enter this path.
        """
        target_key = _target_key(target_key)
        if not callable(dispatcher):
            return "none", None
        governance = _resolved_governance(self._governance_resolver, target_key)
        if (
            governance is None
            or governance.yolo is not True
            or governance.enrolled is not True
        ):
            return "none", None
        # A legacy/crashed ledger can contain more than one occupied record
        # while its partial unique index is missing. Rebuilding that index is
        # fail-closed: SQLite raises IntegrityError rather than allowing
        # recovery to activate a second run. Keep the run paused and let the
        # next signal/heartbeat retry after reconciliation.
        try:
            self.start()
        except sqlite3.IntegrityError:
            return "none", None
        capability: ManagedSpaceCapability | None = None
        pause_reason: str | None = None
        pause_at = None
        record: sqlite3.Row | None = None
        with self._immediate_connection() as connection:
            record = connection.execute(
                """SELECT * FROM supervisor_admissions
                   WHERE target_key = ? AND state = 'paused'
                   ORDER BY updated_at DESC LIMIT 1""",
                (target_key,),
            ).fetchone()
            if (
                record is None
                or not _governance_matches_record(governance, record)
            ):
                return "none", None
            # Do not open the child store or inspect its catalog while another
            # occupied admission owns the global slot. This preflight keeps
            # recovery strictly behind the slot boundary; the later guard
            # remains as a race-safe check immediately before activation.
            occupied = connection.execute(
                """SELECT 1 FROM supervisor_admissions
                   WHERE admission_id != ? AND state IN (
                       'provisioning', 'active', 'paused',
                       'cancelling', 'abandoning'
                   ) LIMIT 1""",
                (record["admission_id"],),
            ).fetchone()
            if occupied is not None:
                return "none", None
            try:
                reader = ProjectSwarmStore.open_read_only(
                    Path(record["canonical_root"])
                )
                run = reader.get_run(record["run_id"])
                lease_held = reader.has_run_execution_lease(record["run_id"])
                events = reader.list_events(record["run_id"])
                catalog = reader.get_model_catalog_snapshot(_OLLAMA_CLOUD_PROVIDER)
            except (OSError, RuntimeError, ValueError, KeyError, sqlite3.Error):
                return "none", None
            if (
                run is None
                or run.status != "paused"
                or lease_held
                or not _diagnostic_metadata_matches_record(run.metadata, record)
            ):
                return "none", None
            for event in reversed(events):
                if event.event_type != "run.paused":
                    continue
                candidate = event.payload.get("reason") if isinstance(event.payload, Mapping) else None
                if isinstance(candidate, str) and _CHILD_PAUSE_REASON_RE.fullmatch(candidate):
                    pause_reason = candidate
                    pause_at = event.timestamp
                break
            if pause_reason not in _RECOVERABLE_PROVIDER_PAUSE_REASONS:
                _audit_auto_resume_wait_once(
                    connection,
                    record["admission_id"],
                    (
                        "model_chain_exhausted"
                        if pause_reason == "model_chain_exhausted"
                        else "pause_not_recoverable"
                    ),
                )
                return "none", None
            if not (
                catalog is not None
                and catalog.provider == _OLLAMA_CLOUD_PROVIDER
                and catalog.healthy is True
                and catalog.source == _OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE
                and catalog.models
            ):
                if pause_reason == "model_chain_exhausted":
                    _audit_auto_resume_wait_once(
                        connection,
                        record["admission_id"],
                        "catalog_refresh_required",
                    )
                    return "waiting_for_catalog", record["run_id"]
                _audit_auto_resume_wait_once(
                    connection,
                    record["admission_id"],
                    "catalog_refresh_required",
                )
                return "none", None
            # A catalog that predates the pause cannot prove that the provider
            # recovered. Require a strictly newer verified snapshot so the
            # heartbeat cannot repeatedly wake a run on stale model metadata.
            if pause_at is None or catalog.refreshed_at <= pause_at:
                if pause_reason == "model_chain_exhausted":
                    _audit_auto_resume_wait_once(
                        connection,
                        record["admission_id"],
                        "catalog_refresh_required",
                    )
                    return "waiting_for_catalog", record["run_id"]
                _audit_auto_resume_wait_once(
                    connection,
                    record["admission_id"],
                    "catalog_refresh_required",
                )
                return "none", None

            next_generation = int(record["attachment_generation"]) + 1
            capability = _capability_from_record(
                record,
                attachment_generation=next_generation,
            )
            if capability is None:
                return "none", None
            # Recovery must obey the same global one-run admission boundary
            # as a fresh signal. A paused record is itself part of the
            # occupied-slot index, but another occupied admission can still
            # be visible in a legacy ledger before this paused -> active
            # update. Keep recovery pending so normal admission backoff can
            # report ``active_limit`` instead of leaking IntegrityError.
            occupied = connection.execute(
                """SELECT 1 FROM supervisor_admissions
                   WHERE admission_id != ? AND state IN (
                       'provisioning', 'active', 'paused',
                       'cancelling', 'abandoning'
                   ) LIMIT 1""",
                (record["admission_id"],),
            ).fetchone()
            if occupied is not None:
                return "none", None
            cursor = connection.execute(
                """UPDATE supervisor_admissions
                   SET state = 'active', attachment_generation = ?,
                       record_version = record_version + 1, updated_at = ?
                   WHERE admission_id = ? AND state = 'paused'
                     AND attachment_generation = ? AND record_version = ?""",
                (
                    next_generation,
                    _timestamp(),
                    record["admission_id"],
                    record["attachment_generation"],
                    record["record_version"],
                ),
            )
            if cursor.rowcount != 1:
                return "none", None
            _audit(
                connection,
                record["admission_id"],
                "auto_resumed_after_recovery",
                SYSTEM_SPACE_LIFECYCLE_ACTOR,
                pause_reason,
                _timestamp(),
            )
        assert capability is not None and record is not None
        self._install_binding(capability)
        try:
            store = self._child_store_factory(Path(record["canonical_root"]))
            store.append_event_once(
                record["run_id"],
                "nova.supervisor.auto_resumed_after_recovery",
                {"reason": pause_reason},
                idempotency_key=(
                    "supervisor-auto-resume:" + str(capability._attachment_generation)
                ),
            )
        except (OSError, RuntimeError, ValueError, KeyError, sqlite3.Error):
            self._pause(capability, "auto_resume_audit_failed")
            return "start_failed", record["run_id"]
        started = self.start_admitted_run(capability, dispatcher=dispatcher)
        return ("auto_resumed" if started else "start_failed"), record["run_id"]
    def _reconcile_completed_record(
        self,
        record: Mapping[str, Any],
        *,
        allowed_states: tuple[str, ...],
        event_type: str,
    ) -> bool:
        """Record a verified child completion without consulting live governance."""
        with self._immediate_connection() as connection:
            reconciled = self._reconcile_completed_record_in_connection(
                connection,
                record,
                allowed_states=allowed_states,
                event_type=event_type,
            )
        if reconciled:
            self._remove_binding_for_record(record)
        return reconciled

    def _reconcile_completed_record_in_connection(
        self,
        connection: sqlite3.Connection,
        record: Mapping[str, Any],
        *,
        allowed_states: tuple[str, ...],
        event_type: str,
    ) -> bool:
        """Reconcile a completed child while the caller holds the ledger lock."""
        try:
            store = ProjectSwarmStore.open_read_only(Path(record["canonical_root"]))
            run = store.get_run(record["run_id"])
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return False
        if (
            run is None
            or run.status != "completed"
            or not _diagnostic_metadata_matches_record(run.metadata, record)
        ):
            return False
        placeholders = ", ".join("?" for _ in allowed_states)
        cursor = connection.execute(
            f"""UPDATE supervisor_admissions
                SET state = 'completed',
                    attachment_generation = attachment_generation + 1,
                    record_version = record_version + 1,
                    updated_at = ?
                WHERE admission_id = ? AND state IN ({placeholders})
                  AND attachment_generation = ? AND record_version = ?""",
            (
                _timestamp(),
                record["admission_id"],
                *allowed_states,
                record["attachment_generation"],
                record["record_version"],
            ),
        )
        if cursor.rowcount:
            _audit(
                connection, record["admission_id"], event_type, None, None, _timestamp()
            )
        return cursor.rowcount == 1

    def completion_observer_for_run(
        self, run_id: str
    ) -> Callable[[Path, SwarmRun], None]:
        """Return the host completion seam for this one in-process binding."""

        def observe(project_root: Path, run: SwarmRun) -> None:
            with self._bindings_lock:
                capability = self._bindings.get(run_id)
            if capability is None or run.run_id != run_id or run.status != "completed":
                return
            record = self._record(capability._admission_id)
            if (
                record is None
                or not _capability_matches_record(capability, record)
                or Path(project_root).resolve() != Path(record["canonical_root"])
            ):
                self._pause(capability, "capability_invalid")
                return
            self._reconcile_completed_record(
                record,
                allowed_states=("active",),
                event_type="completed",
            )

        return observe

    def cancel(self, admission_id: str, *, actor: str) -> bool:
        _dashboard_actor(actor)
        self.start()
        with self._immediate_connection() as connection:
            record = connection.execute(
                "SELECT * FROM supervisor_admissions WHERE admission_id = ?",
                (admission_id,),
            ).fetchone()
            if record is None or record["state"] in {
                "cancelled",
                "completed",
                "abandoning",
            }:
                return False
            if record["state"] == "provisioning":
                cursor = connection.execute(
                    """UPDATE supervisor_admissions
                       SET state = 'cancelled', terminal_actor = ?,
                           attachment_generation = attachment_generation + 1,
                           record_version = record_version + 1, updated_at = ?
                       WHERE admission_id = ? AND state = 'provisioning'
                         AND attachment_generation = ? AND record_version = ?""",
                    (
                        actor,
                        _timestamp(),
                        admission_id,
                        record["attachment_generation"],
                        record["record_version"],
                    ),
                )
                if cursor.rowcount:
                    _audit(
                        connection,
                        admission_id,
                        "cancelled",
                        actor,
                        "cancelled_before_child_create",
                        _timestamp(),
                    )
                return cursor.rowcount == 1
            if record["state"] != "cancelling":
                if record["state"] not in {"active", "paused"}:
                    return False
                cursor = connection.execute(
                    """UPDATE supervisor_admissions
                       SET state = 'cancelling', terminal_actor = ?,
                           attachment_generation = attachment_generation + 1,
                           record_version = record_version + 1, updated_at = ?
                       WHERE admission_id = ? AND state = ?
                         AND attachment_generation = ? AND record_version = ?""",
                    (
                        actor,
                        _timestamp(),
                        admission_id,
                        record["state"],
                        record["attachment_generation"],
                        record["record_version"],
                    ),
                )
                if cursor.rowcount != 1:
                    return False
                _audit(
                    connection,
                    admission_id,
                    "cancelling",
                    actor,
                    "cancel_requested",
                    _timestamp(),
                )
        self._remove_binding_for_record(record)
        child = self._existing_child(record)
        if child is not None and child.status == "completed":
            if self._reconcile_terminal_child_completion(
                admission_id,
                transitional_state="cancelling",
            ):
                return False
        elif child is not None and child.status not in {"cancelled"}:
            try:
                self._child_store_factory(
                    Path(record["canonical_root"])
                ).cancel_run_by_human(record["run_id"], actor)
            except ValueError:
                if self._reconcile_terminal_child_completion(
                    admission_id,
                    transitional_state="cancelling",
                ):
                    return False
                raise
            except KeyError:
                pass
        return self._finish_human_terminal(
            admission_id,
            transitional_state="cancelling",
            final_state="cancelled",
            actor=actor,
        )

    def abandon_run_by_id(self, run_id: str, *, actor: str) -> bool:
        """Human-only release by opaque run id for the Nova Presence card."""
        _dashboard_actor(actor)
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise ValueError("supervisor run id is invalid")
        self.start()
        with self._read_connection() as connection:
            record = connection.execute(
                "SELECT admission_id, canonical_root FROM supervisor_admissions "
                "WHERE run_id = ? ORDER BY updated_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if record is None:
            return False
        child = ProjectSwarmStore.open_read_only(Path(record["canonical_root"])).get_run(run_id)
        if child is None or child.status != "paused":
            return False
        if str(child.metadata.get("started_by") or "").strip().lower() == "nova":
            raise PermissionError("Nova-owned runs cannot be released from Presence")
        return self.abandon(str(record["admission_id"]), actor=actor)

    def abandon(self, admission_id: str, *, actor: str) -> bool:
        _dashboard_actor(actor)
        self.start()
        with self._immediate_connection() as connection:
            record = connection.execute(
                "SELECT * FROM supervisor_admissions WHERE admission_id = ?",
                (admission_id,),
            ).fetchone()
            if record is None or record["state"] in {
                "cancelled",
                "completed",
                "abandoned",
                "cancelling",
            }:
                return False
            if record["state"] == "provisioning":
                cursor = connection.execute(
                    """UPDATE supervisor_admissions
                       SET state = 'abandoned', terminal_actor = ?,
                           attachment_generation = attachment_generation + 1,
                           record_version = record_version + 1, updated_at = ?
                       WHERE admission_id = ? AND state = 'provisioning'
                         AND attachment_generation = ? AND record_version = ?""",
                    (
                        actor,
                        _timestamp(),
                        admission_id,
                        record["attachment_generation"],
                        record["record_version"],
                    ),
                )
                if cursor.rowcount:
                    _audit(
                        connection,
                        admission_id,
                        "abandoned",
                        actor,
                        "abandoned_before_child_create",
                        _timestamp(),
                    )
                return cursor.rowcount == 1
            if record["state"] != "abandoning":
                if record["state"] not in {"active", "paused"}:
                    return False
                cursor = connection.execute(
                    """UPDATE supervisor_admissions
                       SET state = 'abandoning', terminal_actor = ?,
                           attachment_generation = attachment_generation + 1,
                           record_version = record_version + 1, updated_at = ?
                       WHERE admission_id = ? AND state = ?
                         AND attachment_generation = ? AND record_version = ?""",
                    (
                        actor,
                        _timestamp(),
                        admission_id,
                        record["state"],
                        record["attachment_generation"],
                        record["record_version"],
                    ),
                )
                if cursor.rowcount != 1:
                    return False
                _audit(
                    connection,
                    admission_id,
                    "abandoning",
                    actor,
                    "abandon_requested",
                    _timestamp(),
                )
        self._remove_binding_for_record(record)
        child = self._existing_child(record)
        if child is not None and child.status == "completed":
            if self._reconcile_terminal_child_completion(
                admission_id,
                transitional_state="abandoning",
            ):
                return False
        elif child is not None and child.status != "abandoned":
            try:
                self._child_store_factory(
                    Path(record["canonical_root"])
                ).abandon_run_by_human(record["run_id"], actor)
            except ValueError:
                if self._reconcile_terminal_child_completion(
                    admission_id,
                    transitional_state="abandoning",
                ):
                    return False
                raise
        return self._finish_human_terminal(
            admission_id,
            transitional_state="abandoning",
            final_state="abandoned",
            actor=actor,
        )

    def _existing_child(self, record: Mapping[str, Any]) -> SwarmRun | None:
        """Probe a ledger-rooted child without creating its project store."""
        try:
            return ProjectSwarmStore.open_read_only(
                Path(record["canonical_root"])
            ).get_run(record["run_id"])
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return None

    def _reconcile_terminal_child_completion(
        self,
        admission_id: str,
        *,
        transitional_state: str,
    ) -> bool:
        with self._immediate_connection() as connection:
            record = connection.execute(
                "SELECT * FROM supervisor_admissions WHERE admission_id = ?",
                (admission_id,),
            ).fetchone()
            if record is None or record["state"] != transitional_state:
                return False
            return self._reconcile_completed_record_in_connection(
                connection,
                record,
                allowed_states=(transitional_state,),
                event_type="reconciled_completed_after_human_terminal",
            )

    def _finish_human_terminal(
        self,
        admission_id: str,
        *,
        transitional_state: str,
        final_state: str,
        actor: str,
    ) -> bool:
        with self._immediate_connection() as connection:
            record = connection.execute(
                "SELECT * FROM supervisor_admissions WHERE admission_id = ?",
                (admission_id,),
            ).fetchone()
            if record is None or record["state"] != transitional_state:
                return False
            cursor = connection.execute(
                """UPDATE supervisor_admissions
                   SET state = ?, terminal_actor = ?,
                       attachment_generation = attachment_generation + 1,
                       record_version = record_version + 1, updated_at = ?
                   WHERE admission_id = ? AND state = ?
                     AND attachment_generation = ? AND record_version = ?""",
                (
                    final_state,
                    actor,
                    _timestamp(),
                    admission_id,
                    transitional_state,
                    record["attachment_generation"],
                    record["record_version"],
                ),
            )
            if cursor.rowcount:
                _audit(
                    connection,
                    admission_id,
                    final_state,
                    actor,
                    None,
                    _timestamp(),
                )
            return cursor.rowcount == 1

    def _install_binding(self, capability: ManagedSpaceCapability) -> None:
        """Keep only the newest process-local generation for one child run."""
        try:
            record = self._record(capability._admission_id)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return
        if (
            record is None
            or record["state"] not in _EXECUTABLE_STATES
            or not _capability_matches_record(capability, record)
        ):
            return
        with self._bindings_lock:
            current = self._bindings.get(capability._run_id)
            if (
                current is None
                or current._attachment_generation < capability._attachment_generation
            ):
                self._bindings[capability._run_id] = capability
        try:
            current_record = self._record(capability._admission_id)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            current_record = None
        if (
            current_record is None
            or current_record["state"] not in _EXECUTABLE_STATES
            or not _capability_matches_record(capability, current_record)
        ):
            self._remove_binding(capability)

    def _remove_binding(self, capability: ManagedSpaceCapability) -> None:
        """A stale capability must never remove a newer attachment."""
        with self._bindings_lock:
            current = self._bindings.get(capability._run_id)
            if (
                current is not None
                and current._admission_id == capability._admission_id
                and current._attachment_generation == capability._attachment_generation
            ):
                self._bindings.pop(capability._run_id, None)

    def _remove_binding_for_record(self, record: Mapping[str, Any]) -> None:
        with self._bindings_lock:
            current = self._bindings.get(record["run_id"])
            if (
                current is not None
                and current._admission_id == record["admission_id"]
                and current._attachment_generation == record["attachment_generation"]
            ):
                self._bindings.pop(record["run_id"], None)

    def _binding_for_run(
        self, project_root: Path, run: SwarmRun
    ) -> ManagedSpaceCapability | None:
        with self._bindings_lock:
            capability = self._bindings.get(run.run_id)
        if (
            capability is None
            or Path(project_root).resolve() != capability._canonical_root
            or not _diagnostic_metadata_matches_capability(run.metadata, capability)
        ):
            return None
        return capability

    def _revalidate(self, capability: ManagedSpaceCapability) -> str | None:
        if not isinstance(capability, ManagedSpaceCapability):
            return "capability_invalid"
        record = self._record(capability._admission_id)
        if record is None or record["state"] not in _EXECUTABLE_STATES:
            return "admission_inactive"
        if not _capability_matches_record(capability, record):
            return "capability_invalid"
        governance = _resolved_governance(
            self._governance_resolver, capability._target_key
        )
        if (
            governance is None
            or governance.yolo is not True
            or governance.enrolled is not True
        ):
            return "governance_revoked"
        if (
            governance.canonical_root != capability._canonical_root
            or governance.root_fingerprint != capability._root_fingerprint
        ):
            return "root_mismatch"
        if (
            governance.space_id != capability._target_space_id
            or governance.revision != capability._governance_revision
            or governance.policy_identity != capability._policy_identity
        ):
            return "governance_revoked"
        try:
            reader = ProjectSwarmStore.open_read_only(Path(record["canonical_root"]))
            run = reader.get_run(record["run_id"])
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return "capability_invalid"
        if run is None or not _diagnostic_metadata_matches_record(run.metadata, record):
            return "capability_invalid"
        return None

    def _pause(self, capability: ManagedSpaceCapability, reason: str) -> None:
        record = self._record(capability._admission_id)
        # Object-level tampering must never turn a capability field into a
        # filesystem write target.  Only a matching ledger record supplies
        # the root/run to pause; a missing record is ledger-only fail-closed.
        if (
            record is None
            or record["state"] not in _EXECUTABLE_STATES
            or not _capability_targets_record_generation(capability, record)
        ):
            return
        if record["state"] == "active":
            if self._reconcile_completed_record(
                record,
                allowed_states=("active",),
                event_type="reconciled_completed_during_pause",
            ):
                return
        with self._immediate_connection() as connection:
            current = connection.execute(
                "SELECT * FROM supervisor_admissions WHERE admission_id = ?",
                (record["admission_id"],),
            ).fetchone()
            if (
                current is None
                or not _same_record_generation(current, record)
                or not _capability_targets_record_generation(capability, current)
            ):
                return
            changed = self._pause_record_in_connection(
                connection,
                current,
                reason,
            )
        if changed:
            self._remove_binding(capability)
            self._pause_existing_child(record, reason)

    def target_key_for_run(self, project_root: Path, run_id: str) -> str | None:
        """Resolve a bound run to its opaque Space key without mutating state."""
        if not isinstance(project_root, Path) or not isinstance(run_id, str) or not run_id:
            return None
        status, record = self._routing_record(project_root, run_id)
        if status != "known" or record is None:
            return None
        target = record["target_key"]
        return target if isinstance(target, str) and target else None

    def target_key_for_run_id(self, run_id: str) -> str | None:
        """Resolve a run id across the supervisor ledger without mutation."""
        if not isinstance(run_id, str) or not run_id:
            return None
        if not self._ledger_path.exists():
            return None
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT target_key FROM supervisor_admissions "
                "WHERE run_id = ? ORDER BY updated_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        target = row["target_key"] if row is not None else None
        return target if isinstance(target, str) and target else None

    def admission_id_for_run(self, project_root: Path, run_id: str) -> str | None:
        """Resolve a ledger-bound run to its admission id without writing."""
        if not isinstance(project_root, Path) or not isinstance(run_id, str) or not run_id:
            return None
        status, record = self._routing_record(project_root, run_id)
        if status != "known" or record is None:
            return None
        admission_id = record["admission_id"]
        return admission_id if isinstance(admission_id, str) and admission_id else None

    def _record(self, admission_id: str) -> sqlite3.Row | None:
        if not self._ledger_path.exists():
            return None
        with self._read_connection() as connection:
            return connection.execute(
                "SELECT * FROM supervisor_admissions WHERE admission_id = ?",
                (admission_id,),
            ).fetchone()

    def _routing_record(
        self,
        project_root: Path,
        run_id: str,
    ) -> tuple[str, sqlite3.Row | None]:
        """Classify one host lookup without creating or migrating the ledger."""
        if not self._ledger_path.exists():
            return "absent", None
        try:
            with self._read_connection() as connection:
                record = connection.execute(
                    "SELECT * FROM supervisor_admissions WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return "unreadable", None
        if record is None:
            return "unknown", None
        if Path(project_root).resolve() != Path(record["canonical_root"]).resolve():
            return "root_mismatch", record
        return "known", record

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        uri = self._ledger_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _immediate_connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self._ledger_path, timeout=30, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class ManagedSpaceHostRouter:
    """Route ledger-known runs only through supervisor-owned authority."""

    def __init__(
        self,
        supervisor: ManagedSpaceSupervisor,
        fallback: Callable[[Path, SwarmRun], Any],
    ) -> None:
        if not isinstance(supervisor, ManagedSpaceSupervisor):
            raise TypeError("managed Space host router requires a supervisor")
        if not callable(fallback):
            raise TypeError("managed Space host router requires a fallback")
        self._supervisor = supervisor
        self._fallback = fallback

    def __call__(self, project_root: Path, run: SwarmRun):
        from cli.swarm_host import SwarmExecutionOptions

        status, _record = self._supervisor._routing_record(
            project_root,
            run.run_id,
        )
        if status == "known":
            return self._supervisor.execution_options_for_run(project_root, run)
        if status in {"unreadable", "root_mismatch"}:
            return SwarmExecutionOptions(
                blocked_reason="supervisor_binding_unavailable"
            )
        return self._fallback(project_root, run)


def _supervisor_ledger_schema_ready(connection: sqlite3.Connection) -> bool:
    """Return whether the ledger has the complete, current core schema.

    This is intentionally a read-only predicate.  ``start`` and runtime
    callers use it before deciding whether a migration is necessary, so a
    ready database never drops and recreates indexes merely to be checked.
    """
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if {"supervisor_admissions", "supervisor_audit"} - tables:
            return False
        admission_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(supervisor_admissions)")
        }
        if not _SUPERVISOR_ADMISSION_COLUMNS <= admission_columns:
            return False
        index_sql = {
            row[0]: _normalized_sql(row[1])
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'index'"
            )
            if row[1] is not None
        }
    except sqlite3.Error:
        return False
    return all(
        index_sql.get(name) == expected
        for name, expected in _SUPERVISOR_REQUIRED_INDEX_SQL.items()
    )


def _ensure_ticker_lease_schema(connection: sqlite3.Connection) -> None:
    """Create the durable single-host-ticker lease table idempotently."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS supervisor_ticker_leases (
            lease_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            state TEXT NOT NULL,
            started_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            terminal_reason TEXT
        )"""
    )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_supervisor_one_ticker_lease
           ON supervisor_ticker_leases((1)) WHERE state = 'active'"""
    )


def _sqlite_schema_objects_present(
    connection: sqlite3.Connection,
    schema_objects: tuple[str, ...],
) -> bool:
    """Check named tables/indexes without issuing schema-changing SQL."""
    if any(not isinstance(name, str) or not name for name in schema_objects):
        raise TypeError("supervision state schema object name is invalid")
    placeholders = ", ".join("?" for _ in schema_objects)
    rows = connection.execute(
        f"SELECT name FROM sqlite_master WHERE name IN ({placeholders})",
        schema_objects,
    ).fetchall()
    return {row[0] for row in rows} == set(schema_objects)


def _normalized_sql(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", "", value).lower()


_PRODUCTION_SUPERVISORS: dict[Path, ManagedSpaceSupervisor] = {}
_PRODUCTION_SUPERVISORS_LOCK = threading.Lock()


def production_supervisor_ledger_path() -> Path:
    """Return the profile/config-owned global supervisor authority location."""
    from runtime._compat.shim_constants import get_sidekick_home

    return (
        Path(get_sidekick_home()).expanduser().resolve()
        / "state"
        / "nova-space-supervisor.sqlite"
    )


def get_production_managed_space_supervisor() -> ManagedSpaceSupervisor:
    """Cache one process authority owner per trusted ledger path without writes."""
    ledger_path = production_supervisor_ledger_path()
    with _PRODUCTION_SUPERVISORS_LOCK:
        supervisor = _PRODUCTION_SUPERVISORS.get(ledger_path)
        if supervisor is None:
            supervisor = ManagedSpaceSupervisor(
                ledger_path=ledger_path,
                governance_resolver=resolve_managed_space_governance,
            )
            # The executor is a host-owned proxy. It builds the action gateway
            # only after a current capability/run binding has been revalidated.
            from nova.production_workers import ProductionManagedActionExecutor

            supervisor._action_executor = ProductionManagedActionExecutor(supervisor)
            _PRODUCTION_SUPERVISORS[ledger_path] = supervisor
        return supervisor


def resolve_managed_space_governance(target_key: str) -> ManagedSpaceGovernance:
    """Production resolver: Space identity plus independently trusted root only."""
    from web.api.space_engine import (
        get_existing_space_read_only,
        list_nova_management_audit,
    )
    from web.api.workspace import resolve_enrollment_trusted_workspace_read_only

    space = get_existing_space_read_only(_target_key(target_key))
    if space is None:
        raise ValueError("managed Space is unavailable")
    config = space.load_config()
    if config.get("_space_config_malformed") or config.get(
        "_nova_management_malformed"
    ):
        raise ValueError("managed Space governance is malformed")
    management = config.get("nova_management")
    if not isinstance(management, Mapping):
        raise ValueError("managed Space governance is unavailable")
    root = resolve_enrollment_trusted_workspace_read_only(config.get("project_dir"))
    audits = list_nova_management_audit(space)
    if not audits:
        raise ValueError("managed Space governance lacks audit evidence")
    audit = audits[-1]
    return ManagedSpaceGovernance.from_values(
        space_id=config.get("space_id"),
        canonical_root=root,
        root_fingerprint=audit.get("root_fingerprint"),
        yolo=management.get("yolo"),
        enrolled=management.get("enrolled"),
        revision=management.get("revision"),
        policy_identity="space-governance:" + str(management.get("revision")),
    )


def list_current_managed_space_governance() -> dict[str, ManagedSpaceGovernance]:
    """Return a bounded, read-only snapshot of currently enrolled YOLO Spaces.

    This registry enumeration is intentionally separate from the per-target
    resolver: malformed, untrusted, or revoked Spaces are skipped rather than
    becoming autonomous work. It performs no ledger writes and never touches a
    model/provider; the host ticker supplies the snapshot to the runtime.
    """
    try:
        from web.api.space_engine import get_all_spaces
        spaces = get_all_spaces()
    except Exception:
        return {}
    snapshot: dict[str, ManagedSpaceGovernance] = {}
    for space in spaces[:256]:
        target = getattr(space, "slug", "")
        if not isinstance(target, str) or not target:
            continue
        try:
            governance = resolve_managed_space_governance(target)
        except Exception:
            continue
        if governance.yolo is True and governance.enrolled is True:
            snapshot[target] = governance
    return snapshot


def managed_space_execution_options_for_run(
    supervisor: ManagedSpaceSupervisor,
    project_root: Path,
    run: SwarmRun,
):
    """Strict host resolver adapter; no global supervisor is installed here."""
    if not isinstance(supervisor, ManagedSpaceSupervisor):
        raise TypeError("managed Space execution resolver requires a supervisor")
    return supervisor.execution_options_for_run(project_root, run)


def _capability(
    admission_id: str,
    target_key: str,
    governance: ManagedSpaceGovernance,
    run_id: str,
    digest: str,
    *,
    attachment_generation: int,
) -> ManagedSpaceCapability:
    families = _ALLOWED_ACTION_FAMILIES
    return ManagedSpaceCapability(
        admission_id,
        target_key,
        governance.space_id,
        governance.canonical_root,
        governance.root_fingerprint,
        governance.revision,
        governance.policy_identity,
        run_id,
        digest,
        families,
        _allowed_action_families_digest(families),
        attachment_generation,
        _token=_CAPABILITY_TOKEN,
    )


def _capability_from_record(
    record: Mapping[str, Any],
    *,
    attachment_generation: int | None = None,
) -> ManagedSpaceCapability | None:
    try:
        families = tuple(json.loads(record["allowed_action_families_json"]))
        if families != _ALLOWED_ACTION_FAMILIES:
            return None
        return ManagedSpaceCapability(
            record["admission_id"],
            record["target_key"],
            record["target_space_id"],
            Path(record["canonical_root"]).resolve(),
            record["root_fingerprint"],
            record["governance_revision"],
            record["policy_identity"],
            record["run_id"],
            record["intent_digest"],
            families,
            _allowed_action_families_digest(families),
            (
                record["attachment_generation"]
                if attachment_generation is None
                else attachment_generation
            ),
            _token=_CAPABILITY_TOKEN,
        )
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _diagnostic_metadata(
    capability: ManagedSpaceCapability, intent: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "goal": str(intent.get("goal", "Nova managed Space supervision")),
        "pack": "coding-team",
        "autonomy": "autonomous",
        "started_by": "nova-supervisor",
        "project_root": str(capability._canonical_root),
        "integration_namespace": "nova-space-supervisor",
        "required_pre_completion_hook": ManagedSpacePreCompletionHook.hook_id,
        "production_verifier": ManagedSpacePreCompletionHook.verifier_contract,
        "nova_supervisor": {
            "admission_id": capability._admission_id,
            "target_space_id": capability._target_space_id,
            "intent_digest": capability._intent_digest,
            "root_fingerprint": capability._root_fingerprint,
            "governance_revision": capability._governance_revision,
            "policy_identity": capability._policy_identity,
            "allowed_action_families": list(capability._allowed_action_families),
            "allowed_action_families_digest": capability._allowed_action_families_digest,
        },
    }


def _resolved_governance(
    resolver: Callable[[str], ManagedSpaceGovernance], target_key: str
) -> ManagedSpaceGovernance | None:
    try:
        result = resolver(target_key)
        if not isinstance(result, ManagedSpaceGovernance):
            return None
        return ManagedSpaceGovernance.from_values(
            space_id=result.space_id,
            canonical_root=result.canonical_root,
            root_fingerprint=result.root_fingerprint,
            yolo=result.yolo,
            enrolled=result.enrolled,
            revision=result.revision,
            policy_identity=result.policy_identity,
        )
    except Exception:
        # Governance comes from independently parsed Space/audit evidence.
        # Any malformed or unavailable resolver result must reject admission
        # without propagating a partially trusted configuration into a host
        # execution path.  Do not catch BaseException: shutdown/interrupt
        # semantics remain owned by the host process.
        return None


def _capability_matches_record(
    capability: ManagedSpaceCapability, record: Mapping[str, object]
) -> bool:
    try:
        action_families = _allowed_action_families_json(
            capability._allowed_action_families
        )
    except (TypeError, ValueError):
        return False
    return (
        record["target_key"] == capability._target_key
        and record["target_space_id"] == capability._target_space_id
        and record["intent_digest"] == capability._intent_digest
        and record["canonical_root"] == str(capability._canonical_root)
        and record["root_fingerprint"] == capability._root_fingerprint
        and record["governance_revision"] == capability._governance_revision
        and record["policy_identity"] == capability._policy_identity
        and record["allowed_action_families_json"] == action_families
        and record["run_id"] == capability._run_id
        and record["attachment_generation"] == capability._attachment_generation
    )


def _capability_targets_record_generation(
    capability: ManagedSpaceCapability,
    record: Mapping[str, object],
) -> bool:
    """Authorize only ledger-rooted fail-closed mutation for this generation."""
    return (
        isinstance(capability, ManagedSpaceCapability)
        and record["admission_id"] == capability._admission_id
        and record["run_id"] == capability._run_id
        and record["attachment_generation"] == capability._attachment_generation
    )


def _same_record_generation(
    current: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> bool:
    return (
        current["admission_id"] == snapshot["admission_id"]
        and current["run_id"] == snapshot["run_id"]
        and current["state"] == snapshot["state"]
        and current["attachment_generation"] == snapshot["attachment_generation"]
        and current["record_version"] == snapshot["record_version"]
    )


def _same_recovery_snapshot(
    current: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> bool:
    immutable_fields = (
        "admission_id",
        "target_key",
        "target_space_id",
        "intent_digest",
        "canonical_root",
        "root_fingerprint",
        "governance_revision",
        "policy_identity",
        "allowed_action_families_json",
        "workflow_contract_digest",
        "run_id",
        "state",
        "attachment_generation",
        "record_version",
    )
    return all(current[field] == snapshot[field] for field in immutable_fields)


def _diagnostic_metadata_matches_capability(
    metadata: Mapping[str, Any], capability: ManagedSpaceCapability
) -> bool:
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("project_root") != str(capability._canonical_root)
        or metadata.get("integration_namespace") != "nova-space-supervisor"
        or metadata.get("required_pre_completion_hook")
        != ManagedSpacePreCompletionHook.hook_id
    ):
        return False
    diagnostic = metadata.get("nova_supervisor")
    return isinstance(diagnostic, Mapping) and diagnostic == {
        "admission_id": capability._admission_id,
        "target_space_id": capability._target_space_id,
        "intent_digest": capability._intent_digest,
        "root_fingerprint": capability._root_fingerprint,
        "governance_revision": capability._governance_revision,
        "policy_identity": capability._policy_identity,
        "allowed_action_families": list(capability._allowed_action_families),
        "allowed_action_families_digest": capability._allowed_action_families_digest,
    }


def _diagnostic_metadata_matches_record(
    metadata: Mapping[str, Any], record: Mapping[str, Any]
) -> bool:
    try:
        families = tuple(json.loads(record["allowed_action_families_json"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if families != _ALLOWED_ACTION_FAMILIES:
        return False
    diagnostic = (
        metadata.get("nova_supervisor") if isinstance(metadata, Mapping) else None
    )
    return (
        record["workflow_contract_digest"] == _workflow_contract_digest(metadata)
        and record["workflow_contract_digest"] != ""
        and isinstance(diagnostic, Mapping)
        and metadata.get("project_root") == record["canonical_root"]
        and metadata.get("integration_namespace") == "nova-space-supervisor"
        and metadata.get("required_pre_completion_hook")
        == ManagedSpacePreCompletionHook.hook_id
        and diagnostic
        == {
            "admission_id": record["admission_id"],
            "target_space_id": record["target_space_id"],
            "intent_digest": record["intent_digest"],
            "root_fingerprint": record["root_fingerprint"],
            "governance_revision": record["governance_revision"],
            "policy_identity": record["policy_identity"],
            "allowed_action_families": list(families),
            "allowed_action_families_digest": _allowed_action_families_digest(families),
        }
    )


def _governance_matches_record(
    governance: ManagedSpaceGovernance | None,
    record: Mapping[str, Any],
) -> bool:
    return governance is not None and (
        governance.yolo is True
        and governance.enrolled is True
        and governance.space_id == record["target_space_id"]
        and governance.canonical_root == Path(record["canonical_root"]).resolve()
        and governance.root_fingerprint == record["root_fingerprint"]
        and governance.revision == record["governance_revision"]
        and governance.policy_identity == record["policy_identity"]
    )


def _workflow_contract_digest(metadata: Mapping[str, Any]) -> str:
    """Bind every durable workflow input and supervisor diagnostic exactly."""
    try:
        canonical = json.dumps(
            dict(metadata),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return ""
    return sha256(canonical.encode("utf-8")).hexdigest()


def _allowed_action_families_json(
    families: tuple[str, ...] = _ALLOWED_ACTION_FAMILIES,
) -> str:
    if tuple(families) != _ALLOWED_ACTION_FAMILIES:
        raise ValueError("managed Space action families are not permitted")
    return json.dumps(list(families), separators=(",", ":"))


def _allowed_action_families_digest(
    families: tuple[str, ...] = _ALLOWED_ACTION_FAMILIES,
) -> str:
    return sha256(_allowed_action_families_json(families).encode("utf-8")).hexdigest()


def _audit(
    connection: sqlite3.Connection,
    admission_id: str,
    event_type: str,
    actor: str | None,
    reason: str | None,
    now: str,
) -> None:
    connection.execute(
        "INSERT INTO supervisor_audit (admission_id, event_type, actor, reason, created_at) VALUES (?, ?, ?, ?, ?)",
        (admission_id, event_type, actor, reason, now),
    )


def _audit_auto_resume_wait_once(
    connection: sqlite3.Connection,
    admission_id: str,
    reason: str,
) -> None:
    """Record one fixed recovery-block decision after each audited pause."""
    if reason not in _AUTO_RESUME_WAIT_REASONS:
        return
    paused = connection.execute(
        """SELECT MAX(sequence) FROM supervisor_audit
           WHERE admission_id = ? AND event_type = 'paused'""",
        (admission_id,),
    ).fetchone()
    pause_sequence = int(paused[0] or 0) if paused is not None else 0
    if pause_sequence <= 0:
        return
    already_recorded = connection.execute(
        """SELECT 1 FROM supervisor_audit
           WHERE admission_id = ? AND event_type = ? AND sequence > ?
           LIMIT 1""",
        (admission_id, _AUTO_RESUME_WAIT_AUDIT_EVENT, pause_sequence),
    ).fetchone()
    if already_recorded is not None:
        return
    _audit(
        connection,
        admission_id,
        _AUTO_RESUME_WAIT_AUDIT_EVENT,
        SYSTEM_SPACE_LIFECYCLE_ACTOR,
        reason,
        _timestamp(),
    )


def _target_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("managed Space target is required")
    return value.strip()


def _intent_digest(intent: Mapping[str, Any]) -> str:
    if not isinstance(intent, Mapping):
        raise TypeError("managed Space intent must be a mapping")
    return sha256(
        json.dumps(
            dict(intent),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _root_fingerprint(root: Path) -> str:
    return sha256(str(root).encode("utf-8")).hexdigest()


def _timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _dashboard_actor(actor: str) -> None:
    if not isinstance(actor, str) or DASHBOARD_ACTOR_RE.fullmatch(actor) is None:
        raise PermissionError(
            "only a dashboard human actor may terminally transition a managed Space run"
        )


