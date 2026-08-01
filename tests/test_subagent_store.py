from __future__ import annotations

import time

from tools.subagent_store import SubagentStore


def test_run_and_events_are_redacted_idempotent_and_bounded(tmp_path):
    """A duplicate sequence must not expose secrets or grow the event stream."""
    store = SubagentStore(tmp_path)
    store.record_run(
        subagent_id="sa-1",
        session_id="chat-1",
        space_slug="nova",
        goal="Inspect C:\\Users\\alice\\private\\token.txt with sk-secret-value",
        role="leaf",
        model="model-a",
        status="queued",
    )

    assert store.append_event("sa-1", 1, "start", "Bearer top-secret") is True
    assert store.append_event("sa-1", 1, "start", "duplicate") is False
    for sequence in range(2, 203):
        assert store.append_event("sa-1", sequence, "progress", f"step {sequence}") is True

    run = store.get_run("sa-1")
    events = store.list_events("sa-1")

    assert run["status"] == "queued"
    assert "sk-secret-value" not in run["goal_summary"]
    assert "C:\\Users\\alice" not in run["goal_summary"]
    assert len(events) == 200
    assert events[0]["sequence"] == 3
    assert "top-secret" not in " ".join(event["detail"] for event in events)


def test_reconcile_marks_stale_live_run_as_abandoned(tmp_path):
    """A server restart must not leave a stale worker shown as running."""
    store = SubagentStore(tmp_path)
    store.record_run(
        subagent_id="sa-stale",
        session_id="chat-1",
        space_slug="nova",
        goal="Verify deployment",
        role="leaf",
        model="model-a",
        status="running",
        started_at=time.time() - 120,
        heartbeat_at=time.time() - 120,
    )

    assert store.reconcile_stale_runs(max_age_seconds=30) == 1
    run = store.get_run("sa-stale")
    assert run["status"] == "abandoned"
    assert run["error_reason"] == "server_restart"
    assert store.list_events("sa-stale")[-1]["kind"] == "abandoned"


def test_retention_keeps_only_newest_thousand_runs(tmp_path):
    """History must remain bounded when a profile executes many delegates."""
    store = SubagentStore(tmp_path)
    base_time = time.time()
    for index in range(1002):
        store.record_run(
            subagent_id=f"sa-{index}",
            session_id="chat-1",
            space_slug="nova",
            goal="Task",
            role="leaf",
            model="model-a",
            status="completed",
            started_at=base_time + index,
        )

    assert store.count_runs() == 1000
    assert store.get_run("sa-0") is None
    assert store.get_run("sa-1001") is not None


def test_delegate_completion_is_persisted_without_changing_active_registry(monkeypatch, tmp_path):
    """A normal delegate must leave durable redacted history after it disappears live."""
    from tools import delegate_tool

    store = SubagentStore(tmp_path)
    monkeypatch.setattr(delegate_tool, "get_subagent_store", lambda: store, raising=False)

    class Child:
        _subagent_id = "sa-persisted"
        _delegate_depth = 1
        _parent_subagent_id = None
        _delegate_role = "leaf"
        session_id = "chat-1"
        model = "model-a"
        tool_progress_callback = None
        _credential_pool = None
        session_prompt_tokens = 0
        session_completion_tokens = 0
        session_estimated_cost_usd = 0.0
        session_reasoning_tokens = 0

        def run_conversation(self, **_kwargs):
            return {"final_response": "Finished C:\\private\\result.txt", "completed": True, "messages": []}

        def close(self):
            return None

    result = delegate_tool._run_single_child(0, "Inspect C:\\private\\goal.txt", Child())

    assert result["status"] == "completed"
    assert delegate_tool.list_active_subagents() == []
    run = store.get_run("sa-persisted")
    assert run["status"] == "completed"
    assert run["summary"] is None
    assert "Finished" not in " ".join(event["detail"] for event in store.list_events("sa-persisted"))
    assert "C:\\private" not in run["goal_summary"]
    assert [event["kind"] for event in store.list_events("sa-persisted")] == ["queued", "running", "completed"]


def test_event_sequence_rejects_out_of_order_event(tmp_path):
    """A late event cannot rewrite the persisted event timeline."""
    store = SubagentStore(tmp_path)
    store.record_run(subagent_id="sa-seq", session_id="chat", space_slug="nova", goal="Task", role="leaf", model="m")
    assert store.append_event("sa-seq", 2, "running", "later") is True
    assert store.append_event("sa-seq", 1, "queued", "earlier") is False
    assert [event["sequence"] for event in store.list_events("sa-seq")] == [2]


def test_read_only_store_does_not_create_a_database(tmp_path):
    """Read-only API consumers must not initialize state on a GET/SSE request."""
    missing_home = tmp_path / "missing-profile"
    store = SubagentStore(missing_home, read_only=True)
    assert store.get_run("missing") is None
    assert store.list_events("missing") == []
    assert not missing_home.exists()