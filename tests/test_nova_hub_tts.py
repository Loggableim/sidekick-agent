from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path
import asyncio


NOVA_DIR = Path(r"C:\sidekick\home\spaces\nova")
HUB_PATH = NOVA_DIR / "hub.py"
HUB_SPEAK_PATH = NOVA_DIR / "hub_speak.py"
DASHBOARD_PATH = Path(r"C:\HermesPortable\home\cockpit\dashboard_server.py")
COCKPIT_DIR = DASHBOARD_PATH.parent
VOICE_ID = "d130be0856b3419a8d73b0a94db4a1dc"


def load_hub_module():
    spec = importlib.util.spec_from_file_location("nova_hub_under_test", HUB_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_hub_speak_module():
    if str(NOVA_DIR) not in sys.path:
        sys.path.insert(0, str(NOVA_DIR))
    spec = importlib.util.spec_from_file_location("nova_hub_speak_under_test", HUB_SPEAK_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_dashboard_module():
    if str(COCKPIT_DIR) not in sys.path:
        sys.path.insert(0, str(COCKPIT_DIR))
    spec = importlib.util.spec_from_file_location("nova_dashboard_tts_under_test", DASHBOARD_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fish_tts_payload_uses_reference_id_for_voice_model():
    hub = load_hub_module()

    payload = hub.build_fish_tts_payload(
        "Hallo Nova",
        {"voice_id": VOICE_ID},
        {"speed": 1.08, "volume": 3, "latency": "balanced", "chunk_length": 200, "min_chunk_length": 100},
        "neutral",
    )

    assert payload["text"] == "Hallo Nova"
    assert payload["reference_id"] == VOICE_ID
    assert payload["format"] == "mp3"
    assert payload["prosody"]["speed"] == 1.08
    assert payload["prosody"]["volume"] == 3
    assert payload["latency"] == "balanced"
    assert payload["chunk_length"] == 200
    assert payload["min_chunk_length"] == 100
    assert "voice_id" not in payload


def test_fish_tts_payload_defaults_to_streaming_latency():
    hub = load_hub_module()

    payload = hub.build_fish_tts_payload("Hallo", {"voice_id": VOICE_ID}, {}, "neutral")

    assert payload["latency"] == "balanced"
    assert payload["chunk_length"] == 200
    assert payload["min_chunk_length"] == 100


def test_fish_tts_payload_uses_pronunciation_text_for_cid():
    hub = load_hub_module()

    payload = hub.build_fish_tts_payload(
        "Hallo Cid, CID bleibt sichtbar. Acid bleibt Acid.",
        {"voice_id": VOICE_ID},
        {},
        "neutral",
    )

    assert payload["text"] == "Hallo Sidd, Sidd bleibt sichtbar. Acid bleibt Acid."


def test_nova_hub_uses_requested_fish_audio_voice():
    hub = load_hub_module()

    assert hub.load_config()["voice_id"] == VOICE_ID


def test_nova_hub_audio_cache_is_dashboard_static_dir():
    hub = load_hub_module()
    config = hub.load_config()
    dashboard_targets = [target for target in config["targets"] if target.get("type") == "dashboard"]

    assert config["audio_cache"] == r"C:\HermesPortable\home\cockpit\tts_audio"
    assert dashboard_targets
    assert dashboard_targets[0]["url"] == "http://192.168.1.110:8765/api/nova/say"


def test_dashboard_generation_entrypoint_exists():
    assert (NOVA_DIR / "hub_speak.py").exists()


def test_hub_speak_streams_audio_from_dashboard_static_mount(monkeypatch, tmp_path):
    hub_speak = load_hub_speak_module()

    monkeypatch.setenv("NOVA_HUB_TTS_AUDIO_DIR", str(tmp_path))
    monkeypatch.setenv("NOVA_HUB_PUBLIC_BASE_URL", "http://hub.example:8765/")

    output = hub_speak._default_output_path()

    assert output.parent == tmp_path
    assert output.name.startswith("nova_hub_")
    assert hub_speak._hub_audio_url(output) == f"http://hub.example:8765/tts-audio/{output.name}"


def test_hub_speak_json_path_does_not_start_audio_miniserver():
    source = HUB_SPEAK_PATH.read_text(encoding="utf-8")

    assert "_ensure_audio_server" not in source
    assert "8866" not in source


def test_hub_cast_uses_dashboard_static_audio_url(monkeypatch, tmp_path):
    hub = load_hub_module()
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"audio")

    monkeypatch.setattr(
        hub,
        "load_config",
        lambda: {
            "audio_cache": str(tmp_path),
            "audio_server": {"public_host": "192.168.1.110"},
        },
    )
    monkeypatch.setenv("NOVA_HUB_PUBLIC_BASE_URL", "http://hub.local:8765")

    assert hub._hub_audio_url(audio) == "http://hub.local:8765/tts-audio/voice.mp3"
    assert "_ensure_audio_server" not in inspect.getsource(hub._cast_chromecast_oneshot)


def test_hub_notify_dashboard_sends_playable_audio_url(monkeypatch, tmp_path):
    hub = load_hub_module()
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"audio")
    captured = {}

    monkeypatch.setattr(hub, "load_config", lambda: {"audio_cache": str(tmp_path)})
    monkeypatch.setenv("NOVA_HUB_PUBLIC_BASE_URL", "http://hub.local:8765")

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(hub.urllib.request, "urlopen", fake_urlopen)

    target = hub.HubTarget(type="dashboard", url="http://dashboard/api/nova/say")

    assert hub._notify_dashboard(target, audio, "Hallo") is True
    assert captured["payload"]["audio_path"] == str(audio)
    assert captured["payload"]["audio_url"] == "http://hub.local:8765/tts-audio/voice.mp3"


def test_hub_cast_status_rejects_chrome_mirroring_media():
    hub = load_hub_module()

    class Status:
        player_state = "PLAYING"
        content_id = ""
        content_type = "video/webm"

    assert hub._cast_media_status_matches_url(Status(), "http://hub/voice.mp3") is False


def test_hub_cast_status_accepts_requested_audio():
    hub = load_hub_module()

    class Status:
        player_state = "PLAYING"
        content_id = "http://hub/voice.mp3"
        content_type = "audio/mpeg"

    assert hub._cast_media_status_matches_url(Status(), "http://hub/voice.mp3") is True


def test_hub_cast_status_rejects_idle_requested_audio_without_active_playback():
    hub = load_hub_module()

    class Status:
        player_state = "IDLE"
        content_id = "http://hub/voice.mp3"
        content_type = "audio/mpeg"

    assert hub._cast_media_status_matches_url(Status(), "http://hub/voice.mp3") is False


def test_hub_cast_status_rejects_paused_requested_audio_without_active_playback():
    hub = load_hub_module()

    class Status:
        player_state = "PAUSED"
        content_id = "http://hub/voice.mp3"
        content_type = "audio/mpeg"

    assert hub._cast_media_status_matches_url(Status(), "http://hub/voice.mp3") is False


def test_hub_cast_quits_existing_mirroring_app_before_playback(monkeypatch, tmp_path):
    from types import SimpleNamespace

    hub = load_hub_module()
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"audio" * 40)
    played = {}

    class FakeMediaStatus:
        player_state = "PLAYING"
        content_type = "audio/mpeg"
        content_id = "http://hub.local:8765/tts-audio/voice.mp3"

    class FakeMediaController:
        status = FakeMediaStatus()

        def play_media(self, url, content_type, title=None, thumb=None):
            played["url"] = url
            played["content_type"] = content_type
            played["title"] = title

        def block_until_active(self, timeout=None):
            played["block_timeout"] = timeout

        def update_status(self):
            played["updates"] = played.get("updates", 0) + 1

    class FakeCast:
        cast_info = SimpleNamespace(host="192.168.1.37", friendly_name="Hub")

        def __init__(self):
            self.status = SimpleNamespace(display_name="Chrome Mirroring")
            self.media_controller = FakeMediaController()
            self.quit_calls = 0

        def wait(self, timeout=None):
            played["wait_timeout"] = timeout

        def quit_app(self):
            self.quit_calls += 1
            self.status.display_name = None

    fake_cast = FakeCast()

    class FakeZeroconf:
        def close(self):
            played["closed"] = True

    monkeypatch.setitem(sys.modules, "pychromecast", SimpleNamespace(get_chromecasts=lambda **kwargs: ([fake_cast], None)))
    monkeypatch.setitem(sys.modules, "zeroconf", SimpleNamespace(Zeroconf=lambda: FakeZeroconf()))
    monkeypatch.setattr(hub, "load_config", lambda: {"audio_cache": str(tmp_path)})
    monkeypatch.setenv("NOVA_HUB_PUBLIC_BASE_URL", "http://hub.local:8765")

    ok = hub._cast_chromecast_oneshot(
        hub.HubTarget(type="chromecast", ip="192.168.1.37", enabled=True),
        audio,
        "Hallo",
        {"cast_timeout": 3},
    )

    assert ok is True
    assert fake_cast.quit_calls == 1
    assert played["url"] == "http://hub.local:8765/tts-audio/voice.mp3"
    assert played["content_type"] == "audio/mpeg"
    assert played["closed"] is True


