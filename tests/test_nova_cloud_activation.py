from __future__ import annotations


def test_cloud_responder_is_disabled_without_explicit_opt_in(monkeypatch):
    from cli import web_server
    monkeypatch.delenv("SIDEKICK_NOVA_CLOUD_RESPONDER", raising=False)
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    assert web_server._build_nova_cloud_feedback_responder() is None


def test_cloud_responder_requires_cloud_credential(monkeypatch):
    from cli import web_server
    monkeypatch.setenv("SIDEKICK_NOVA_CLOUD_RESPONDER", "1")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    assert web_server._build_nova_cloud_feedback_responder() is None


def test_cloud_responder_is_explicitly_ollama_cloud_and_bounded(monkeypatch):
    from cli import web_server
    monkeypatch.setenv("SIDEKICK_NOVA_CLOUD_RESPONDER", "1")
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    monkeypatch.setenv("SIDEKICK_NOVA_CLOUD_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("SIDEKICK_NOVA_CLOUD_TIMEOUT", "3")
    responder = web_server._build_nova_cloud_feedback_responder()
    assert responder is not None
    assert responder.enabled is True
    assert responder.model == "deepseek-v4-flash"
    assert responder.timeout_seconds == 3.0
    assert responder.transport.__class__.__name__ == "OllamaCloudTransport"