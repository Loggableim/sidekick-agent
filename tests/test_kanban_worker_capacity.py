"""Regression coverage for Kanban worker admission control."""

from types import SimpleNamespace
import threading
from contextlib import nullcontext


def _kanban_conn(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_KANBAN_HOME", str(tmp_path / "kanban-home"))
    monkeypatch.delenv("SIDEKICK_KANBAN_DB", raising=False)
    monkeypatch.delenv("SIDEKICK_KANBAN_BOARD", raising=False)
    monkeypatch.delenv("SIDEKICK_KANBAN_WORKSPACES_ROOT", raising=False)

    from cli import kanban_db as kb

    return kb, kb.connect()


def test_kanban_max_spawn_default_is_safe_and_configured():
    from cli.config import DEFAULT_CONFIG
    from cli import kanban_db as kb

    assert DEFAULT_CONFIG["kanban"]["max_spawn"] == 16
    assert kb.effective_max_spawn(None) == 16


def test_kanban_max_spawn_is_clamped_to_hard_worker_limit():
    from cli import kanban_db as kb

    assert kb.effective_max_spawn(0) == 1
    assert kb.effective_max_spawn(-5) == 1
    assert kb.effective_max_spawn("bad") == 16
    assert kb.effective_max_spawn(99) == 16
    assert kb.effective_max_spawn(7) == 7


def test_dispatch_once_leaves_overflow_tasks_ready(monkeypatch, tmp_path):
    kb, conn = _kanban_conn(monkeypatch, tmp_path)
    monkeypatch.setattr("cli.profiles.profile_exists", lambda _name: True)
    spawned = []

    try:
        for number in range(20):
            kb.create_task(conn, title=f"task {number}", assignee="worker")

        def spawn(task, _workspace, *, board=None):
            spawned.append((task.id, board))
            return 100_000 + len(spawned)

        result = kb.dispatch_once(conn, spawn_fn=spawn)

        assert len(result.spawned) == 16
        assert len(spawned) == 16
        states = dict(conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status"))
        assert states == {"ready": 4, "running": 16}
    finally:
        conn.close()


def test_legacy_dispatcher_reserves_a_slot_before_marking_task_running(monkeypatch):
    import web.api.dispatcher as dispatcher
    from web.api import kanban_bridge

    dispatcher._worker_semaphore = threading.BoundedSemaphore(1)
    patches = []
    spawns = []
    monkeypatch.setattr(
        kanban_bridge,
        "_patch_task",
        lambda _conn, task_id, update: patches.append((task_id, update)),
    )
    monkeypatch.setattr(
        dispatcher,
        "_spawn_worker",
        lambda *_args, **kwargs: spawns.append(kwargs) or True,
    )

    tasks = [
        SimpleNamespace(id="first", status="ready", assignee="worker"),
        SimpleNamespace(id="second", status="ready", assignee="worker"),
    ]
    result = {"found_ready": 0, "dispatched": 0}
    dispatcher._dispatch_ready_tasks(
        SimpleNamespace(), ["worker"], object(), tasks, "default", result
    )

    assert result == {"found_ready": 2, "dispatched": 1}
    assert patches == [("first", {"status": "in_progress"})]
    assert spawns == [{"slot_reserved": True}]


def test_gateway_worker_budget_is_shared_across_boards():
    from runtime.gateway import run

    running = {"first": 10, "second": 0}
    assert run._kanban_board_dispatch_cap(16, running, "first", 0) == 16
    assert run._kanban_board_dispatch_cap(16, running, "second", 6) == 0


def test_cli_dispatch_cannot_exceed_configured_worker_cap(monkeypatch):
    from cli import kanban
    from cli import config

    monkeypatch.setattr(config, "load_config", lambda: {"kanban": {"max_spawn": 7}})

    assert kanban._effective_dispatch_cap(None) == 7
    assert kanban._effective_dispatch_cap(100) == 7
    assert kanban._effective_dispatch_cap(3) == 3


def test_web_dispatch_cannot_exceed_configured_worker_cap(monkeypatch):
    from cli import kanban_db as kb
    from cli import config
    from web.api import kanban_bridge

    captured = {}
    monkeypatch.setattr(config, "load_config", lambda: {"kanban": {"max_spawn": 7}})
    monkeypatch.setattr(kanban_bridge, "_resolve_board", lambda _parsed: "default")
    monkeypatch.setattr(kanban_bridge, "_conn", lambda **_kwargs: nullcontext(object()))
    monkeypatch.setattr(
        kb,
        "dispatch_once",
        lambda _conn, **kwargs: captured.update(kwargs) or {"ok": True},
    )

    result = kanban_bridge._dispatch_payload(SimpleNamespace(query="max=100"))

    assert result == {"ok": True}
    assert captured["max_spawn"] == 7


def test_dead_worker_is_recovered_without_unbounded_respawn(monkeypatch, tmp_path):
    kb, conn = _kanban_conn(monkeypatch, tmp_path)
    monkeypatch.setattr("cli.profiles.profile_exists", lambda _name: True)

    try:
        task_id = kb.create_task(conn, title="recover", assignee="worker")
        kb.dispatch_once(conn, max_spawn=1, spawn_fn=lambda *_args, **_kwargs: 999_999)

        assert kb.detect_crashed_workers(conn) == [task_id]
        task = kb.get_task(conn, task_id)
        assert task is not None
        assert task.status == "ready"
        assert task.worker_pid is None
    finally:
        conn.close()
