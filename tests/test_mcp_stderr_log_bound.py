"""Regression tests for the shared MCP stderr log size bound.

Bug (observed 2026-09-14 on a live install): ``mcp-stderr.log`` grew to
2.75 MB / 50k lines of periodic ``Received list tools request`` DEBUG
output. The handle is opened in append mode once per process and never
rotates, so the file grows without bound for the lifetime of the install.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh_stderr_log(monkeypatch, tmp_path):
    """Isolate the stderr-log path and reset the module-level handle cache."""
    import tools.mcp_tool as mcp_tool

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    import runtime._compat.shim_constants as shim_constants
    # get_sidekick_home() must return the PARENT of the logs dir: the
    # function appends / "logs" itself.
    monkeypatch.setattr(shim_constants, "get_sidekick_home", lambda: tmp_path)
    monkeypatch.setattr(mcp_tool, "_mcp_stderr_log_fh", None)

    yield log_dir, mcp_tool

    fh = mcp_tool._mcp_stderr_log_fh
    if fh is not None and fh is not mcp_tool.sys.stderr:
        try:
            fh.close()
        except Exception:
            pass
    monkeypatch.setattr(mcp_tool, "_mcp_stderr_log_fh", None)


def test_stderr_log_trimmed_when_over_bound(fresh_stderr_log, monkeypatch):
    log_dir, mcp_tool = fresh_stderr_log
    log_path = log_dir / "mcp-stderr.log"
    # Pre-fill beyond the bound with line-oriented debug spam.
    spam_line = b"[DEBUG] Received list tools request\n"
    payload = spam_line * ((mcp_tool._MAX_MCP_STDERR_BYTES // len(spam_line)) + 100)
    log_path.write_bytes(payload)
    assert log_path.stat().st_size > mcp_tool._MAX_MCP_STDERR_BYTES

    fh = mcp_tool._get_mcp_stderr_log()

    assert log_path.stat().st_size <= mcp_tool._MAX_MCP_STDERR_BYTES, (
        "oversized stderr log must be trimmed to the keep bound at open time"
    )
    # The kept tail must still contain complete lines of recent context.
    content = log_path.read_bytes()
    assert content.startswith(b"[DEBUG]"), "tail must start at a line boundary"
    # The handle must be usable.
    fh.write("[DEBUG] after-trim\n")
    fh.flush()
    assert b"after-trim" in log_path.read_bytes()


def test_stderr_log_untouched_when_under_bound(fresh_stderr_log):
    log_dir, mcp_tool = fresh_stderr_log
    log_path = log_dir / "mcp-stderr.log"
    log_path.write_bytes(b"[DEBUG] small log\n")

    mcp_tool._get_mcp_stderr_log()

    assert log_path.read_bytes() == b"[DEBUG] small log\n"


def test_stderr_log_missing_file_opens_clean(fresh_stderr_log):
    log_dir, mcp_tool = fresh_stderr_log
    log_path = log_dir / "mcp-stderr.log"
    assert not log_path.exists()

    fh = mcp_tool._get_mcp_stderr_log()

    assert log_path.exists()  # created by the append-mode open