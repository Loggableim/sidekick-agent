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
    assert sessions[0]["session_id"] == "sess-1"


def test_session_db_get_session_normalizes_legacy_id_schema(tmp_path):
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
        VALUES (?, 'Legacy title', 'cli', 'deepseek-v4-flash', 1000)
        """,
        ("legacy-1",),
    )
    conn.commit()
    conn.close()

    db = SessionDB(db_path=db_path)
    try:
        session = db.get_session("legacy-1")
        db.update_title("legacy-1", "Updated legacy title")
        updated = db.get_session("legacy-1")
    finally:
        db.close()

    assert session is not None
    assert session["id"] == "legacy-1"
    assert session["session_id"] == "legacy-1"
    assert updated is not None
    assert updated["title"] == "Updated legacy title"


def test_session_db_treats_no_title_as_missing(tmp_path):
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
        VALUES (?, 'No title', 'cli', 'deepseek-v4-flash', 1000)
        """,
        ("sess-1",),
    )
    conn.execute(
        """
        INSERT INTO messages (session_id, role, content, timestamp)
        VALUES (?, 'user', ?, 1001)
        """,
        ("sess-1", "Analyze why Sidekick shows untitled sessions."),
    )
    conn.commit()
    conn.close()

    db = SessionDB(db_path=db_path)
    try:
        sessions = db.list_sessions(limit=10, offset=0)
    finally:
        db.close()

    assert sessions[0]["title"] == "Analyze why Sidekick shows untitled sessions."


def test_session_db_generates_fallback_for_empty_legacy_session(tmp_path):
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
            content TEXT NOT NULL,
            timestamp REAL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO sessions (id, title, source, model, started_at, message_count)
        VALUES (?, '', 'cron', 'deepseek-v4-flash', 1000, 0)
        """,
        ("cron-1",),
    )
    conn.commit()
    conn.close()

    db = SessionDB(db_path=db_path)
    try:
        sessions = db.list_sessions(limit=10, offset=0)
    finally:
        db.close()

    assert sessions[0]["title"] == "Cron session"


def test_session_db_ignores_internal_important_prompt_for_cron_title(tmp_path):
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
        VALUES (?, NULL, 'cron', 'deepseek-v4-flash', 1000)
        """,
        ("cron-important",),
    )
    conn.execute(
        """
        INSERT INTO messages (session_id, role, content, timestamp)
        VALUES (?, 'user', ?, 1001)
        """,
        (
            "cron-important",
            "[IMPORTANT: You are running as a scheduled cron job. DELIVERY: YES]",
        ),
    )
    conn.commit()
    conn.close()

    db = SessionDB(db_path=db_path)
    try:
        sessions = db.list_sessions(limit=10, offset=0)
    finally:
        db.close()

    assert sessions[0]["title"] == "Cron session"
