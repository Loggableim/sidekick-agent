"""Minimal stub for BasePlatformAdapter and related types.

Full implementation lives in the platforms/ subpackage (not yet copied).
These stubs exist so gateway/run.py can be imported without errors.
"""

from __future__ import annotations

import re
import os
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

    @property
    def source(self):
        """
        Build a SessionSource on-demand for compatibility with the
        rest of the gateway (which expects event.source to be a SessionSource
        with platform / chat_id / user_id / etc.).
        """
        from ..session import Platform, SessionSource
        try:
            platform = Platform(self.platform)
        except (ValueError, ImportError):
            platform = Platform.LOCAL
        return SessionSource(
            platform=platform,
            chat_id=self.chat_id,
            chat_name=self.chat_name or None,
            chat_type=self.chat_type,
            user_id=self.user_id,
            user_name=self.user_name or None,
            thread_id=self.thread_id,
            message_id=self.message_id,
        )

    def get_command(self) -> Optional[str]:
        """
        If text starts with a slash-command (e.g. /new, /status), return the
        command name (without leading slash). Otherwise return None.
        """
        text = (self.text or "").strip()
        if not text.startswith("/"):
            return None
        # Take the first whitespace-delimited token, strip the leading slash
        first = text.split(maxsplit=1)[0]
        if first.startswith("/"):
            first = first[1:]
        # Strip @bot suffix (Telegram): "/cmd@botname" -> "cmd"
        if "@" in first:
            first = first.split("@", 1)[0]
        return first or None

    def get_command_args(self) -> str:
        """
        Return the text after the slash-command token. If no command, return "".
        """
        text = (self.text or "").strip()
        if not text.startswith("/"):
            return ""
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return ""
        return parts[1]


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

    async def connect(self):
        return await self.start()

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

    async def disconnect(self):
        await self.stop()

    async def cancel_background_tasks(self):
        return None

    async def send(self, chat_id: str, message: str, **kwargs) -> dict:
        return {
            "success": False,
            "error": f"{type(self).__name__} does not implement send()",
        }

    @staticmethod
    def truncate_message(text: str, max_length: int, len_fn=None) -> list[str]:
        """Split text into chunks that fit a platform message limit."""
        message = str(text or "")
        if max_length <= 0:
            return [message]
        measure = len_fn or len
        if measure(message) <= max_length:
            return [message]

        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for char in message:
            char_len = measure(char)
            if current and current_len + char_len > max_length:
                chunks.append("".join(current))
                current = []
                current_len = 0
            current.append(char)
            current_len += char_len
        if current:
            chunks.append("".join(current))
        return chunks or [""]

    @staticmethod
    def extract_media(text: str) -> tuple[list[tuple[str, bool]], str]:
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


def utf16_len(value: str) -> int:
    """Return the number of UTF-16 code units used by a message."""
    return len(str(value or "").encode("utf-16-le")) // 2


def resolve_proxy_url(*, platform_env_var: str | None = None) -> Optional[str]:
    """Return the configured outbound proxy URL for platform REST calls."""
    candidates = []
    if platform_env_var:
        candidates.append(platform_env_var)
    candidates.extend(("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"))
    for name in candidates:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def proxy_kwargs_for_aiohttp(proxy_url: Optional[str]) -> tuple[dict, dict]:
    """Split a proxy URL into aiohttp session and request kwargs."""
    if not proxy_url:
        return {}, {}
    return {}, {"proxy": proxy_url}
