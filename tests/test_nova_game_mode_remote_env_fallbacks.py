from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from urllib import request as urllib_request


NOVA_MIND_PATH = Path("C:/sidekick/home/spaces/nova/nova_mind.py")
LOCAL_LLM_BRIDGE_PATH = Path("C:/sidekick/home/spaces/nova/local_llm_bridge.py")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, content: str):
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps({"choices": [{"message": {"content": self._content}}]}).encode("utf-8")


def _patch_urlopen(monkeypatch, content: str, captured: dict[str, object]):
    def _fake_urlopen(req, timeout=300):
        captured["url"] = req.full_url
        captured["headers"] = {key.lower(): value for key, value in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response(content)

    monkeypatch.setattr(urllib_request, "urlopen", _fake_urlopen)


def test_nova_mind_game_mode_reads_ollama_credentials_from_env_file(tmp_path, monkeypatch):
    module = _load_module(NOVA_MIND_PATH, "nova_mind_under_test")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setattr(module, "HERE", tmp_path / "spaces" / "nova")
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".env").write_text(
        "OLLAMA_API_KEY=test-mind-token\nOLLAMA_BASE_URL=https://ollama.example/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_game_mode_enabled", lambda: True)

    captured: dict[str, object] = {}
    _patch_urlopen(monkeypatch, "remote mind ok", captured)

    result = module._call_chat(
        [{"role": "user", "content": "test"}],
        port=8080,
        max_tokens=1,
        temperature=0.1,
    )

    assert result == "remote mind ok"
    assert captured["url"] == "https://ollama.example/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer test-mind-token"
    assert captured["body"]["model"] == "deepseek-v4-flash"


def test_local_llm_bridge_game_mode_reads_ollama_credentials_from_env_file(tmp_path, monkeypatch):
    module = _load_module(LOCAL_LLM_BRIDGE_PATH, "nova_local_llm_bridge_under_test")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "OLLAMA_API_KEY=test-bridge-token\nOLLAMA_BASE_URL=https://ollama.example/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_game_mode_enabled", lambda: True)

    captured: dict[str, object] = {}
    _patch_urlopen(monkeypatch, "remote bridge ok", captured)

    result = module.call_llm("test", port=8082, max_tokens=1, temperature=0.1)

    assert result == "remote bridge ok"
    assert captured["url"] == "https://ollama.example/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer test-bridge-token"
    assert captured["body"]["model"] == "deepseek-v4-flash"
