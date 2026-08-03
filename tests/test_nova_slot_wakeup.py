from pathlib import Path
from uuid import uuid4

from nova.space_supervision_runtime import (
    NovaSpaceSupervisionRuntime,
    _SUPERVISION_SCHEMA_OBJECTS,
    _ensure_schema,
)
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor


def test_human_release_wakes_pending_active_limit_state(tmp_path: Path):
    root = tmp_path / "alpha"
    governance = ManagedSpaceGovernance.from_values(
        space_id=str(uuid4()), canonical_root=root, root_fingerprint="",
        yolo=True, enrolled=True, revision=1, policy_identity="space-governance:1",
    )
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda _target: governance,
    )
    runtime = NovaSpaceSupervisionRuntime(supervisor=supervisor, dispatch_run=lambda *_: None)
    runtime.ingest_signal("alpha", source="heartbeat", event_id="wake-test", reason_code="periodic_check")
    with supervisor._supervision_state_transaction(
        schema_objects=_SUPERVISION_SCHEMA_OBJECTS,
        schema_initializer=_ensure_schema,
    ) as connection:
        connection.execute(
            "UPDATE nova_supervision_space_state SET last_checked_at = 10, "
            "last_check_code = 'active_limit' WHERE target_key = 'alpha'"
        )
    assert runtime.wake_space("alpha") is True
    with supervisor._supervision_state_reader() as connection:
        row = connection.execute(
            "SELECT pending_digest, last_checked_at, last_check_code, last_outcome_code "
            "FROM nova_supervision_space_state WHERE target_key = 'alpha'"
        ).fetchone()
    assert row["pending_digest"]
    assert row["last_checked_at"] is None
    assert row["last_check_code"] == ""
    assert row["last_outcome_code"] == "human_release"


def test_wakeup_does_not_touch_clear_or_non_slot_state(tmp_path: Path):
    root = tmp_path / "alpha"
    governance = ManagedSpaceGovernance.from_values(
        space_id=str(uuid4()), canonical_root=root, root_fingerprint="",
        yolo=True, enrolled=True, revision=1, policy_identity="space-governance:1",
    )
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda _target: governance,
    )
    runtime = NovaSpaceSupervisionRuntime(supervisor=supervisor, dispatch_run=lambda *_: None)
    assert runtime.wake_space("alpha") is False
