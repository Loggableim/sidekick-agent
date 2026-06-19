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


_CONTEXT_SEGMENTS = (
    ('chat_history', 'Chat History', '', '#4fc3f7'),
    ('system_prompt', 'System Prompt', '', '#ffb74d'),
    ('files', 'Files', '', '#81c784'),
    ('memory', 'Memory', '', '#ce93d8'),
)


def _positive_int(value, fallback=0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _message_has_file_content(message: dict[str, Any]) -> bool:
    if not isinstance(message, dict):
        return False
    if message.get('_hasFile') or message.get('attachments'):
        return True
    content = message.get('content')
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get('type') in {'file', 'image', 'image_url', 'input_image'}:
                return True
    if isinstance(content, dict) and content.get('_multimodal'):
        return True
    return False


def _classify_context_message(message: dict[str, Any]) -> str:
    text = _extract_text(message.get('content', '') if isinstance(message, dict) else message)
    low = text.lower()
    if '[memory]' in low or '#memory' in low:
        return 'memory'
    if _message_has_file_content(message):
        return 'files'
    if isinstance(message, dict) and message.get('role') == 'system':
        return 'system_prompt'
    return 'chat_history'


def _estimate_message_tokens(message: dict[str, Any]) -> int:
    try:
        from runtime.model_metadata import estimate_messages_tokens_rough

        return max(0, int(estimate_messages_tokens_rough([message]) or 0))
    except Exception:
        content = message.get('content', '') if isinstance(message, dict) else message
        return max(0, (len(_extract_text(content)) + 3) // 4)


def _scale_tokens_to_total(tokens_by_id: dict[str, int], target_total: int) -> dict[str, int]:
    current_total = sum(max(0, int(value or 0)) for value in tokens_by_id.values())
    target_total = _positive_int(target_total, 0)
    if current_total <= 0 or target_total <= 0:
        return tokens_by_id

    ratio = target_total / current_total
    scaled = {
        key: max(0, int(round(max(0, int(value or 0)) * ratio)))
        for key, value in tokens_by_id.items()
    }
    delta = target_total - sum(scaled.values())
    if delta:
        ordered = sorted(tokens_by_id, key=lambda key: tokens_by_id.get(key, 0), reverse=True)
        if ordered:
            if delta > 0:
                scaled[ordered[0]] += delta
            else:
                remaining = -delta
                for key in ordered:
                    take = min(remaining, scaled.get(key, 0))
                    scaled[key] -= take
                    remaining -= take
                    if remaining <= 0:
                        break
    return scaled


def session_context_info(session_id: str) -> dict[str, Any]:
    """Return a WebUI-friendly context window breakdown for one session."""
    s = get_session(session_id)
    messages = (
        getattr(s, 'context_messages', None)
        if isinstance(getattr(s, 'context_messages', None), list)
        else None
    )
    if not messages:
        messages = (
            getattr(s, 'messages', None)
            if isinstance(getattr(s, 'messages', None), list)
            else []
        )

    tokens_by_id = {segment_id: 0 for segment_id, _, _, _ in _CONTEXT_SEGMENTS}
    file_attachment_count = 0
    for message in messages:
        if not isinstance(message, dict):
            message = {'role': 'user', 'content': str(message)}
        segment_id = _classify_context_message(message)
        if segment_id == 'files':
            file_attachment_count += 1
        tokens_by_id[segment_id] = tokens_by_id.get(segment_id, 0) + _estimate_message_tokens(message)

    last_prompt_tokens = _positive_int(getattr(s, 'last_prompt_tokens', None), 0)
    if last_prompt_tokens:
        tokens_by_id = _scale_tokens_to_total(tokens_by_id, last_prompt_tokens)

    total_tokens = sum(tokens_by_id.values())
    context_length = _positive_int(getattr(s, 'context_length', None), 128 * 1024)
    threshold_tokens = _positive_int(getattr(s, 'threshold_tokens', None), max(1, context_length // 2))
    pct_used = min(100, round((total_tokens / context_length) * 100)) if context_length else 0

    segments = []
    stacked = []
    for segment_id, label, icon, color in _CONTEXT_SEGMENTS:
        tokens = max(0, int(tokens_by_id.get(segment_id, 0) or 0))
        pct = round((tokens / total_tokens) * 100) if total_tokens else 0
        item = {
            'id': segment_id,
            'label': label,
            'icon': icon,
            'tokens': tokens,
            'pct': pct,
            'color': color,
        }
        segments.append(item)
        if tokens > 0:
            stacked.append({'id': segment_id, 'label': label, 'pct': pct, 'color': color})

    return {
        'session_id': session_id,
        'total_tokens': total_tokens,
        'context_length': context_length,
        'threshold_tokens': threshold_tokens,
        'pct_used': pct_used,
        'has_real_data': False,
        'segments': segments,
        'stacked': stacked,
        'metadata': {
            'message_count': len(getattr(s, 'messages', []) or []),
            'context_message_count': len(messages),
            'file_attachment_count': file_attachment_count,
            'workspace': getattr(s, 'workspace', '') or '',
            'model': getattr(s, 'model', '') or '',
        },
    }


def session_usage(session_id: str) -> dict[str, Any]:
    """Return stored token/cost usage for a session."""
    s = get_session(session_id)
    input_tokens = _positive_int(getattr(s, 'input_tokens', None), 0)
    output_tokens = _positive_int(getattr(s, 'output_tokens', None), 0)
    return {
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'total_tokens': input_tokens + output_tokens,
        'estimated_cost': getattr(s, 'estimated_cost', None),
        'context_length': _positive_int(getattr(s, 'context_length', None), 0),
        'threshold_tokens': _positive_int(getattr(s, 'threshold_tokens', None), 0),
        'last_prompt_tokens': _positive_int(getattr(s, 'last_prompt_tokens', None), 0),
    }


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
