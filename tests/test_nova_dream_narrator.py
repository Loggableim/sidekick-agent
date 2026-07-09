from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from urllib import request as urllib_request


MODULE_PATH = Path("C:/sidekick/home/spaces/nova/dream_narrator.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("nova_dream_narrator_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dream_narrator_reads_ollama_credentials_from_env_file(tmp_path, monkeypatch):
    module = _load_module()
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setattr(module, "HERE", tmp_path / "spaces" / "nova")
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".env").write_text(
        "OLLAMA_API_KEY=test-dotenv-token\nOLLAMA_BASE_URL=https://ollama.example/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_game_mode_enabled", lambda: True)

    captured: dict[str, object] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "remote dream ok"}}]}
            ).encode("utf-8")

    def _fake_urlopen(req, timeout=300):
        captured["url"] = req.full_url
        captured["headers"] = {key.lower(): value for key, value in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(urllib_request, "urlopen", _fake_urlopen)

    result = module.dream_narrate(
        [{"thought": "Nova traumt von einem ruhigen Game Mode.", "tags": "nova,dream"}],
        dream_type="rem",
        dream_port=8082,
        temperature=0.1,
        max_tokens=1,
    )

    assert result["model"] == "ollama-cloud:deepseek-v4-flash"
    assert result["narrative"] == "remote dream ok"
    assert captured["url"] == "https://ollama.example/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer test-dotenv-token"
    assert captured["body"]["model"] == "deepseek-v4-flash"
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["body"]["messages"][1]["role"] == "user"
