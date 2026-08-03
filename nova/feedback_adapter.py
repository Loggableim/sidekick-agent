"""Local Nova feedback bridge with bounded ack/timeout and offline mode."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
import math
import sqlite3
import re
import time
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class FeedbackResult:
    status: str
    detail: str = ""



def responder_allowed(*, target_space_id: str, space_id: str, yolo: bool, enrolled: bool, run_id: str, correlation_id: str) -> bool:
    """Fail-closed gate for the host-injected responder; no cross-Space use."""
    return (
        isinstance(target_space_id, str)
        and isinstance(space_id, str)
        and target_space_id == space_id
        and yolo is True
        and enrolled is True
        and isinstance(run_id, str)
        and isinstance(correlation_id, str)
        and run_id == correlation_id
    )
class LocalFeedbackLedger:
    """Profile-local durable inbox/outbox with exactly-once correlation IDs."""
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS feedback (correlation_id TEXT PRIMARY KEY, message TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL, updated_at REAL NOT NULL)")

    def queue(self, message: str, *, correlation_id: str) -> dict[str, object]:
        if not isinstance(correlation_id, str) or not correlation_id.strip():
            raise ValueError("correlation_id required")
        bounded = re.sub(r"(?i)(secret|token|password|api[_-]?key)\\s*[:=]\\s*[^\\s]+", r"\\1=[redacted]", str(message))[:4000]
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR IGNORE INTO feedback VALUES (?, ?, 'queued', '', ?)", (correlation_id[:128], bounded, time.time()))
        return self.latest_status(correlation_id)

    def set_status(self, correlation_id: str, status: str, detail: str = "") -> bool:
        if status not in {"queued", "received", "failed", "offline"}:
            raise ValueError("invalid feedback status")
        with sqlite3.connect(self.path) as db:
            cur = db.execute("UPDATE feedback SET status=?, detail=?, updated_at=? WHERE correlation_id=?", (status, str(detail)[:200], time.time(), correlation_id))
        return cur.rowcount == 1

    def receive(self, correlation_id: str, response: str, *, target_space_id: str = "", run_id: str = "") -> dict[str, object] | None:
        """Accept one response only for a queued message, idempotently."""
        redacted = re.sub(r"(?i)(secret|token|password|api[_-]?key)\\s*[:=]\\s*[^\\s]+", r"\\1=[redacted]", str(response))[:400]
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT correlation_id, message, status, detail, updated_at FROM feedback WHERE correlation_id=?", (correlation_id,)).fetchone()
            if row is None or row[2] != "queued":
                return self.latest_status(correlation_id)
            if (target_space_id and target_space_id != correlation_id) or (run_id and run_id != correlation_id):
                return self.latest_status(correlation_id)
            db.execute("UPDATE feedback SET status='received', detail=?, updated_at=? WHERE correlation_id=? AND status='queued'", (redacted, time.time(), correlation_id))
        return self.latest_status(correlation_id)
    def queued_items(self, limit: int = 16) -> list[dict[str, object]]:
        """Return a bounded snapshot of queued messages for an injected consumer."""
        bounded = max(1, min(int(limit), 64))
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT correlation_id, message, status, detail, updated_at FROM feedback WHERE status='queued' ORDER BY updated_at ASC LIMIT ?", (bounded,)).fetchall()
        return [dict(zip(("correlation_id", "message", "status", "detail", "updated_at"), row)) for row in rows]
    def mark_received(self, correlation_id: str) -> bool:
        return self.set_status(correlation_id, "received")

    def latest_status(self, correlation_id: str) -> dict[str, object] | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT correlation_id, message, status, detail, updated_at FROM feedback WHERE correlation_id=?", (correlation_id,)).fetchone()
        if row is None:
            return None
        return dict(zip(("correlation_id", "message", "status", "detail", "updated_at"), row))


class LocalNovaFeedbackAdapter:
    def __init__(self, sender: Callable[[str], object] | None, ledger: LocalFeedbackLedger | None = None):
        self._sender = sender
        self._ledger = ledger

    def send(self, message: str, *, timeout: float = 5.0, correlation_id: str | None = None) -> FeedbackResult:
        if not isinstance(message, str) or not message.strip():
            return FeedbackResult("rejected", "empty feedback")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(float(timeout)) or timeout <= 0:
            return FeedbackResult("rejected", "invalid timeout")
        correlation_id = correlation_id or f"feedback-{time.time_ns()}"
        if self._ledger is not None:
            self._ledger.queue(message, correlation_id=correlation_id)
        if not self._sender:
            if self._ledger is not None: self._ledger.set_status(correlation_id, "offline", "feedback sender unavailable")
            return FeedbackResult("offline", "feedback sender unavailable")
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            ack = pool.submit(self._sender, message[:4000]).result(timeout=min(float(timeout), 30.0))
            if self._ledger is not None: self._ledger.receive(correlation_id, str(ack)[:400])
            return FeedbackResult("acked", str(ack)[:200])
        except TimeoutError:
            if self._ledger is not None: self._ledger.set_status(correlation_id, "failed", "feedback ack timeout")
            return FeedbackResult("timeout", "feedback ack timeout")
        except Exception:
            if self._ledger is not None: self._ledger.set_status(correlation_id, "failed", "feedback sender failed")
            return FeedbackResult("offline", "feedback sender failed")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)






