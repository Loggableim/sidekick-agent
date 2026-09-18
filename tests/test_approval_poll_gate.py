"""Contract tests for the cross-session approval poll (backlog item 21).

The poll feeds sidebar badges and toasts, so it must not keep hitting
/api/approval/pending-all in a background tab, and it must refresh once
immediately when the tab becomes visible again.
"""
from __future__ import annotations

from pathlib import Path

MESSAGES_JS = Path(__file__).resolve().parents[1] / "web" / "static" / "messages.js"


def test_approval_poll_is_gated_on_document_visibility() -> None:
    source = MESSAGES_JS.read_text(encoding="utf-8")

    # The active poll definition (the later one wins at runtime).
    active_start = source.rindex("async function _pollGlobalApprovals()")
    active_body = source[active_start : active_start + 400]
    assert "document.hidden" in active_body, (
        "the approval poll must skip the request while the tab is hidden"
    )


def test_approval_poll_refreshes_on_visibilitychange() -> None:
    source = MESSAGES_JS.read_text(encoding="utf-8")

    # Our handler is registered right after the poll helpers; other modules
    # register their own visibilitychange listeners, so match the exact block.
    marker = "document.addEventListener('visibilitychange', function () {"
    assert marker in source, "no visibilitychange handler for the approval poll"
    handler_start = source.index(marker)
    handler_body = source[handler_start : handler_start + 300]
    assert "document.hidden" in handler_body
    assert "_pollGlobalApprovals()" in handler_body, (
        "returning to the tab must poll immediately instead of waiting for the tick"
    )
    assert "_globalApprovalTimer" in handler_body, (
        "the resume poll must only run while the poll is active"
    )


def test_approval_poll_interval_is_not_the_old_3s_cadence() -> None:
    source = MESSAGES_JS.read_text(encoding="utf-8")

    active_start = source.rindex("function _startGlobalApprovalPoll()")
    active_body = source[active_start : active_start + 600]
    assert "setInterval(_pollGlobalApprovals, 8000)" in active_body, (
        "the visible-tab cadence must be relaxed from the old 3s interval"
    )
