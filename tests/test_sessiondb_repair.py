from __future__ import annotations

import logging
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_agent
from runtime.transports import get_transport
from runtime._compat.shim_state import SessionDB
from tools import tirith_security


def test_append_message_creates_missing_parent_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    db = SessionDB(tmp_path / "state.db")

    db.append_message(
        session_id="space-session-1",
        role="user",
        content="hello",
        model="test-model",
    )

    assert db.get_session("space-session-1") is not None
    messages = db.get_messages("space-session-1")
    assert len(messages) == 1
    assert messages[0]["content"] == "hello"


def test_create_session_ignores_missing_legacy_parent_fk(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    db_path = tmp_path / "legacy-state.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT,
            source TEXT NOT NULL,
            started_at REAL NOT NULL,
            parent_session_id TEXT REFERENCES sessions(id)
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            created_at REAL
        );
        """
    )
    conn.close()

    db = SessionDB(db_path)
    db.create_session("child", title="Child", source="webui", parent_session_id="missing-parent")
    db.append_message("child", "user", "hello")

    session = db.get_session("child")
    assert session is not None
    assert session.get("parent_session_id") is None
    assert db.get_messages("child")[0]["content"] == "hello"


def test_sessiondb_repairs_existing_missing_parent_refs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    db_path = tmp_path / "legacy-state.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT,
            source TEXT NOT NULL,
            started_at REAL NOT NULL,
            parent_session_id TEXT REFERENCES sessions(id)
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            role TEXT NOT NULL,
            content TEXT,
            timestamp REAL NOT NULL
        );
        INSERT INTO sessions (id, title, source, started_at, parent_session_id)
        VALUES ('child', 'Child', 'webui', 1.0, 'missing-parent');
        PRAGMA foreign_keys = ON;
        """
    )
    conn.close()

    db = SessionDB(db_path)

    session = db.get_session("child")
    assert session is not None
    assert session.get("parent_session_id") is None
    assert list(db._conn.execute("PRAGMA foreign_key_check")) == []


def test_create_session_survives_unique_default_title(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT,
            started_at REAL NOT NULL,
            parent_session_id TEXT REFERENCES sessions(id)
        );
        CREATE UNIQUE INDEX idx_sessions_title_unique
            ON sessions(title) WHERE title IS NOT NULL;
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            role TEXT NOT NULL,
            content TEXT,
            timestamp REAL NOT NULL
        );
        """
    )
    conn.close()

    db = SessionDB(db_path)
    db.create_session("existing", title="Untitled", source="unknown")
    db.create_session("cron-session", title="Untitled", source="cron")
    db.append_message("cron-session", "user", "hello")

    session = db.get_session("cron-session")
    assert session is not None
    assert db.get_messages("cron-session")[0]["content"] == "hello"


def test_agent_flush_skips_append_when_session_create_fails(caplog) -> None:
    class FailingSessionDB:
        def __init__(self) -> None:
            self.append_calls = 0

        def create_session(self, **_kwargs):
            raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

        def append_message(self, **_kwargs):
            self.append_calls += 1

    db = FailingSessionDB()
    agent = run_agent.AIAgent.__new__(run_agent.AIAgent)
    agent._session_db = db
    agent._session_db_created = False
    agent._parent_session_id = "missing-parent"
    agent._last_flushed_db_idx = 0
    agent._session_init_model_config = {}
    agent._cached_system_prompt = ""
    agent.session_id = "child-session"
    agent.platform = "webui"
    agent.model = "test-model"

    with caplog.at_level(logging.WARNING):
        agent._flush_messages_to_session_db([{"role": "user", "content": "hello"}])

    assert db.append_calls == 0
    assert any("Session DB creation failed" in record.message for record in caplog.records)


def test_agent_does_not_mark_session_created_when_row_is_missing(caplog) -> None:
    class SilentNoopSessionDB:
        def create_session(self, **_kwargs):
            return {"session_id": "child-session"}

        def get_session(self, _session_id):
            return None

    agent = run_agent.AIAgent.__new__(run_agent.AIAgent)
    agent._session_db = SilentNoopSessionDB()
    agent._session_db_created = False
    agent._parent_session_id = None
    agent._last_flushed_db_idx = 0
    agent._session_init_model_config = {}
    agent._cached_system_prompt = ""
    agent.session_id = "child-session"
    agent.platform = "webui"
    agent.model = "test-model"

    with caplog.at_level(logging.WARNING):
        agent._ensure_db_session()

    assert agent._session_db_created is False
    assert any("Session DB creation failed" in record.message for record in caplog.records)


def test_agent_flush_recovers_when_append_hits_missing_session_fk() -> None:
    class RecoveringSessionDB:
        def __init__(self) -> None:
            self.create_calls = 0
            self.append_calls = 0

        def create_session(self, **_kwargs):
            self.create_calls += 1

        def append_message(self, **_kwargs):
            self.append_calls += 1
            if self.append_calls == 1:
                raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    db = RecoveringSessionDB()
    agent = run_agent.AIAgent.__new__(run_agent.AIAgent)
    agent._session_db = db
    agent._session_db_created = True
    agent._parent_session_id = None
    agent._last_flushed_db_idx = 0
    agent._session_init_model_config = {}
    agent._cached_system_prompt = ""
    agent.session_id = "child-session"
    agent.platform = "webui"
    agent.model = "test-model"

    agent._flush_messages_to_session_db([{"role": "user", "content": "hello"}])

    assert db.create_calls == 1
    assert db.append_calls == 2
    assert agent._session_db_created is True
    assert agent._last_flushed_db_idx == 1


def test_non_chat_transports_are_registered() -> None:
    assert get_transport("anthropic_messages") is not None
    assert get_transport("bedrock_converse") is not None
    assert get_transport("codex_responses") is not None


def test_anthropic_transport_normalizes_response() -> None:
    transport = get_transport("anthropic_messages")
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="hidden chain"),
            SimpleNamespace(type="text", text="done"),
            SimpleNamespace(type="tool_use", id="toolu_1", name="mcp_read_file", input={"path": "x"}),
        ],
        stop_reason="tool_use",
    )

    message = transport.normalize_response(response, strip_tool_prefix=True)

    assert message.content == "done"
    assert message.reasoning_content == "hidden chain"
    assert message.finish_reason == "tool_calls"
    assert message.tool_calls[0].function.name == "read_file"


def test_tirith_spawn_failure_warns_once(monkeypatch, caplog) -> None:
    tirith_security._WARNED_OPERATIONAL_FAILURES.clear()
    monkeypatch.setattr(
        tirith_security,
        "_load_security_config",
        lambda: {
            "tirith_enabled": True,
            "tirith_path": "tirith",
            "tirith_timeout": 5,
            "tirith_fail_open": True,
        },
    )
    monkeypatch.setattr(tirith_security, "_resolve_tirith_path", lambda _path: "missing-tirith")

    def _raise(*_args, **_kwargs):
        raise FileNotFoundError("missing-tirith")

    monkeypatch.setattr(subprocess, "run", _raise)

    with caplog.at_level(logging.WARNING):
        first = tirith_security.check_command_security("echo first")
        second = tirith_security.check_command_security("echo second")

    assert first["action"] == "allow"
    assert second["action"] == "allow"
    warnings = [record for record in caplog.records if "tirith spawn failed" in record.message]
    assert len(warnings) == 1
