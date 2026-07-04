import subprocess
import inspect
from pathlib import Path


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
    assert "NousResearch/hermes-agent" not in source
    assert 'branch = "main"' not in source
