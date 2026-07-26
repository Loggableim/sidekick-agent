#!/usr/bin/env python3
"""Tests for Nova autobiography timeline."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova.autobiography import AutobiographyStore


class AutobiographyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AutobiographyStore(Path(self.tmp.name) / "autobiography.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_and_query_event(self):
        event_id = self.store.record_event(
            event_type="decision",
            title="Choose connection intent",
            summary="Nova chose to contact Cid about an open thread.",
            why="connection need was high",
            actors=["Nova", "Cid"],
            importance=0.8,
            emotion_snapshot={"valence": 0.6},
            need_snapshot={"connection": {"level": 0.8}},
            intent_id="intent-1",
            memory_refs=[],
            tags=["entity_kernel", "decision"],
        )
        events = self.store.recent(limit=5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["id"], event_id)
        self.assertEqual(events[0]["type"], "decision")
        self.assertEqual(events[0]["actors"], ["Nova", "Cid"])

    def test_filter_by_type(self):
        self.store.record_event("reflection", "Daily", "summary", "why", ["Nova"], 0.4, {}, {}, None, [], ["daily"])
        self.store.record_event("action", "Acted", "summary", "why", ["Nova"], 0.5, {}, {}, None, [], ["action"])
        reflections = self.store.by_type("reflection", limit=10)
        self.assertEqual(len(reflections), 1)
        self.assertEqual(reflections[0]["title"], "Daily")


if __name__ == "__main__":
    unittest.main()
