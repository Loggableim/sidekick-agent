"""
Session management for the gateway.

Handles:
- Session context tracking (where messages come from)
- Session storage (conversations persisted to disk)
- Reset policy evaluation (when to start fresh)
- Dynamic system prompt injection (agent knows its context)
"""

import hashlib
import logging
import os
import json
import threading
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


def _now() -> datetime:
    """Return the current local time."""
    return datetime.now()


# ---------------------------------------------------------------------------
# PII redaction helpers
# ---------------------------------------------------------------------------

def _hash_id(value: str) -> str:
    """Deterministic 12-char hex hash of an identifier."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _hash_sender_id(value: str) -> str:
    """Hash a sender ID to ``user_<12hex>``."""
    return f"user_{_hash_id(value)}"


def _hash_chat_id(value: str) -> str:
    """Hash the numeric portion of a chat ID, preserving platform prefix.

    ``telegram:12345`` → ``telegram:<hash>``
    ``12345``          → ``<hash>``
    """
    colon = value.find(":")
    if colon > 0:
        prefix = value[:colon]
        return f"{prefix}:{_hash_id(value[colon + 1:])}"
    return _hash_id(value)


from .config import (
    Platform,
    GatewayConfig,
    SessionResetPolicy,  # noqa: F401 — re-exported via gateway/__init__.py
    HomeChannel,
)
from .whatsapp_identity import (
    canonical_whatsapp_identifier,
    normalize_whatsapp_identifier,  # noqa: F401 - re-exported for gateway.session callers
)
from shared.utils import atomic_replace


@dataclass
class SessionSource:
    """
    Describes where a message originated from.
    
    This information is used to:
    1. Route responses back to the right place
    2. Inject context into the system prompt
    3. Track origin for cron job delivery
    """
    platform: Platform
    chat_id: str
    chat_name: str = ""
    user_id: str = ""
    user_name: str = ""
    thread_id: Optional[str] = None
    chat_type: str = "dm"  # "dm", "group", "channel", "thread"


def build_session_key(
    platform: Platform,
    chat_id: str,
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> str:
    """Build a stable session key from a message's origin.

    The session key is a colon-delimited string used for session lookup and
    storage. Format: ``platform:chat_id[:thread_id][:user_id]``.

    For WhatsApp DM chat_ids and group participant_ids, the chat_id is
    canonicalized through :func:`~.whatsapp_identity.canonical_whatsapp_identifier`
    so that phone-JID / LID aliases share the same session.
    """
    _normalized_chat_id = chat_id
    if platform == Platform.WHATSAPP:
        try:
            _normalized_chat_id = canonical_whatsapp_identifier(chat_id)
        except Exception:
            pass  # best-effort, fall through to raw chat_id

    parts = [platform.value, _normalized_chat_id]
    if thread_id:
        parts.append(thread_id)
    if user_id:
        if platform == Platform.WHATSAPP:
            try:
                user_id = canonical_whatsapp_identifier(user_id)
            except Exception:
                pass
        parts.append(user_id)
    return ":".join(parts)


def parse_session_key(key: str) -> dict:
    """Parse a session key back into its components.
    
    Format: platform:chat_id[:thread_id][:user_id]
    """
    parts = key.split(":")
    result = {
        "platform": parts[0] if len(parts) > 0 else "",
        "chat_id": parts[1] if len(parts) > 1 else "",
        "thread_id": parts[2] if len(parts) > 2 else None,
        "user_id": parts[3] if len(parts) > 3 else None,
    }
    return result


def build_session_context_prompt(source: SessionSource) -> str:
    """Build a system prompt fragment describing where the message is coming from.
    
    This is injected into the agent's system prompt to give it context about
    the messaging platform and conversation.
    """
    lines = []
    lines.append(f"You are communicating via {source.platform.value.upper()}.")
    
    chat_type_labels = {
        "dm": "direct message",
        "group": "group chat",
        "channel": "channel",
        "thread": "thread",
    }
    chat_type_str = chat_type_labels.get(source.chat_type, source.chat_type)
    lines.append(f"This is a {chat_type_str}.")
    
    if source.chat_name:
        lines.append(f"Chat: {source.chat_name}")
    
    if source.thread_id and not source.chat_name:
        lines.append(f"Thread ID: {source.thread_id}")
    
    if source.user_name:
        lines.append(f"User: {source.user_name}")
    elif source.user_id and source.user_id != source.chat_id:
        lines.append(f"User ID: {_hash_sender_id(source.user_id)}")
    
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Session entry
# ---------------------------------------------------------------------------

@dataclass
class SessionEntry:
    """Represents a single session record."""
    session_id: str
    session_key: str
    platform: str
    chat_id: str
    chat_name: str = ""
    thread_id: Optional[str] = None
    user_id: Optional[str] = None
    user_name: str = ""
    chat_type: str = "dm"
    created_at: str = ""
    updated_at: str = ""
    message_count: int = 0


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------

SESSION_STORE_LOCK = threading.Lock()


@dataclass
class SessionContext:
    """Mutable session context passed through the gateway pipeline.
    
    Carries metadata about the current message being processed — platform,
    chat, user, session info — that downstream stages (agent runner, tools,
    hooks, delivery) can read without environment-variable gymnastics.
    """
    source: SessionSource
    session_id: str = ""
    session_key: str = ""
    is_new_session: bool = False
    is_reset: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


class SessionStore:
    """
    Manages session persistence and lookup.
    
    Sessions are stored in ~/.sidekick/sessions/sessions.json.
    Each session has a unique session_id and a session_key that identifies
    the conversation (platform + chat_id + optional thread/user).
    """
    
    def __init__(self, config: Optional[GatewayConfig] = None):
        self._config = config or GatewayConfig()
        self._sessions_dir = self._config.sessions_dir
        self._sessions_path = self._sessions_dir / "sessions.json"
        self._sessions: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load()
    
    @property
    def sessions_dir(self) -> Path:
        return self._sessions_dir
    
    def _load(self) -> None:
        """Load sessions from disk."""
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        if self._sessions_path.exists():
            try:
                with open(self._sessions_path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._sessions = data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load sessions: %s", e)
                self._sessions = {}
    
    def _save(self) -> None:
        """Save sessions to disk atomically."""
        tmp_path = self._sessions_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._sessions, f, indent=2, ensure_ascii=False)
            atomic_replace(tmp_path, self._sessions_path)
        except OSError as e:
            logger.error("Failed to save sessions: %s", e)
    
    def get(self, session_key: str) -> Optional[dict]:
        """Look up a session by key."""
        with self._lock:
            return self._sessions.get(session_key)
    
    def get_by_id(self, session_id: str) -> Optional[dict]:
        """Look up a session by ID."""
        with self._lock:
            for entry in self._sessions.values():
                if entry.get("session_id") == session_id:
                    return entry
        return None
    
    def get_or_create(self, source: SessionSource) -> tuple:
        """Get an existing session or create a new one.
        
        Returns (session_entry, is_new).
        """
        session_key = build_session_key(
            platform=source.platform,
            chat_id=source.chat_id,
            thread_id=source.thread_id,
            user_id=source.user_id,
        )
        
        with self._lock:
            existing = self._sessions.get(session_key)
            if existing:
                existing["updated_at"] = _now().isoformat()
                self._save()
                return existing, False
            
            # Create new session
            session_id = str(uuid.uuid4())
            now = _now().isoformat()
            entry = {
                "session_id": session_id,
                "session_key": session_key,
                "platform": source.platform.value,
                "chat_id": source.chat_id,
                "chat_name": source.chat_name,
                "thread_id": source.thread_id,
                "user_id": source.user_id,
                "user_name": source.user_name,
                "chat_type": source.chat_type,
                "origin": {
                    "platform": source.platform.value,
                    "chat_id": source.chat_id,
                    "chat_name": source.chat_name,
                    "thread_id": source.thread_id,
                    "user_id": source.user_id,
                    "user_name": source.user_name,
                    "chat_type": source.chat_type,
                },
                "created_at": now,
                "updated_at": now,
                "message_count": 0,
            }
            self._sessions[session_key] = entry
            self._save()
            return entry, True
    
    def update_message_count(self, session_key: str) -> None:
        """Increment the message count for a session."""
        with self._lock:
            entry = self._sessions.get(session_key)
            if entry:
                entry["message_count"] = entry.get("message_count", 0) + 1
                entry["updated_at"] = _now().isoformat()
                self._save()
    
    def list_sessions(self, platform: Optional[str] = None) -> List[dict]:
        """List all sessions, optionally filtered by platform."""
        with self._lock:
            results = []
            for entry in self._sessions.values():
                if platform and entry.get("platform") != platform:
                    continue
                results.append(dict(entry))
            return sorted(results, key=lambda e: e.get("updated_at", ""), reverse=True)
    
    def reset_session(self, session_key: str) -> Optional[dict]:
        """Reset a session (mark as ended, create a fresh one on next message).
        
        Returns the old session entry, or None if not found.
        """
        with self._lock:
            entry = self._sessions.pop(session_key, None)
            if entry:
                self._save()
            return entry
    
    def should_reset(self, session_key: str, policy: Optional[SessionResetPolicy] = None) -> bool:
        """Check whether a session should be reset based on policy.
        
        Args:
            session_key: The session key to check.
            policy: The reset policy to evaluate. Uses config default if None.
        
        Returns:
            True if the session should be reset.
        """
        with self._lock:
            entry = self._sessions.get(session_key)
            if not entry:
                return False
        
        p = policy or self._config.default_reset_policy
        
        if p.mode == "none":
            return False
        
        now = _now()
        updated_str = entry.get("updated_at", "")
        if not updated_str:
            return False
        
        try:
            updated = datetime.fromisoformat(updated_str)
        except (ValueError, TypeError):
            return False
        
        # Daily reset
        if p.mode in ("daily", "both"):
            today_reset = now.replace(hour=p.at_hour, minute=0, second=0, microsecond=0)
            if now >= today_reset and updated < today_reset:
                return True
        
        # Idle reset
        if p.mode in ("idle", "both"):
            idle_delta = timedelta(minutes=p.idle_minutes)
            if now - updated >= idle_delta:
                return True
        
        return False
    
    def prune_old_sessions(self, max_age_days: int = 90) -> int:
        """Remove sessions older than max_age_days.
        
        Returns the number of pruned entries.
        """
        if max_age_days <= 0:
            return 0
        
        cutoff = _now() - timedelta(days=max_age_days)
        pruned = 0
        
        with self._lock:
            to_delete = []
            for key, entry in self._sessions.items():
                updated_str = entry.get("updated_at", "")
                if updated_str:
                    try:
                        updated = datetime.fromisoformat(updated_str)
                        if updated < cutoff:
                            to_delete.append(key)
                    except (ValueError, TypeError):
                        continue
            
            for key in to_delete:
                del self._sessions[key]
                pruned += 1
            
            if pruned > 0:
                self._save()
                logger.info("Pruned %d sessions older than %d days", pruned, max_age_days)
        
        return pruned


def export_session_entries(store: SessionStore) -> List[SessionEntry]:
    """Export sessions from the store as SessionEntry objects."""
    entries = []
    for data in store.list_sessions():
        entries.append(SessionEntry(
            session_id=data.get("session_id", ""),
            session_key=data.get("session_key", ""),
            platform=data.get("platform", ""),
            chat_id=data.get("chat_id", ""),
            chat_name=data.get("chat_name", ""),
            thread_id=data.get("thread_id"),
            user_id=data.get("user_id"),
            user_name=data.get("user_name", ""),
            chat_type=data.get("chat_type", "dm"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            message_count=data.get("message_count", 0),
        ))
    return entries
