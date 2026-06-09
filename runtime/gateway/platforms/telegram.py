"""Telegram platform adapter — minimal polling with Groq Whisper STT + Fish Audio TTS."""
from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
from typing import Any, Callable, Optional

from runtime.gateway.config import PlatformConfig
from runtime.gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
)

logger = logging.getLogger(__name__)

FISH_VOICE_DEFAULT = "83018e49155141d8887f7193b8c1454c"
FISH_ENDPOINT = "https://api.fish.audio/v1/tts"
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"


def check_telegram_requirements() -> bool:
    try:
        import telegram  # noqa: F401
        return True
    except ImportError:
        return False


class TelegramAdapter(BasePlatformAdapter):
    """Telegram Bot adapter using python-telegram-bot (polling)."""

    def __init__(self, config: PlatformConfig):
        super().__init__(config)
        self._token = config.token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._msg_handler: Optional[Callable] = None
        self._fatal_handler: Optional[Callable] = None
        self._busy_handler: Optional[Callable] = None
        self._session_store: Any = None
        self._app: Any = None
        self._allowed: set = set()
        self._notifications_mode = "important"

        allowed = os.getenv("TELEGRAM_ALLOWED_USERS", "")
        if allowed:
            self._allowed = set(u.strip() for u in allowed.split(",") if u.strip())

    def set_message_handler(self, h):
        self._msg_handler = h
    def set_fatal_error_handler(self, h):
        self._fatal_handler = h
    def set_session_store(self, s):
        self._session_store = s
    def set_busy_session_handler(self, h):
        self._busy_handler = h

    async def connect(self) -> bool:
        """Alias for start() - called by the gateway runner."""
        if not self._token:
            logger.error("Telegram: no token")
            return False
        try:
            from telegram.ext import Application, MessageHandler, filters
            self._app = Application.builder().token(self._token).build()
            self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
            self._app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self._on_voice))
            self._app.add_handler(MessageHandler(filters.COMMAND, self._on_cmd))
            logger.info("Telegram: handlers registered, starting polling...")
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling()
            logger.info("Telegram: connected (polling)")
            return True
        except Exception as e:
            logger.error(f"Telegram start failed: {e}", exc_info=True)
            return False

    async def start(self) -> bool:
        return await self.connect()

    async def stop(self):
        if self._app:
            try:
                await self._app.stop()
                await self._app.shutdown()
            except Exception:
                pass
            self._app = None

    async def send(self, chat_id: str, message: str, **kwargs) -> dict:
        if not self._app:
            return {"success": False}
        try:
            msg = await self._app.bot.send_message(chat_id=chat_id, text=message)
            return {"success": True, "message_id": str(msg.message_id)}
        except Exception as e:
            logger.warning(f"Telegram send: {e}")
            return {"success": False}

    async def send_voice(self, chat_id: str, audio_path: str) -> dict:
        if not self._app:
            return {"success": False}
        try:
            with open(audio_path, "rb") as f:
                msg = await self._app.bot.send_voice(chat_id=chat_id, voice=f)
            return {"success": True, "message_id": str(msg.message_id)}
        except Exception as e:
            logger.error(f"send_voice: {e}")
            return {"success": False}

    async def _on_text(self, update, ctx):
        if not self._check(update):
            return
        await self._dispatch(update, update.message.text)

    async def _on_voice(self, update, ctx):
        if not self._check(update):
            return
        chat_id = str(update.effective_chat.id)
        try:
            file = await (update.message.voice or update.message.audio).get_file()
            bio = io.BytesIO()
            await file.download_to_memory(bio)
            audio_bytes = bio.getvalue()

            text = await self._transcribe(audio_bytes)
            if not text:
                await self._app.bot.send_message(chat_id=chat_id, text="🎤 Konnte nicht transkribieren.")
                return

            await self._dispatch(update, text, message_type=MessageType.VOICE,
                                 raw={"voice": True, "transcribed": text})
        except Exception as e:
            logger.error(f"voice handling: {e}")

    async def _on_cmd(self, update, ctx):
        if not self._check(update):
            return
        cmd = update.message.text.strip().split()[0].lower()
        if cmd == "/start":
            await self._app.bot.send_message(
                chat_id=update.effective_chat.id,
                text="👋 Bin online! Text oder Sprachnachricht.",
            )
            return
        await self._on_text(update, ctx)

    # ── Transcribe ──────────────────────────────────────
    async def _transcribe(self, audio_bytes: bytes) -> Optional[str]:
        key = os.getenv("GROQ_API_KEY", "")
        if key:
            return await self._transcribe_groq(audio_bytes, key)
        return await self._transcribe_local(audio_bytes)

    async def _transcribe_groq(self, audio_bytes: bytes, key: str) -> Optional[str]:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60) as c:
                files = {"file": ("audio.ogg", audio_bytes, "audio/ogg"),
                         "model": (None, "whisper-large-v3-turbo"),
                         "response_format": (None, "json"),
                         "language": (None, "de")}
                r = await c.post(GROQ_ENDPOINT, headers={"Authorization": f"Bearer {key}"}, files=files)
                if r.status_code == 200:
                    return r.json().get("text", "").strip()
                logger.error(f"Groq {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"Groq: {e}")
        return None

    async def _transcribe_local(self, audio_bytes: bytes) -> Optional[str]:
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel("base", device="cpu", compute_type="int8")
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                tmp.write(audio_bytes)
                p = tmp.name
            try:
                segs, _ = model.transcribe(p, language="de")
                return " ".join(s.text for s in segs).strip() or None
            finally:
                os.unlink(p)
        except Exception as e:
            logger.error(f"local whisper: {e}")
            return None

    # ── TTS ─────────────────────────────────────────────
    async def _tts(self, text: str) -> Optional[str]:
        key = os.getenv("FISHAUDIO_API_KEY", "")
        if not key:
            return None
        try:
            import httpx
            os.makedirs("/c/HermesPortable/home/audio_cache", exist_ok=True)
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(FISH_ENDPOINT,
                    headers={"Authorization": f"Bearer {key}", "model": "s2-pro"},
                    json={"text": text, "voice_id": FISH_VOICE_DEFAULT})
                if r.status_code == 200:
                    import uuid
                    p = f"/c/HermesPortable/home/audio_cache/tts_{uuid.uuid4().hex[:12]}.mp3"
                    with open(p, "wb") as f:
                        f.write(r.content)
                    return p
                logger.error(f"Fish TTS {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"Fish TTS: {e}")
        return None

    # ── Helpers ─────────────────────────────────────────
    def _check(self, update) -> bool:
        if not self._allowed:
            return True
        return str(update.effective_user.id) in self._allowed

    def has_fatal_error(self) -> bool:
        return False

    async def _dispatch(self, update, text: str, message_type=MessageType.TEXT, raw=None):
        if not self._msg_handler or not text:
            return
        event = MessageEvent(
            platform="telegram",
            chat_id=str(update.effective_chat.id),
            user_id=str(update.effective_user.id),
            message_id=str(update.message.message_id),
            text=text,
            message_type=message_type,
            raw=raw or {},
        )
        await self._msg_handler(event)