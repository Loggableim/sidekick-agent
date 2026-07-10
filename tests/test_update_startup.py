import subprocess
import inspect
import sys
import textwrap
import os
from pathlib import Path
from types import SimpleNamespace


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _make_git_install(tmp_path: Path, branch: str = "master") -> Path:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    install = tmp_path / "install"

    _git(tmp_path, "init", "--bare", f"--initial-branch={branch}", str(remote))
    _git(tmp_path, "init", f"--initial-branch={branch}", str(seed))
    _git(seed, "config", "user.email", "test@example.com")
    _git(seed, "config", "user.name", "Sidekick Test")
    (seed / "README.md").write_text("sidekick\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", branch)

    _git(tmp_path, "clone", str(remote), str(install))
    return install


def test_loggableim_sidekick_origin_is_official_not_fork():
    import cli.main as main

    assert not main._is_fork("https://github.com/Loggableim/sidekick-agent.git")
    assert not main._is_fork("https://github.com/loggableim/sidekick-agent")
    assert not main._is_fork("git@github.com:Loggableim/sidekick-agent.git")


def test_resolve_remote_default_branch_prefers_origin_head_master(tmp_path):
    import cli.main as main

    install = _make_git_install(tmp_path, branch="master")
    _git(install, "fetch", "origin")

    assert main._resolve_remote_default_branch(["git"], install, "origin") == "master"


def test_update_check_uses_origin_default_branch_master(monkeypatch, tmp_path, capsys):
    import cli.main as main

    install = _make_git_install(tmp_path, branch="master")
    monkeypatch.setattr(main, "PROJECT_ROOT", install)

    main._cmd_update_check()

    assert "Already up to date" in capsys.readouterr().out


def test_windows_zip_update_fallback_uses_sidekick_master_archive():
    import cli.main as main

    source = inspect.getsource(main._update_via_zip)

    assert "Loggableim/sidekick-agent" in source
    assert 'branch = "master"' in source
    assert "NousResearch/sidekick-agent" not in source
    assert 'branch = "main"' not in source


def test_import_fastapi_web_server_does_not_run_git():
    script = textwrap.dedent(
        """
        import subprocess

        real_run = subprocess.run

        def blocked_run(cmd, *args, **kwargs):
            if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "git":
                raise AssertionError("cli.web_server import ran git")
            return real_run(cmd, *args, **kwargs)

        subprocess.run = blocked_run

        import cli.web_server
        assert cli.web_server.app.title == "Sidekick Agent"
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_web_server_run_git_uses_utf8_decode_guards(monkeypatch, tmp_path):
    import cli.web_server as web_server

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="status ok")

    monkeypatch.setattr(web_server.subprocess, "run", fake_run)

    assert web_server._run_git(tmp_path, "status") == "status ok"
    assert captured["cmd"] == ["git", "status"]
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_tui_node_bootstrap_uses_sidekick_home(monkeypatch, tmp_path):
    import cli.main as main

    repo_root = tmp_path / "repo"
    helper = repo_root / "scripts" / "lib" / "node-bootstrap.sh"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    captured = {}

    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "sidekick-home"))
    monkeypatch.setattr(main, "PROJECT_ROOT", repo_root)
    monkeypatch.setattr(main.shutil, "which", lambda _name: None)

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return SimpleNamespace(stdout="/tmp/node\n", returncode=0)

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    main._ensure_tui_node()

    assert captured["env"]["SIDEKICK_HOME"] == str(tmp_path / "sidekick-home")


def test_cleanup_gateway_service_restores_unset_sidekick_home(monkeypatch, tmp_path):
    import cli.profiles as profiles

    profile_dir = tmp_path / "profiles" / "coder"
    profile_dir.mkdir(parents=True)

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("cli.gateway.get_service_name", lambda: "sidekick-coder")
    monkeypatch.setattr("cli.gateway.get_launchd_plist_path", lambda: tmp_path / "unused.plist")

    profiles._cleanup_gateway_service("coder", profile_dir)

    assert os.environ.get("SIDEKICK_HOME") is None
