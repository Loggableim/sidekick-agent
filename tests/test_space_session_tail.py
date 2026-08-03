from __future__ import annotations

import json
from pathlib import Path

from cli.web_server import _load_space_session_metadata, _load_space_session_tail
from web.api.models import _write_message_tail_index


def test_space_session_tail_scanner_keeps_metadata_and_recent_messages(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    messages = [
        {"role": "user", "content": f"message {idx}", "timestamp": idx}
        for idx in range(40)
    ]
    path.write_text(
        json.dumps(
            {
                "session_id": "tail-test",
                "title": "Large transcript",
                "workspace": "C:\\workspace",
                "message_count": len(messages),
                "active_stream_id": None,
                "messages": messages,
            }
        ),
        encoding="utf-8",
    )

    session = _load_space_session_tail(path, limit=5)

    assert session is not None
    assert session["title"] == "Large transcript"
    assert [item["content"] for item in session["messages"]] == [
        "message 35",
        "message 36",
        "message 37",
        "message 38",
        "message 39",
    ]
    assert session["_tail_messages_truncated"] is True
    assert session["_tail_messages_offset"] == 35


def test_space_session_tail_scanner_returns_none_for_non_messages_tail(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(
        json.dumps({"session_id": "not-tail", "messages": [], "after": {"x": 1}}),
        encoding="utf-8",
    )

    assert _load_space_session_tail(path, limit=5) is None


def test_space_session_tail_uses_validated_persistent_index(tmp_path: Path) -> None:
    path = tmp_path / "indexed.json"
    messages = [{"role": "user", "content": f"message {idx}"} for idx in range(40)]
    payload = json.dumps({"session_id": "indexed", "title": "Indexed", "message_count": 40, "messages": messages})
    path.write_text(payload, encoding="utf-8")
    _write_message_tail_index(path, payload, messages)

    session = _load_space_session_tail(path, limit=5)

    assert session is not None
    assert [item["content"] for item in session["messages"]] == [
        "message 35", "message 36", "message 37", "message 38", "message 39"
    ]
    assert session["_tail_messages_offset"] == 35


def test_space_session_tail_ignores_stale_persistent_index(tmp_path: Path) -> None:
    path = tmp_path / "stale.json"
    messages = [{"role": "user", "content": f"message {idx}"} for idx in range(40)]
    payload = json.dumps({"session_id": "stale", "message_count": 40, "messages": messages})
    path.write_text(payload, encoding="utf-8")
    _write_message_tail_index(path, payload, messages)
    path.write_text(json.dumps({"session_id": "stale", "message_count": 41, "messages": messages + [{"role": "assistant", "content": "new"}]}), encoding="utf-8")

    session = _load_space_session_tail(path, limit=1)

    assert session is not None
    assert session["messages"][0]["content"] == "new"


def test_space_session_metadata_does_not_decode_transcript(tmp_path: Path) -> None:
    path = tmp_path / "metadata-only.json"
    messages = [{"role": "user", "content": "x" * 1000} for _ in range(5000)]
    path.write_text(
        json.dumps(
            {
                "session_id": "metadata-only",
                "title": "Fast boot",
                "message_count": len(messages),
                "model": "deepseek-v4-flash",
                "messages": messages,
            }
        ),
        encoding="utf-8",
    )

    session = _load_space_session_metadata(path)

    assert session is not None
    assert session["session_id"] == "metadata-only"
    assert session["message_count"] == 5000
    assert session["messages"] == []
    assert session["_metadata_only"] is True
