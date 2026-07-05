#!/usr/bin/env python3
"""Tests for Nova reflection cycle."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agenda import AgendaStore
from autobiography import AutobiographyStore
from reflection_cycle import build_daily_reflection


class ReflectionCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.agenda = AgendaStore(root / "agenda.json")
        self.bio = AutobiographyStore(root / "autobiography.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_daily_reflection_mentions_events_and_open_intents(self):
        self.agenda.upsert_intent("continuity", "Follow open thread", "thread remains", "prioritize_thread", 0.7)
        self.bio.record_event("action", "Recorded action", "Nova acted.", "need was high", ["Nova"], 0.6, {}, {}, None, [], ["entity_kernel"])
        reflection = build_daily_reflection(self.agenda, self.bio)
        self.assertIn("Follow open thread", reflection["summary"])
        self.assertIn("Recorded action", reflection["summary"])
        self.assertEqual(reflection["type"], "reflection")


if __name__ == "__main__":
    unittest.main()
