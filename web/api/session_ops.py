"""
Sidekick -- Session manipulation helpers (retry, undo, status, truncate).
"""
import logging
import json
import time
from typing import Any

logger = logging.getLogger(__name__)

from web.api.config import LOCK, SESSIONS, SESSIONS_MAX, _get_session_agent_lock
from web.api.models import get_session as _get_session, new_session

# Re-export get_session for internal callers
def get_session(sid):
    return _get_session(sid)


# def gates(s) magic commands — testing

def _extract_text(content) -> str:
    """Extract plain text from mixed content (list of parts or plain string)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [p.get('text', '') if isinstance(p, dict) else str(p) for p in content]
        return ''.join(texts)
    return str(content or '')


def _truncate_at_last_user(history):
    """Find the last user message in a message list and truncate after it."""
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
    """Remove the last assistant response and any subsequent messages.

    Mirrors gateway/run.py:_handle_retry_command. Leaves the user's most
    recent prompt in place so the client can resend it with a different
    model or after a provider error.

    Raises:
        KeyError: session not found
        ValueError: no user message in transcript
    """
    # Acquire the per-session agent lock as the outermost lock so that the
    # read-modify-write of s.messages is serialised with the periodic
    # checkpoint thread, cancel_stream, and all other session writers.
    # Lock ordering: _agent_lock → _write_session_index (LOCK).
    with _get_session_agent_lock(session_id):
        # get_session() internally acquires the module-level LOCK. We
        # re-bind s from SESSIONS cache to avoid a stale parallel copy
        # (the stale-object guard). SESSIONS.get() is GIL-safe (single
        # dict read), and the per-session _agent_lock already serializes
        # modifications to this session, so the module-level LOCK is not
        # needed here.
        s = get_session(session_id)  # raises KeyError if missing
        # Stale-object guard: re-bind to canonical cached instance.
        s = SESSIONS.get(session_id, s)
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
        s.save()  # save() internally acquires LOCK via _write_session_index()
    return {'last_user_text': last_user_text, 'removed_count': removed_count}


def undo_last(session_id: str) -> dict[str, Any]:
    """Remove the most recent user message and everything after it.

    Mirrors gateway/run.py:_handle_undo_command. Returns a preview of the
    removed text so the UI can confirm to the user.

    Raises:
        KeyError: session not found
        ValueError: no user message in transcript
    """
    # Acquire the per-session agent lock as the outermost lock so that the
    # read-modify-write of s.messages is serialised with the periodic
    # checkpoint thread, cancel_stream, and all other session writers.
    # Lock ordering: _agent_lock → _write_session_index (LOCK).
    with _get_session_agent_lock(session_id):
        s = get_session(session_id)  # acquires LOCK transiently
        # Stale-object guard — see retry_last for rationale.
        # Per-session _agent_lock already serializes; SESSIONS.get() is GIL-safe.
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
        s.save()  # outside LOCK -- save() internally acquires LOCK via _write_session_index()
    preview = (removed_text[:40] + '...') if len(removed_text) > 40 else removed_text
    return {
        'removed_count': removed_count,
        'removed_preview': preview,
    }


def session_status(session_id: str) -> dict[str, Any]:
    """Return a snapshot of session state for /status.

    Webui equivalent of gateway/run.py:_handle_status_command. The agent's
    turn_journal provides a richer stream lifecycle, but /status is a
    synchronous REST fallback for polling clients and CLI tools.

    Returns:
        dict with keys: session_id, title, model, stream_active, pending,
        message_count, last_user_text, last_assistant_text
    """
    # Fast metadata-only load — no LOCK needed for a single read
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
    # Find the last user and assistant texts
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    session["last_user_text"] = _extract_text(last_user.get("content", ""))[:200] if last_user else ""
    last_assistant = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
    session["last_assistant_text"] = _extract_text(last_assistant.get("content", ""))[:200] if last_assistant else ""
    return session