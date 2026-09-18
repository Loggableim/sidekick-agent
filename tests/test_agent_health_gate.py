"""Tests for the agent-health poll gate (backlog item 30).

pollAgentHealth only checked document.visibilityState, so it kept hitting
/api/health/agent every 30s even when neither the health surface nor the
alert banner was on screen.
"""
from __future__ import annotations

from pathlib import Path

UI_JS = Path(__file__).resolve().parents[1] / "web" / "static" / "ui.js"
PANELS_JS = Path(__file__).resolve().parents[1] / "web" / "static" / "panels.js"


def test_gate_helpers_exist() -> None:
    source = UI_JS.read_text(encoding="utf-8")

    assert "function _agentHealthPanelIsVisible()" in source
    assert "function _agentHealthAlertIsActive()" in source
    assert "function _agentHealthShouldPoll()" in source


def test_gate_requires_visibility_and_a_surface() -> None:
    source = UI_JS.read_text(encoding="utf-8")
    start = source.index("function _agentHealthShouldPoll()")
    body = source[start : start + 300]

    assert "document.visibilityState === 'visible'" in body, "visibility check lost"
    assert "_agentHealthPanelIsVisible()" in body
    assert "_agentHealthAlertIsActive()" in body


def test_poll_and_monitor_use_the_gate() -> None:
    source = UI_JS.read_text(encoding="utf-8")

    poll_start = source.index("async function pollAgentHealth()")
    assert "_agentHealthShouldPoll()" in source[poll_start : poll_start + 200], (
        "pollAgentHealth does not use the gate"
    )

    start_start = source.index("function startAgentHealthMonitor()")
    assert "_agentHealthShouldPoll()" in source[start_start : start_start + 200], (
        "startAgentHealthMonitor does not use the gate"
    )


def test_panel_switch_re_evaluates_the_gate() -> None:
    """The gate depends on the visible panel, so switchPanel must re-check it."""
    panels = PANELS_JS.read_text(encoding="utf-8")
    assert "_syncAgentHealthMonitorVisibility" in panels, (
        "a panel switch does not re-evaluate the agent-health gate"
    )
