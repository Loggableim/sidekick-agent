from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "config, expected",
    [
        ({}, {"enabled": False, "cleanup_on_exit": True}),
        ({"worktree": True}, {"enabled": True, "cleanup_on_exit": True}),
        ({"worktree": False}, {"enabled": False, "cleanup_on_exit": False}),
        ({"worktree": {"cleanup_on_exit": False}}, {"enabled": False, "cleanup_on_exit": False}),
        (
            {"worktree": {"enabled": True, "cleanup_on_exit": False}},
            {"enabled": True, "cleanup_on_exit": False},
        ),
    ],
)
def test_get_worktree_settings_normalizes_config(config, expected):
    from cli.config import get_worktree_settings

    assert get_worktree_settings(config) == expected


def test_launch_tui_skips_worktree_cleanup_when_disabled(monkeypatch, tmp_path):
    import cli.cli as cli_impl
    import cli.main as main

    cleanup_calls = []

    monkeypatch.setattr(
        "cli.config.get_worktree_settings",
        lambda config=None: {"enabled": True, "cleanup_on_exit": False},
    )
    monkeypatch.setattr(main, "_make_tui_argv", lambda tui_dir, tui_dev: (["node", "index.js"], Path(tmp_path)))
    monkeypatch.setattr(main.subprocess, "call", lambda *args, **kwargs: 0)
    monkeypatch.setattr(main, "_print_tui_exit_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli_impl, "_git_repo_root", lambda: str(tmp_path))
    monkeypatch.setattr(cli_impl, "_prune_stale_worktrees", lambda repo_root: None)
    monkeypatch.setattr(
        cli_impl,
        "_setup_worktree",
        lambda repo_root=None: {
            "path": str(tmp_path / "worktree"),
            "branch": "sidekick-test",
        },
    )
    monkeypatch.setattr(cli_impl, "_cleanup_worktree", lambda info=None: cleanup_calls.append(info))

    with pytest.raises(SystemExit) as excinfo:
        main._launch_tui(worktree=True)

    assert excinfo.value.code == 0
    assert cleanup_calls == []


def test_launch_tui_uses_configured_worktree_when_flag_is_off(monkeypatch, tmp_path):
    import cli.cli as cli_impl
    import cli.main as main

    setup_calls = []
    cleanup_calls = []

    monkeypatch.setattr(
        "cli.config.get_worktree_settings",
        lambda config=None: {"enabled": True, "cleanup_on_exit": True},
    )
    monkeypatch.setattr(main, "_make_tui_argv", lambda tui_dir, tui_dev: (["node", "index.js"], Path(tmp_path)))
    monkeypatch.setattr(main.subprocess, "call", lambda *args, **kwargs: 0)
    monkeypatch.setattr(main, "_print_tui_exit_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli_impl, "_git_repo_root", lambda: str(tmp_path))
    monkeypatch.setattr(cli_impl, "_prune_stale_worktrees", lambda repo_root: None)
    monkeypatch.setattr(
        cli_impl,
        "_setup_worktree",
        lambda repo_root=None: setup_calls.append(repo_root) or {
            "path": str(tmp_path / "worktree"),
            "branch": "sidekick-test",
        },
    )
    monkeypatch.setattr(cli_impl, "_cleanup_worktree", lambda info=None: cleanup_calls.append(info))

    with pytest.raises(SystemExit) as excinfo:
        main._launch_tui(worktree=False)

    assert excinfo.value.code == 0
    assert setup_calls == [None]
    assert cleanup_calls == [{"path": str(tmp_path / "worktree"), "branch": "sidekick-test"}]
