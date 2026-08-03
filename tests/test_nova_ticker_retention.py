from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from nova.space_supervision_runtime import _append_ticker_event
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor


def test_ticker_writer_rotates_bounded_redacted_log(tmp_path: Path) -> None:
    root = tmp_path / "aquarium-zentrum"
    governance = ManagedSpaceGovernance.from_values(
        space_id=str(uuid4()), canonical_root=root, root_fingerprint="",
        yolo=True, enrolled=True, revision=1, policy_identity="space-governance:1",
    )
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda key: governance if key == "aquarium-zentrum" else None,
    )
    for index in range(2200):
        _append_ticker_event(
            supervisor,
            target_key="aquarium-zentrum",
            source="ci",
            reason_code="ci_failed",
            event_id=f"{index + 1:064x}",
            stage="handled",
            status="failed",
            observed_at=float(index),
        )
    path = tmp_path / "ticker_events.jsonl"
    assert path.stat().st_size <= 256 * 1024 + 512
    lines = path.read_text(encoding="utf-8").splitlines()
    assert 0 < len(lines) < 2200
    records = [json.loads(line) for line in lines]
    assert all(set(record) <= {"event_id", "space", "source", "reason", "stage", "status", "at"} for record in records)
    assert records[-1]["event_id"] == f"{2200:064x}"
    assert records[0]["event_id"] != f"{1:064x}"
