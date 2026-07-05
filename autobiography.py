#!/usr/bin/env python3
"""Chronological autobiography store for Nova."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent.resolve()
DEFAULT_DB = HERE / "nova_data" / "entity_kernel" / "autobiography.db"


class AutobiographyStore:
    def __init__(self, db_path: Path = DEFAULT_DB):
        self.db_path = Path(db_path)
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
                INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["actors"] = json.loads(data.pop("actors_json"))
        data["emotion_snapshot"] = json.loads(data.pop("emotion_snapshot_json"))
        data["need_snapshot"] = json.loads(data.pop("need_snapshot_json"))
        data["memory_refs"] = json.loads(data.pop("memory_refs_json"))
        data["tags"] = json.loads(data.pop("tags_json"))
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
