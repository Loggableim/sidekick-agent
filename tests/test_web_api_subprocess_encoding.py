from __future__ import annotations

from types import SimpleNamespace


def test_appstore_run_hermes_cli_uses_utf8_encoding(monkeypatch, tmp_path):
    import web.api.appstore as appstore

    captured = {}

    monkeypatch.setattr(appstore, "_VENV_PYTHON", tmp_path / "python.exe")
    monkeypatch.setattr(appstore, "_AGENT_DIR", tmp_path / "agent")

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="done\n")

    monkeypatch.setattr(appstore.subprocess, "run", fake_run)

    rc, out = appstore._run_hermes_cli(["tools", "status"])

    assert rc == 0
    assert out == "done"
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_startup_auto_install_agent_deps_uses_utf8_encoding(monkeypatch, tmp_path):
    import web.api.startup as startup

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "requirements.txt").write_text("demo\n", encoding="utf-8")

    captured = {}
    monkeypatch.setenv("SIDEKICK_WEBUI_AUTO_INSTALL", "1")
    monkeypatch.setattr(startup, "_agent_dir", lambda: agent_dir)
    monkeypatch.setattr(startup, "_trusted_agent_dir", lambda _path: True)

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="installed\n", stderr="")

    monkeypatch.setattr(startup.subprocess, "run", fake_run)

    assert startup.auto_install_agent_deps() is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_rollback_git_helpers_use_utf8_encoding(monkeypatch, tmp_path):
    import web.api.rollback as rollback

    checkpoint = tmp_path / "checkpoint"
    git_dir = checkpoint / ".git"
    git_dir.mkdir(parents=True)

    captured = []
    responses = [
        SimpleNamespace(returncode=0, stdout="abc123\ncheckpoint message\n2026-01-01T00:00:00+00:00\n"),
        SimpleNamespace(returncode=0, stdout="one\ntwo\n"),
    ]

    def fake_run(cmd, **kwargs):
        captured.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr(rollback.subprocess, "run", fake_run)

    info = rollback._inspect_checkpoint(checkpoint, "git")

    assert info is not None
    assert info["message"] == "checkpoint message"
    assert info["files"] == 2
    assert all(kwargs["encoding"] == "utf-8" for kwargs in captured)
    assert all(kwargs["errors"] == "replace" for kwargs in captured)


def test_updates_run_git_uses_utf8_encoding(monkeypatch, tmp_path):
    import web.api.updates as updates

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="status ok\n", stderr="")

    monkeypatch.setattr(updates.subprocess, "run", fake_run)

    out, ok = updates._run_git(["status"], tmp_path)

    assert ok is True
    assert out == "status ok"
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_workspace_run_git_uses_utf8_encoding(monkeypatch, tmp_path):
    import web.api.workspace as workspace

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="branch ok\n", stderr="")

    monkeypatch.setattr(workspace.subprocess, "run", fake_run)

    out = workspace._run_git(["rev-parse", "--abbrev-ref", "HEAD"], tmp_path)

    assert out == "branch ok"
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_worktrees_run_git_uses_utf8_encoding(monkeypatch, tmp_path):
    import web.api.worktrees as worktrees

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="worktree list\n", stderr="")

    monkeypatch.setattr(worktrees.subprocess, "run", fake_run)

    result = worktrees._run_git(["worktree", "list"], tmp_path)

    assert result.returncode == 0
    assert result.stdout == "worktree list\n"
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_worktrees_find_git_repo_root_uses_utf8_encoding(monkeypatch, tmp_path):
    import web.api.worktrees as worktrees

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=f"{repo}\n", stderr="")

    monkeypatch.setattr(worktrees.subprocess, "run", fake_run)

    root = worktrees.find_git_repo_root(repo)

    assert root == repo.resolve()
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"
