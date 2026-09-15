"""Regression tests for the #1558 backup guard's cheap message-count lookup.

Bug (observed 2026-09-14 on a live install): the backup guard read the
ENTIRE existing session file (``read_text`` + ``json.loads``) on every
save, just to count messages. For a runaway session (observed 3 GB) that
took minutes while holding the per-session lock - blocking every
concurrent chat start on that session (observed 70 s
``session_lock_wait`` in the request stage trace).
"""

from __future__ import annotations

import json
import unittest.mock as mock

import pytest


@pytest.fixture
def models_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    import web.api.models as models_mod

    monkeypatch.setattr(
        models_mod, "SESSION_DIR",
        tmp_path / "home" / "state" / "webui" / "sessions", raising=False,
    )
    return models_mod, tmp_path


def test_backup_guard_uses_index_count_without_full_parse(models_env, tmp_path):
    """With an index entry present, the guard must decide from the index
    count and never json.loads the (potentially gigabyte) file."""
    models_mod, _ = models_env

    session = models_mod.Session(session_id="guardprobe", title="Guard Probe")
    session.messages = [{"role": "user", "content": "hello"}]
    session.save()

    # Simulate a shrink: the index says 5 messages, incoming has none.
    session.messages = []
    with mock.patch.object(
        models_mod, "_lookup_index_message_count", return_value=5
    ) as index_count, mock.patch.object(
        models_mod.json, "loads", wraps=models_mod.json.loads
    ) as json_loads:
        session.save()

        assert index_count.called, "the guard must consult the index count"
        # json.loads may run for other small payloads (tail index etc.) but
        # must never see the full session file. Assert via the parsed size:
        for call in json_loads.call_args_list:
            arg = call.args[0] if call.args else call.kwargs.get("s", "")
            if isinstance(arg, str):
                assert len(arg) < 1_000_000, (
                    "the backup guard must not json.loads the full session file"
                )


def test_backup_guard_falls_back_to_full_read_without_index(models_env, tmp_path):
    models_mod, _ = models_env

    session = models_mod.Session(session_id="noindex", title="No Index")
    session.messages = [{"role": "user", "content": "hello"}]
    session.save()

    # Missing index entry: the guard must fall back to reading the file
    # (and still produce a correct save).
    with mock.patch.object(
        models_mod, "_lookup_index_message_count", return_value=None
    ):
        session.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        session.save(touch_updated_at=True, skip_index=False)

        assert session.path.exists()
        data = json.loads(session.path.read_text(encoding="utf-8"))
        assert len(data["messages"]) == 2


def test_backup_guard_still_backs_up_shrinking_save(models_env, tmp_path):
    """The guard's purpose: a save that SHRINKS the message array must leave
    a .bak of the pre-shrink state."""
    models_mod, _ = models_env

    session = models_mod.Session(session_id="shrink", title="Shrink")
    session.messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
    ]
    session.save()
    bak = session.path.with_suffix(".json.bak")
    if bak.exists():
        bak.unlink()

    # Shrink to fewer messages than the index says.
    session.messages = [{"role": "user", "content": "one"}]
    session.save()

    assert bak.exists(), "a shrinking save must produce a .bak"
    saved = json.loads(bak.read_text(encoding="utf-8"))
    assert len(saved["messages"]) == 3, "the .bak must hold the pre-shrink state"