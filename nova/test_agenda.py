#!/usr/bin/env python3
"""Tests for Nova agenda intentions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova.agenda import AgendaStore


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
        high = self.store.upsert_intent("connection", "Contact Cid", "urgent", "telegram_message", 0.9)
        low = self.store.upsert_intent("archive", "Old task", "low prio", "telegram_message", 0.3)
        self.store.mark_result(low["id"], "blocked", {"reason": "policy"})
        self.assertEqual(self.store.best_intent()["id"], high["id"])

    def test_reupsert_revives_blocked_intent(self):
        """A policy-blocked intent must become selectable again once the same
        (need, action) pair is re-proposed; otherwise it becomes a permanent
        zombie that wedges the agenda."""
        intent = self.store.upsert_intent("connection", "Contact Cid", "reason one", "telegram_message", 0.6)
        self.store.mark_result(intent["id"], "blocked", {"reason": "quiet hours"})
        revived = self.store.upsert_intent("connection", "Contact Cid again", "reason two", "telegram_message", 0.9)
        self.assertEqual(revived["id"], intent["id"])
        self.assertEqual(revived["status"], "open")
        best = self.store.best_intent()
        self.assertIsNotNone(best)
        self.assertEqual(best["id"], intent["id"])


if __name__ == "__main__":
    unittest.main()
