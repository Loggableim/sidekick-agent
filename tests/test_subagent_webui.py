"""Browser-contract coverage for the current-chat Subagent overview."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_subagent_overview_is_session_scoped_and_has_live_fallbacks():
    messages = (ROOT / "web" / "static" / "messages.js").read_text(encoding="utf-8")
    panels = (ROOT / "web" / "static" / "panels.js").read_text(encoding="utf-8")

    assert "function _subagentOverviewPath(" in messages
    assert "'/api/subagents?session_id='" in messages
    assert "'/api/subagents/events/stream?session_id='" in messages
    assert "EventSource" in messages
    assert "Last-Event-ID" not in messages  # EventSource owns its reconnect header.
    assert "_subagentLastEventId" in messages
    assert "function _subagentRenderSection(" in messages
    assert "Active" in messages
    assert "Waiting / paused" in messages
    assert "Completed" in messages
    assert "Failed" in messages
    assert "subagent-panel-detail" in messages
    assert "function loadSubagentsPanel(" in panels
    assert "_subagentCurrentSessionId" in panels


def test_subagent_overview_observation_paths_remain_read_only():
    messages = (ROOT / "web" / "static" / "messages.js").read_text(encoding="utf-8")
    start = messages.index("function _subagentOverviewPath(")
    end = messages.index("function _subagentSetOffline", start)
    observation = messages[start:end]

    assert "method: 'POST'" not in observation
    assert "method: \"POST\"" not in observation
    assert "api(_subagentOverviewPath(sid))" in messages
    assert "subagent-panel-offline" in messages
    assert "subagent-panel-empty" in messages
