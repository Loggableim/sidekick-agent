from pathlib import Path


def test_voice_frontend_reports_entity_presence_and_exactly_once_cycle() -> None:
    boot = (Path(__file__).parents[1] / "web" / "static" / "boot.js").read_text(encoding="utf-8")
    assert "/api/nova/voice-event" in boot
    assert "phase:'transcript'" in boot
    assert "phase:'speaking'" in boot
    assert "phase:'complete'" in boot
    assert "_voiceEntityCycleId" in boot


def test_nova_voice_api_runs_one_complete_cycle(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    from fastapi.testclient import TestClient
    from cli import web_server

    client = TestClient(web_server.app)
    headers = {web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN}
    transcript = client.post(
        "/api/nova/voice-event",
        json={"phase": "transcript", "text": "Nova, hallo", "source": "wake_word", "confidence": 0.9},
        headers=headers,
    )
    assert transcript.status_code == 200
    payload = transcript.json()
    assert payload["ok"] is True
    cycle_id = payload["cycle_id"]

    first = client.post(
        "/api/nova/voice-event",
        json={"phase": "speaking", "text": "Hallo.", "source": "web_tts", "cycle_id": cycle_id, "response_id": "r1"},
        headers=headers,
    ).json()
    second = client.post(
        "/api/nova/voice-event",
        json={"phase": "speaking", "text": "Hallo.", "source": "web_tts", "cycle_id": cycle_id, "response_id": "r1"},
        headers=headers,
    ).json()
    done = client.post(
        "/api/nova/voice-event",
        json={"phase": "complete", "source": "web_tts", "cycle_id": cycle_id},
        headers=headers,
    ).json()

    assert first["ok"] is True
    assert second == {"ok": False, "reason": "already_spoken", "cycle_id": cycle_id}
    assert done["ok"] is True
    assert client.get("/api/nova/presence", headers=headers).json()["presence"] == "available"
