"""Bounded durable consumer for redacted Nova ticker resonance memory."""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any
from nova.space_supervisor import ManagedSpaceSupervisor
from nova.space_supervision_runtime import ticker_event_log_path, _TICKER_EVENT_LOCK

_MAX_EVENTS=64
_MAX_SEEN=2048
_MAX_LINE=512
_SPACE_RE=re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_EVENT_RE=re.compile(r"[0-9a-f]{16,128}\Z")
_REASON_RE=re.compile(r"[a-z0-9_:-]{1,64}\Z")
_SOURCES=frozenset({"git","kanban","ci","heartbeat","bridge"})
_STAGES=frozenset({"observed","handled"})
_STATUSES=frozenset({"pending","handled","failed"})

@dataclass(frozen=True, slots=True)
class ResonanceMemoryResult:
    consumed:int
    accepted:int
    skipped:int
    offset:int

class TickerResonanceMemory:
    """Read only new ticker lines, revalidate enrollment, and dedupe IDs."""
    def __init__(self, *, supervisor:ManagedSpaceSupervisor, ticker_path:Path|None=None, memory_path:Path|None=None):
        if not isinstance(supervisor, ManagedSpaceSupervisor): raise TypeError("resonance memory supervisor is invalid")
        self.supervisor=supervisor
        self.ticker_path=Path(ticker_path) if ticker_path is not None else ticker_event_log_path(supervisor)
        self.memory_path=Path(memory_path) if memory_path is not None else self.ticker_path.with_name("resonance_memory.sqlite")

    def consume(self, *, max_events:int=_MAX_EVENTS)->ResonanceMemoryResult:
        try: limit=max(1,min(int(max_events),_MAX_EVENTS))
        except (TypeError,ValueError): limit=_MAX_EVENTS
        try:
            with _TICKER_EVENT_LOCK:
                stat=self.ticker_path.stat()
                inode = int(getattr(stat, "st_ino", 0) or 0)
                generation = inode if inode else int(getattr(stat, "st_ctime_ns", 0) or 0)
                key=f"{self.ticker_path.resolve()}:{inode}:{generation}"
                self.memory_path.parent.mkdir(parents=True,exist_ok=True)
                db=sqlite3.connect(self.memory_path,timeout=5); db.row_factory=sqlite3.Row
        except OSError:
            return ResonanceMemoryResult(0,0,0,self._offset())
        try:
            _schema(db)
            old=db.execute("SELECT path_key,offset FROM resonance_cursor WHERE id=1").fetchone()
            offset=int(old["offset"] or 0) if old else 0
            if old is not None and (old["path_key"]!=key or offset<0 or offset>int(stat.st_size)): offset=0
            try:
                with _TICKER_EVENT_LOCK:
                    handle=self.ticker_path.open("rb")
                    rows=[]
                    with handle:
                        handle.seek(offset)
                        for _ in range(limit):
                            raw=handle.readline(_MAX_LINE+1)
                            if not raw: break
                            if not raw.endswith(b"\n"):
                                break
                            end=handle.tell()
                            if len(raw)>_MAX_LINE and not raw.endswith(b"\n"):
                                rows.append((end,None)); continue
                            rows.append((end,_parse(raw)))
            except OSError:
                return ResonanceMemoryResult(0,0,0,offset)
            if not rows: return ResonanceMemoryResult(0,0,0,offset)
            db.execute("BEGIN IMMEDIATE"); accepted=skipped=0
            for end,item in rows:
                if item is None or not _valid(item): skipped+=1; continue
                eid=item["event_id"]
                try:
                    g=self.supervisor.current_governance(item["space"])
                    enrolled=bool(g is not None and g.yolo is True and g.enrolled is True)
                except Exception:
                    enrolled=False
                if db.execute("SELECT 1 FROM resonance_seen WHERE event_id=?",(eid,)).fetchone() is not None:
                    if not enrolled:
                        skipped+=1
                        continue
                    # Terminal ticker records reuse the accepted identity.
                    # Keep exactly-once identity while advancing the redacted
                    # entity projection from observed/pending.
                    current=db.execute("SELECT stage,status FROM resonance_events WHERE event_id=?",(eid,)).fetchone()
                    if current is not None and current[0] == "observed" and current[1] == "pending" and item["stage"] == "handled" and item["status"] in {"handled","failed"}:
                        db.execute("UPDATE resonance_events SET stage=?,status=?,reason=?,observed_at=? WHERE event_id=?",(item["stage"],item["status"],item["reason"],item["at"],eid))
                    continue
                seen_count=db.execute("SELECT COUNT(*) FROM resonance_seen").fetchone()[0]
                if int(seen_count or 0)>=_MAX_SEEN:
                    skipped+=1; continue
                db.execute("INSERT INTO resonance_seen VALUES(?,?,?)",(eid,int(enrolled),time.time()))
                if not enrolled: skipped+=1; continue
                db.execute("INSERT OR IGNORE INTO resonance_events VALUES(?,?,?,?,?,?,?)",(eid,item["space"],item["source"],item["stage"],item["status"],item["reason"],item["at"]))
                accepted+=1
            end=rows[-1][0]
            db.execute("INSERT INTO resonance_cursor VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET path_key=excluded.path_key,offset=excluded.offset",(key,end)); db.commit()
            return ResonanceMemoryResult(len(rows),accepted,skipped,int(end))
        except Exception:
            db.rollback(); raise
        finally: db.close()

    def _offset(self)->int:
        try:
            with sqlite3.connect(self.memory_path) as db:
                row=db.execute("SELECT offset FROM resonance_cursor WHERE id=1").fetchone(); return int(row[0]) if row else 0
        except (OSError,sqlite3.Error,TypeError,ValueError): return 0

    def events(self, *, limit:int=50)->list[dict[str,Any]]:
        try: limit=max(1,min(int(limit),200))
        except (TypeError,ValueError): limit=50
        try:
            with sqlite3.connect(self.memory_path) as db:
                db.row_factory=sqlite3.Row
                return [dict(r) for r in db.execute("SELECT event_id,space,source,stage,status,reason,observed_at FROM resonance_events ORDER BY observed_at DESC,event_id DESC LIMIT ?",(limit,)).fetchall()]
        except (OSError,sqlite3.Error): return []

    def publish_pending(self, entity_sink, *, max_events:int=32)->int:
        """Deliver terminal redacted signals to Nova's entity exactly once."""
        try: limit=max(1,min(int(max_events),32))
        except (TypeError,ValueError): limit=32
        delivered=0
        try:
            with sqlite3.connect(self.memory_path) as db:
                db.row_factory=sqlite3.Row
                rows=db.execute("""SELECT e.event_id,e.space,e.source,e.stage,e.status,e.reason,e.observed_at
                    FROM resonance_events e LEFT JOIN resonance_entity_delivery d ON d.event_id=e.event_id
                    LEFT JOIN resonance_entity_tombstone t ON t.event_id=e.event_id
                    WHERE d.event_id IS NULL AND t.event_id IS NULL AND e.stage='handled' AND e.status IN ('handled','failed') ORDER BY e.observed_at ASC,e.event_id ASC LIMIT ?""",(limit,)).fetchall()
                for row in rows:
                    event=dict(row)
                    try:
                        governance = self.supervisor.current_governance(event["space"])
                    except Exception:
                        continue
                    if not (
                        governance is not None
                        and governance.yolo is True
                        and governance.enrolled is True
                    ):
                        db.execute("INSERT OR IGNORE INTO resonance_entity_tombstone(event_id,tombstoned_at) VALUES(?,?)",(event["event_id"],time.time()))
                        continue
                    try: ok=bool(entity_sink(event))
                    except Exception: ok=False
                    if ok:
                        db.execute("INSERT OR IGNORE INTO resonance_entity_delivery(event_id,delivered_at) VALUES(?,?)",(event["event_id"],time.time()))
                        delivered+=1
                db.commit()
        except (OSError,sqlite3.Error):
            return delivered
        return delivered