def test_dashboard_nova_say_broadcasts_existing_hub_audio(monkeypatch, tmp_path):
    dashboard = load_dashboard_module()
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"audio")
    broadcasts = []

    dashboard.TTS_AUDIO_DIR = tmp_path
    dashboard.PUBLIC_BASE_URL = "http://hub.local:8765"
    monkeypatch.setattr(
        dashboard,
        "_broadcast_tts_play",
        lambda url, source, streaming=False: broadcasts.append((url, source, streaming)) or 1,
    )

    result = asyncio.run(dashboard.api_nova_say({
        "message": "Hallo",
        "audio_path": str(audio),
        "source": "nova-hub",
    }))

    assert result["tts"] == {
        "ok": True,
        "url": "http://hub.local:8765/tts-audio/voice.mp3",
        "clients": 1,
        "streaming": False,
    }
    assert broadcasts == [("http://hub.local:8765/tts-audio/voice.mp3", "nova-hub", False)]


def test_dashboard_tts_starts_live_stream_before_file_is_complete(monkeypatch, tmp_path):
    dashboard = load_dashboard_module()
    dashboard.TTS_AUDIO_DIR = tmp_path
    dashboard.PUBLIC_BASE_URL = "http://hub.local:8765"
    broadcasts = []

    async def fake_stream(text, output, state, api_key):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"audio" * 40)
        state["first_chunk_at"] = 123.0
        state["bytes"] = output.stat().st_size
        state["ok"] = True
        state["done"] = True

    monkeypatch.setattr(dashboard, "_fish_audio_token", lambda: "fish-key")
    monkeypatch.setattr(dashboard, "_stream_fish_tts_to_file", fake_stream)
    monkeypatch.setattr(dashboard, "_broadcast_tts_play", lambda url, source, streaming=False: broadcasts.append((url, source, streaming)) or 0)

    async def run_case():
        result = dashboard._start_streaming_tts("Hallo Cid", "voice-assistant")
        await asyncio.sleep(0)
        return result

    result = asyncio.run(run_case())

    assert result["ok"] is True
    assert result["streaming"] is True
    assert "/tts-live/" in result["url"]
    assert broadcasts == [(result["url"], "voice-assistant", True)]
    state = dashboard._tts_live_streams[result["name"]]
    assert state["done"] is True
    assert state["bytes"] >= 100


