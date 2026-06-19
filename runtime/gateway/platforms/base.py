"""Minimal stub for BasePlatformAdapter and related types.

Full implementation lives in the platforms/ subpackage (not yet copied).
These stubs exist so gateway/run.py can be imported without errors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class MessageType(Enum):
    """Type of message event."""
    TEXT = "text"
    COMMAND = "command"
    VOICE = "voice"
    AUDIO = "audio"
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

    @property
    def has_fatal_error(self) -> bool:
        """Whether the adapter currently carries a platform-level fatal error."""
        return bool(self.fatal_error_code or self.fatal_error_message)

    @property
    def fatal_error_retryable(self) -> bool:
        """Whether the current fatal error is retryable (transient) or permanent.
        Override in subclass when the platform can distinguish error types."""
        return True

    @property
    def fatal_error_code(self) -> Optional[str]:
        """Error code for the current fatal error, if any."""
        return getattr(self, "_fatal_error_code", None)

    @property
    def fatal_error_message(self) -> Optional[str]:
        """Error message for the current fatal error, if any."""
        return getattr(self, "_fatal_error_message", None)

    async def stop(self):
        pass

    async def send(self, chat_id: str, message: str, **kwargs) -> dict:
        return {"success": True, "message_id": None}

    def extract_media(self, text: str) -> tuple[list[tuple[str, bool]], str]:
        """Extract MEDIA:path tags and optional [[audio_as_voice]] directives."""
        media: list[tuple[str, bool]] = []
        cleaned_lines: list[str] = []
        voice_next = False
        media_pattern = re.compile(r"MEDIA:(\S+)")

        for raw_line in str(text or "").splitlines():
            line = raw_line
            if "[[audio_as_voice]]" in line:
                voice_next = True
                line = line.replace("[[audio_as_voice]]", "")

            def repl(match: re.Match[str]) -> str:
                nonlocal voice_next
                path = match.group(1).strip().rstrip('",}')
                if path:
                    media.append((path, voice_next))
                voice_next = False
                return ""

            line = media_pattern.sub(repl, line).strip()
            if line:
                cleaned_lines.append(line)

        return media, "\n".join(cleaned_lines).strip()

    def extract_images(self, text: str) -> tuple[list[tuple[str, str]], str]:
        return [], text

    def extract_local_files(self, text: str) -> tuple[list[str], str]:
        return [], text


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


def should_send_media_as_audio(platform: Any, ext: str, *, is_voice: bool = False) -> bool:
    platform_value = getattr(platform, "value", platform)
    platform_name = str(platform_value or "").lower()
    ext = str(ext or "").lower()
    audio_exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac"}
    if ext not in audio_exts:
        return False
    if platform_name == "telegram":
        return True
    return bool(is_voice)
