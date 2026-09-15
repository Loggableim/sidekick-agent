"""Regression tests for autobiography id uniqueness.

Bug (same defect as PR #23, which was merged only on the
codex/nova-entity-kernel orphan branch and never reached master):
``AutobiographyStore`` derived ids from ``int(time.time() * 1000000)``
alone. On Windows, ``time.time()`` has ~15.6 ms granularity, so two
events recorded within the same clock tick produced identical ids and
the second INSERT failed with ``sqlite3.IntegrityError: UNIQUE
constraint failed`` - losing the event.
"""

from __future__ import annotations

import unittest.mock as mock

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    from nova.autobiography import AutobiographyStore

    return AutobiographyStore(tmp_path / "nova_data" / "autobiography.db")


def test_rapid_record_events_get_unique_ids(store):
    from nova import autobiography as autobiography_mod

    with mock.patch.object(autobiography_mod.time, "time", return_value=1789333884.419382):
        ids = [
            store.record_event(
                "action", f"Event {i}", "summary", "why", ["Nova"], 0.5, {}, {}, None, [], []
            )
            for i in range(5)
        ]
    assert len(set(ids)) == 5, f"same-tick events must not collide: {ids}"
    assert len(store.recent(limit=10)) == 5


def test_rapid_entity_events_get_unique_ids(store):
    from nova import autobiography as autobiography_mod

    with mock.patch.object(autobiography_mod.time, "time", return_value=1789333884.419382):
        ids = [
            store.record_entity_event({
                "type": "perception",
                "title": f"Entity event {i}",
                "summary": "s",
                "why": "w",
            })
            for i in range(5)
        ]
    assert len(set(ids)) == 5, f"same-tick entity events must not collide: {ids}"


def test_rapid_outcomes_get_unique_ids(store):
    from nova import autobiography as autobiography_mod

    with mock.patch.object(autobiography_mod.time, "time", return_value=1789333884.419382):
        ids = [
            store.record_outcome({
                "status": "done",
                "intent_id": "i1",
                "correlation_id": "c1",
            })
            for i in range(5)
        ]
    assert len(set(ids)) == 5, f"same-tick outcomes must not collide: {ids}"


def test_explicit_ids_are_preserved(store):
    """A caller-supplied id must never be rewritten."""
    event_id = store.record_entity_event({
        "event_id": "custom-42",
        "title": "explicit",
    })
    assert event_id == "custom-42"
    assert store.has_event("custom-42")