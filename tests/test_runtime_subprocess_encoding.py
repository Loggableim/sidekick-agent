from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace


def test_gateway_status_terminate_pid_uses_utf8_encoding(monkeypatch):
    import runtime.gateway.status as status

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(status, "_IS_WINDOWS", True)
    monkeypatch.setattr(status.subprocess, "run", fake_run)

    status.terminate_pid(1234, force=True)

    assert captured["cmd"] == ["taskkill", "/PID", "1234", "/T", "/F"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_skill_preprocessing_run_inline_shell_uses_utf8_encoding(monkeypatch, tmp_path):
    import runtime.skill_preprocessing as skill_preprocessing

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="hello\n", stderr="")

    monkeypatch.setattr(skill_preprocessing.subprocess, "run", fake_run)

    assert skill_preprocessing.run_inline_shell("echo hello", tmp_path, 5) == "hello"
    assert captured["cmd"] == ["bash", "-c", "echo hello"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_shell_hooks_spawn_uses_utf8_encoding(monkeypatch):
    import runtime.shell_hooks as shell_hooks

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout='{"ok": true}\n', stderr="")

    monkeypatch.setattr(shell_hooks.subprocess, "run", fake_run)

    spec = shell_hooks.ShellHookSpec(event="post_llm_call", command="echo hook", timeout=5)
    result = shell_hooks._spawn(spec, "{\"hello\": \"world\"}")

    assert result["returncode"] == 0
    assert captured["cmd"] == ["echo", "hook"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_shutdown_forensics_spawn_async_diagnostic_uses_utf8_encoding(monkeypatch):
    import runtime.gateway.shutdown_forensics as shutdown_forensics

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ps tree\n", stderr="")

    monkeypatch.setattr(shutdown_forensics.subprocess, "run", fake_run)
    monkeypatch.setattr(shutdown_forensics.os, "listdir", lambda _path: [])

    result = asyncio.run(shutdown_forensics.spawn_async_diagnostic({"signal_name": "SIGTERM"}))

    assert result["ps_aux_output"] == "ps tree\n"
    assert captured["cmd"] == ["ps", "aux", "--forest"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_context_references_expand_git_reference_uses_utf8_encoding(monkeypatch, tmp_path):
    import runtime.context_references as context_references

    captured = {}
    ref = context_references.ContextReference(
        raw="@git:README.md",
        kind="git",
        target="README.md",
        start=0,
        end=13,
    )

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="diff output\n", stderr="")

    monkeypatch.setattr(context_references.subprocess, "run", fake_run)

    error, rendered = context_references._expand_git_reference(ref, Path(tmp_path), ["status"], "Diff")

    assert error is None
    assert "Diff" in rendered
    assert captured["cmd"] == ["git", "status"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_context_references_rg_files_uses_utf8_encoding(monkeypatch, tmp_path):
    import runtime.context_references as context_references

    captured = {}
    target = tmp_path / "folder"
    target.mkdir()

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="a.txt\nb.txt\n", stderr="")

    monkeypatch.setattr(context_references.subprocess, "run", fake_run)

    files = context_references._rg_files(target, tmp_path, 10)

    assert [path.name for path in files] == ["a.txt", "b.txt"]
    assert captured["cmd"] == ["rg", "--files", "folder"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_anthropic_detect_claude_code_version_uses_utf8_encoding(monkeypatch):
    import runtime.anthropic_adapter as anthropic_adapter

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="2.1.74 (Claude Code)\n", stderr="")

    monkeypatch.setattr(anthropic_adapter.subprocess, "run", fake_run)

    assert anthropic_adapter._detect_claude_code_version() == "2.1.74"
    assert captured["cmd"] == ["claude", "--version"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_anthropic_keychain_read_uses_utf8_encoding(monkeypatch):
    import runtime.anthropic_adapter as anthropic_adapter

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "token",
                        "refreshToken": "refresh",
                        "expiresAt": 123,
                    }
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr(anthropic_adapter.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(anthropic_adapter.subprocess, "run", fake_run)

    creds = anthropic_adapter._read_claude_code_credentials_from_keychain()

    assert creds["accessToken"] == "token"
    assert captured["cmd"] == [
        "security",
        "find-generic-password",
        "-s",
        "Claude Code-credentials",
        "-w",
    ]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_copilot_acp_client_run_prompt_uses_utf8_encoding(monkeypatch, tmp_path):
    import runtime.copilot_acp_client as copilot_acp_client

    captured = {}

    class FakeStdin:
        def __init__(self):
            self.writes = []

        def write(self, data):
            self.writes.append(data)
            return len(data)

        def flush(self):
            return None

    class FakeProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = StringIO(
                json.dumps({"id": 1, "result": {}}) + "\n"
                + json.dumps({"id": 2, "result": {"sessionId": "sess-1"}}) + "\n"
                + json.dumps({"id": 3, "result": {}}) + "\n"
            )
            self.stderr = StringIO("")
            self.returncode = 0
            self.terminated = False
            self.killed = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    def fake_popen(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(copilot_acp_client.subprocess, "Popen", fake_popen)

    client = copilot_acp_client.CopilotACPClient(
        acp_command="copilot-acp",
        acp_args=[],
        acp_cwd=str(tmp_path),
    )
    text, reasoning = client._run_prompt("hello", timeout_seconds=1)

    assert text == ""
    assert reasoning == ""
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"
