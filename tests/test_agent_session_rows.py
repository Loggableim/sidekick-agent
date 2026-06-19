from __future__ import annotations

import sqlite3

from web.api.agent_sessions import read_importable_agent_session_rows


def test_importable_agent_rows_project_message_stats(tmp_path):
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
            message_count INTEGER,
            parent_session_id TEXT,
            ended_at REAL,
            end_reason TEXT
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
        VALUES ('empty', 'Empty', 'cli', 'm', 1000, 0)
        """
    )
    conn.execute(
        """
        INSERT INTO sessions (id, title, source, model, started_at, message_count)
        VALUES ('visible', 'Visible', 'cli', 'm', 1001, 2)
        """
    )
    conn.executemany(
        """
        INSERT INTO messages (session_id, role, content, timestamp)
        VALUES ('visible', ?, ?, ?)
        """,
        [
            ("user", "hello", 2000),
            ("assistant", "hi", 2005),
        ],
    )
    conn.commit()
    conn.close()

    rows = read_importable_agent_session_rows(db_path, limit=10, exclude_sources=None)

    assert [row["id"] for row in rows] == ["visible"]
    assert rows[0]["actual_message_count"] == 2
    assert rows[0]["actual_user_message_count"] == 1
    assert rows[0]["last_activity"] == 2005
