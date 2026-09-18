"""Tests for the throttled session prune (backlog item 7).

``verify_session`` runs on every authenticated request and used to prune (and
potentially write the session file) every time. The prune is now throttled to
one pass per interval so the normal verify path performs no file write.
"""
from __future__ import annotations

import importlib
import time

import pytest


def _fresh_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    from web.api import auth as auth_module

    importlib.reload(auth_module)
    return auth_module


def _count_writes(auth):
    writes = []
    original = auth._save_sessions

    def counting(sessions):
        writes.append(1)
        return original(sessions)

    auth._save_sessions = counting
    return writes, original


def test_verify_without_expired_entries_writes_nothing(monkeypatch, tmp_path):
    auth = _fresh_auth(monkeypatch, tmp_path)
    cookie = auth.create_session()

    writes, original = _count_writes(auth)
    try:
        for _ in range(20):
            assert auth.verify_session(cookie) is True
    finally:
        auth._save_sessions = original

    assert writes == [], "the verify path must not write the session file"


def test_prune_is_throttled_within_the_interval(monkeypatch, tmp_path):
    auth = _fresh_auth(monkeypatch, tmp_path)
    cookie = auth.create_session()
    auth.verify_session(cookie)  # consume the first prune window

    auth._sessions["expired-token"] = time.time() - 10
    writes, original = _count_writes(auth)
    try:
        for _ in range(10):
            auth.verify_session(cookie)
        within_window_writes = len(writes)
        still_present = "expired-token" in auth._sessions

        # Let the interval elapse: the next verify prunes and writes once.
        auth._last_prune_at -= auth._PRUNE_INTERVAL_SECONDS + 1
        writes.clear()
        auth.verify_session(cookie)
        after_window_writes = len(writes)
        removed = "expired-token" not in auth._sessions
    finally:
        auth._save_sessions = original

    assert within_window_writes == 0, "prune must be throttled inside the window"
    assert still_present, "the expired entry survives until the window elapses"
    assert after_window_writes == 1, "the prune must run once the window elapsed"
    assert removed, "the expired entry must be pruned"


def test_force_prune_bypasses_the_throttle(monkeypatch, tmp_path):
    auth = _fresh_auth(monkeypatch, tmp_path)
    auth.verify_session(auth.create_session())

    auth._sessions["expired-token"] = time.time() - 10
    writes, original = _count_writes(auth)
    try:
        auth._prune_expired_sessions(force=True)
    finally:
        auth._save_sessions = original

    assert len(writes) == 1
    assert "expired-token" not in auth._sessions
