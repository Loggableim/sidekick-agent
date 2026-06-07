"""
Sidekick -- Session manipulation helpers (retry, undo, status, truncate).

Delegates generic operations to ``shared.sessions`` while keeping
WebUI-specific Session class integration (``web.api.models.Session``
with agent state, locks, streams).

Canonical session storage: ``~/.sidekick/state/webui/sessions/`` (shared
path with ``shared.sessions``).  WebUI-specific additional state (agent
lock, active_stream_id, context_messages) is held only in-memory.
"""
import logging
import json
import time
from typing import Any

logger = logging.getLogger(__name__)

from web.api.config import LOCK, SESSIONS, SESSIONS_MAX, _get_session_agent_lock
from web.api.models import get_session as _get_session, new_session, _active_stream_ids

# Re-export get_session for internal callers
def get_session(sid):
    return _get_session(sid)


def _extract_text(content) -> str:
    """Extract plain text from mixed content (list of parts or plain string)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [p.get('text', '') if isinstance(p, dict) else str(p) for p in content]
        return ''.join(texts)
    return str(content or '')


def _truncate_at_last_user(history):
    """Find the last user message and truncate after it."""
    if not history:
        return None
    last_user_idx = None
    for i in range(len(history) - 1, -1, -1):
        if history[i].get('role') == 'user':
            last_user_idx = i
            break
    if last_user_idx is None:
        return None
    return history[:last_user_idx]


def retry_last(session_id: str) -> dict[str, Any]:
    """Remove the last assistant response — WebUI-specific wrapper.

    Delegates to ``shared.sessions.retry_last()`` for the generic logic
    but wraps it with the WebUI's per-session agent lock and in-memory
    Session object (``SESSIONS`` cache).
    """
    with _get_session_agent_lock(session_id):
        s = get_session(session_id)  # raises KeyError if missing
        s = SESSIONS.get(session_id, s)  # stale-object guard
        history = s.messages or []
        last_user_idx = None
        for i in range(len(history) - 1, -1, -1):
            if history[i].get('role') == 'user':
                last_user_idx = i
                break
        if last_user_idx is None:
            raise ValueError('No previous message to retry.')

        last_user_text = _extract_text(history[last_user_idx].get('content', ''))
        removed_count = len(history) - last_user_idx
        s.messages = history[:last_user_idx]
        if isinstance(getattr(s, 'context_messages', None), list) and s.context_messages:
            truncated_context = _truncate_at_last_user(s.context_messages)
            if truncated_context is not None:
                s.context_messages = truncated_context
        s.save()
    return {'last_user_text': last_user_text, 'removed_count': removed_count}


def undo_last(session_id: str) -> dict[str, Any]:
    """Remove the most recent user message — WebUI-specific wrapper.

    See ``retry_last`` for the lock/stale-guard pattern rationale.
    """
    with _get_session_agent_lock(session_id):
        s = get_session(session_id)
        s = SESSIONS.get(session_id, s)
        history = s.messages or []
        last_user_idx = None
        for i in range(len(history) - 1, -1, -1):
            if history[i].get('role') == 'user':
                last_user_idx = i
                break
        if last_user_idx is None:
            raise ValueError('Nothing to undo.')

        removed_text = _extract_text(history[last_user_idx].get('content', ''))
        removed_count = len(history) - last_user_idx
        s.messages = history[:last_user_idx]
        if isinstance(getattr(s, 'context_messages', None), list) and s.context_messages:
            truncated_context = _truncate_at_last_user(s.context_messages)
            if truncated_context is not None:
                s.context_messages = truncated_context
        s.save()
    preview = (removed_text[:40] + '...') if len(removed_text) > 40 else removed_text
    return {'removed_count': removed_count, 'removed_preview': preview}


def session_status(session_id: str) -> dict[str, Any]:
    """Return a snapshot of session state for /status.

    WebUI-specific wrapper around ``shared.sessions.session_status()``.
    """
    s = get_session(session_id, metadata_only=True)
    session = {"session_id": s.session_id, "title": s.title, "model": s.model}
    stream_active = bool(getattr(s, "active_stream_id", None))
    try:
        stream_active = stream_active and getattr(s, "active_stream_id", None) in _active_stream_ids()
    except Exception:
        pass
    session["stream_active"] = stream_active
    session["pending"] = bool(getattr(s, "pending_user_message", None))
    messages = getattr(s, "messages", None) or []
    session["message_count"] = len(messages)
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    session["last_user_text"] = _extract_text(last_user.get("content", ""))[:200] if last_user else ""
    last_assistant = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
    session["last_assistant_text"] = _extract_text(last_assistant.get("content", ""))[:200] if last_assistant else ""
    return session