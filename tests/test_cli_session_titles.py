from __future__ import annotations

import sqlite3

from runtime._compat.shim_state import SessionDB


def test_session_db_list_sessions_derives_missing_titles_from_first_user_message(tmp_path):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT,
            source TEXT,
            model TEXT,
            started_at REAL
        );

        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO sessions (id, title, source, model, started_at)
        VALUES (?, NULL, 'cli', 'deepseek-v4-flash', 1000)
        """,
        ("sess-1",),
    )
    conn.execute(
        """
        INSERT INTO messages (session_id, role, content, timestamp)
        VALUES (?, 'user', ?, 1001)
        """,
        ("sess-1", "Fix the one line installer and verify the desktop launcher."),
    )
    conn.commit()
    conn.close()

    db = SessionDB(db_path=db_path)
    try:
        sessions = db.list_sessions(limit=10, offset=0)
    finally:
        db.close()

    assert sessions[0]["title"] == "Fix the one line installer and verify the desktop launcher."
