from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from uuid import uuid4
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from nova.resonance_memory import TickerResonanceMemory

def _gov(root:Path, enrolled:bool=True):
    return ManagedSpaceGovernance.from_values(space_id=str(uuid4()),canonical_root=root,root_fingerprint="",yolo=True,enrolled=enrolled,revision=1,policy_identity="space-governance:1")

def test_consumer_is_incremental_redacted_and_idempotent(tmp_path:Path):
    records={"alpha":_gov(tmp_path/"alpha")}; ledger=tmp_path/"supervisor.sqlite"; ticker=tmp_path/"ticker_events.jsonl"
    ticker.write_text(json.dumps({"event_id":"a"*64,"space":"alpha","source":"git","stage":"handled","status":"failed","reason":"skipped_slot_occupied","at":1,"path":str(tmp_path),"secret":"token"})+"\n",encoding="utf-8")
    sup=ManagedSpaceSupervisor(ledger_path=ledger,governance_resolver=lambda k:records.get(k)); mem=TickerResonanceMemory(supervisor=sup,ticker_path=ticker,memory_path=tmp_path/"memory.sqlite")
    first=mem.consume(); second=mem.consume()
    assert (first.consumed,first.accepted)==(1,1); assert second.consumed==0
    rows=mem.events(); assert rows[0]["space"]=="alpha" and rows[0]["reason"]=="skipped_slot_occupied"
    assert str(tmp_path) not in json.dumps(rows) and "token" not in json.dumps(rows)

def test_consumer_tombstones_non_enrolled_event_and_does_not_replay(tmp_path:Path):
    records={"alpha":_gov(tmp_path/"alpha",enrolled=False)}; ticker=tmp_path/"ticker_events.jsonl"
    ticker.write_text(json.dumps({"event_id":"b"*64,"space":"alpha","source":"ci","stage":"observed","status":"pending","reason":"ci_change","at":2})+"\n",encoding="utf-8")
    sup=ManagedSpaceSupervisor(ledger_path=tmp_path/"supervisor.sqlite",governance_resolver=lambda k:records.get(k)); mem=TickerResonanceMemory(supervisor=sup,ticker_path=ticker,memory_path=tmp_path/"memory.sqlite")
    assert mem.consume().skipped==1
    records["alpha"]=_gov(tmp_path/"alpha",enrolled=True)
    assert mem.consume().consumed==0 and mem.events()==[]
def test_consumer_fails_closed_when_seen_id_capacity_is_full(tmp_path:Path, monkeypatch):
    import nova.resonance_memory as resonance_memory
    monkeypatch.setattr(resonance_memory, "_MAX_SEEN", 2)
    records={"alpha":_gov(tmp_path/"alpha")}; ticker=tmp_path/"ticker_events.jsonl"
    with ticker.open("w",encoding="utf-8") as f:
        for i in range(3):
            f.write(json.dumps({"event_id":format(i+1,"064x"),"space":"alpha","source":"heartbeat","stage":"handled","status":"handled","reason":"periodic_check","at":i})+"\n")
    sup=ManagedSpaceSupervisor(ledger_path=tmp_path/"supervisor.sqlite",governance_resolver=lambda k:records.get(k))
    mem=TickerResonanceMemory(supervisor=sup,ticker_path=ticker,memory_path=tmp_path/"memory.sqlite")
    result=mem.consume(max_events=3)
    assert (result.consumed,result.accepted,result.skipped)==(3,2,1)
    assert len(mem.events())==2

def test_consumer_bounds_batch_and_advances_only_consumed_lines(tmp_path:Path):
    records={"alpha":_gov(tmp_path/"alpha")}; ticker=tmp_path/"ticker_events.jsonl"
    with ticker.open("w",encoding="utf-8") as f:
        for i in range(3): f.write(json.dumps({"event_id":format(i+1,"064x"),"space":"alpha","source":"heartbeat","stage":"handled","status":"handled","reason":"periodic_check","at":i})+"\n")
    sup=ManagedSpaceSupervisor(ledger_path=tmp_path/"supervisor.sqlite",governance_resolver=lambda k:records.get(k)); mem=TickerResonanceMemory(supervisor=sup,ticker_path=ticker,memory_path=tmp_path/"memory.sqlite")
    assert mem.consume(max_events=2).consumed==2; assert len(mem.events())==2
    assert mem.consume(max_events=2).consumed==1; assert len(mem.events())==3
def test_entity_delivery_is_redacted_and_exactly_once(tmp_path:Path):
    records={"alpha":_gov(tmp_path/"alpha")}; ticker=tmp_path/"ticker_events.jsonl"
    ticker.write_text(json.dumps({"event_id":"c"*64,"space":"alpha","source":"git","stage":"handled","status":"failed","reason":"ci_failed","at":3})+"\n",encoding="utf-8")
    sup=ManagedSpaceSupervisor(ledger_path=tmp_path/"supervisor.sqlite",governance_resolver=lambda k:records.get(k)); mem=TickerResonanceMemory(supervisor=sup,ticker_path=ticker,memory_path=tmp_path/"memory.sqlite")
    mem.consume()
    delivered=[]
    assert mem.publish_pending(lambda event: delivered.append(event) or True)==1
    assert mem.publish_pending(lambda event: delivered.append(event) or True)==0
    assert delivered[0]["space"]=="alpha" and "secret" not in json.dumps(delivered[0])
