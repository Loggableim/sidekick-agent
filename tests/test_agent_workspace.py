import queue
import importlib
import subprocess
import sys
from types import SimpleNamespace
from types import SimpleNamespace


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


def test_agent_workspace_uses_active_profile_home_after_import(monkeypatch, tmp_path):
    import yaml

    import_path_home = tmp_path / "import-home"
    active_home = tmp_path / "active-home"
    active_home.mkdir(parents=True)
    (active_home / ".env").write_text("OPENROUTER_API_KEY=active-key\n", encoding="utf-8")
    (active_home / "config.yaml").write_text("model:\n  default: gpt-4o\n", encoding="utf-8")

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("SIDEKICK_HOME", str(import_path_home))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    sys.modules.pop("web.api.agent_workspace", None)
    agent_workspace = importlib.import_module("web.api.agent_workspace")

    monkeypatch.setattr(agent_workspace, "get_active_webui_home", lambda: active_home)
    monkeypatch.setattr("web.api.config.resolve_active_provider_context", lambda: {})

    cfg = agent_workspace._get_llm_config()
    workspace = agent_workspace.ensure_agent_workspace("alpha")

    assert cfg["api_key"] == "active-key"
    assert cfg["model"] == "gpt-4o"
    assert workspace == str(active_home / "workspaces" / "alpha")
    assert (active_home / "workspaces" / "alpha" / "README.md").exists()
    assert not (import_path_home / "workspaces" / "alpha").exists()


def test_agent_workspace_llm_uses_remote_deepseek_in_game_mode(monkeypatch, tmp_path):
    from web.api import agent_workspace
    from web.api import config as cfg

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    monkeypatch.setattr(
        agent_workspace,
        "_get_llm_config",
        lambda: {
            "provider": "ollama",
            "api_key": "local-key",
            "model": "qwen3:4b",
            "base_url": "http://127.0.0.1:11434",
        },
    )

    captured = {}

    def fake_call_llm(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content='{"intent":"search","commands":[],"explanation":"ok","needs_confirmation":false}'))
            ]
        )

    monkeypatch.setattr("runtime.auxiliary_client.call_llm", fake_call_llm)
    monkeypatch.setattr(
        agent_workspace.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local agent workspace model must not be called in Game Mode")),
    )

    result = agent_workspace._call_llm([{"role": "user", "content": "find docs"}], timeout=20)

    assert captured["provider"] == "ollama-cloud"
    assert captured["model"] == "deepseek-v4-flash"
    assert result == '{"intent":"search","commands":[],"explanation":"ok","needs_confirmation":false}'


def test_agent_workspace_llm_stops_after_remote_game_mode_failure(monkeypatch, tmp_path):
    from web.api import agent_workspace
    from web.api import config as cfg

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})

    monkeypatch.setattr(
        agent_workspace,
        "_get_llm_config",
        lambda: {
            "provider": "ollama",
            "api_key": "local-key",
            "model": "qwen3:4b",
            "base_url": "http://127.0.0.1:11434",
        },
    )

    def fake_call_llm(**kwargs):
        raise RuntimeError("remote deepseek down")

    monkeypatch.setattr("runtime.auxiliary_client.call_llm", fake_call_llm)
    monkeypatch.setattr(
        agent_workspace.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local agent workspace model must not be called in Game Mode")),
    )

    result = agent_workspace._call_llm([{"role": "user", "content": "find docs"}], timeout=20)

    assert result is None
