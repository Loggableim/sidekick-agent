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
from typing import Any, Callable, Iterator, Mapping
from uuid import UUID, uuid4

from swarm_core.engine import PreCompletionContext, PreCompletionResult
from swarm_core.store import ProjectSwarmStore
from swarm_core.types import SwarmRun


DASHBOARD_ACTOR_RE = re.compile(r"dashboard:[0-9a-f]{64}\Z")
_INTENT_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_CAPABILITY_TOKEN = object()
_OCCUPIED_STATES = frozenset({"active", "paused", "abandoned"})
_EXECUTABLE_STATES = frozenset({"active"})
_ALLOWED_ACTION_FAMILIES = (
    "target_local_worktree",
    "github_publication",
    "target_deployment_worker",
)


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

    def __init__(self, *args: object, _token: object | None = None) -> None:
        if _token is not _CAPABILITY_TOKEN:
            raise TypeError("ManagedSpaceCapability is host-owned")
        (
            admission_id, target_key, target_space_id, canonical_root,
            root_fingerprint, governance_revision, policy_identity, run_id,
            intent_digest, allowed_action_families, allowed_action_families_digest,
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
        object.__setattr__(self, "_allowed_action_families_digest", allowed_action_families_digest)

    def __reduce__(self):
        raise TypeError("ManagedSpaceCapability cannot be serialized")

    def __reduce_ex__(self, protocol: int):
        raise TypeError("ManagedSpaceCapability cannot be serialized")

    def __repr__(self) -> str:
        return "<ManagedSpaceCapability>"


@dataclass(frozen=True, slots=True)
class SupervisorAdmission:
    status: str
    admission_id: str | None
    run_id: str | None
    capability: ManagedSpaceCapability | None
    reason: str | None


class ManagedSpacePreCompletionHook:
    hook_id = "nova-managed-space-supervisor-v1"

    def __init__(self, supervisor: "ManagedSpaceSupervisor", capability: ManagedSpaceCapability) -> None:
        self._supervisor = supervisor
        self._capability = capability

    def run(self, context: PreCompletionContext) -> PreCompletionResult:
        capability = self._capability
        if (
            context.run.run_id != capability._run_id
            or Path(context.project_root).resolve() != capability._canonical_root
            or not _diagnostic_metadata_matches_capability(context.run.metadata, capability)
        ):
            self._supervisor._pause(capability, "capability_invalid")
            return PreCompletionResult(False, "capability_invalid")
        reason = self._supervisor._revalidate(capability)
        if reason is None:
            return PreCompletionResult(continue_completion=True)
        self._supervisor._pause(capability, reason)
        return PreCompletionResult(continue_completion=False, pause_reason=reason)


class ManagedSpaceSupervisor:
    """The write-capable owner of a single global supervisor admission slot."""

    def __init__(
        self,
        *,
        ledger_path: Path,
        governance_resolver: Callable[[str], ManagedSpaceGovernance],
        child_store_factory: Callable[[Path], ProjectSwarmStore] = ProjectSwarmStore,
    ) -> None:
        if not callable(governance_resolver) or not callable(child_store_factory):
            raise TypeError("managed Space supervisor requires callable dependencies")
        self._ledger_path = Path(ledger_path)
        self._governance_resolver = governance_resolver
        self._child_store_factory = child_store_factory
        self._bindings: dict[str, ManagedSpaceCapability] = {}
        self._bindings_lock = threading.RLock()

    def start(self) -> None:
        """Initialize the central ledger only on a write-capable start path."""
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._ledger_path) as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS supervisor_admissions (
                    admission_id TEXT PRIMARY KEY,
                    target_key TEXT NOT NULL,
                    target_space_id TEXT NOT NULL,
                    intent_digest TEXT NOT NULL,
                    canonical_root TEXT NOT NULL,
                    root_fingerprint TEXT NOT NULL,
                    governance_revision INTEGER NOT NULL,
                    policy_identity TEXT NOT NULL,
                    allowed_action_families_json TEXT NOT NULL DEFAULT '[]',
                    run_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    terminal_actor TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_supervisor_target_intent
                    ON supervisor_admissions(target_space_id, intent_digest);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_supervisor_one_active
                    ON supervisor_admissions((1))
                    WHERE state IN ('active', 'paused', 'abandoned');
                CREATE TABLE IF NOT EXISTS supervisor_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    admission_id TEXT NOT NULL REFERENCES supervisor_admissions(admission_id),
                    event_type TEXT NOT NULL,
                    actor TEXT,
                    reason TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(supervisor_admissions)")
            }
            if "allowed_action_families_json" not in columns:
                connection.execute(
                    "ALTER TABLE supervisor_admissions ADD COLUMN allowed_action_families_json TEXT NOT NULL DEFAULT '[]'"
                )

    def admit(self, target_key: str, intent: Mapping[str, Any]) -> SupervisorAdmission:
        target_key = _target_key(target_key)
        governance = _resolved_governance(self._governance_resolver, target_key)
        if governance is None or governance.yolo is not True or governance.enrolled is not True:
            return SupervisorAdmission("rejected", None, None, None, "not_yolo_enrolled")
        intent_digest = _intent_digest(intent)
        self.start()
        admission_id = str(uuid4())
        run_id = str(uuid4())
        now = _timestamp()
        with self._immediate_connection() as connection:
            self._reconcile_completed_admissions(connection)
            existing = connection.execute(
                """SELECT admission_id, run_id, state FROM supervisor_admissions
                   WHERE target_space_id = ? AND intent_digest = ?""",
                (governance.space_id, intent_digest),
            ).fetchone()
            if existing is not None:
                if existing["state"] in _OCCUPIED_STATES | {"completed"}:
                    return SupervisorAdmission("coalesced", existing["admission_id"], existing["run_id"], None, None)
                return SupervisorAdmission("rejected", existing["admission_id"], existing["run_id"], None, "terminal_admission")
            active = connection.execute(
                "SELECT 1 FROM supervisor_admissions WHERE state IN ('active', 'paused', 'abandoned')"
            ).fetchone()
            if active is not None:
                return SupervisorAdmission("rejected", None, None, None, "active_limit")
            connection.execute(
                """INSERT INTO supervisor_admissions (
                    admission_id, target_key, target_space_id, intent_digest, canonical_root,
                    root_fingerprint, governance_revision, policy_identity, allowed_action_families_json, run_id, state,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (
                    admission_id, target_key, governance.space_id, intent_digest,
                    str(governance.canonical_root), governance.root_fingerprint,
                    governance.revision, governance.policy_identity,
                    _allowed_action_families_json(), run_id, now, now,
                ),
            )
            _audit(connection, admission_id, "admitted", None, None, now)
        capability = _capability(admission_id, target_key, governance, run_id, intent_digest)
        try:
            store = self._child_store_factory(governance.canonical_root)
            store.create_run(run_id, metadata=_diagnostic_metadata(capability, intent))
        except BaseException:
            with self._immediate_connection() as connection:
                connection.execute(
                    "UPDATE supervisor_admissions SET state = 'paused', updated_at = ? WHERE admission_id = ?",
                    (_timestamp(), admission_id),
                )
                _audit(connection, admission_id, "paused", None, "child_store_failure", _timestamp())
            raise
        with self._bindings_lock:
            self._bindings[run_id] = capability
        return SupervisorAdmission("created", admission_id, run_id, capability, None)

    def list_active_admissions(self) -> list[dict[str, str]]:
        if not self._ledger_path.exists():
            return []
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT admission_id, target_space_id, run_id, state FROM supervisor_admissions WHERE state IN ('active', 'paused', 'abandoned')"
            ).fetchall()
        return [dict(row) for row in rows]

    def _reconcile_completed_admissions(self, connection: sqlite3.Connection) -> None:
        """Release only active slots whose trusted child run is durably complete.

        This runs only from an admission write transaction, never from a
        status/read API.  It deliberately has no governance lookup: recovery
        validates the immutable ledger and child diagnostic contract instead.
        """
        records = connection.execute(
            "SELECT * FROM supervisor_admissions WHERE state = 'active'"
        ).fetchall()
        for record in records:
            try:
                store = ProjectSwarmStore.open_read_only(Path(record["canonical_root"]))
                run = store.get_run(record["run_id"])
            except (OSError, RuntimeError, ValueError, sqlite3.Error):
                run = None
            if run is not None and run.status == "completed" and _diagnostic_metadata_matches_record(run.metadata, record):
                cursor = connection.execute(
                    "UPDATE supervisor_admissions SET state = 'completed', updated_at = ? WHERE admission_id = ? AND state = 'active'",
                    (_timestamp(), record["admission_id"]),
                )
                if cursor.rowcount:
                    _audit(connection, record["admission_id"], "reconciled_completed", None, None, _timestamp())
            elif run is not None and run.status == "completed":
                _audit(connection, record["admission_id"], "reconciliation_failed", None, "diagnostic_mismatch", _timestamp())

    def execution_options_for_run(self, project_root: Path, run: SwarmRun):
        """Return host-compatible options only for a live, active binding."""
        from cli.swarm_host import SwarmExecutionOptions

        capability = self._binding_for_run(project_root, run)
        if capability is None:
            return SwarmExecutionOptions(blocked_reason="supervisor_binding_unavailable")
        if not _diagnostic_metadata_matches_capability(run.metadata, capability):
            self._pause(capability, "capability_invalid")
            return SwarmExecutionOptions(blocked_reason="capability_invalid")
        reason = self._revalidate(capability)
        if reason is not None:
            self._pause(capability, reason)
            return SwarmExecutionOptions(blocked_reason=reason)
        return SwarmExecutionOptions(
            max_calls=128,
            max_concurrent=3,
            pre_completion_hook=ManagedSpacePreCompletionHook(self, capability),
            on_completed=self.completion_observer_for_run(run.run_id),
        )

    def pre_completion_hook_for_run(self, run_id: str) -> ManagedSpacePreCompletionHook:
        with self._bindings_lock:
            capability = self._bindings.get(run_id)
        if capability is None:
            raise ValueError("managed Space supervisor binding is unavailable")
        return ManagedSpacePreCompletionHook(self, capability)

    def revalidate_action_boundary(self, capability: ManagedSpaceCapability) -> bool:
        reason = self._revalidate(capability)
        if reason is None:
            return True
        self._pause(capability, reason)
        return False

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

    def _reconcile_completed_record(
        self,
        record: Mapping[str, Any],
        *,
        allowed_states: tuple[str, ...],
        event_type: str,
    ) -> bool:
        """Record a verified child completion without consulting live governance."""
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
        with self._immediate_connection() as connection:
            cursor = connection.execute(
                f"UPDATE supervisor_admissions SET state = 'completed', updated_at = ? WHERE admission_id = ? AND state IN ({placeholders})",
                (_timestamp(), record["admission_id"], *allowed_states),
            )
            if cursor.rowcount:
                _audit(connection, record["admission_id"], event_type, None, None, _timestamp())
        if cursor.rowcount:
            with self._bindings_lock:
                self._bindings.pop(record["run_id"], None)
        return cursor.rowcount == 1

    def completion_observer_for_run(self, run_id: str) -> Callable[[Path, SwarmRun], None]:
        """Return the host completion seam for this one in-process binding."""
        def observe(project_root: Path, run: SwarmRun) -> None:
            with self._bindings_lock:
                capability = self._bindings.get(run_id)
            if (
                capability is None
                or run.run_id != run_id
                or run.status != "completed"
            ):
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
        record = self._record(admission_id)
        if record is None or record["state"] in {"cancelled", "completed"}:
            return False
        # Invalidate execution before touching the child store.  If that
        # operation fails, the ledger remains paused and unbound rather than
        # leaving a window for another action boundary to proceed.
        if record["state"] in {"active", "paused"}:
            with self._immediate_connection() as connection:
                connection.execute(
                    "UPDATE supervisor_admissions SET state = 'paused', updated_at = ? WHERE admission_id = ? AND state IN ('active', 'paused')",
                    (_timestamp(), admission_id),
                )
        with self._bindings_lock:
            self._bindings.pop(record["run_id"], None)
        store = self._child_store_factory(Path(record["canonical_root"]))
        try:
            store.cancel_run_by_human(record["run_id"], actor)
        except ValueError:
            if self._reconcile_completed_record(
                record,
                allowed_states=("active", "paused"),
                event_type="reconciled_completed_after_human_terminal",
            ):
                return False
            raise
        except KeyError:
            # A child-store bootstrap can fail after the global ledger reserves
            # its slot.  A dashboard cancellation still records the durable
            # human cleanup; no worker/model path can release that slot.
            pass
        with self._immediate_connection() as connection:
            cursor = connection.execute(
                """UPDATE supervisor_admissions
                   SET state = 'cancelled', terminal_actor = ?, updated_at = ?
                   WHERE admission_id = ? AND state != 'cancelled'""",
                (actor, _timestamp(), admission_id),
            )
            if cursor.rowcount:
                _audit(connection, admission_id, "cancelled", actor, None, _timestamp())
        return cursor.rowcount == 1

    def abandon(self, admission_id: str, *, actor: str) -> bool:
        _dashboard_actor(actor)
        record = self._record(admission_id)
        if record is None or record["state"] in {"cancelled", "completed", "abandoned"}:
            return False
        with self._immediate_connection() as connection:
            cursor = connection.execute(
                "UPDATE supervisor_admissions SET state = 'paused', updated_at = ? WHERE admission_id = ? AND state IN ('active', 'paused')",
                (_timestamp(), admission_id),
            )
            if cursor.rowcount:
                _audit(connection, admission_id, "paused", actor, "abandon_requested", _timestamp())
        with self._bindings_lock:
            self._bindings.pop(record["run_id"], None)
        store = self._child_store_factory(Path(record["canonical_root"]))
        try:
            store.abandon_run_by_human(record["run_id"], actor)
        except ValueError:
            if self._reconcile_completed_record(
                record,
                allowed_states=("active", "paused"),
                event_type="reconciled_completed_after_human_terminal",
            ):
                return False
            raise
        with self._immediate_connection() as connection:
            cursor = connection.execute(
                "UPDATE supervisor_admissions SET state = 'abandoned', terminal_actor = ?, updated_at = ? WHERE admission_id = ? AND state NOT IN ('cancelled', 'completed', 'abandoned')",
                (actor, _timestamp(), admission_id),
            )
            if cursor.rowcount:
                _audit(connection, admission_id, "abandoned", actor, None, _timestamp())
        return cursor.rowcount == 1

    def _binding_for_run(self, project_root: Path, run: SwarmRun) -> ManagedSpaceCapability | None:
        with self._bindings_lock:
            capability = self._bindings.get(run.run_id)
        if capability is None or Path(project_root).resolve() != capability._canonical_root:
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
        governance = _resolved_governance(self._governance_resolver, capability._target_key)
        if governance is None or governance.yolo is not True or governance.enrolled is not True:
            return "governance_revoked"
        if governance.canonical_root != capability._canonical_root or governance.root_fingerprint != capability._root_fingerprint:
            return "root_mismatch"
        if governance.space_id != capability._target_space_id or governance.revision != capability._governance_revision or governance.policy_identity != capability._policy_identity:
            return "governance_revoked"
        return None

    def _pause(self, capability: ManagedSpaceCapability, reason: str) -> None:
        record = self._record(capability._admission_id)
        # Object-level tampering must never turn a capability field into a
        # filesystem write target.  Only a matching ledger record supplies
        # the root/run to pause; a missing record is ledger-only fail-closed.
        if record is not None and record["state"] == "active":
            if self._reconcile_completed_record(
                record,
                allowed_states=("active",),
                event_type="reconciled_completed_during_pause",
            ):
                return
        target_root: Path | None = None
        target_run_id: str | None = None
        target_admission_id: str | None = None
        if record is not None and record["state"] in _EXECUTABLE_STATES:
            target_admission_id = record["admission_id"]
            target_root = Path(record["canonical_root"])
            target_run_id = record["run_id"]
        try:
            if target_root is not None and target_run_id is not None:
                store = self._child_store_factory(target_root)
                run = store.get_run(target_run_id)
                if run is not None and run.status == "running":
                    store.set_run_status(target_run_id, "paused")
                if run is not None:
                    store.append_event_once(
                        target_run_id,
                        "nova.supervisor.paused",
                        {"reason": reason},
                        idempotency_key="supervisor-pause:" + reason,
                    )
        finally:
            if target_admission_id is not None:
                with self._immediate_connection() as connection:
                    cursor = connection.execute(
                        "UPDATE supervisor_admissions SET state = 'paused', updated_at = ? WHERE admission_id = ? AND state = 'active'",
                        (_timestamp(), target_admission_id),
                    )
                    if cursor.rowcount:
                        _audit(connection, target_admission_id, "paused", None, reason, _timestamp())

    def _record(self, admission_id: str) -> sqlite3.Row | None:
        if not self._ledger_path.exists():
            return None
        with self._read_connection() as connection:
            return connection.execute("SELECT * FROM supervisor_admissions WHERE admission_id = ?", (admission_id,)).fetchone()

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._ledger_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _immediate_connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._ledger_path, timeout=30, isolation_level=None)
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


def resolve_managed_space_governance(target_key: str) -> ManagedSpaceGovernance:
    """Production resolver: Space identity plus independently trusted root only."""
    from web.api.space_engine import get_existing_space_read_only, list_nova_management_audit
    from web.api.workspace import resolve_enrollment_trusted_workspace_read_only

    space = get_existing_space_read_only(_target_key(target_key))
    if space is None:
        raise ValueError("managed Space is unavailable")
    config = space.load_config()
    if config.get("_space_config_malformed") or config.get("_nova_management_malformed"):
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


def managed_space_execution_options_for_run(
    supervisor: ManagedSpaceSupervisor,
    project_root: Path,
    run: SwarmRun,
):
    """Strict host resolver adapter; no global supervisor is installed here."""
    if not isinstance(supervisor, ManagedSpaceSupervisor):
        raise TypeError("managed Space execution resolver requires a supervisor")
    return supervisor.execution_options_for_run(project_root, run)


def _capability(admission_id: str, target_key: str, governance: ManagedSpaceGovernance, run_id: str, digest: str) -> ManagedSpaceCapability:
    families = _ALLOWED_ACTION_FAMILIES
    return ManagedSpaceCapability(
        admission_id, target_key, governance.space_id, governance.canonical_root,
        governance.root_fingerprint, governance.revision, governance.policy_identity,
        run_id, digest, families, _allowed_action_families_digest(families),
        _token=_CAPABILITY_TOKEN,
    )


def _diagnostic_metadata(capability: ManagedSpaceCapability, intent: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "goal": str(intent.get("goal", "Nova managed Space supervision")),
        "pack": "coding-team",
        "autonomy": "autonomous",
        "integration_namespace": "nova-space-supervisor",
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


def _resolved_governance(resolver: Callable[[str], ManagedSpaceGovernance], target_key: str) -> ManagedSpaceGovernance | None:
    try:
        result = resolver(target_key)
        if not isinstance(result, ManagedSpaceGovernance):
            return None
        return ManagedSpaceGovernance.from_values(
            space_id=result.space_id, canonical_root=result.canonical_root,
            root_fingerprint=result.root_fingerprint, yolo=result.yolo,
            enrolled=result.enrolled, revision=result.revision,
            policy_identity=result.policy_identity,
        )
    except (OSError, TypeError, ValueError):
        return None


def _capability_matches_record(capability: ManagedSpaceCapability, record: Mapping[str, object]) -> bool:
    try:
        action_families = _allowed_action_families_json(capability._allowed_action_families)
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
    )


def _diagnostic_metadata_matches_capability(metadata: Mapping[str, Any], capability: ManagedSpaceCapability) -> bool:
    if not isinstance(metadata, Mapping) or metadata.get("integration_namespace") != "nova-space-supervisor":
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


def _diagnostic_metadata_matches_record(metadata: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    try:
        families = tuple(json.loads(record["allowed_action_families_json"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if families != _ALLOWED_ACTION_FAMILIES:
        return False
    diagnostic = metadata.get("nova_supervisor") if isinstance(metadata, Mapping) else None
    return (
        isinstance(diagnostic, Mapping)
        and metadata.get("integration_namespace") == "nova-space-supervisor"
        and diagnostic == {
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


def _allowed_action_families_json(families: tuple[str, ...] = _ALLOWED_ACTION_FAMILIES) -> str:
    if tuple(families) != _ALLOWED_ACTION_FAMILIES:
        raise ValueError("managed Space action families are not permitted")
    return json.dumps(list(families), separators=(",", ":"))


def _allowed_action_families_digest(families: tuple[str, ...] = _ALLOWED_ACTION_FAMILIES) -> str:
    return sha256(_allowed_action_families_json(families).encode("utf-8")).hexdigest()


def _audit(connection: sqlite3.Connection, admission_id: str, event_type: str, actor: str | None, reason: str | None, now: str) -> None:
    connection.execute(
        "INSERT INTO supervisor_audit (admission_id, event_type, actor, reason, created_at) VALUES (?, ?, ?, ?, ?)",
        (admission_id, event_type, actor, reason, now),
    )


def _target_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("managed Space target is required")
    return value.strip()


def _intent_digest(intent: Mapping[str, Any]) -> str:
    if not isinstance(intent, Mapping):
        raise TypeError("managed Space intent must be a mapping")
    return sha256(json.dumps(dict(intent), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def _root_fingerprint(root: Path) -> str:
    return sha256(str(root).encode("utf-8")).hexdigest()


def _timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _dashboard_actor(actor: str) -> None:
    if not isinstance(actor, str) or DASHBOARD_ACTOR_RE.fullmatch(actor) is None:
        raise PermissionError("only a dashboard human actor may terminally transition a managed Space run")
