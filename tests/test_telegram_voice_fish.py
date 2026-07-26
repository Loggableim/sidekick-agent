import asyncio
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

from runtime.gateway.config import PlatformConfig
from runtime.gateway.platforms.telegram import TelegramAdapter
from runtime.gateway.platforms.base import BasePlatformAdapter, MessageType, should_send_media_as_audio


def test_telegram_transcribe_prefers_fish_audio(monkeypatch):
    adapter = TelegramAdapter(PlatformConfig(token="telegram-token"))
    calls = []

    async def fake_fish(audio_bytes, mime_type="audio/ogg"):
        calls.append(("fish", audio_bytes, mime_type))
        return "hallo nova"

    async def fake_groq(audio_bytes, key):
        calls.append(("groq", audio_bytes, key))
        return "groq"

    monkeypatch.setenv("FISHAUDIO_API_KEY", "fish-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    adapter._transcribe_fish_audio = fake_fish
    adapter._transcribe_groq = fake_groq

    text = asyncio.run(adapter._transcribe(b"audio", mime_type="audio/ogg"))

    assert text == "hallo nova"
    assert calls == [("fish", b"audio", "audio/ogg")]


def test_telegram_fish_tts_payload_uses_hub_voice_reference_id():
    adapter = TelegramAdapter(PlatformConfig(token="telegram-token"))

    payload = adapter._fish_tts_payload("Hallo Cid")

    assert payload["reference_id"] == "d130be0856b3419a8d73b0a94db4a1dc"
    assert payload["text"] == "Hallo Sidd"
    assert "voice_id" not in payload


def test_telegram_fish_audio_key_uses_sidekick_home(monkeypatch, tmp_path):
    adapter = TelegramAdapter(PlatformConfig(token="telegram-token"))
    sidekick_home = tmp_path / "sidekick-home"
    seen_paths = []

    monkeypatch.setenv("SIDEKICK_HOME", str(sidekick_home))
    monkeypatch.delenv("FISHAUDIO_API_KEY", raising=False)
    monkeypatch.delenv("FISH_AUDIO_API_KEY", raising=False)

    def fake_read(path):
        seen_paths.append(path)
        return "fish-key"

    monkeypatch.setattr(adapter, "_read_fish_key_from_auth", fake_read)

    assert adapter._fish_audio_key() == "fish-key"
    assert seen_paths == [sidekick_home / "auth.json"]


def test_telegram_send_adds_voice_reply_after_voice_input(tmp_path):
    class FakeBot:
        def __init__(self):
            self.messages = []
            self.voices = []

        async def send_message(self, chat_id, text, **kwargs):
            self.messages.append((str(chat_id), text, kwargs))
            return SimpleNamespace(message_id=11)

        async def send_voice(self, chat_id, voice, **kwargs):
            self.voices.append((str(chat_id), voice.read(), kwargs))
            return SimpleNamespace(message_id=12)

    async def run_case():
        adapter = TelegramAdapter(PlatformConfig(token="telegram-token"))
        bot = FakeBot()
        adapter._app = SimpleNamespace(bot=bot)
        audio_path = tmp_path / "reply.ogg"
        audio_path.write_bytes(b"opus")

        async def fake_tts(text):
            return str(audio_path)

        adapter._tts = fake_tts
        adapter._voice_reply_chats["42"] = time.time() + 30

        result = await adapter.send("42", "Antwort")

        assert result["success"] is True
        assert bot.messages == [("42", "Antwort", {})]
        assert bot.voices == [("42", b"opus", {})]
        assert "42" not in adapter._voice_reply_chats

    asyncio.run(run_case())


def test_telegram_voice_update_transcribes_and_dispatches_voice_event():
    class FakeFile:
        async def download_to_memory(self, bio):
            bio.write(b"telegram-audio")

    class FakeMedia:
        mime_type = "audio/ogg"

        async def get_file(self):
            return FakeFile()

    async def run_case():
        adapter = TelegramAdapter(PlatformConfig(token="telegram-token"))
        events = []

        async def fake_handler(event):
            events.append(event)

        async def fake_transcribe(audio_bytes, mime_type="audio/ogg"):
            assert audio_bytes == b"telegram-audio"
            assert mime_type == "audio/ogg"
            return "hey nova was ist offen"

        adapter.set_message_handler(fake_handler)
        adapter._transcribe = fake_transcribe
        adapter._app = SimpleNamespace(bot=SimpleNamespace(send_message=None))
        adapter._allowed = set()

        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=42),
            effective_user=SimpleNamespace(id=7),
            message=SimpleNamespace(
                message_id=99,
                voice=FakeMedia(),
                audio=None,
            ),
        )

        await adapter._on_voice(update, SimpleNamespace())

        assert len(events) == 1
        assert events[0].platform == "telegram"
        assert events[0].chat_id == "42"
        assert events[0].user_id == "7"
        assert events[0].message_id == "99"
        assert events[0].text == "hey nova was ist offen"
        assert events[0].message_type == MessageType.VOICE
        assert events[0].raw == {"voice": True, "transcribed": "hey nova was ist offen"}
        assert adapter._voice_reply_chats["42"] > time.time()

    asyncio.run(run_case())


