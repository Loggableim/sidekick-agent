"""Preserve the useful local schema repair without removing session APIs."""

import sqlite3

import pytest

from runtime._compat.shim_state import SessionDB


@pytest.mark.parametrize("primary_key", ["session_id", "id"])
def test_legacy_store_keeps_history_and_persists_completion(tmp_path, primary_key):
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute(f"CREATE TABLE sessions ({primary_key} TEXT PRIMARY KEY, title TEXT, source TEXT, model TEXT, started_at REAL)")
        conn.execute(f"INSERT INTO sessions ({primary_key}, title, source, started_at) VALUES ('old', 'Existing chat', 'cli', 123)")
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL)")
        conn.execute("INSERT INTO messages VALUES (1, 'old', 'user', 'migration regression marker', 124)")
    db = SessionDB(path)
    try:
        db.end_session("old", status="completed")
        row = db.get_session("old")
        assert row["status"] == "completed"
        assert row["ended_at"] >= 124
        assert row["started_at"] == 123
        assert row["input_tokens"] == 0
        assert db.list_sessions()[0]["title"] == "Existing chat"
        assert db.get_messages("old")[0]["content"] == "migration regression marker"
        assert db.search_messages("regression")[0]["session_id"] == "old"
    finally:
        db.close()
    reopened = SessionDB(path)
    try:
        assert reopened.get_session("old")["status"] == "completed"
        assert len(reopened.get_messages("old")) == 1
    finally:
        reopened.close()


def test_fresh_store_retains_context_manager_and_completion(tmp_path):
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("new")
        db.end_session("new")
        assert db.get_session("new")["status"] == "ended"
        assert db.get_session("new")["ended_at"] is not None
