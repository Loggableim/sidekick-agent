from pathlib import Path
from uuid import uuid4
from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor

def test_three_space_heartbeat_throttle_and_revoke_fail_closed(tmp_path: Path):
    roots={s:tmp_path/s for s in ("nova","finanzjunkie","aquarium-zentrum")}
    for r in roots.values(): r.mkdir()
    gov={s:ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=r, yolo=s=="aquarium-zentrum", enrolled=s=="aquarium-zentrum", revision=1, policy_identity="tick") for s,r in roots.items()}
    sup=ManagedSpaceSupervisor(ledger_path=tmp_path/"state.sqlite", governance_resolver=gov.get)
    dispatched=[]; rt=NovaSpaceSupervisionRuntime(supervisor=sup,dispatch_run=lambda r,i:dispatched.append((r,i)),governance_snapshots=lambda:gov)
    assert rt.ingest_signal("aquarium-zentrum",source="heartbeat",event_id="h1",reason_code="periodic_check")
    assert len(rt.pulse(now_epoch=0))==1
    assert rt.pulse(now_epoch=60)==()
    assert rt.pulse(now_epoch=899)==()
    gov["aquarium-zentrum"] = ManagedSpaceGovernance.from_values(space_id=gov["aquarium-zentrum"].space_id, canonical_root=roots["aquarium-zentrum"], yolo=False, enrolled=False, revision=2, policy_identity="tick")
    assert rt.pulse(now_epoch=900)==()
    assert len(dispatched)==1
