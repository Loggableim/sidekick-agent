import queue
import subprocess
import sys


def test_run_in_terminal_captures_output_on_windows_pipe(tmp_path):
    from web.api.agent_workspace import _run_in_terminal

    session = {
        "id": "session-1",
        "events": [],
        "event_queue": queue.Queue(),
        "workdir": str(tmp_path),
    }
    command = subprocess.list2cmdline(
        [sys.executable, "-c", "print('hello from workspace')"]
    )

    try:
        result = _run_in_terminal(session, command, timeout=5)
    finally:
        process = session.get("process")
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    assert result["exit_code"] == 0
    assert "hello from workspace" in result["output"]


class _NeverReadyStdout:
    def fileno(self):
        return 0

    def read(self):
        return ""

    def readline(self):
        return ""


class _StubbornProcess:
    def __init__(self, *args, **kwargs):
        self.pid = 4242
        self.stdout = _NeverReadyStdout()
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.returncode = -9

    def communicate(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return "", None


def test_run_in_terminal_kills_process_after_timeout(monkeypatch, tmp_path):
    from web.api import agent_workspace

    created = []

    def _fake_popen(*args, **kwargs):
        proc = _StubbornProcess(*args, **kwargs)
        created.append(proc)
        return proc

    monkeypatch.setattr(agent_workspace.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(agent_workspace.select, "select", lambda *args, **kwargs: ([], [], []))

    session = {
        "id": "session-timeout",
        "events": [],
        "event_queue": queue.Queue(),
        "workdir": str(tmp_path),
    }

    result = agent_workspace._run_in_terminal(session, "slow-command", timeout=0)

    assert created[0].terminated is True
    assert created[0].killed is True
    assert result["exit_code"] == -9
    assert "Timeout" in result["output"]
