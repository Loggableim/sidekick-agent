"""Regression tests for stale chat stream reaping.

Bug (documented in the sidekick-agent skill, still live on master
2026-09-14): ``StreamChannel`` never carried its worker thread, so
``_cleanup_stale_streams`` never matched anything - and the function was
never called anywhere. When a streaming worker died without reaching its
``finally`` (killed process, hang in a C-level call like ssl recv), its
STREAMS entry leaked forever, and leaked entries block WebUI updates
("Cannot update webui while N active chat streams is running").
"""

from __future__ import annotations

import threading

import pytest


def test_cleanup_removes_entry_with_dead_thread():
    from web.api.config import StreamChannel, _cleanup_stale_streams, STREAMS, STREAMS_LOCK

    channel = StreamChannel()
    dead = threading.Thread(target=lambda: None, name="dead-worker")
    dead.start()
    dead.join()
    channel._thread = dead

    with STREAMS_LOCK:
        STREAMS["stale-probe"] = channel

    try:
        removed = _cleanup_stale_streams()
        assert removed >= 1
        with STREAMS_LOCK:
            assert "stale-probe" not in STREAMS
    finally:
        with STREAMS_LOCK:
            STREAMS.pop("stale-probe", None)


def test_cleanup_keeps_entry_with_live_thread():
    from web.api.config import StreamChannel, _cleanup_stale_streams, STREAMS, STREAMS_LOCK
    import threading as _thr

    channel = StreamChannel()
    alive = _thr.Thread(target=lambda: _thr.current_thread(), name="alive-probe")
    alive.start()
    alive.join()
    # A thread object that has finished is dead; simulate a live one instead
    # by using a thread that blocks on an event.
    import threading as _t
    release = _t.Event()
    live = _t.Thread(target=release.wait, name="alive-probe")
    live.start()
    channel._thread = live

    with STREAMS_LOCK:
        STREAMS["alive-probe"] = channel

    try:
        _cleanup_stale_streams()
        with STREAMS_LOCK:
            assert "alive-probe" in STREAMS, (
                "a live worker's stream entry must not be reaped"
            )
    finally:
        release.set()
        live.join(timeout=5)
        with STREAMS_LOCK:
            STREAMS.pop("alive-probe", None)


def test_streaming_worker_stamps_channel_thread(monkeypatch, tmp_path):
    """_run_agent_streaming must stamp the channel with its worker thread so
    the reaper can see it."""
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    import threading as _t
    import web.api.streaming as streaming
    import web.api.config as config

    stream_id = "stamp-probe"
    channel = config.StreamChannel()
    with config.STREAMS_LOCK:
        config.STREAMS[stream_id] = channel

    captured = {}

    class _FakeAgent:
        def __init__(self, *a, **k):
            pass

        def add_tool(self, *a, **k):
            return self

        def run(self, *a, **k):
            return "ok"

    # Patch the heavy machinery the worker touches, then run the worker in
    # a real thread and verify the stamp.
    monkeypatch.setattr(streaming, "create_stream_channel", lambda: channel, raising=False)
    monkeypatch.setattr(streaming, "AIAgent", _FakeAgent, raising=False)

    try:
        thr = _t.Thread(
            target=streaming._run_agent_streaming,
            args=("stamp-session", "hi", "model", str(tmp_path / "ws"), stream_id),
            daemon=True,
        )
        thr.start()
        thr.join(timeout=10)

        stamped = getattr(channel, "_thread", None)
        assert stamped is not None, "the worker must stamp the channel"
        assert not stamped.is_alive() or stamped is thr
    finally:
        with config.STREAMS_LOCK:
            config.STREAMS.pop(stream_id, None)