def consume_ticker_resonance_memory(supervisor:ManagedSpaceSupervisor, *, max_events:int=_MAX_EVENTS)->ResonanceMemoryResult:
    return TickerResonanceMemory(supervisor=supervisor).consume(max_events=max_events)

def _schema(db:sqlite3.Connection)->None:
    db.executescript("""PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS resonance_cursor(id INTEGER PRIMARY KEY CHECK(id=1),path_key TEXT NOT NULL,offset INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS resonance_seen(event_id TEXT PRIMARY KEY,accepted INTEGER NOT NULL,seen_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS resonance_events(event_id TEXT PRIMARY KEY,space TEXT NOT NULL,source TEXT NOT NULL,stage TEXT NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL,observed_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS resonance_entity_delivery(event_id TEXT PRIMARY KEY,delivered_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS resonance_entity_tombstone(event_id TEXT PRIMARY KEY,tombstoned_at REAL NOT NULL);""")
    db.commit()

def _parse(raw:bytes)->dict[str,str]|None:
    try: value=json.loads(raw.decode("utf-8",errors="replace"))
    except (TypeError,ValueError,json.JSONDecodeError): return None
    if not isinstance(value,dict): return None
    out={}
    for k in ("event_id","space","source","stage","status","reason"):
        if not isinstance(value.get(k),str): return None
        out[k]=value[k].strip().lower()
    try: out["at"]=str(float(value.get("at")))
    except (TypeError,ValueError): return None
    return out

def _valid(v:dict[str,str])->bool:
    return (_EVENT_RE.fullmatch(v["event_id"]) is not None and _SPACE_RE.fullmatch(v["space"]) is not None and v["source"] in _SOURCES and v["stage"] in _STAGES and v["status"] in _STATUSES and _REASON_RE.fullmatch(v["reason"]) is not None and len(v["at"])<=32)

__all__=["ResonanceMemoryResult","TickerResonanceMemory","consume_ticker_resonance_memory"]