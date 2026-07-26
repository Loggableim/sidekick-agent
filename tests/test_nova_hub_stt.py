import importlib.util
import struct
import sys
from pathlib import Path

import os
import pytest

_COCKPIT_ROOT = os.getenv("SIDEKICK_COCKPIT_ROOT", "").strip()
pytestmark = pytest.mark.skipif(
    not _COCKPIT_ROOT,
    reason="external cockpit integration requires SIDEKICK_COCKPIT_ROOT",
)


MODULE_PATH = Path("C:/SidekickPortable/home/cockpit/nova_stt.py")
DASHBOARD_PATH = Path("C:/SidekickPortable/home/cockpit/dashboard_server.py")
COCKPIT_DIR = DASHBOARD_PATH.parent


def load_stt_module():
    spec = importlib.util.spec_from_file_location("nova_stt", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_dashboard_module():
    if str(COCKPIT_DIR) not in sys.path:
        sys.path.insert(0, str(COCKPIT_DIR))
    spec = importlib.util.spec_from_file_location("nova_dashboard_stt_under_test", DASHBOARD_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_wake_word_requires_nova_prefix_before_question():
    stt = load_stt_module()
    session = stt.WakeWordSession()

    events = session.accept_text("hey nova wie spaet ist es")

    assert events == [{"type": "heard", "text": "hey nova wie spaet ist es"}]


def test_wake_word_can_arm_next_utterance():
    stt = load_stt_module()
    session = stt.WakeWordSession()

    first = session.accept_text("hey nova")
    second = session.accept_text("was macht der rechner")

    assert first == [{"type": "heard", "text": "hey nova"}]
    assert second == [{"type": "heard", "text": "was macht der rechner"}]


def test_wake_word_accepts_common_vosk_transcription_variants():
    stt = load_stt_module()

    for phrase in [
        "hey nowa was gibt es neues",
        "hey nora was gibt es neues",
        "hey no war was gibt es neues",
        "hei nova was gibt es neues",
        "hi nowa was gibt es neues",
        "hey no va was gibt es neues",
        "ey nora was gibt es neues",
        "hey nofa was gibt es neues",
    ]:
        session = stt.WakeWordSession()
        events = session.accept_text(phrase)
        assert events == [{"type": "heard", "text": phrase}]


def test_partial_wake_word_combines_short_vosk_fragments():
    stt = load_stt_module()
    now = [100.0]
    session = stt.WakeWordSession(clock=lambda: now[0])

    first = session.accept_partial("hey")
    now[0] += 0.35
    second = session.accept_partial("nowa")

    assert first is None
    assert second == {"type": "wake", "wake_word": "nova"}


def test_wake_word_fuzzy_match_accepts_nova_prefix():
    stt = load_stt_module()
    session = stt.WakeWordSession()

    events = session.accept_text("nova ist ein stern")

    assert events == [
        {"type": "wake", "wake_word": "nova"},
        {"type": "question", "text": "ist ein stern"},
    ]


def test_partial_wake_word_does_not_arm_from_plain_greeting_phrase():
    stt = load_stt_module()
    session = stt.WakeWordSession()

    partial = session.accept_partial("hey nova")
    final = session.accept_text("hey nova was gibt es neues")

    assert partial is None
    assert final == [{"type": "heard", "text": "hey nova was gibt es neues"}]


def test_followup_window_accepts_next_question_without_wake_word():
    stt = load_stt_module()
    now = [100.0]
    session = stt.WakeWordSession(clock=lambda: now[0])

    session.arm_followup(16)
    events = session.accept_text("und wie sieht es mit dem speicher aus")

    assert events == [{"type": "question", "text": "und wie sieht es mit dem speicher aus"}]


def test_default_followup_window_is_eight_seconds():
    stt = load_stt_module()
    now = [100.0]
    session = stt.WakeWordSession(clock=lambda: now[0])

    session.arm_followup()
    now[0] = 108.5
    events = session.accept_text("noch eine frage")

    assert events == [{"type": "heard", "text": "noch eine frage"}]


def test_followup_window_expires_after_timeout():
    stt = load_stt_module()
    now = [100.0]
    session = stt.WakeWordSession(clock=lambda: now[0])

    session.arm_followup(16)
    now[0] = 117.0
    events = session.accept_text("und jetzt")

    assert events == [{"type": "heard", "text": "und jetzt"}]


def test_partial_status_ignores_noise_without_wake_word():
    stt = load_stt_module()
    session = stt.WakeWordSession()

    events = session.accept_text("irgendwas im raum")

    assert events == [{"type": "heard", "text": "irgendwas im raum"}]


def test_build_status_event_reports_missing_engine_cleanly():
    stt = load_stt_module()

    event = stt.build_stt_status_event(False, "Vosk nicht installiert")

    assert event == {
        "type": "stt_status",
        "ok": False,
        "message": "Vosk nicht installiert",
    }


def test_vosk_stream_exposes_reset_for_tts_echo_flush():
    load_stt_module()

    assert "def reset(self)" in MODULE_PATH.read_text(encoding="utf-8")


def test_dashboard_stt_routes_partial_wake_word():
    server = Path("C:/SidekickPortable/home/cockpit/dashboard_server.py").read_text(encoding="utf-8")

    assert "wake_event = session.accept_partial(event[\"text\"])" in server
    assert "\"type\": \"stt_wake\"" in server


def test_dashboard_exposes_selectable_stt_providers():
    server = Path("C:/SidekickPortable/home/cockpit/dashboard_server.py").read_text(encoding="utf-8")

    assert '"whisper-large-v3-turbo"' in server
    assert '"fish-audio"' in server
    assert '"vosk"' in server
    assert '@app.get("/api/stt/settings")' in server
    assert '@app.post("/api/stt/settings")' in server
    assert "https://api.fish.audio/v1/asr" in server
    assert "WhisperModel(\"large-v3-turbo\"" in server
    assert "NamedTemporaryFile(delete=False, suffix=\".wav\")" in server
    assert '"followup_seconds": 8' in server


def test_dashboard_followup_has_hard_idle_timeout():
    server = Path("C:/SidekickPortable/home/cockpit/dashboard_server.py").read_text(encoding="utf-8")
    js = Path("C:/SidekickPortable/home/cockpit/dashboard/app.js").read_text(encoding="utf-8")

    assert "capture_started_at = time.monotonic()" in server
    assert "capture_until = capture_started_at + seconds" in server
    assert "now > capture_until" in server
    assert '"type": "stt_idle"' in server
    assert "session.disarm()" in server
    assert "capture_chunks = []" in server
    assert "else if(ev.type==='stt_idle')" in js
    assert "commHide(0)" in js


def test_followup_capture_detects_pcm_voice_signal():
    dashboard = load_dashboard_module()
    silence = b"\x00\x00" * 80
    voiced = struct.pack("<" + "h" * 80, *([6000] * 80))

    assert dashboard._pcm16_rms(silence) == 0
    assert dashboard._pcm16_has_voice(voiced)
    assert not dashboard._pcm16_has_voice(silence)


def test_followup_capture_finalizes_after_speech_silence():
    dashboard = load_dashboard_module()

    assert dashboard._capture_should_finalize(
        now=11.05,
        capture_until=18.0,
        speech_started=True,
        last_voice_at=10.0,
    )
    assert not dashboard._capture_should_finalize(
        now=10.4,
        capture_until=18.0,
        speech_started=True,
        last_voice_at=10.0,
    )
    assert not dashboard._capture_should_finalize(
        now=19.0,
        capture_until=18.0,
        speech_started=False,
        last_voice_at=0.0,
    )


def test_dashboard_followup_transcribes_captured_audio_without_waiting_for_vosk_final():
    server = DASHBOARD_PATH.read_text(encoding="utf-8")

    assert "_capture_should_finalize(" in server
    assert "_finish_stt_question(" in server
    assert "speech_started" in server
    assert "capture_started_at = max(now_followup, tts_muted_until or 0.0)" in server
    assert "tail_seconds = float(data.get(\"seconds\") or 1.8)" in server


def test_dashboard_provider_status_is_cached_and_fast():
    server = Path("C:/SidekickPortable/home/cockpit/dashboard_server.py").read_text(encoding="utf-8")

    assert "_providers_cache" in server
    assert "(now - _providers_cache_ts) < 45" in server
    assert "sock.settimeout(0.18)" in server


def test_stt_websocket_sends_initial_status_and_config_ack():
    server = DASHBOARD_PATH.read_text(encoding="utf-8")

    assert '"message": "STT initialisiert..."' in server
    assert '"type": "stt_config"' in server
    assert '"message": "STT-Konfiguration aktiv."' in server
    assert '"source_rate": int(data.get("source_rate") or sample_rate)' in server
