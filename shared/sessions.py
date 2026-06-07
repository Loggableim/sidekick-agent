from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from shared.config import get_default_workspace
from shared.runtime import web_state_dir


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
    for path in sessions_dir().glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            session = Session(**data)
            rows.append(session.compact())
        except Exception:
            continue
    rows.sort(key=lambda row: row.get("updated_at", 0), reverse=True)
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
