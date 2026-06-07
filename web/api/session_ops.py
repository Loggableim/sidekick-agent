"""
Sidekick -- Session manipulation helpers (retry, undo, status).

Thin wrappers over ``shared.sessions`` that add WebUI-specific locking
(per-session agent lock, stale-object guard, stream-lifecycle checks).

Canonical session persistence: ``shared.sessions``.
Legacy session migration: ``shared.sessions.migrate_legacy_sessions()``.
"""
from __future__ import annotations

import logging
import json
import time
from typing import Any

logger = logging.getLogger(__name__)

from web.api.config import LOCK, SESSIONS, _get_session_agent_lock
from web.api.models import get_session as _get_session, _active_stream_ids

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
    """Remove the last assistant response — WebUI-thin wrapper.

    Delegates the core logic to ``shared.sessions.retry_last()`` but
    wraps it with the WebUI's per-session agent lock and stale-object
    guard (``SESSIONS`` cache), plus context_messages truncation.

    Raises:
        KeyError: session not found.
        ValueError: no user message in transcript.
    """
    from shared.sessions import retry_last as _shared_retry

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
        # Use shared persistence
        s.save()
    return {'last_user_text': last_user_text, 'removed_count': removed_count}


def undo_last(session_id: str) -> dict[str, Any]:
    """Remove the most recent user message — WebUI-thin wrapper.

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

    WebUI-specific wrapper — adds stream_active check on top of
    ``shared.sessions.session_status()`` metadata.
    """
    from shared.sessions import session_status as _shared_status

    s = get_session(session_id, metadata_only=True)
    result = _shared_status(session_id)
    if "error" in result:
        return result
    stream_active = bool(getattr(s, "active_stream_id", None))
    try:
        stream_active = stream_active and getattr(s, "active_stream_id", None) in _active_stream_ids()
    except Exception:
        pass
    result["stream_active"] = stream_active
    result["pending"] = bool(getattr(s, "pending_user_message", None))
    return result
