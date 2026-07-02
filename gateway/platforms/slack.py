"""Slack platform adapter."""
from __future__ import annotations

import logging
import os
from typing import Any

from runtime.gateway.platforms.base import BasePlatformAdapter

logger = logging.getLogger(__name__)


class _SendResult(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def check_slack_requirements() -> bool:
    try:
        import aiohttp  # noqa: F401
        return True
    except ImportError:
        return False


class SlackAdapter(BasePlatformAdapter):
    """Slack adapter with REST send support."""

    MAX_MESSAGE_LENGTH = 40000

    def __init__(self, config: Any = None):
        super().__init__(config)
        self._token = (
            getattr(config, "token", None)
            or os.getenv("SLACK_BOT_TOKEN", "")
        ).strip()
        self._msg_handler = None
        self._fatal_handler = None
        self._session_store = None
        self._busy_handler = None

    @property
    def fatal_error_retryable(self) -> bool:
        return str(getattr(self, "_fatal_error_code", "") or "") != "MissingToken"

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
            self._set_fatal("MissingToken", "Slack bot token is not configured")
            return False
        return True

    async def stop(self):
        return None

    def format_message(self, message: str) -> str:
        return str(message or "")

    async def send(self, chat_id: str, message: str = "", **kwargs) -> dict:
        content = kwargs.pop("content", None)
        if content is not None:
            message = content

        if not self._token:
            return _SendResult(
                success=False,
                error="Slack bot token is not configured",
            )

        from tools.send_message_tool import _send_slack

        result = await _send_slack(self._token, str(chat_id), self.format_message(message))
        if result.get("success"):
            return _SendResult(success=True, message_id=result.get("message_id"))
        return _SendResult(
            success=False,
            error=result.get("error") or "Slack send failed",
        )

    def _set_fatal(self, code: str, message: str) -> None:
        self._fatal_error_code = code
        self._fatal_error_message = message
        logger.error("Slack adapter fatal error (%s): %s", code, message)
