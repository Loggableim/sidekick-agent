#!/usr/bin/env python3
"""Tests for Nova Entity Kernel."""

from __future__ import annotations

from datetime import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from entity_kernel import EntityKernel
import autonomer_tick


class EntityKernelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "autonomy_policy.json").write_text("""
{
  "timezone": "Europe/Vienna",
  "quiet_hours": {"start": "22:00", "end": "08:00"},
  "tiers": {
    "silent": {"allowed": true},
    "internal": {"allowed": true},
    "notify": {"allowed": true, "daily_limit": 3, "cooldown_minutes": 120},
    "external": {"allowed": false},
    "risky": {"allowed": false, "requires_approval": true}
  },
  "actions": {
    "telegram_message": {"tier": "notify"},
    "inner_voice": {"tier": "internal"},
    "prioritize_thread": {"tier": "silent"},
    "agenda_update": {"tier": "silent"}
  }
}
""", encoding="utf-8")
        (self.root / "self_model.json").write_text('{"version": 1, "identity": {"name": "Nova"}}', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def fixture_state(self):
        return {
            "emotion": {"arousal": 0.52, "valence": 0.7, "novelty": 0.45, "coherence": 0.8},
            "continuity": {"open_threads": ["review entity kernel spec"]},
            "will": {"will": {"drive": 0.25, "boredom_level": 0.2, "boredom_pressure": 0.1}},
            "memory": {"total_memories": 300},
            "hormones": {"hormones": {"oxy": 0.5, "mela": 0.1}},
        }

    def test_decide_returns_traceable_intent_and_policy(self):
        kernel = EntityKernel(space_dir=self.root, state_provider=self.fixture_state)
        decision = kernel.decide(now_iso="2026-07-05T12:00:00")
        self.assertIn("state", decision)
        self.assertIn("needs", decision)
        self.assertIn("intent", decision)
        self.assertIn("policy", decision)
        self.assertTrue(decision["intent"]["why"])

    def test_tick_dry_run_does_not_execute_action(self):
        kernel = EntityKernel(space_dir=self.root, state_provider=self.fixture_state)
        result = kernel.tick(dry_run=True, now_iso="2026-07-05T12:00:00")
        self.assertEqual(result["mode"], "dry-run")
        self.assertFalse(result["executed"])
        self.assertIn("decision", result)
        self.assertEqual(kernel.agenda.list_open(), [])

    def test_scan_provider_shape_is_accepted(self):
        kernel = EntityKernel(space_dir=self.root, state_provider=lambda: {
            "emotion": {"arousal": 0.4, "valence": 0.5, "novelty": 0.6, "coherence": 0.7},
            "continuity": {"open_threads": []},
            "memory": {"total_memories": 42},
            "will": {"will": {"drive": 0.1, "boredom_pressure": 0.2}},
            "hormones": {"hormones": {"mela": 0.2}},
        })
        scan = kernel.scan()
        self.assertEqual(scan["memory"]["total_memories"], 42)

    def test_blocked_action_records_blocked_result(self):
        kernel = EntityKernel(space_dir=self.root, state_provider=self.fixture_state)
        decision = kernel.decide(now_iso="2026-07-05T23:00:00")
        decision["intent"]["action"] = "telegram_message"
        decision["intent"]["tier"] = "notify"
        decision["intent"]["why"] = "quiet hour test"
        decision["policy"] = kernel.policy.check(decision["intent"], now=datetime(2026, 7, 5, 23, 0, 0), history=[])
        result = kernel.act(decision)
        self.assertFalse(result["executed"])
        self.assertIn("reason", result)

    def test_allowed_internal_action_records_autobiography_event(self):
        kernel = EntityKernel(space_dir=self.root, state_provider=self.fixture_state)
        decision = kernel.decide(now_iso="2026-07-05T12:00:00")
        decision["intent"]["action"] = "inner_voice"
        decision["intent"]["tier"] = "internal"
        decision["intent"]["why"] = "expression need test"
        decision["policy"] = kernel.policy.check(decision["intent"], now=datetime(2026, 7, 5, 12, 0, 0), history=[])
        result = kernel.act(decision)
        self.assertTrue(result["executed"])
        self.assertGreaterEqual(len(kernel.bio.recent()), 1)

    def test_autonomer_tick_evaluates_notification_before_persisting_new_state(self):
        calls = []

        class FakeGate:
            def update_emotion(self, emotion):
                calls.append(("update_emotion", emotion))

            def update_open_threads(self, count):
                calls.append(("update_open_threads", count))

            def should_notify(self, event):
                calls.append(("should_notify", dict(event)))
                return False, "batched", 0.6

            def mark_sent(self, significance):
                calls.append(("mark_sent", significance))

        with patch.object(autonomer_tick, "NotificationGate", return_value=FakeGate()), \
             patch.object(autonomer_tick, "run_entity_kernel_tick", return_value={"executed": False}), \
             patch.object(autonomer_tick, "scan_state", return_value={
                 "emotion": {"arousal": 0.8, "valence": 0.3, "coherence": 0.9},
                 "will": {"will": {"drive": 0.2, "desire": 0.4, "boredom_level": 0.1}},
                 "continuity": {"open_threads": ["alpha", "beta"]},
                 "memory": {"total_memories": 12},
             }), \
             patch.object(autonomer_tick, "decide_action", return_value={"action": "reflect", "reason": "test"}), \
             patch.object(autonomer_tick, "execute_action", return_value={"action": "reflect", "success": True, "output": "done"}), \
             patch.object(autonomer_tick, "build_message", return_value="msg"), \
             patch.object(autonomer_tick, "send_telegram", return_value=True), \
             patch.object(autonomer_tick, "_run", return_value=None):
            autonomer_tick.main(silent=True)

        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(calls[0][0], "should_notify")
        self.assertEqual([name for name, _ in calls[1:3]], ["update_emotion", "update_open_threads"])


if __name__ == "__main__":
    unittest.main()
