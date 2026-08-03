import json
import sqlite3
from pathlib import Path

from web.api.nova_presence import _read_feedback_inbox


def test_feedback_presence_projects_only_attested_spaces_read_only(tmp_path: Path):
    root = tmp_path / "nova"
    db = root / "nova_data" / "entity" / "autobiography.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE events (timestamp TEXT, payload_json TEXT, correlation_id TEXT, type TEXT, source TEXT, visibility TEXT)")
        conn.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", ("2026-08-03T10:00:00Z", json.dumps({"target_key":"aquarium-zentrum","status":"received"}), "run-1", "nova_feedback", "local_feedback_adapter", "private"))
        conn.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", ("2026-08-03T10:01:00Z", json.dumps({"target_key":"finanzjunkie","status":"received"}), "run-2", "nova_feedback", "local_feedback_adapter", "private"))
        conn.commit()
    before = db.stat().st_mtime_ns
    payload = _read_feedback_inbox(root, [{"space":"aquarium-zentrum"}])
    assert payload["status"] == "received"
    assert [item["space"] for item in payload["items"]] == ["aquarium-zentrum"]
    assert db.stat().st_mtime_ns == before