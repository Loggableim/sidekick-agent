#!/usr/bin/env python3
"""Tests for Nova autonomy policy."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from autonomy_policy import AutonomyPolicy


class AutonomyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "policy.json"
        self.path.write_text(json.dumps({
            "timezone": "Europe/Vienna",
            "quiet_hours": {"start": "22:00", "end": "08:00"},
            "tiers": {
                "silent": {"allowed": True},
                "internal": {"allowed": True},
                "notify": {"allowed": True, "daily_limit": 3, "cooldown_minutes": 120},
                "external": {"allowed": False},
                "risky": {"allowed": False, "requires_approval": True}
            },
            "actions": {
                "telegram_message": {"tier": "notify"},
                "reflection": {"tier": "internal"},
                "code_change": {"tier": "risky"}
            }
        }), encoding="utf-8")
        self.policy = AutonomyPolicy(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_internal_action_allowed(self):
        decision = self.policy.check({"action": "reflection", "why": "daily reflection"}, now=datetime(2026, 7, 5, 12, 0, 0), history=[])
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["tier"], "internal")

    def test_notify_requires_reason_and_respects_daily_limit(self):
        history = [
            {"action": "telegram_message", "timestamp": "2026-07-05T09:00:00"},
            {"action": "telegram_message", "timestamp": "2026-07-05T12:00:00"},
            {"action": "telegram_message", "timestamp": "2026-07-05T15:00:00"},
        ]
        decision = self.policy.check({"action": "telegram_message", "why": "open thread"}, now=datetime(2026, 7, 5, 18, 0, 0), history=history)
        self.assertFalse(decision["allowed"])
        self.assertIn("daily limit", decision["reason"])

    def test_risky_action_blocked_for_approval(self):
        decision = self.policy.check({"action": "code_change", "why": "modify file"}, now=datetime(2026, 7, 5, 12, 0, 0), history=[])
        self.assertFalse(decision["allowed"])
        self.assertTrue(decision["requires_approval"])


if __name__ == "__main__":
    unittest.main()
