"""Regression tests for the legacy session mirror size bound.

Bug (observed 2026-09-14 on a live install): a runaway agent session grew
to 3 GB and existed in THREE locations (primary session dir, workspace-
scoped dir, legacy STATE_DIR mirror) because ``Session._sync_legacy_
session_copy`` wrote the full payload to the legacy path on every save
with no size guard - ~9 GB of write I/O per chat message for that session.
The legacy mirror exists for backward compatibility with old WebUI
versions, which cannot usefully open a huge session anyway.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def isolated_session_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    import web.api.models as models_mod

    # Point the module-level session dir at the isolated tmp home.
    monkeypatch.setattr(models_mod, "SESSION_DIR", tmp_path / "home" / "state" / "webui" / "sessions", raising=False)
    return models_mod


def test_legacy_sync_skips_oversized_payload(isolated_session_env, tmp_path):
    models_mod = isolated_session_env

    class _Session(models_mod.Session):
        def __init__(self):
            # Bypass dataclass init; only the attributes used by the
            # legacy-sync path are needed. `path` is a read-only property
            # resolved from SESSION_DIR, which the fixture isolates.
            self.session_id = "runaway-session"

        def _legacy_session_path(self):
            return tmp_path / "legacy" / "runaway-session.json"

    session = _Session()
    oversized = "x" * (models_mod._MAX_LEGACY_SYNC_BYTES + 1024)
    session._sync_legacy_session_copy(oversized)

    assert not session._legacy_session_path().exists(), (
        "legacy mirror must not be written for oversized payloads"
    )


def test_legacy_sync_writes_normal_payload(isolated_session_env, tmp_path):
    models_mod = isolated_session_env

    class _Session(models_mod.Session):
        def __init__(self):
            self.session_id = "normal-session"

        def _legacy_session_path(self):
            return tmp_path / "legacy" / "normal-session.json"

    session = _Session()
    payload = json.dumps({"session_id": "normal-session", "messages": [{"role": "user", "content": "hi"}]})
    session._sync_legacy_session_copy(payload)

    legacy = session._legacy_session_path()
    assert legacy.exists()
    assert json.loads(legacy.read_text(encoding="utf-8"))["session_id"] == "normal-session"


def test_legacy_sync_skips_when_paths_match(isolated_session_env, tmp_path):
    models_mod = isolated_session_env
    same = tmp_path / "same" / "session.json"

    class _Session(models_mod.Session):
        def __init__(self):
            self.session_id = "same-path"

        def _legacy_session_path(self):
            return same

    session = _Session()
    session._sync_legacy_session_copy("{}")  # must not raise or write a .tmp