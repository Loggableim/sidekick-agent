"""Redacted persistent subagent history with bounded events and retention."""
from __future__ import annotations
import re, sqlite3, time
from pathlib import Path

_SECRET = re.compile(r"(?i)(token|secret|password|api[_-]?key)=\S+")
_PATH = re.compile(r"(?i)([A-Z]:\\|/home/|/Users/)[^\s,]+")

def _redact(value: object) -> str:
    text = str(value or "")
    text = _SECRET.sub(r"\1=<redacted>", text)
    text = _PATH.sub("<path>", text)
    return text[:500]

def _db(home: Path) -> Path:
    return Path(home) / "subagents.db"

def _schema(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (subagent_id TEXT PRIMARY KEY, session_id TEXT, space_slug TEXT, status TEXT, summary TEXT, updated_at REAL, started_at REAL, finished_at REAL)")
    db.execute("CREATE TABLE IF NOT EXISTS events (subagent_id TEXT NOT NULL, sequence INTEGER NOT NULL, session_id TEXT, event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(subagent_id, sequence), UNIQUE(subagent_id,event_type,payload))")
    db.execute("CREATE INDEX IF NOT EXISTS idx_runs_session_updated ON runs(session_id, updated_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_events_run_sequence ON events(subagent_id, sequence)")

def record(home: Path, *, subagent_id: str, session_id: str, space_slug: str, status: str, summary: str = "", event_type: str | None = None, event_payload: object = "") -> None:
    path = _db(home); path.parent.mkdir(parents=True, exist_ok=True); now=time.time(); sid=subagent_id[:128]
    with sqlite3.connect(path) as db:
        _schema(db)
        prior=db.execute("SELECT started_at FROM runs WHERE subagent_id=?",(sid,)).fetchone()
        started=prior[0] if prior else now
        finished=now if status[:40] in {"completed","failed","interrupted","abandoned","cancelled"} else None
        db.execute("INSERT INTO runs(subagent_id,session_id,space_slug,status,summary,updated_at,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(subagent_id) DO UPDATE SET session_id=excluded.session_id,space_slug=excluded.space_slug,status=excluded.status,summary=excluded.summary,updated_at=excluded.updated_at,finished_at=COALESCE(excluded.finished_at,runs.finished_at)", (sid,session_id[:128],space_slug[:128],status[:40],_redact(summary),now,started,finished))
        if event_type:
            payload=_redact(event_payload)
            seq=(db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE subagent_id=?",(sid,)).fetchone()[0])
            db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)",(sid,seq,session_id[:128],event_type[:64],payload,now))
        cutoff=now-90*86400
        db.execute("DELETE FROM runs WHERE updated_at<?",(cutoff,))
        db.execute("DELETE FROM events WHERE created_at<?",(cutoff,))
        old=db.execute("SELECT subagent_id FROM runs ORDER BY updated_at DESC LIMIT -1 OFFSET 1000").fetchall()
        if old: db.executemany("DELETE FROM runs WHERE subagent_id=?",old)
        db.execute("DELETE FROM events WHERE subagent_id NOT IN (SELECT subagent_id FROM runs)")

def list_history(home: Path, *, session_id: str, limit: int = 50) -> list[dict[str, object]]:
    if not session_id: return []
    path=_db(home)
    if not path.is_file(): return []
    with sqlite3.connect(path) as db:
        _schema(db)
        rows=db.execute("SELECT subagent_id,session_id,space_slug,status,summary,updated_at,started_at,finished_at FROM runs WHERE session_id=? ORDER BY updated_at DESC LIMIT ?",(session_id[:128],max(1,min(int(limit),100)))).fetchall()
        out=[]
        for r in rows:
            ev=db.execute("SELECT sequence,event_type,payload,created_at FROM events WHERE subagent_id=? ORDER BY sequence LIMIT 200",(r[0],)).fetchall()
            out.append({"subagent_id":r[0],"session_id":r[1],"space_slug":r[2],"status":r[3],"summary":r[4],"updated_at":r[5],"started_at":r[6],"finished_at":r[7],"events":[{"sequence":e[0],"event_type":e[1],"payload":e[2],"created_at":e[3]} for e in ev]})
        return out


def reconcile_stale(home: Path) -> int:
    path = _db(home)
    if not path.is_file():
        return 0
    changed = 0
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        rows = db.execute("SELECT subagent_id, session_id, space_slug FROM runs WHERE status IN ('running','waiting')").fetchall()
        for sid, session, space in rows:
            now = time.time()
            db.execute("UPDATE runs SET status='abandoned', summary='server_restart', updated_at=? WHERE subagent_id=?", (now, sid))
            seq = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE subagent_id=?", (sid,)).fetchone()[0]
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (sid, seq, session, 'abandoned', 'server_restart', now))
            changed += 1
    return changed
