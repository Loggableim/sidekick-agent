"""Regression tests for the log-rotation PermissionError fallback.

Bug (observed 2026-09-14 on a live install): ``agent.log`` grew to
138.8 MB with a 5 MB rotation limit - 27x over. On Windows the rename in
``RotatingFileHandler.doRollover`` fails with ``PermissionError`` whenever
another Sidekick process still holds the log open, and a long-running
WebUI holds it around the clock. The old fallback kept logging to the
current file with no bound, so the file grew unbounded.
"""

from __future__ import annotations

import logging

import pytest


def test_do_rollover_truncates_when_rename_is_blocked(tmp_path, monkeypatch):
    """When the rename fails permanently, the handler must truncate the
    current file instead of letting it grow unbounded."""
    from shared.logging_setup import SidekickRotatingFileHandler

    # Block the rename and the backup deletion from the start, like a
    # permanently-held Windows lock. RotatingFileHandler.doRollover uses
    # os.rename (not os.replace) plus os.remove for the .1 slot.
    def failing_op(*args, **kwargs):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr("logging.handlers.os.rename", failing_op)
    monkeypatch.setattr("logging.handlers.os.remove", failing_op)

    log_file = tmp_path / "agent.log"
    handler = SidekickRotatingFileHandler(
        log_file, maxBytes=1024, backupCount=2, encoding="utf-8"
    )
    logger = logging.getLogger("rollover-test")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        # Fill the file far beyond maxBytes with a single huge record - with
        # the rename blocked, the old code kept appending forever (the
        # 138.8 MB agent.log). A single oversized record avoids the
        # fill/rollover/truncate oscillation the fix produces on normal
        # records.
        logger.info("x" * 4096)
        handler.flush()
        assert log_file.stat().st_size > 1024, "test setup: file should exceed the limit"

        handler.doRollover()

        size = log_file.stat().st_size
        assert size <= 1024, (
            f"log file must be truncated when rollover fails (got {size} bytes)"
        )
        # The handler must remain usable after the fallback.
        logger.info("after-rollover")
        handler.flush()
        assert log_file.stat().st_size > 0
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_do_rollover_normal_path_still_rotates(tmp_path):
    """Without lock contention the standard rename-based rollover works."""
    from shared.logging_setup import SidekickRotatingFileHandler

    log_file = tmp_path / "agent.log"
    handler = SidekickRotatingFileHandler(
        log_file, maxBytes=1024, backupCount=2, encoding="utf-8"
    )
    logger = logging.getLogger("rollover-test-normal")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        blob = "y" * 512
        for _ in range(10):
            logger.info(blob)
        handler.flush()
        rotated = tmp_path / "agent.log.1"
        assert rotated.exists(), "normal rollover must rename to .1"
        assert log_file.stat().st_size <= 1024
    finally:
        logger.removeHandler(handler)
        handler.close()