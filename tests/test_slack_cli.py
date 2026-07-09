from __future__ import annotations

import builtins
from types import SimpleNamespace


def test_slack_manifest_write_falls_back_to_hermes_home_when_sidekick_home_is_missing(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    import cli.slack_cli as slack_cli

    real_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "runtime._compat.shim_constants":
            raise ImportError("blocked for test")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    result = slack_cli.slack_manifest_command(SimpleNamespace(write=True))

    assert result == 0
    assert (hermes_home / "slack-manifest.json").exists()
