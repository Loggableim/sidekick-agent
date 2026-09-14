"""Regression tests for the background_tick event payload bloat.

Bug (observed 2026-09-14): ``background_tick()`` embedded the full entity tick
decision state (the complete cognitive snapshot: substrate microdrifts, continuity
history, emotion state — ~150 KB) plus the full soak status (400 samples, ~89 KB)
into every persisted ``background_tick`` event. With a 10-minute cron cadence the
space's ``events.json`` grew to 20 MB (97% background_tick payloads), and because
every ``_update_event`` call rewrites the whole file, each chat turn paid ~140 MB
of read+write I/O and each tick ~60 MB.

Nobody consumes these snapshots from the persisted events:
- the cron script prints the same payload with ``wakeAgent: false`` — the scheduler
  treats that stdout as silent, so the payload is never delivered;
- ``nova_presence`` reads ``soak_v2.json`` directly, not from events;
- no frontend, test, or nova module reads ``entity_tick``/``soak`` from events.

The persisted event must carry a bounded summary instead of the full snapshots.
The ``background_tick()`` return value keeps the full payload (callers may use it).
"""

from __future__ import annotations

import json

import pytest


def _stub_kernel(monkeypatch, lifecycle, *, state_chars: int = 200_000, samples: int = 400):
    """Stub EntityKernel with a tick/soak that produce realistically huge payloads."""

    class _FakeSoak:
        def status(self):
            return {
                "schema_version": 1,
                "started_at": "2026-07-10T21:12:44+00:00",
                "required_hours": 24,
                "samples": [
                    {
                        "timestamp": f"2026-09-14T19:{i // 60:02d}:{i % 60:02d}+00:00",
                        "state_revision": 50000 + i,
                        "mind_process_count": 1,
                        "reflection_queue_depth": 0,
                        "duplicate_action_correlations": 0,
                        "duplicate_voice_responses": 0,
                        "raw_audio_events": 0,
                    }
                    for i in range(samples)
                ],
                "violations": [],
                "due_at": "2026-07-11T21:12:44+00:00",
                "last_sample_at": "2026-09-14T19:00:00+00:00",
                "elapsed_hours": 1600.0,
                "complete": True,
                "passed": True,
            }

        def sample(self, **kwargs):
            return self.status()

    class _FakeBio:
        pass

    class _FakeKernel:
        def __init__(self, *args, **kwargs):
            self.reflections = type("R", (), {"drain": staticmethod(lambda limit=25: []), "status": staticmethod(lambda: {"queued": 0})})()
            self.soak = _FakeSoak()
            self.bio = _FakeBio()

        def tick(self):
            return {
                "executed": True,
                "result": {"ok": True, "message": "tick"},
                "outcome_id": "outcome-x",
                "event_id": "event-x",
                "decision": {
                    "timestamp": "2026-09-14T19:00:00+00:00",
                    "state": {"blob": "x" * state_chars},
                    "needs": {},
                    "intent": {},
                    "policy": {"allowed": True},
                    "autonomy": {},
                },
            }

        def sample_soak(self):
            return self.soak.status()

    monkeypatch.setattr(lifecycle, "EntityKernel", _FakeKernel)

    class _FakeEvaluator:
        def __init__(self, bio=None):
            pass

        def evaluate_pending(self):
            return []

    import nova.outcome_evaluator as outcome_evaluator
    monkeypatch.setattr(outcome_evaluator, "OutcomeEvaluator", _FakeEvaluator)
    return _FakeKernel


def test_background_tick_event_does_not_embed_full_cognitive_snapshot(monkeypatch, tmp_path):
    """The persisted event must store a bounded summary, not the full snapshot."""
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    import web.api.nova_lifecycle as lifecycle

    _stub_kernel(monkeypatch, lifecycle)
    result = lifecycle.background_tick()
    assert result["ok"] is True

    # The return value keeps the full payload for callers.
    assert len(json.dumps(result["entity_tick"]["decision"]["state"])) >= 100_000
    assert len(result["soak"]["samples"]) == 400

    # The persisted event must NOT carry the full snapshots.
    events = lifecycle._load_all_events()
    event = next(e for e in events if e["type"] == "background_tick")
    persisted = json.dumps(event, ensure_ascii=False)
    assert len(persisted) < 20_000, (
        f"background_tick event persisted {len(persisted)} chars — the full "
        "entity_tick.decision.state / soak.samples snapshots are embedded in "
        "events.json and rewrite-amplify every subsequent event update"
    )
    assert "decision" not in event.get("entity_tick", {}) or "state" not in (event["entity_tick"].get("decision") or {})
    assert "samples" not in (event.get("soak") or {})


def test_background_tick_event_keeps_bounded_summary(monkeypatch, tmp_path):
    """The bounded summary must preserve the observability-relevant fields."""
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    import web.api.nova_lifecycle as lifecycle

    _stub_kernel(monkeypatch, lifecycle)
    lifecycle.background_tick()

    events = lifecycle._load_all_events()
    event = next(e for e in events if e["type"] == "background_tick")
    assert event["steps"][-1] == "substrate_done"
    tick_summary = event.get("entity_tick") or {}
    assert tick_summary.get("executed") is True
    soak_summary = event.get("soak") or {}
    assert soak_summary.get("complete") is True
    assert soak_summary.get("sample_count") == 400


def test_repeated_background_ticks_keep_events_json_small(monkeypatch, tmp_path):
    """Three ticks must not grow events.json into the megabyte range."""
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    import web.api.nova_lifecycle as lifecycle

    _stub_kernel(monkeypatch, lifecycle)
    for _ in range(3):
        result = lifecycle.background_tick()
        assert result["ok"] is True

    events_path = lifecycle.get_nova_state_paths().events
    size = events_path_size = events_path.stat().st_size
    assert size < 1_000_000, (
        f"events.json grew to {size} bytes after 3 background ticks — "
        "full cognitive snapshots are being persisted per tick"
    )