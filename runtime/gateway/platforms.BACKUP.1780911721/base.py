"""Minimal stub for BasePlatformAdapter and related types.

Full implementation lives in the platforms/ subpackage (not yet copied).
These stubs exist so gateway/run.py can be imported without errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class MessageType(Enum):
    """Type of message event."""
    TEXT = "text"
    COMMAND = "command"
    VOICE = "voice"
    IMAGE = "image"
    DOCUMENT = "document"
    STICKER = "sticker"
    CALLBACK = "callback"
    REACTION = "reaction"
    SYSTEM = "system"
    UNKNOWN = "unknown"


@dataclass
class MessageEvent:
    """A single message event from a platform adapter."""
    platform: str
    chat_id: str
    user_id: str
    message_id: str
    text: str = ""
    thread_id: Optional[str] = None
    chat_name: str = ""
    user_name: str = ""
    chat_type: str = "dm"
    message_type: MessageType = MessageType.TEXT
    raw: Optional[Dict[str, Any]] = None
    attachments: list = field(default_factory=list)


@dataclass
class EphemeralReply:
    """A reply that is not persisted to the session transcript."""
    text: str
    delete_after: float = 5.0  # seconds


class BasePlatformAdapter:
    """Base class for platform adapters. Stub for structural compatibility."""

    def __init__(self, config: Any = None):
        self.config = config

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send(self, chat_id: str, message: str, **kwargs) -> dict:
        return {"success": True, "message_id": None}


def _reply_anchor_for_event(event: MessageEvent) -> Optional[str]:
    """Return the message ID to reply to, if any."""
    return None


def merge_pending_message_event(event: MessageEvent, existing: Optional[MessageEvent] = None) -> MessageEvent:
    """Merge a pending message event with an existing one."""
    return event or existing or MessageEvent(
        platform="",
        chat_id="",
        user_id="",
        message_id="",
    )