def test_telegram_audio_update_transcribes_and_dispatches_audio_event():
    class FakeFile:
        async def download_to_memory(self, bio):
            bio.write(b"telegram-audio-file")

    class FakeAudio:
        mime_type = "audio/mpeg"

        async def get_file(self):
            return FakeFile()

    async def run_case():
        adapter = TelegramAdapter(PlatformConfig(token="telegram-token"))
        events = []

        async def fake_handler(event):
            events.append(event)

        async def fake_transcribe(audio_bytes, mime_type="audio/ogg"):
            assert audio_bytes == b"telegram-audio-file"
            assert mime_type == "audio/mpeg"
            return "audio datei verstanden"

        adapter.set_message_handler(fake_handler)
        adapter._transcribe = fake_transcribe
        adapter._app = SimpleNamespace(bot=SimpleNamespace(send_message=None))
        adapter._allowed = set()

        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=42),
            effective_user=SimpleNamespace(id=7),
            message=SimpleNamespace(
                message_id=100,
                voice=None,
                audio=FakeAudio(),
            ),
        )

        await adapter._on_voice(update, SimpleNamespace())

        assert len(events) == 1
        assert events[0].message_type == MessageType.AUDIO
        assert events[0].raw == {"audio": True, "transcribed": "audio datei verstanden"}
        assert adapter._voice_reply_chats["42"] > time.time()

    asyncio.run(run_case())


def test_telegram_media_routing_sends_ogg_voice_directive_as_voice(tmp_path):
    audio_path = tmp_path / "reply.ogg"
    audio_path.write_bytes(b"opus")

    assert should_send_media_as_audio("telegram", ".ogg", is_voice=True) is True
    assert should_send_media_as_audio("telegram", ".mp3", is_voice=False) is True
    assert should_send_media_as_audio("discord", ".mp3", is_voice=False) is False


def test_telegram_adapter_extracts_audio_as_voice_media(tmp_path):
    audio_path = tmp_path / "reply.ogg"
    audio_path.write_bytes(b"opus")
    adapter = TelegramAdapter(PlatformConfig(token="telegram-token"))

    media, cleaned = adapter.extract_media(f"Antwort\n[[audio_as_voice]]\nMEDIA:{audio_path}")

    assert media == [(str(audio_path), True)]
    assert "MEDIA:" not in cleaned
    assert "[[audio_as_voice]]" not in cleaned


def test_telegram_send_voice_falls_back_to_send_audio_for_mp3(tmp_path):
    class FakeBot:
        def __init__(self):
            self.audio = []
            self.voice = []

        async def send_voice(self, chat_id, voice, **kwargs):
            self.voice.append((str(chat_id), voice.read(), kwargs))
            raise RuntimeError("Telegram voice requires opus")

        async def send_audio(self, chat_id, audio, **kwargs):
            self.audio.append((str(chat_id), audio.read(), kwargs))
            return SimpleNamespace(message_id=33)

    async def run_case():
        adapter = TelegramAdapter(PlatformConfig(token="telegram-token"))
        bot = FakeBot()
        adapter._app = SimpleNamespace(bot=bot)
        audio = tmp_path / "reply.mp3"
        audio.write_bytes(b"mp3")

        result = await adapter.send_voice("42", str(audio), reply_to="99")

        assert result == {"success": True, "message_id": "33"}
        assert bot.voice == [("42", b"mp3", {"reply_to_message_id": 99})]
        assert bot.audio == [("42", b"mp3", {"reply_to_message_id": 99})]

    asyncio.run(run_case())


