from pathlib import Path
from uuid import uuid4

from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor


def test_three_space_restart_never_silently_resumes_or_duplicates_intent(tmp_path):
    roots = {slug: tmp_path / slug for slug in ("nova", "finanz-junkie", "aquarium-zentrum")}
    for root in roots.values():
        root.mkdir(parents=True)
    governance = {
        "nova": ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=roots["nova"], root_fingerprint="", yolo=False, enrolled=False, revision=1, policy_identity="policy"),
        "finanz-junkie": ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=roots["finanz-junkie"], root_fingerprint="", yolo=True, enrolled=True, revision=1, policy_identity="policy"),
        "aquarium-zentrum": ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=roots["aquarium-zentrum"], root_fingerprint="", yolo=True, enrolled=True, revision=1, policy_identity="policy"),
    }
    supervisor = ManagedSpaceSupervisor(ledger_path=tmp_path / "state" / "supervisor.sqlite", governance_resolver=governance.get)
    dispatched = []
    dispatch = lambda root, run_id: dispatched.append((root, run_id))
    runtime = NovaSpaceSupervisionRuntime(supervisor=supervisor, dispatch_run=dispatch)
    assert runtime.ingest_signal("aquarium-zentrum", source="git", event_id="same-intent", reason_code="git_change")
    assert not runtime.ingest_signal("aquarium-zentrum", source="git", event_id="same-intent", reason_code="git_change")
    first = runtime.pulse(now_epoch=0.0)
    assert [(item.target_key, item.status) for item in first] == [("aquarium-zentrum", "started")]
    assert len(dispatched) == 1

    restarted = NovaSpaceSupervisionRuntime(supervisor=supervisor, dispatch_run=dispatch)
    assert restarted.pulse(now_epoch=1.0) == ()
    assert len(dispatched) == 1
    assert restarted.ingest_signal("finanz-junkie", source="ci", event_id="finance-1", reason_code="ci_change")
    blocked = restarted.pulse(now_epoch=2.0)
    assert [(item.target_key, item.status) for item in blocked] == [("finanz-junkie", "active_limit")]
    assert len(dispatched) == 1