def test_entity_delivery_waits_for_terminal_outcome(tmp_path: Path) -> None:
    records = {"alpha": _gov(tmp_path / "alpha")}
    ticker = tmp_path / "ticker_events.jsonl"
    ticker.write_text(
        json.dumps({
            "event_id": "d" * 64,
            "space": "alpha",
            "source": "git",
            "stage": "observed",
            "status": "pending",
            "reason": "git_change",
            "at": 4,
        }) + "\n",
        encoding="utf-8",
    )
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda key: records.get(key),
    )
    memory = TickerResonanceMemory(
        supervisor=supervisor,
        ticker_path=ticker,
        memory_path=tmp_path / "memory.sqlite",
    )
    memory.consume()
    delivered: list[dict] = []
    assert memory.publish_pending(lambda event: delivered.append(event) or True) == 0
    with sqlite3.connect(tmp_path / "memory.sqlite") as connection:
        connection.execute(
            "UPDATE resonance_events SET stage='handled', status='handled', reason='started' WHERE event_id=?",
            ("d" * 64,),
        )
        connection.commit()
    assert memory.publish_pending(lambda event: delivered.append(event) or True) == 1
    assert memory.publish_pending(lambda event: delivered.append(event) or True) == 0
    assert delivered[0]["stage"] == "handled"
    assert delivered[0]["status"] == "handled"

def test_terminal_event_is_not_delivered_after_governance_revocation(tmp_path: Path):
    records = {"alpha": _gov(tmp_path / "alpha")}
    ticker = tmp_path / "ticker_events.jsonl"
    eid = "e" * 64
    ticker.write_text(json.dumps({"event_id": eid, "space": "alpha", "source": "git", "stage": "observed", "status": "pending", "reason": "git_change", "at": 1}) + "\n", encoding="utf-8")
    supervisor = ManagedSpaceSupervisor(ledger_path=tmp_path / "supervisor.sqlite", governance_resolver=lambda key: records.get(key))
    memory = TickerResonanceMemory(supervisor=supervisor, ticker_path=ticker, memory_path=tmp_path / "memory.sqlite")
    assert memory.consume().accepted == 1
    records["alpha"] = _gov(tmp_path / "alpha", enrolled=False)
    with ticker.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event_id": eid, "space": "alpha", "source": "git", "stage": "handled", "status": "failed", "reason": "ci_failed", "at": 2}) + "\n")
    assert memory.consume().skipped == 1
    assert memory.events()[0]["stage"] == "observed"
    assert memory.publish_pending(lambda _: True) == 0
def test_entity_delivery_rechecks_yolo_enrollment_before_publishing(tmp_path: Path):
    records = {"alpha": _gov(tmp_path / "alpha", enrolled=True)}
    ticker = tmp_path / "ticker_events.jsonl"
    eid = "f" * 64
    ticker.write_text(json.dumps({
        "event_id": eid, "space": "alpha", "source": "ci",
        "stage": "handled", "status": "failed", "reason": "ci_failed",
        "at": 5, "path": "C:/secret", "token": "secret",
    }) + chr(10), encoding="utf-8")
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda key: records.get(key),
    )
    memory = TickerResonanceMemory(
        supervisor=supervisor,
        ticker_path=ticker,
        memory_path=tmp_path / "memory.sqlite",
    )
    assert memory.consume().accepted == 1
    records["alpha"] = _gov(tmp_path / "alpha", enrolled=False)
    delivered = []
    assert memory.publish_pending(lambda event: delivered.append(event) or True) == 0
    assert delivered == []


def test_consumer_waits_for_a_complete_final_jsonl_record(tmp_path: Path):
    records = {"alpha": _gov(tmp_path / "alpha")}
    ticker = tmp_path / "ticker_events.jsonl"
    event = json.dumps({
        "event_id": "9" * 64, "space": "alpha", "source": "ci",
        "stage": "handled", "status": "failed", "reason": "ci_failed", "at": 9,
    })
    split = len(event) // 2
    ticker.write_text(event[:split], encoding="utf-8")
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda key: records.get(key),
    )
    memory = TickerResonanceMemory(
        supervisor=supervisor, ticker_path=ticker, memory_path=tmp_path / "memory.sqlite"
    )
    first = memory.consume()
    assert (first.consumed, first.accepted, first.skipped, first.offset) == (0, 0, 0, 0)
    with ticker.open("a", encoding="utf-8") as handle:
        handle.write(event[split:] + chr(10))
    assert memory.consume().accepted == 1
    assert [item["event_id"] for item in memory.events()] == ["9" * 64]
