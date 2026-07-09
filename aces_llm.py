#!/usr/bin/env python3
"""LLM bridge for ACES code and test generation."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aces_types import ACESConfig, Goal, Tool

REMOTE_GAME_MODE_MODEL = "deepseek-v4-flash"
REMOTE_GAME_MODE_ENDPOINT = "https://ollama.com/v1/chat/completions"


def _sidekick_home() -> Path:
    raw = os.environ.get("SIDEKICK_HOME", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent.parent


def _load_env() -> dict[str, str]:
    env_path = _sidekick_home() / ".env"
    if not env_path.exists():
        env_path = Path("C:/sidekick/home/.env")
    if not env_path.exists():
        return {}

    env: dict[str, str] = {}
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip()
    except Exception:
        return {}
    return env


def _game_mode_enabled() -> bool:
    try:
        settings_file = _sidekick_home() / "state" / "webui" / "settings.json"
        if settings_file.exists():
            data = json.loads(settings_file.read_text(encoding="utf-8"))
            if bool(data.get("game_mode_enabled")):
                return True
        settings_dir = settings_file.parent
        for lock_file in (settings_dir / "game_mode.lock", settings_dir.parent / "game_mode.lock"):
            if lock_file.exists():
                return True
    except Exception:
        pass
    return False


def _ollama_cloud_endpoint() -> str:
    env = _load_env()
    raw_base = (
        os.environ.get("OLLAMA_BASE_URL")
        or env.get("OLLAMA_BASE_URL")
        or REMOTE_GAME_MODE_ENDPOINT
    ).strip().rstrip("/")
    if not raw_base:
        raw_base = REMOTE_GAME_MODE_ENDPOINT
    if raw_base.endswith("/chat/completions"):
        return raw_base
    if raw_base.endswith("/v1"):
        return f"{raw_base}/chat/completions"
    return f"{raw_base}/v1/chat/completions"


@dataclass
class LLMResult:
    text: str
    backend: str
    tokens_estimate: int
    confidence: float
    error: str = ""


class ACESLLMClient:
    """Small OpenAI-compatible client with local-first fallback."""

    def __init__(self, config: ACESConfig):
        self.config = config
        self.tokens_used = 0

    def generate_code(self, goal: Goal, existing_tools: list[Tool]) -> LLMResult:
        """Generate one safe Python module for a goal."""
        existing_summary = "\n".join(f"- {t.name}: {t.description}" for t in existing_tools[:10]) or "none"
        prompt = (
            "You are writing one small, pure Python 3.11 tool for Nova ACES.\n"
            "Return only Python code, no markdown.\n"
            "Hard rules: no os/subprocess/sys/socket/requests/urllib imports; no eval/exec/open writes; "
            "no network; no secrets; deterministic functions; include a public function named run.\n\n"
            f"Goal: {goal.description}\n"
            f"Success criteria: {goal.success_criteria}\n"
            f"Existing tools:\n{existing_summary}\n"
        )
        result = self._complete(prompt, prefer_cloud=False)
        code = extract_python(result.text)
        if result.confidence < 0.45 or "def run" not in code:
            cloud_result = self._complete(prompt, prefer_cloud=True)
            if cloud_result.confidence > result.confidence:
                result = cloud_result
                code = extract_python(result.text)
        if not code.strip() or "def run" not in code:
            code = deterministic_tool_template(goal)
            result = LLMResult(code, "template", estimate_tokens(code), 0.35, result.error)
        result.text = code
        self.tokens_used += result.tokens_estimate
        return result

    def generate_tests(self, goal: Goal, tool: Tool) -> LLMResult:
        """Generate unittest-compatible tests for a candidate tool."""
        prompt = (
            "Write Python unittest tests for candidate_tool.py.\n"
            "Return only Python code. Use unittest, import candidate_tool, and include at least "
            "3 happy path tests, 2 edge cases, and 1 failure case when reasonable.\n\n"
            f"Goal: {goal.description}\n"
            f"Success criteria: {goal.success_criteria}\n"
            f"Tool code:\n{tool.code[:6000]}\n"
        )
        result = self._complete(prompt, prefer_cloud=False)
        code = extract_python(result.text)
        if result.confidence < 0.45 or "unittest" not in code:
            cloud_result = self._complete(prompt, prefer_cloud=True)
            if cloud_result.confidence > result.confidence:
                result = cloud_result
                code = extract_python(result.text)
        if "unittest" not in code or "candidate_tool" not in code:
            code = deterministic_test_template()
            result = LLMResult(code, "template", estimate_tokens(code), 0.35, result.error)
        result.text = code
        self.tokens_used += result.tokens_estimate
        return result

    def _complete(self, prompt: str, prefer_cloud: bool) -> LLMResult:
        backends = []
        game_mode = _game_mode_enabled()
        if game_mode:
            env = _load_env()
            api_key = (os.environ.get("OLLAMA_API_KEY") or env.get("OLLAMA_API_KEY") or "").strip()
            if api_key:
                backends.append((
                    "cloud",
                    _ollama_cloud_endpoint(),
                    REMOTE_GAME_MODE_MODEL,
                    api_key,
                    self.config.cloud_timeout_seconds,
                ))
        else:
            if not prefer_cloud and self.config.local_enabled:
                backends.append(("local", self.config.local_endpoint, self.config.local_model, None, self.config.local_timeout_seconds))
            if self.config.cloud_enabled and self.config.cloud_endpoint:
                api_key = os.environ.get(self.config.cloud_api_key_env, "")
                backends.append(("cloud", self.config.cloud_endpoint, self.config.cloud_model, api_key, self.config.cloud_timeout_seconds))
            if prefer_cloud and self.config.local_enabled:
                backends.append(("local", self.config.local_endpoint, self.config.local_model, None, self.config.local_timeout_seconds))

        last_error = ""
        for backend, endpoint, model, api_key, timeout in backends:
            try:
                text = self._post_chat(endpoint, model, prompt, api_key, timeout)
                confidence = 0.75 if text.strip() else 0.0
                return LLMResult(text, backend, estimate_tokens(prompt + text), confidence)
            except Exception as exc:
                last_error = f"{backend}: {exc}"
        return LLMResult("", "none", estimate_tokens(prompt), 0.0, last_error)

    def _post_chat(self, endpoint: str, model: str, prompt: str, api_key: str | None, timeout: int) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You output compact, safe Python only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 2500,
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("no choices")
        message = choices[0].get("message") or {}
        return str(message.get("content") or "")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def extract_python(text: str) -> str:
    """Extract Python code from plain text or fenced markdown."""
    if not text:
        return ""
    match = re.search(r"```(?:python)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def safe_module_name(raw: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", raw.lower()).strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"tool_{cleaned}"
    return cleaned[:60]


def deterministic_tool_template(goal: Goal) -> str:
    name = safe_module_name(goal.description) or "aces_candidate"
    return f'''"""Generated fallback tool for: {goal.description}."""

from __future__ import annotations


DESCRIPTION = {goal.description!r}
SUCCESS_CRITERIA = {goal.success_criteria!r}


def normalize_items(items: list[str]) -> list[str]:
    """Return unique, trimmed items while preserving order."""
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        value = str(item).strip()
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def run(items: list[str] | None = None) -> dict[str, object]:
    """Summarize input items for the ACES goal {name!r}."""
    normalized = normalize_items(items or [])
    return {{
        "goal": DESCRIPTION,
        "count": len(normalized),
        "items": normalized,
        "meets_basic_contract": True,
    }}
'''


def deterministic_test_template() -> str:
    return '''import unittest

import candidate_tool


class CandidateToolTests(unittest.TestCase):
    def test_run_empty(self):
        result = candidate_tool.run([])
        self.assertEqual(result["count"], 0)
        self.assertTrue(result["meets_basic_contract"])

    def test_run_normalizes_items(self):
        result = candidate_tool.run([" alpha ", "Alpha", "beta"])
        self.assertEqual(result["items"], ["alpha", "beta"])

    def test_run_accepts_none(self):
        result = candidate_tool.run(None)
        self.assertEqual(result["items"], [])

    def test_normalize_drops_empty(self):
        self.assertEqual(candidate_tool.normalize_items(["", " x "]), ["x"])

    def test_normalize_preserves_order(self):
        self.assertEqual(candidate_tool.normalize_items(["b", "a", "b"]), ["b", "a"])

    def test_failure_contract(self):
        self.assertIsInstance(candidate_tool.run(["x"]), dict)


if __name__ == "__main__":
    unittest.main()
'''
