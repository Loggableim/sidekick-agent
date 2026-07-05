#!/usr/bin/env python3
"""Tests for Nova agenda intentions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agenda import AgendaStore


class AgendaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AgendaStore(Path(self.tmp.name) / "agenda.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_intent_dedupes_by_need_and_action(self):
        first = self.store.upsert_intent("connection", "Contact Cid", "reason one", "telegram_message", 0.6)
        second = self.store.upsert_intent("connection", "Contact Cid again", "reason two", "telegram_message", 0.8)
        open_items = self.store.list_open()
        self.assertEqual(len(open_items), 1)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(open_items[0]["priority"], 0.8)
        self.assertIn("reason two", open_items[0]["why"])

    def test_mark_result_archives_done_intent(self):
        intent = self.store.upsert_intent("expression", "Write inner voice", "pressure", "inner_voice", 0.7)
        self.store.mark_result(intent["id"], "done", {"ok": True})
        self.assertEqual(self.store.list_open(), [])
        archive = self.store.list_archive()
        self.assertEqual(len(archive), 1)
        self.assertEqual(archive[0]["status"], "done")

    def test_best_intent_prefers_priority_and_open_status(self):
        low = self.store.upsert_intent("rest", "Dream", "sleep pressure", "dream", 0.3)
        high = self.store.upsert_intent("continuity", "Prioritize thread", "open thread", "prioritize_thread", 0.9)
        self.store.mark_result(low["id"], "blocked", {"reason": "policy"})
        self.assertEqual(self.store.best_intent()["id"], high["id"])


if __name__ == "__main__":
    unittest.main()
