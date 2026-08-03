from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from nova.resonance_memory import TickerResonanceMemory
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor


def _governance(root: Path, *, enrolled: bool) -> ManagedSpaceGovernance:
    return ManagedSpaceGovernance.from_values(
        space_id=str(uuid4()),
        canonical_root=root,
        root_fingerprint="",
        yolo=True,
        enrolled=enrolled,
        revision=1,
        policy_identity="space-governance:1",
    )


def test_revoked_terminal_resonance_is_tombstoned_before_later_reenrollment(
    tmp_path: Path,
) -> None:
    """Re-enrolling a Space never replays a terminal event from its old epoch."""
    root = tmp_path / "aquarium-zentrum"
    records = {"aquarium-zentrum": _governance(root, enrolled=True)}
    ticker = tmp_path / "ticker_events.jsonl"
    event_id = "f" * 64
    ticker.write_text(
        json.dumps(
            {
                "event_id": event_id,
                "space": "aquarium-zentrum",
                "source": "ci",
                "stage": "handled",
                "status": "failed",
                "reason": "ci_failed",
                "at": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=records.get,
    )
    memory = TickerResonanceMemory(
        supervisor=supervisor,
        ticker_path=ticker,
        memory_path=tmp_path / "resonance.sqlite",
    )
    assert memory.consume().accepted == 1

    records["aquarium-zentrum"] = _governance(root, enrolled=False)
    delivered: list[dict[str, object]] = []
    assert memory.publish_pending(lambda event: delivered.append(dict(event)) or True) == 0

    records["aquarium-zentrum"] = _governance(root, enrolled=True)
    assert memory.publish_pending(lambda event: delivered.append(dict(event)) or True) == 0
    assert delivered == []
