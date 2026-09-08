from cli import models
from runtime import models_dev


def test_new_cloud_models_survive_live_picker_filter(monkeypatch, tmp_path):
    available = [
        "glm-5.3", "glm-5.3-flash", "gpt-oss:20b", "gpt-oss:120b",
        "kimi-k3", "mistral-large-3:675b", "nemotron-3-nano:30b",
    ]
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path))
    monkeypatch.setattr(models, "fetch_api_models", lambda *_args, **_kwargs: available + ["unrelated-model"])
    monkeypatch.setattr(models_dev, "list_agentic_models", lambda *_args: ["kimi-k3:cloud"])
    result = models.fetch_ollama_cloud_models(api_key="test-only", force_refresh=True)
    assert result == available
    # A cached request must retain the new choices without a provider call.
    monkeypatch.setattr(models, "fetch_api_models", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected network")))
    assert models.fetch_ollama_cloud_models(api_key="test-only") == available
