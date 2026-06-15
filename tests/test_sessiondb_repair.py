from __future__ import annotations

import logging
import sqlite3
import subprocess
from types import SimpleNamespace

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
