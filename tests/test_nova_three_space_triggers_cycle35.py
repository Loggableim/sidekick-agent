"""Git/Kanban/CI trigger projection regressions for fixed Spaces."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor


def test_three_space_trigger_sources_are_deduplicated_and_stale_events_do_not_dispatch(tmp_path: Path) -> None:
    roots = {slug: tmp_path / slug for slug in ("nova", "finanzjunkie", "aquarium-zentrum")}
    for root in roots.values(): root.mkdir(parents=True)
    governance = {
        "nova": ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=roots["nova"], yolo=False, enrolled=False, revision=1, policy_identity="cycle35"),
        "finanzjunkie": ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=roots["finanzjunkie"], yolo=True, enrolled=True, revision=1, policy_identity="cycle35"),
        "aquarium-zentrum": ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=roots["aquarium-zentrum"], yolo=True, enrolled=True, revision=1, policy_identity="cycle35"),
    }
    supervisor = ManagedSpaceSupervisor(ledger_path=tmp_path / "state" / "supervisor.sqlite", governance_resolver=governance.get)
    dispatched: list[tuple[Path, str]] = []
    runtime = NovaSpaceSupervisionRuntime(supervisor=supervisor, dispatch_run=lambda root, run_id: dispatched.append((root, run_id)), governance_snapshots=lambda: governance)

    assert runtime.ingest_signal("nova", source="git", event_id="nova-git-1", reason_code="git_change") is False
    for source, event_id, reason in (("git", "finance-git-1", "git_change"), ("kanban", "finance-kanban-1", "kanban_change"), ("ci", "finance-ci-1", "ci_failed")):
        assert runtime.ingest_signal("finanzjunkie", source=source, event_id=event_id, reason_code=reason) is True
        assert runtime.ingest_signal("finanzjunkie", source=source, event_id=event_id, reason_code=reason) is False
    assert runtime.ingest_signal("aquarium-zentrum", source="ci", event_id="aquarium-ci-1", reason_code="ci_failed") is True
    outcomes = runtime.pulse(now_epoch=100.0)
    assert len(dispatched) == 1
    assert dispatched[0][0] == roots["aquarium-zentrum"]
    assert {item.target_key for item in outcomes} == {"aquarium-zentrum", "finanzjunkie"}
    assert any(item.status == "active_limit" for item in outcomes)

    # A stale event after a registry generation change must remain pending but
    # cannot inherit the old root or dispatch a second run.
    moved = tmp_path / "finanzjunkie-moved"; moved.mkdir()
    governance["finanzjunkie"] = replace(governance["finanzjunkie"], canonical_root=moved, revision=2)
    assert runtime.ingest_signal("finanzjunkie", source="git", event_id="finance-git-1", reason_code="git_change") is False
    assert [(item.target_key, item.status) for item in runtime.pulse(now_epoch=101.0)] == [("finanzjunkie", "ineligible")]
    assert len(dispatched) == 1


