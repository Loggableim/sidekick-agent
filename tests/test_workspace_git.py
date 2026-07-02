import subprocess


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=True,
    )


def test_git_info_for_workspace_detects_nested_repo(tmp_path):
    from web.api.workspace import git_info_for_workspace

    repo = tmp_path / "repo"
    nested = repo / "app"
    nested.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    (nested / "feature.txt").write_text("nested change\n", encoding="utf-8")

    info = git_info_for_workspace(nested)

    assert info is not None
    assert info["is_git"] is True
    assert info["branch"] in {"master", "main"}
    assert info["dirty"] >= 1
    assert info["untracked"] == 1
