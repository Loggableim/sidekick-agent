from cli import swarm_host


def test_swarm_cloud_adapter_always_sets_bounded_timeout(monkeypatch):
    captured = {}

    monkeypatch.setattr(swarm_host, "_uses_canonical_ollama_cloud_endpoint", lambda: True)

    def fake_call_llm(**kwargs):
        captured.update(kwargs)
        return {"content": "{}"}

    monkeypatch.setattr("runtime.auxiliary_client.call_llm", fake_call_llm)
    result = swarm_host._sidekick_call_llm(
        task="swarm", provider="ollama-cloud", model="deepseek-v4-flash", messages=[]
    )

    assert result == {"content": "{}"}
    assert captured["timeout"] == swarm_host.SWARM_MODEL_TIMEOUT_SECONDS
    assert captured["required_base_url"] == swarm_host.OLLAMA_CLOUD_CANONICAL_BASE_URL
