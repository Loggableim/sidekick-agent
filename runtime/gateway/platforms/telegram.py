"""Telegram platform adapter — minimal polling with Groq Whisper STT + Fish Audio TTS."""
from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from runtime.gateway.config import PlatformConfig
from runtime.gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
)

logger = logging.getLogger(__name__)

FISH_VOICE_DEFAULT = "d130be0856b3419a8d73b0a94db4a1dc"
FISH_ENDPOINT = "https://api.fish.audio/v1/tts"
FISH_STT_ENDPOINT = "https://api.fish.audio/v1/asr"
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"


def check_telegram_requirements() -> bool:
    try:
        import telegram  # noqa: F401
        return True
    except ImportError:
        return False


class TelegramAdapter(BasePlatformAdapter):
    """Telegram Bot adapter using python-telegram-bot (polling)."""

    MAX_MESSAGE_LENGTH = 4096

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
        self._voice_reply_chats: dict[str, float] = {}
        self._last_connect_error: str = ""

        allowed = os.getenv("TELEGRAM_ALLOWED_USERS", "")
        if allowed:
            self._allowed = set(u.strip() for u in allowed.split(",") if u.strip())

    @property
    def fatal_error_retryable(self) -> bool:
        """InvalidToken errors are permanent; don't retry."""
        return "InvalidToken" not in self._last_connect_error

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
            self._last_connect_error = type(e).__name__
            self._fatal_error_code = type(e).__name__
            self._fatal_error_message = str(e)
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
            send_kwargs = self._telegram_send_kwargs(kwargs)
            msg = await self._app.bot.send_message(chat_id=chat_id, text=message, **send_kwargs)
            await self._send_pending_voice_reply(str(chat_id), message)
            return {"success": True, "message_id": str(msg.message_id)}
        except Exception as e:
            logger.warning(f"Telegram send: {e}")
            return {"success": False}

    async def send_voice(self, chat_id: str, audio_path: str, **kwargs) -> dict:
        if not self._app:
            return {"success": False}
        try:
            send_kwargs = self._telegram_send_kwargs(kwargs)
            with open(audio_path, "rb") as f:
                msg = await self._app.bot.send_voice(chat_id=chat_id, voice=f, **send_kwargs)
            return {"success": True, "message_id": str(msg.message_id)}
        except Exception as e:
            logger.error(f"send_voice: {e}")
            if Path(audio_path).suffix.lower() != ".ogg" and hasattr(self._app.bot, "send_audio"):
                try:
                    with open(audio_path, "rb") as f:
                        msg = await self._app.bot.send_audio(
                            chat_id=chat_id,
                            audio=f,
                            **self._telegram_send_kwargs(kwargs),
                        )
                    return {"success": True, "message_id": str(msg.message_id)}
                except Exception as audio_exc:
                    logger.error(f"send_audio fallback: {audio_exc}")
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
            is_voice_message = bool(update.message.voice)
            media = update.message.voice or update.message.audio
            file = await media.get_file()
            bio = io.BytesIO()
            await file.download_to_memory(bio)
            audio_bytes = bio.getvalue()

            mime_type = getattr(media, "mime_type", None) or "audio/ogg"
            text = await self._transcribe(audio_bytes, mime_type=mime_type)
            if not text:
                await self._app.bot.send_message(chat_id=chat_id, text="🎤 Konnte nicht transkribieren.")
                return

            self._voice_reply_chats[chat_id] = time.time() + 300
            message_type = MessageType.VOICE if is_voice_message else MessageType.AUDIO
            raw_key = "voice" if is_voice_message else "audio"
            await self._dispatch(update, text, message_type=message_type,
                                 raw={raw_key: True, "transcribed": text})
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
    _whisper_model = None  # cache WhisperModel so we don't reload per call

    def _get_whisper_model(self):
        if self._whisper_model is None:
            from faster_whisper import WhisperModel
            # Try GPU (Vulkan/CUDA) first, fall back to CPU
            try:
                self._whisper_model = WhisperModel(
                    "large-v3-turbo", device="cuda", compute_type="float16"
                )
                logger.info("Whisper STT loaded on CUDA")
            except Exception:
                try:
                    self._whisper_model = WhisperModel(
                        "large-v3-turbo", device="cpu", compute_type="int8"
                    )
                    logger.info("Whisper STT loaded on CPU (int8)")
                except Exception as e:
                    logger.error(f"Whisper STT model load failed: {e}")
                    self._whisper_model = False  # mark unavailable
        return self._whisper_model if self._whisper_model else None

    async def _transcribe(self, audio_bytes: bytes, mime_type: str = "audio/ogg") -> Optional[str]:
        # Local Whisper first — fastest, no codec issues, accepts OGG Opus directly.
        local_model = self._get_whisper_model()
        if local_model:
            text = await self._transcribe_local(audio_bytes, local_model)
            if text:
                return text
        if self._fish_audio_key():
            text = await self._transcribe_fish_audio(audio_bytes, mime_type=mime_type)
            if text:
                return text
        key = os.getenv("GROQ_API_KEY", "")
        if key:
            return await self._transcribe_groq(audio_bytes, key)
        return None  # no providers left

    async def _transcribe_fish_audio(self, audio_bytes: bytes, mime_type: str = "audio/ogg") -> Optional[str]:
        key = self._fish_audio_key()
        if not key:
            return None
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60) as c:
                files = {"audio": ("telegram-voice.ogg", audio_bytes, mime_type or "audio/ogg")}
                data = {"language": "de", "ignore_timestamps": "true"}
                r = await c.post(FISH_STT_ENDPOINT, headers={"Authorization": f"Bearer {key}"}, data=data, files=files)
                if r.status_code == 200:
                    return " ".join(str(r.json().get("text") or "").split()) or None
                logger.error(f"Fish STT {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"Fish STT: {e}")
        return None

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

    async def _transcribe_local(self, audio_bytes: bytes, model) -> Optional[str]:
        try:
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                tmp.write(audio_bytes)
                p = tmp.name
            try:
                # faster_whisper handles OGG Opus natively via ffmpeg bindings.
                segs, _ = model.transcribe(p, language="de", beam_size=1)
                return " ".join(s.text for s in segs).strip() or None
            finally:
                os.unlink(p)
        except Exception as e:
            logger.error(f"local whisper: {e}")
            return None

    # ── TTS ─────────────────────────────────────────────
    async def _tts(self, text: str) -> Optional[str]:
        key = self._fish_audio_key()
        if not key:
            return None
        try:
            import httpx
            audio_dir = Path(os.getenv("SIDEKICK_HOME", "C:/sidekick/home")) / "audio_cache"
            audio_dir.mkdir(parents=True, exist_ok=True)
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(FISH_ENDPOINT,
                    headers={"Authorization": f"Bearer {key}", "model": "s2-pro"},
                    json=self._fish_tts_payload(text))
                if r.status_code == 200:
                    p = audio_dir / f"telegram_tts_{uuid.uuid4().hex[:12]}.mp3"
                    with open(p, "wb") as f:
                        f.write(r.content)
                    return str(self._convert_to_ogg_opus(p) or p)
                logger.error(f"Fish TTS {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"Fish TTS: {e}")
        return None

    # ── Helpers ─────────────────────────────────────────
    def _fish_tts_payload(self, text: str) -> dict[str, Any]:
        spoken = re.sub(r"\bCid\b", "Sidd", str(text or ""), flags=re.IGNORECASE)
        return {
            "text": spoken,
            "reference_id": FISH_VOICE_DEFAULT,
            "temperature": 0.7,
            "top_p": 0.7,
            "prosody": {"speed": 1.0, "volume": 0.0, "normalize_loudness": True},
            "chunk_length": 300,
            "normalize": True,
            "format": "mp3",
            "sample_rate": 44100,
            "mp3_bitrate": 128,
            "latency": "normal",
            "max_new_tokens": 1024,
            "repetition_penalty": 1.2,
            "min_chunk_length": 50,
            "condition_on_previous_chunks": True,
        }

    def _convert_to_ogg_opus(self, mp3_path: Path) -> Optional[Path]:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            out = mp3_path.with_suffix(".ogg")
            proc = subprocess.run(
                [ffmpeg, "-y", "-i", str(mp3_path), "-c:a", "libopus", "-b:a", "48k", str(out)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=45,
                check=False,
            )
            if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
                return out
        except Exception as e:
            logger.debug("Fish TTS opus conversion failed: %s", e)
        return None

    async def _send_pending_voice_reply(self, chat_id: str, message: str) -> None:
        deadline = self._voice_reply_chats.get(str(chat_id), 0)
        if not deadline:
            return
        if deadline < time.time():
            self._voice_reply_chats.pop(str(chat_id), None)
            return
        self._voice_reply_chats.pop(str(chat_id), None)
        audio_path = await self._tts(message)
        if not audio_path:
            return
        await self.send_voice(chat_id, audio_path)

    def _telegram_send_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        reply_to = kwargs.get("reply_to")
        if reply_to:
            try:
                out["reply_to_message_id"] = int(reply_to)
            except Exception:
                pass
        metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
        thread_id = metadata.get("thread_id") if metadata else None
        if thread_id:
            try:
                out["message_thread_id"] = int(thread_id)
            except Exception:
                pass
        return out

    def _fish_audio_key(self) -> str:
        for name in ("FISHAUDIO_API_KEY", "FISH_AUDIO_API_KEY"):
            value = os.getenv(name, "").strip()
            if value:
                return value
        extra = getattr(self.config, "extra", {}) or {}
        for key in ("FISHAUDIO_API_KEY", "FISH_AUDIO_API_KEY", "fish_audio_api_key", "fish_api_key"):
            value = str(extra.get(key) or "").strip()
            if value:
                return value
        for path in (
            Path(os.getenv("SIDEKICK_HOME", "C:/sidekick/home")) / "auth.json",
            Path("C:/HermesPortable/home/auth.json"),
        ):
            value = self._read_fish_key_from_auth(path)
            if value:
                return value
        return ""

    def _read_fish_key_from_auth(self, path: Path) -> str:
        try:
            if not path.exists():
                return ""
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ""

        wanted = {"fishaudio_api_key", "fish_audio_api_key", "fish_api_key", "api_key", "token"}

        def walk(node: Any, parent: str = "") -> str:
            if isinstance(node, dict):
                parent_l = parent.lower()
                for key, value in node.items():
                    key_l = str(key).lower()
                    if isinstance(value, str) and value.strip():
                        if "fish" in parent_l and key_l in wanted:
                            return value.strip()
                        if key_l in {"fishaudio_api_key", "fish_audio_api_key", "fish_api_key"}:
                            return value.strip()
                    found = walk(value, key_l)
                    if found:
                        return found
            elif isinstance(node, list):
                for item in node:
                    found = walk(item, parent)
                    if found:
                        return found
            return ""

        return walk(data)

    def _check(self, update) -> bool:
        if not self._allowed:
            return True
        return str(update.effective_user.id) in self._allowed

    @property
    def has_fatal_error(self) -> bool:
        return bool(self._last_connect_error or self.fatal_error_code or self.fatal_error_message)

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
