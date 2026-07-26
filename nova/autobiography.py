#!/usr/bin/env python3
"""Chronological autobiography store for Nova."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nova.paths import get_nova_data_dir
from nova.entity_types import EntityEvent, Outcome

class AutobiographyStore:
    def __init__(self, db_path: Path | None = None):
        # Resolve the active Sidekick home at construction time. A default path
        # evaluated at import time leaks test or profile state across homes.
        self.db_path = Path(db_path) if db_path is not None else get_nova_data_dir() / "autobiography.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        conn = self._connect()
        try:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                why TEXT NOT NULL,
                actors_json TEXT NOT NULL,
                importance REAL NOT NULL,
                emotion_snapshot_json TEXT NOT NULL,
                need_snapshot_json TEXT NOT NULL,
                intent_id TEXT,
                memory_refs_json TEXT NOT NULL,
                tags_json TEXT NOT NULL
            )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")
            existing = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
            additions = {
                "source": "TEXT NOT NULL DEFAULT 'legacy'",
                "payload_json": "TEXT NOT NULL DEFAULT '{}'",
                "visibility": "TEXT NOT NULL DEFAULT 'private'",
                "correlation_id": "TEXT",
            }
            for column, definition in additions.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE events ADD COLUMN {column} {definition}")
            # Correlation groups a lifecycle, but one lifecycle may legitimately
            # contain several events of the same type (for example multiple
            # presence transitions in a single voice cycle). Event IDs provide
            # the exactly-once boundary.
            conn.execute("DROP INDEX IF EXISTS idx_events_correlation_type")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id)")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS outcomes (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                intent_id TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                status TEXT NOT NULL,
                effects_json TEXT NOT NULL,
                observation_due_at TEXT,
                reward REAL
            )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_intent ON outcomes(intent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_correlation ON outcomes(correlation_id)")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS self_revisions (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                path TEXT NOT NULL,
                before_json TEXT NOT NULL,
                after_json TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                confidence REAL NOT NULL,
                mode TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """)
            conn.commit()
        finally:
            conn.close()

    def record_event(self, event_type: str, title: str, summary: str, why: str, actors: list[str],
                     importance: float, emotion_snapshot: dict[str, Any], need_snapshot: dict[str, Any],
                     intent_id: str | None, memory_refs: list[str], tags: list[str]) -> str:
        event_id = f"bio-{int(time.time() * 1000000)}"
        timestamp = datetime.now().isoformat()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO events (
                    id, timestamp, type, title, summary, why, actors_json,
                    importance, emotion_snapshot_json, need_snapshot_json,
                    intent_id, memory_refs_json, tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, timestamp, event_type, title, summary, why,
                    json.dumps(actors, ensure_ascii=False),
                    max(0.0, min(1.0, float(importance))),
                    json.dumps(emotion_snapshot, ensure_ascii=False),
                    json.dumps(need_snapshot, ensure_ascii=False),
                    intent_id,
                    json.dumps(memory_refs, ensure_ascii=False),
                    json.dumps(tags, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return event_id

    def record_entity_event(self, event: EntityEvent | dict[str, Any]) -> str:
        data = event.to_dict() if isinstance(event, EntityEvent) else dict(event)
        event_id = str(data.get("event_id") or data.get("id") or f"event-{int(time.time() * 1000000)}")
        correlation_id = str(data.get("correlation_id") or event_id)
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    id, timestamp, type, title, summary, why, actors_json,
                    importance, emotion_snapshot_json, need_snapshot_json,
                    intent_id, memory_refs_json, tags_json, source,
                    payload_json, visibility, correlation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(data.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                    str(data.get("type") or "perception"),
                    str(data.get("title") or data.get("type") or "Nova event"),
                    str(data.get("summary") or ""),
                    str(data.get("why") or ""),
                    json.dumps(data.get("actors") or ["Nova"], ensure_ascii=False),
                    max(0.0, min(1.0, float(data.get("salience", data.get("importance", 0.5))))),
                    json.dumps(data.get("emotion_snapshot") or {}, ensure_ascii=False),
                    json.dumps(data.get("need_snapshot") or {}, ensure_ascii=False),
                    data.get("intent_id"),
                    json.dumps(data.get("memory_refs") or [], ensure_ascii=False),
                    json.dumps(data.get("tags") or [], ensure_ascii=False),
                    str(data.get("source") or "entity_runtime"),
                    json.dumps(data.get("payload") or {}, ensure_ascii=False),
                    str(data.get("visibility") or "private"),
                    correlation_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return event_id

    def has_event(self, event_id: str) -> bool:
        conn = self._connect()
        try:
            return conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone() is not None
        finally:
            conn.close()

    def record_outcome(self, outcome: Outcome | dict[str, Any]) -> str:
        data = outcome.to_dict() if isinstance(outcome, Outcome) else dict(outcome)
        outcome_id = str(data.get("outcome_id") or data.get("id") or f"outcome-{int(time.time() * 1000000)}")
        reward = data.get("reward")
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome_id,
                    str(data.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                    str(data.get("intent_id") or ""),
                    str(data.get("correlation_id") or ""),
                    str(data.get("status") or "unknown"),
                    json.dumps(data.get("effects") or {}, ensure_ascii=False),
                    data.get("observation_due_at"),
                    None if reward is None else max(0.0, min(1.0, float(reward))),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return outcome_id

    def outcome_for_correlation(self, correlation_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM outcomes WHERE correlation_id = ? ORDER BY timestamp DESC LIMIT 1",
                (correlation_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        data = dict(row)
        data["effects"] = json.loads(data.pop("effects_json"))
        return data

    def outcomes_without_reward(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM outcomes WHERE reward IS NULL ORDER BY timestamp ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()
        results = []
        for row in rows:
            data = dict(row)
            data["effects"] = json.loads(data.pop("effects_json"))
            results.append(data)
        return results

    def update_outcome_reward(self, outcome_id: str, reward: float) -> None:
        conn = self._connect()
        try:
            conn.execute("UPDATE outcomes SET reward = ? WHERE id = ?", (max(0.0, min(1.0, float(reward))), outcome_id))
            conn.commit()
        finally:
            conn.close()

    def event_for_correlation(self, correlation_id: str, event_type: str = "action") -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM events WHERE correlation_id = ? AND type = ? ORDER BY timestamp DESC LIMIT 1",
                (correlation_id, event_type),
            ).fetchone()
        finally:
            conn.close()
        return self._row(row) if row is not None else None

    def record_self_revision(self, revision: dict[str, Any], status: str) -> str:
        revision_id = str(revision.get("revision_id") or f"revision-{int(time.time() * 1000000)}")
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO self_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    revision_id,
                    str(revision.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                    str(revision.get("path") or ""),
                    json.dumps(revision.get("before"), ensure_ascii=False),
                    json.dumps(revision.get("after"), ensure_ascii=False),
                    json.dumps(revision.get("evidence_refs") or [], ensure_ascii=False),
                    max(0.0, min(1.0, float(revision.get("confidence", 0.0)))),
                    str(revision.get("mode") or "normal"),
                    str(revision.get("reason") or ""),
                    status,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return revision_id

    def import_legacy_db(self, legacy_path: Path) -> dict[str, Any]:
        legacy_path = Path(legacy_path)
        if not legacy_path.exists() or legacy_path.resolve() == self.db_path.resolve():
            return {"imported": 0, "source": str(legacy_path), "skipped": True}
        source = sqlite3.connect(legacy_path)
        source.row_factory = sqlite3.Row
        imported = 0
        try:
            tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "events" not in tables:
                return {"imported": 0, "source": str(legacy_path), "skipped": True}
            for row in source.execute("SELECT * FROM events ORDER BY timestamp ASC").fetchall():
                data = dict(row)
                if self.has_event(str(data.get("id") or "")):
                    continue
                event_id = self.record_entity_event({
                    "event_id": data.get("id"),
                    "timestamp": data.get("timestamp"),
                    "type": data.get("type"),
                    "source": "legacy_entity_kernel",
                    "title": data.get("title"),
                    "summary": data.get("summary"),
                    "why": data.get("why"),
                    "actors": json.loads(data.get("actors_json") or "[]"),
                    "salience": data.get("importance", 0.5),
                    "emotion_snapshot": json.loads(data.get("emotion_snapshot_json") or "{}"),
                    "need_snapshot": json.loads(data.get("need_snapshot_json") or "{}"),
                    "intent_id": data.get("intent_id"),
                    "memory_refs": json.loads(data.get("memory_refs_json") or "[]"),
                    "tags": json.loads(data.get("tags_json") or "[]"),
                    "payload": {"legacy_import": True},
                    "correlation_id": data.get("id"),
                })
                imported += int(bool(event_id))
        finally:
            source.close()
        return {"imported": imported, "source": str(legacy_path), "skipped": False}

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["actors"] = json.loads(data.pop("actors_json"))
        data["emotion_snapshot"] = json.loads(data.pop("emotion_snapshot_json"))
        data["need_snapshot"] = json.loads(data.pop("need_snapshot_json"))
        data["memory_refs"] = json.loads(data.pop("memory_refs_json"))
        data["tags"] = json.loads(data.pop("tags_json"))
        if "payload_json" in data:
            data["payload"] = json.loads(data.pop("payload_json"))
        return data

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM events ORDER BY timestamp DESC LIMIT ?", (int(limit),)).fetchall()
        finally:
            conn.close()
        return [self._row(row) for row in rows]

    def by_type(self, event_type: str, limit: int = 10) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM events WHERE type = ? ORDER BY timestamp DESC LIMIT ?",
                (event_type, int(limit)),
            ).fetchall()
        finally:
            conn.close()
        return [self._row(row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Nova autobiography timeline")
    parser.add_argument("command", choices=["recent"])
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    store = AutobiographyStore()
    print(json.dumps(store.recent(args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
