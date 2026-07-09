from __future__ import annotations

import sqlite3

from web.api import agent_sessions


def test_read_importable_agent_session_rows_reuses_unchanged_db_snapshot(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT,
            source TEXT,
            model TEXT,
            started_at REAL,
            message_count INTEGER
        );

        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            timestamp REAL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO sessions (id, title, source, model, started_at, message_count)
        VALUES ('sess-cache', 'Cacheable Title', 'cli', 'deepseek-v4-flash', 1000, 2)
        """
    )
    conn.executemany(
        """
        INSERT INTO messages (session_id, role, content, timestamp)
        VALUES ('sess-cache', ?, ?, ?)
        """,
        [
            ("user", "hello", 2000),
            ("assistant", "hi", 2005),
        ],
    )
    conn.commit()
    conn.close()

    connect_calls = {"count": 0}
    real_connect = agent_sessions.sqlite3.connect

    def counting_connect(*args, **kwargs):
        connect_calls["count"] += 1
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(agent_sessions.sqlite3, "connect", counting_connect)

    first = agent_sessions.read_importable_agent_session_rows(
        db_path,
        limit=20,
        exclude_sources=None,
    )
    assert connect_calls["count"] == 1
    assert len(first) == 1
    assert first[0]["id"] == "sess-cache"
    assert first[0]["title"] == "Cacheable Title"

    first[0]["title"] = "Mutated Title"

    second = agent_sessions.read_importable_agent_session_rows(
        db_path,
        limit=20,
        exclude_sources=None,
    )
    assert connect_calls["count"] == 1
    assert len(second) == 1
    assert second[0]["id"] == "sess-cache"
    assert second[0]["title"] == "Cacheable Title"
    assert first is not second
    assert first[0] is not second[0]