def test_dashboard_msgpack_roundtrip_supports_fish_live_audio_event():
    dashboard = load_dashboard_module()

    payload = {
        "event": "audio",
        "audio": b"mp3-bytes",
        "request": {"text": "", "chunk_length": 200, "latency": "balanced", "normalize": True},
    }

    assert dashboard._msgpack_unpack(dashboard._msgpack_pack(payload)) == payload


def test_dashboard_streams_llm_tokens_into_fish_live_websocket(monkeypatch, tmp_path):
    dashboard = load_dashboard_module()
    sent_events = []

    class FakeHub:
        @staticmethod
        def load_config():
            return {
                "voice_id": VOICE_ID,
                "fish": {"model": "s2-pro", "latency": "balanced", "chunk_length": 200, "min_chunk_length": 100},
            }

        @staticmethod
        def build_fish_tts_payload(text, config, fish_config, style):
            return {
                "text": text,
                "reference_id": config["voice_id"],
                "format": "mp3",
                "latency": fish_config["latency"],
                "chunk_length": fish_config["chunk_length"],
                "min_chunk_length": fish_config["min_chunk_length"],
            }

        @staticmethod
        def normalize_tts_pronunciation(text):
            return text.replace("Cid", "Sidd")

    class FakeFishWebSocket:
        def __init__(self):
            self.queue = asyncio.Queue()

        async def send(self, frame):
            event = dashboard._msgpack_unpack(frame)
            sent_events.append(event)
            if event["event"] == "text":
                await self.queue.put(dashboard._msgpack_pack({"event": "audio", "audio": b"audio:" + event["text"].encode("utf-8")}))
            if event["event"] == "stop":
                await self.queue.put(dashboard._msgpack_pack({"event": "finish", "reason": "stop"}))

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await asyncio.wait_for(self.queue.get(), timeout=1)

    class FakeFishContext:
        def __init__(self):
            self.ws = FakeFishWebSocket()

        async def __aenter__(self):
            return self.ws

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_tokens(messages, attempts):
        for token in ("Hallo ", "Cid. ", "Das kommt tokenweise."):
            yield token

    monkeypatch.setattr(dashboard, "_nova_hub_runtime", lambda: FakeHub)
    monkeypatch.setattr(dashboard, "_voice_llm_attempts", lambda: [{"provider": "test", "model": "stream"}])
    monkeypatch.setattr(dashboard, "_stream_llm_chat_tokens", fake_tokens)
    monkeypatch.setattr(dashboard, "_connect_fish_live_ws", lambda api_key, model: FakeFishContext())

    state = {}
    output = tmp_path / "live.mp3"

    answer = asyncio.run(dashboard._stream_voice_llm_to_fish_tts("Sag hallo", output, state, "fish-key"))

    assert answer == "Hallo Cid. Das kommt tokenweise."
    assert output.read_bytes().startswith(b"audio:")
    assert state["token_streaming"] is True
    assert state["fish_live"] is True
    assert state["llm_done"] is True
    assert sent_events[0]["event"] == "start"
    assert sent_events[0]["request"]["text"] == ""
    text_events = [event["text"] for event in sent_events if event["event"] == "text"]
    assert "Sidd" in "".join(text_events)
    assert any(event["event"] == "flush" for event in sent_events)
    assert sent_events[-1]["event"] == "stop"


def test_voice_ask_uses_token_streaming_tts_for_llm_answers(monkeypatch):
    dashboard = load_dashboard_module()
    calls = []

    async def fake_token_answer(question, source):
        calls.append((question, source))
        return {
            "ok": True,
            "question": question,
            "answer": "Streaming Antwort",
            "tts": {"ok": True, "url": "http://hub/tts-live/test.mp3", "streaming": True, "token_streaming": True},
        }

    async def fail_old_path(question):
        raise AssertionError("_quick_voice_answer must not run for streamable LLM voice answers")

    monkeypatch.setattr(dashboard, "_answer_voice_with_token_tts", fake_token_answer)
    monkeypatch.setattr(dashboard, "_quick_voice_answer", fail_old_path)

    result = asyncio.run(dashboard.api_voice_ask({"question": "wie schnell bist du"}))

    assert result["answer"] == "Streaming Antwort"
    assert result["tts"]["token_streaming"] is True
    assert calls == [("wie schnell bist du", "voice-assistant")]
