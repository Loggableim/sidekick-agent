"""Discord platform adapter."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Optional

from runtime.gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType

logger = logging.getLogger(__name__)


class _SendResult(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def check_discord_requirements() -> bool:
    try:
        import discord  # noqa: F401
        return True
    except ImportError:
        return False


class DiscordAdapter(BasePlatformAdapter):
    """Discord bot adapter using discord.py for receive and REST for send."""

    MAX_MESSAGE_LENGTH = 2000

    def __init__(self, config: Any = None):
        super().__init__(config)
        self._token = (
            getattr(config, "token", None)
            or os.getenv("DISCORD_BOT_TOKEN", "")
        ).strip()
        self._client: Any = None
        self._client_task: Optional[asyncio.Task] = None
        self._ready_event: Optional[asyncio.Event] = None
        self._msg_handler: Optional[Callable] = None
        self._fatal_handler: Optional[Callable] = None
        self._session_store: Any = None
        self._busy_handler: Optional[Callable] = None
        self._require_mention = _env_flag("DISCORD_REQUIRE_MENTION", default=False)
        self._allowed_channels = _csv_set(os.getenv("DISCORD_ALLOWED_CHANNELS", ""))
        self._ignored_channels = _csv_set(os.getenv("DISCORD_IGNORED_CHANNELS", ""))

    @property
    def fatal_error_retryable(self) -> bool:
        code = str(getattr(self, "_fatal_error_code", "") or "")
        return code not in {"LoginFailure", "PrivilegedIntentsRequired", "MissingToken"}

    def set_message_handler(self, h):
        self._msg_handler = h

    def set_fatal_error_handler(self, h):
        self._fatal_handler = h

    def set_session_store(self, s):
        self._session_store = s

    def set_busy_session_handler(self, h):
        self._busy_handler = h

    async def connect(self) -> bool:
        return await self.start()

    async def disconnect(self):
        await self.stop()

    async def start(self) -> bool:
        if not self._token:
            self._set_fatal("MissingToken", "Discord bot token is not configured")
            return False

        try:
            import discord
        except ImportError:
            self._set_fatal("ImportError", "discord.py is not installed")
            return False

        try:
            intents = discord.Intents.default()
            if hasattr(intents, "message_content"):
                intents.message_content = True
            client = discord.Client(intents=intents)
            self._client = client
            self._ready_event = asyncio.Event()

            @client.event
            async def on_ready():
                if self._ready_event and not self._ready_event.is_set():
                    self._ready_event.set()
                logger.info("Discord: connected as %s", getattr(client, "user", "unknown"))

            @client.event
            async def on_message(message):
                await self._on_message(message)

            self._client_task = asyncio.create_task(client.start(self._token))
            await asyncio.wait_for(self._ready_event.wait(), timeout=30)
            return True
        except Exception as exc:
            self._set_fatal(type(exc).__name__, str(exc))
            await self.stop()
            return False

    async def stop(self):
        client = self._client
        task = self._client_task
        self._client = None
        self._client_task = None
        self._ready_event = None

        if client is not None:
            try:
                await client.close()
            except Exception:
                logger.debug("Discord client close failed", exc_info=True)

        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("Discord client task failed during stop", exc_info=True)

    async def send(self, chat_id: str, message: str = "", **kwargs) -> dict:
        content = kwargs.pop("content", None)
        if content is not None:
            message = content
        metadata = kwargs.get("metadata") or {}
        thread_id = metadata.get("thread_id") or kwargs.get("thread_id")

        if not self._token:
            return _SendResult(
                success=False,
                error="Discord bot token is not configured",
            )

        from tools.send_message_tool import _send_discord

        result = await _send_discord(
            self._token,
            str(chat_id),
            str(message or ""),
            thread_id=str(thread_id) if thread_id else None,
            media_files=kwargs.get("media_files"),
        )
        if result.get("success"):
            return _SendResult(success=True, message_id=result.get("message_id"))
        return _SendResult(
            success=False,
            error=result.get("error") or "Discord send failed",
        )

    async def _on_message(self, message: Any) -> None:
        if self._msg_handler is None:
            return
        author = getattr(message, "author", None)
        if getattr(author, "bot", False):
            return

        channel = getattr(message, "channel", None)
        channel_id = str(getattr(channel, "id", "") or "")
        if channel_id in self._ignored_channels:
            return
        if self._allowed_channels and channel_id not in self._allowed_channels:
            return

        content = str(getattr(message, "content", "") or "")
        if self._require_mention and getattr(message, "guild", None) is not None:
            user = getattr(self._client, "user", None)
            mentioned = user is not None and user in getattr(message, "mentions", [])
            if not mentioned:
                return
            mention = f"<@{getattr(user, 'id', '')}>"
            nick_mention = f"<@!{getattr(user, 'id', '')}>"
            content = content.replace(mention, "").replace(nick_mention, "").strip()

        event = MessageEvent(
            platform="discord",
            chat_id=channel_id,
            user_id=str(getattr(author, "id", "") or ""),
            message_id=str(getattr(message, "id", "") or ""),
            text=content,
            thread_id=_thread_id_for_message(message),
            chat_name=str(getattr(channel, "name", "") or ""),
            user_name=str(
                getattr(author, "display_name", "")
                or getattr(author, "name", "")
                or ""
            ),
            chat_type="dm" if getattr(message, "guild", None) is None else "group",
            message_type=MessageType.TEXT,
            raw={
                "guild_id": str(
                    getattr(getattr(message, "guild", None), "id", "") or ""
                )
            },
        )
        await self._msg_handler(event)

    def _set_fatal(self, code: str, message: str) -> None:
        self._fatal_error_code = code
        self._fatal_error_message = message
        logger.error("Discord adapter fatal error (%s): %s", code, message)


def _csv_set(value: str) -> set[str]:
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _thread_id_for_message(message: Any) -> Optional[str]:
    channel = getattr(message, "channel", None)
    if channel is None:
        return None
    parent = getattr(channel, "parent", None)
    if parent is None:
        return None
    channel_id = getattr(channel, "id", None)
    return str(channel_id) if channel_id is not None else None
