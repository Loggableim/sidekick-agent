"""Contract tests for the deduped global approval poll (backlog item 22).

messages.js carried two definitions of _startGlobalApprovalPoll /
_stopGlobalApprovalPoll. Only the later pair runs (the earlier one is
overwritten at parse time), so the earlier block was dead code that still
allocated a timer variable and a Set.
"""
from __future__ import annotations

from pathlib import Path

MESSAGES_JS = Path(__file__).resolve().parents[1] / "web" / "static" / "messages.js"


def test_approval_poll_helpers_are_defined_once() -> None:
    source = MESSAGES_JS.read_text(encoding="utf-8")

    assert source.count("function _startGlobalApprovalPoll(") == 1
    assert source.count("function _stopGlobalApprovalPoll(") == 1


def test_dead_poll_state_is_gone() -> None:
    source = MESSAGES_JS.read_text(encoding="utf-8")

    # The dead block's timer variable must not linger (it would be a second,
    # unused timer slot that looks like live state).
    assert "_globalApprovalPollTimer" not in source
    assert "_globalApprovalSessionsSeen" not in source


def test_single_timer_guard_is_present() -> None:
    source = MESSAGES_JS.read_text(encoding="utf-8")

    start = source.index("function _startGlobalApprovalPoll(")
    body = source[start : start + 400]
    assert "if (_globalApprovalTimer) return;" in body, (
        "starting the poll twice must not create a second timer"
    )
