import io
import json

from tools.process_registry import ProcessRegistry


class _ImmediateThread:
    def __init__(self, target, args=(), daemon=None, name=None):
        self._target = target
        self._args = args
        self.daemon = daemon
        self.name = name

    def start(self):
        self._target(*self._args)


class _ExitedPopen:
    def __init__(self, *args, **kwargs):
        self.pid = 12345
        self.stdout = io.StringIO("")
        self.stdin = io.StringIO()
        self.returncode = 0

    def wait(self, timeout=None):
        self.returncode = 0
        return self.returncode

    def poll(self):
        return self.returncode


def test_spawn_local_fast_process_not_left_in_running(monkeypatch, tmp_path):
    registry = ProcessRegistry()
    monkeypatch.setattr("tools.process_registry._find_shell", lambda: "bash")
    monkeypatch.setattr("tools.process_registry.subprocess.Popen", _ExitedPopen)
    monkeypatch.setattr("tools.process_registry.threading.Thread", _ImmediateThread)
    monkeypatch.setattr(registry, "_write_checkpoint", lambda: None)

    session = registry.spawn_local("true", cwd=str(tmp_path))

    assert session.id not in registry._running
    assert registry._finished[session.id] is session
    assert registry.poll(session.id)["status"] == "exited"


class _FailingEnv:
    def execute(self, command, timeout=10):
        raise RuntimeError("sandbox unavailable")


def test_spawn_via_env_start_failure_goes_to_finished(monkeypatch):
    registry = ProcessRegistry()
    monkeypatch.setattr(registry, "_write_checkpoint", lambda: None)

    session = registry.spawn_via_env(_FailingEnv(), "pytest")

    assert session.exited is True
    assert session.exit_code == -1
    assert session.id not in registry._running
    assert registry._finished[session.id] is session
    assert registry.poll(session.id)["status"] == "exited"


class _FakeTerminalEnv:
    env = {}


class _FailedStartSession:
    id = "proc_failedstart"
    pid = None
    exited = True
    exit_code = -1
    output_buffer = "Failed to start: sandbox unavailable"
    watcher_platform = ""


class _FakeProcessRegistry:
    pending_watchers = []

    def spawn_via_env(self, **kwargs):
        return _FailedStartSession()


def test_terminal_background_reports_env_start_failure(monkeypatch):
    import tools.process_registry as process_registry_module
    import tools.terminal_tool as terminal_tool_module

    monkeypatch.setenv("TERMINAL_ENV", "docker")
    with terminal_tool_module._env_lock:
        terminal_tool_module._active_environments.clear()
        terminal_tool_module._last_activity.clear()
    monkeypatch.setattr(terminal_tool_module, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(terminal_tool_module, "_check_all_guards", lambda command, env_type: {"approved": True})
    monkeypatch.setattr(terminal_tool_module, "_create_environment", lambda **kwargs: _FakeTerminalEnv())
    monkeypatch.setattr(process_registry_module, "process_registry", _FakeProcessRegistry())

    result = json.loads(
        terminal_tool_module.terminal_tool(
            command="pytest",
            background=True,
            task_id="test-terminal-start-failure",
        )
    )

    assert result["exit_code"] == -1
    assert result["status"] == "error"
    assert "Failed to start" in result["error"]


def test_terminal_notify_on_complete_survives_fast_local_exit(monkeypatch, tmp_path):
    import tools.process_registry as process_registry_module
    import tools.terminal_tool as terminal_tool_module

    registry = process_registry_module.process_registry
    with registry._lock:
        registry._running.clear()
        registry._finished.clear()
        registry._completion_consumed.clear()
        registry._completion_enqueued.clear()
    while not registry.completion_queue.empty():
        registry.completion_queue.get_nowait()
    with terminal_tool_module._env_lock:
        terminal_tool_module._active_environments.clear()
        terminal_tool_module._last_activity.clear()

    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    monkeypatch.setattr(terminal_tool_module, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(terminal_tool_module, "_check_all_guards", lambda command, env_type: {"approved": True})
    monkeypatch.setattr(process_registry_module, "_find_shell", lambda: "bash")
    monkeypatch.setattr(process_registry_module.subprocess, "Popen", _ExitedPopen)
    monkeypatch.setattr(process_registry_module.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(registry, "_write_checkpoint", lambda: None)

    result = json.loads(
        terminal_tool_module.terminal_tool(
            command="true",
            background=True,
            notify_on_complete=True,
            task_id="fast-notify",
            workdir=str(tmp_path),
        )
    )

    assert result["exit_code"] == 0
    event = registry.completion_queue.get_nowait()
    assert event["type"] == "completion"
    assert event["session_id"] == result["session_id"]
