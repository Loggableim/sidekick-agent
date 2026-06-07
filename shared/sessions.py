"""Canonical Session model — shared by CLI, TUI, and WebUI.

JSON-file-backed session storage.  Each session is one JSON file under
``~/.sidekick/state/webui/sessions/`` (shared path with legacy WebUI).

Fields
------
session_id : str
    Unique session identifier (hex).
title : str
    Human-readable title, auto-set from first user message.
workspace : str
    Active workspace path.
model : str
    Active model identifier.
messages : list[dict]
    Conversation history as OpenAI-format message dicts.
created_at / updated_at : float
    Unix timestamps.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from shared.config import get_default_workspace
from shared.runtime import web_state_dir

logger = logging.getLogger(__name__)


@dataclass
class Session:
    session_id: str
    title: str = "Untitled"
    workspace: str = ""
    model: str = "default"
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def compact(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "workspace": self.workspace,
            "model": self.model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
        }


def sessions_dir() -> Path:
    path = web_state_dir() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_path(session_id: str) -> Path:
    return sessions_dir() / f"{session_id}.json"


def save_session(session: Session) -> Path:
    path = _session_path(session.session_id)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f"{session.session_id}-",
        suffix=".json.tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(asdict(session), handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def load_session(session_id: str) -> Session | None:
    path = _session_path(session_id)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return Session(**data)


def delete_session(session_id: str) -> bool:
    path = _session_path(session_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def update_session(
    session_id: str,
    *,
    title: str | None = None,
    workspace: str | None = None,
    model: str | None = None,
) -> Session | None:
    session = load_session(session_id)
    if session is None:
        return None
    if title is not None:
        session.title = title
    if workspace is not None:
        session.workspace = workspace
    if model is not None:
        session.model = model
    session.updated_at = time.time()
    save_session(session)
    return session


def append_message(
    session_id: str,
    *,
    role: str,
    content: str,
) -> Session | None:
    session = load_session(session_id)
    if session is None:
        return None
    session.messages.append(
        {
            "role": role,
            "content": content,
            "timestamp": time.time(),
        }
    )
    if session.title == "Untitled" and role == "user" and content.strip():
        session.title = content.strip()[:60]
    session.updated_at = time.time()
    save_session(session)
    return session


def list_sessions() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(sessions_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            session = Session(**data)
            rows.append(session.compact())
        except Exception:
            continue
    return rows


def new_session(
    *,
    title: str | None = None,
    workspace: str | None = None,
    model: str | None = None,
) -> Session:
    now = time.time()
    session = Session(
        session_id=uuid.uuid4().hex[:12],
        title=title or "Untitled",
        workspace=workspace or get_default_workspace(),
        model=model or "default",
        created_at=now,
        updated_at=now,
    )
    save_session(session)
    return session


# ── Session manipulation helpers (retry, undo, status) ─────────────────────
# Shared across CLI, TUI, and WebUI surfaces.

def _extract_text(content: Any) -> str:
    """Extract plain text from mixed content (list of parts or plain string)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in content]
        return "".join(texts)
    return str(content or "")


def _truncate_at_last_user(history: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Return history up to (not including) the last user message."""
    if not history:
        return None
    last_user_idx = None
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx is None:
        return None
    return history[:last_user_idx]


def retry_last(session_id: str) -> dict[str, Any]:
    """Remove the last assistant response — leaves the user's prompt in place.

    Returns:\n        dict with keys ``last_user_text`` and ``removed_count``.

    Raises:
        KeyError: session not found.
        ValueError: no user message in transcript.
    """
    session = load_session(session_id)
    if session is None:
        raise KeyError(session_id)
    history = session.messages or []
    last_user_idx = None
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx is None:
        raise ValueError("No previous message to retry.")
    last_user_text = _extract_text(history[last_user_idx].get("content", ""))
    removed_count = len(history) - last_user_idx
    session.messages = history[:last_user_idx]
    session.updated_at = time.time()
    save_session(session)
    return {"last_user_text": last_user_text, "removed_count": removed_count}


def undo_last(session_id: str) -> dict[str, Any]:
    """Remove the most recent user message and everything after it.

    Returns:\n        dict with keys ``removed_count`` and ``removed_preview``.

    Raises:
        KeyError: session not found.
        ValueError: no user message in transcript.
    """
    session = load_session(session_id)
    if session is None:
        raise KeyError(session_id)
    history = session.messages or []
    last_user_idx = None
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx is None:
        raise ValueError("Nothing to undo.")
    removed_text = _extract_text(history[last_user_idx].get("content", ""))
    removed_count = len(history) - last_user_idx
    session.messages = history[:last_user_idx]
    session.updated_at = time.time()
    save_session(session)
    preview = (removed_text[:40] + "...") if len(removed_text) > 40 else removed_text
    return {"removed_count": removed_count, "removed_preview": preview}


def session_status(session_id: str) -> dict[str, Any]:
    """Return a metadata snapshot for a session.

    Returns dict with keys: session_id, title, model, message_count,
    last_user_text, last_assistant_text.
    """
    session = load_session(session_id)
    if session is None:
        return {"error": "session not found"}
    messages = session.messages or []
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    last_assistant = next(
        (m for m in reversed(messages) if m.get("role") == "assistant"), None
    )
    return {
        "session_id": session.session_id,
        "title": session.title,
        "model": session.model,
        "message_count": len(messages),
        "last_user_text": _extract_text(last_user.get("content", ""))[:200] if last_user else "",
        "last_assistant_text": _extract_text(last_assistant.get("content", ""))[:200] if last_assistant else "",
    }