def test_telegram_local_stt_fallback_uses_large_v3_turbo():
    source = Path("runtime/gateway/platforms/telegram.py").read_text(encoding="utf-8")

    assert 'WhisperModel("large-v3-turbo"' in source
    assert 'WhisperModel("base"' not in source


def test_telegram_dependency_is_available_from_project_extras():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    optional = data["project"]["optional-dependencies"]

    assert any(dep.startswith("python-telegram-bot") for dep in optional["telegram"])
    assert any(dep.startswith("python-telegram-bot") for dep in optional["all"])


def test_gateway_adapter_fatal_error_contract_is_boolean_property():
    base = BasePlatformAdapter()
    telegram = TelegramAdapter(PlatformConfig(token="telegram-token"))

    assert base.has_fatal_error is False
    assert telegram.has_fatal_error is False

    telegram._last_connect_error = "InvalidToken"
    telegram._fatal_error_code = "InvalidToken"
    telegram._fatal_error_message = "The token `8721757657:***` was rejected by the server."

    assert telegram.has_fatal_error is True
    assert telegram.fatal_error_retryable is False
    assert telegram.fatal_error_code == "InvalidToken"
    assert "rejected" in telegram.fatal_error_message


def test_telegram_tls_verify_source_prefers_ssl_cert_file(monkeypatch, tmp_path):
    from runtime.gateway.platforms import telegram as telegram_mod

    cert_file = tmp_path / "custom-ca.pem"
    cert_file.write_text("dummy", encoding="utf-8")
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_file))

    verify_source = telegram_mod._telegram_tls_verify_source()

    assert verify_source == str(cert_file)


def test_telegram_tls_verify_source_falls_back_to_certifi(monkeypatch):
    from runtime.gateway.platforms import telegram as telegram_mod
    import certifi

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    verify_source = telegram_mod._telegram_tls_verify_source()

    assert verify_source == certifi.where()


def test_telegram_connect_uses_explicit_ca_bundle_for_polling_requests(monkeypatch, tmp_path):
    from runtime.gateway.platforms import telegram as telegram_mod

    cert_file = tmp_path / "custom-ca.pem"
    cert_file.write_text("dummy", encoding="utf-8")
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_file))

    request_calls = []

    class FakeRequest:
        def __init__(self, *args, **kwargs):
            request_calls.append({"args": args, "kwargs": kwargs})

    class FakeUpdater:
        def __init__(self):
            self.started = False

        async def start_polling(self):
            self.started = True

    class FakeApp:
        def __init__(self, builder_state):
            self.builder_state = builder_state
            self.handlers = []
            self.updater = FakeUpdater()
            self.initialized = False
            self.started = False

        def add_handler(self, handler):
            self.handlers.append(handler)

        async def initialize(self):
            self.initialized = True

        async def start(self):
            self.started = True

        async def stop(self):
            return None

        async def shutdown(self):
            return None

    class FakeBuilder:
        def __init__(self):
            self.state = {"token": None, "request": None, "get_updates_request": None}

        def token(self, token):
            self.state["token"] = token
            return self

        def request(self, request):
            self.state["request"] = request
            return self

        def get_updates_request(self, request):
            self.state["get_updates_request"] = request
            return self

        def build(self):
            return FakeApp(self.state)

    class FakeApplication:
        @staticmethod
        def builder():
            return FakeBuilder()

    monkeypatch.setattr(telegram_mod, "HTTPXRequest", FakeRequest)
    monkeypatch.setattr(telegram_mod, "Application", FakeApplication, raising=False)

    async def run_case():
        adapter = TelegramAdapter(PlatformConfig(token="telegram-token"))
        import telegram.ext as telegram_ext
        monkeypatch.setattr(telegram_ext, "Application", FakeApplication)

        result = await adapter.connect()
        app = adapter._app

        assert result is True
        assert app.initialized is True
        assert app.started is True
        assert app.updater.started is True
        assert len(app.handlers) == 3
        assert app.builder_state["token"] == "telegram-token"
        assert app.builder_state["request"] is not app.builder_state["get_updates_request"]
        assert request_calls[0]["kwargs"]["httpx_kwargs"]["verify"] == str(cert_file)
        assert request_calls[1]["kwargs"]["httpx_kwargs"]["verify"] == str(cert_file)
        assert request_calls[0]["kwargs"]["connection_pool_size"] == 256
        assert request_calls[1]["kwargs"]["connection_pool_size"] == 1

    asyncio.run(run_case())
