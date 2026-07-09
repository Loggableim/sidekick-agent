#!/usr/bin/env python3
"""Local smoke tests for ACES v1."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aces_engine import ACESEngine
from aces_reward import compute_reward
from aces_sandbox import SandboxRunner
from aces_llm import ACESLLMClient
from aces_types import ACESConfig, Goal, TestReport, Tool


class ACESTests(unittest.TestCase):
    def make_config(self) -> ACESConfig:
        root = Path(tempfile.mkdtemp(prefix="aces-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return ACESConfig(
            space_dir=root,
            local_enabled=False,
            cloud_enabled=False,
            goal_templates=[
                {
                    "description": "Memory consolidation helper",
                    "success_criteria": "Normalize repeated memory snippets safely.",
                    "priority": 0.9,
                }
            ],
        )

    def test_sandbox_blocks_dangerous_imports(self):
        config = self.make_config()
        sandbox = SandboxRunner(config)
        report = sandbox.validate_code("import os\n\ndef run():\n    return os.getcwd()\n")
        self.assertFalse(report.ok)
        self.assertTrue(any("blocked import" in item for item in report.violations))

    def test_sandbox_blocks_eval(self):
        config = self.make_config()
        sandbox = SandboxRunner(config)
        report = sandbox.validate_code("def run(x):\n    return eval(x)\n")
        self.assertFalse(report.ok)
        self.assertTrue(any("eval" in item for item in report.violations))

    def test_sandbox_blocks_unapproved_network_url(self):
        config = self.make_config()
        sandbox = SandboxRunner(config)
        report = sandbox.validate_code("def run():\n    return 'https://example.com/data'\n", allow_network=True)
        self.assertFalse(report.ok)
        self.assertTrue(any("not allowlisted" in item for item in report.violations))

    def test_reward_requires_safety(self):
        goal = Goal("g1", "Emotion drift detection", "detect drift")
        tool = Tool("emotion_drift", "Emotion drift detection", "def run(): return True", "")
        test = TestReport(True, 6, 0, 6, 0.05, safety_ok=False, violations=["blocked"])
        reward = compute_reward(goal, tool, test, [])
        self.assertEqual(reward.safety, 0.0)
        self.assertLess(reward.total, 0.8)

    def test_dry_run_cycle_does_not_integrate(self):
        config = self.make_config()
        engine = ACESEngine(config)
        report = asyncio.run(engine.run_cycle(dry_run=True))
        self.assertFalse(report.integrated)
        self.assertTrue((config.log_dir / "audit.jsonl").exists())
        self.assertFalse(config.tools_dir.exists())

    def test_game_mode_forces_ollama_cloud_backend(self):
        config = self.make_config()
        client = ACESLLMClient(config)
        with patch("aces_llm._game_mode_enabled", return_value=True), \
             patch.dict(os.environ, {"OLLAMA_API_KEY": "test-token"}, clear=False), \
             patch.object(ACESLLMClient, "_post_chat", return_value="cloud text") as post_chat:
            result = client._complete("prompt", prefer_cloud=False)

        self.assertEqual(result.backend, "cloud")
        self.assertEqual(result.text, "cloud text")
        self.assertEqual(post_chat.call_count, 1)
        endpoint, model, prompt, api_key, timeout = post_chat.call_args.args
        self.assertEqual(endpoint, "https://ollama.com/v1/chat/completions")
        self.assertEqual(model, "deepseek-v4-flash")
        self.assertEqual(api_key, "test-token")
        self.assertEqual(prompt, "prompt")
        self.assertGreater(timeout, 0)

    def test_game_mode_uses_env_file_fallback(self):
        root = Path(tempfile.mkdtemp(prefix="aces-env-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        (root / "state" / "webui").mkdir(parents=True, exist_ok=True)
        (root / "state" / "webui" / "settings.json").write_text(
            '{"game_mode_enabled": true}',
            encoding="utf-8",
        )
        (root / ".env").write_text(
            "OLLAMA_API_KEY=file-token\nOLLAMA_BASE_URL=https://ollama.example/v1\n",
            encoding="utf-8",
        )

        config = self.make_config()
        client = ACESLLMClient(config)
        with patch("aces_llm._sidekick_home", return_value=root), \
             patch.dict(os.environ, {}, clear=True), \
             patch.object(ACESLLMClient, "_post_chat", return_value="cloud text") as post_chat:
            result = client._complete("prompt", prefer_cloud=False)

        self.assertEqual(result.backend, "cloud")
        self.assertEqual(result.text, "cloud text")
        endpoint, model, prompt, api_key, timeout = post_chat.call_args.args
        self.assertEqual(endpoint, "https://ollama.example/v1/chat/completions")
        self.assertEqual(model, "deepseek-v4-flash")
        self.assertEqual(api_key, "file-token")
        self.assertEqual(prompt, "prompt")
        self.assertGreater(timeout, 0)


if __name__ == "__main__":
    unittest.main()
