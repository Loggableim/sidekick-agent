from pathlib import Path
from uuid import uuid4
from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor

def test_three_space_yolo_only_global_slot_and_intent_coalescing(tmp_path: Path):
    roots={s:tmp_path/s for s in ("nova","finanzjunkie","aquarium-zentrum")}
    for r in roots.values(): r.mkdir()
    gov={
      "nova": ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=roots["nova"], yolo=False, enrolled=True, revision=1, policy_identity="test"),
      "finanzjunkie": ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=roots["finanzjunkie"], yolo=True, enrolled=False, revision=1, policy_identity="test"),
      "aquarium-zentrum": ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=roots["aquarium-zentrum"], yolo=True, enrolled=True, revision=1, policy_identity="test"),
    }
    sup=ManagedSpaceSupervisor(ledger_path=tmp_path/"state.sqlite", governance_resolver=gov.get)
    dispatched=[]; rt=NovaSpaceSupervisionRuntime(supervisor=sup, dispatch_run=lambda root,run: dispatched.append((root,run)), governance_snapshots=lambda:gov)
    assert not rt.ingest_signal("nova",source="git",event_id="n",reason_code="git_change")
    assert not rt.ingest_signal("finanzjunkie",source="git",event_id="f",reason_code="git_change")
    assert rt.ingest_signal("aquarium-zentrum",source="git",event_id="a",reason_code="git_change")
    assert not rt.ingest_signal("aquarium-zentrum",source="git",event_id="a",reason_code="git_change")
    out=rt.pulse(now_epoch=100)
    assert len(dispatched)==1 and dispatched[0][0]==roots["aquarium-zentrum"]
    assert any(x.status=="started" for x in out)
