#!/usr/bin/env python3
"""Tests for Nova Entity Kernel need scoring."""

from __future__ import annotations

import unittest

from nova.needs import compute_needs


class NeedsTests(unittest.TestCase):
    def test_open_threads_raise_continuity_and_connection(self):
        state = {
            "emotion": {"arousal": 0.55, "valence": 0.65, "novelty": 0.35, "coherence": 0.75},
            "continuity": {"open_threads": ["Entity Kernel review", "Hub cooldowns"]},
            "memory": {"total_memories": 500},
            "will": {"will": {"boredom_level": 0.1, "boredom_pressure": 0.1}},
        }
        needs = compute_needs(state)
        self.assertGreaterEqual(needs["continuity"]["level"], 0.55)
        self.assertGreaterEqual(needs["connection"]["level"], 0.35)
        self.assertTrue(any(i["action"] == "telegram_message" for i in needs["connection"]["suggested_intents"]))

    def test_boredom_and_novelty_raise_curiosity_and_autonomy(self):
        state = {
            "emotion": {"arousal": 0.35, "valence": 0.6, "novelty": 0.82, "coherence": 0.7},
            "continuity": {"open_threads": []},
            "will": {"will": {"boredom_level": 0.72, "boredom_pressure": 0.8}},
            "memory": {"total_memories": 120},
        }
        needs = compute_needs(state)
        self.assertGreaterEqual(needs["curiosity"]["level"], 0.7)
        self.assertGreaterEqual(needs["autonomy"]["level"], 0.55)
        self.assertTrue(any(i["action"] == "inner_voice" for i in needs["expression"]["suggested_intents"]))

    def test_low_arousal_and_high_melatonin_signal_rest(self):
        state = {
            "emotion": {"arousal": 0.18, "valence": 0.48, "novelty": 0.4, "coherence": 0.8},
            "hormones": {"hormones": {"mela": 0.76}},
            "continuity": {"open_threads": []},
            "will": {"will": {"boredom_level": 0.0, "boredom_pressure": 0.0}},
        }
        needs = compute_needs(state)
        self.assertGreaterEqual(needs["rest"]["level"], 0.7)
        self.assertTrue(any(i["action"] == "dream" for i in needs["rest"]["suggested_intents"]))

    def test_dashboard_hormone_shape_is_supported(self):
        state = {
            "emotion": {"arousal": 0.18, "valence": 0.48, "novelty": 0.4, "coherence": 0.8},
            "hormones": {"hormones": {"mela": {"value": 0.76}, "oxy": {"value": 0.5}}},
            "continuity": {"open_threads": ["real snapshot"]},
            "will": {"will": {"boredom_level": 0.0, "boredom_pressure": 0.0}},
        }
        needs = compute_needs(state)
        self.assertGreaterEqual(needs["rest"]["level"], 0.7)
        self.assertGreaterEqual(needs["connection"]["level"], 0.45)

    def test_snapshot_memory_count_shape_is_supported(self):
        state = {
            "emotion": {"arousal": 0.5, "valence": 0.5, "novelty": 0.5, "coherence": 0.5},
            "memory": {"count": 1200},
            "continuity": {"open_threads": []},
            "will": {"will": {"drive": 0.1, "boredom_pressure": 0.0}},
        }
        needs = compute_needs(state)
        self.assertIn("memory_count=1200", needs["competence"]["evidence"])


if __name__ == "__main__":
    unittest.main()
