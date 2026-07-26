from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def test_main_probe_container_uses_utf8_encoding(monkeypatch):
    import cli.main as main

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    result = main._probe_container(["docker", "inspect"], "docker")

    assert result.returncode == 0
    assert captured["cmd"] == ["docker", "inspect"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_cli_git_repo_root_uses_utf8_encoding(monkeypatch):
    import cli.cli as cli_impl
    import subprocess

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="/repo/root\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(cli_impl, "_normalize_git_bash_path", lambda value: value)

    assert cli_impl._git_repo_root() == "/repo/root"
    assert captured["cmd"] == ["git", "rev-parse", "--show-toplevel"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_gateway_get_service_pids_uses_utf8_encoding(monkeypatch):
    import cli.gateway as gateway

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="123\t0\tsidekick-gateway\n", stderr="")

    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: False)
    monkeypatch.setattr(gateway, "is_macos", lambda: True)
    monkeypatch.setattr(gateway, "get_launchd_label", lambda: "sidekick-gateway")
    monkeypatch.setattr(gateway.subprocess, "run", fake_run)

    assert gateway._get_service_pids() == {123}
    assert captured["cmd"] == ["launchctl", "list", "sidekick-gateway"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_profiles_check_alias_collision_uses_utf8_encoding(monkeypatch, tmp_path):
    import cli.profiles as profiles

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(profiles, "_get_wrapper_dir", lambda: tmp_path)
    monkeypatch.setattr(profiles.subprocess, "run", fake_run)

    assert profiles.check_alias_collision("example") is None
    assert captured["cmd"] == ["which", "example"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_profiles_seed_profile_skills_uses_utf8_encoding(monkeypatch, tmp_path):
    import cli.profiles as profiles

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"copied": ["a"], "updated": [], "user_modified": []}),
            stderr="",
        )

    monkeypatch.setattr(profiles, "has_bundled_skills_opt_out", lambda _path: False)
    monkeypatch.setattr(profiles.subprocess, "run", fake_run)

    payload = profiles.seed_profile_skills(tmp_path)

    assert payload["copied"] == ["a"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_tools_config_pip_install_uses_utf8_encoding(monkeypatch):
    import cli.tools_config as tools_config

    captured = {}

    monkeypatch.setattr(tools_config.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(tools_config.subprocess, "run", fake_run)

    result = tools_config._pip_install(["demo-package"])

    assert result.returncode == 0
    assert captured["cmd"][:3] == ["/usr/bin/uv", "pip", "install"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_tools_config_install_cua_driver_uses_utf8_encoding(monkeypatch):
    import cli.tools_config as tools_config
    import platform

    captured = {}

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        tools_config.shutil,
        "which",
        lambda name: "/usr/local/bin/cua-driver" if name == "cua-driver" else None,
    )
    monkeypatch.setattr(tools_config, "_print_success", lambda *args, **kwargs: None)
    monkeypatch.setattr(tools_config, "_print_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(tools_config, "_print_warning", lambda *args, **kwargs: None)

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="2.0.0\n", stderr="")

    monkeypatch.setattr(tools_config.subprocess, "run", fake_run)

    assert tools_config.install_cua_driver(upgrade=False) is True
    assert captured["cmd"] == ["cua-driver", "--version"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_tools_config_agent_browser_setup_uses_utf8_encoding(monkeypatch, tmp_path):
    import cli.tools_config as tools_config

    captured = {}
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setattr(tools_config, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(tools_config.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else "/usr/bin/npx")

    fake_browser_tool = ModuleType("tools.browser_tool")
    fake_browser_tool._chromium_installed = lambda: True
    fake_browser_tool._running_in_docker = lambda: False
    monkeypatch.setitem(sys.modules, "tools.browser_tool", fake_browser_tool)

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tools_config.subprocess, "run", fake_run)

    tools_config._run_post_setup("agent_browser")

    assert captured["cmd"] == ["/usr/bin/npm", "install", "--silent"]
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_selected_production_modules_use_utf8_for_text_subprocesses():
    modules = [
        "shared/agent_bridge.py",
        "cli/copilot_auth.py",
        "cli/gateway_windows.py",
        "cli/kanban_db.py",
        "tools/checkpoint_manager.py",
        "tools/environments/docker.py",
        "tools/environments/singularity.py",
        "tools/environments/ssh.py",
        "tools/morph_apply.py",
        "tools/morph_warpgrep.py",
        "tools/sidekick_memory.py",
        "tools/skills_hub.py",
        "tools/tirith_security.py",
        "tools/tts_tool.py",
        "tools/voice_mode.py",
        "tools/web_tools.py",
    ]

    missing: list[str] = []
    for rel in modules:
        text = Path(rel).read_text(encoding="utf-8")
        lines = text.splitlines()
        for index, line in enumerate(lines, 1):
            if "text=True" not in line or "subprocess" not in "\n".join(lines[max(0, index - 3): min(len(lines), index + 3)]):
                continue
            window = "\n".join(lines[max(0, index - 3): min(len(lines), index + 3)])
            if "encoding=" not in window and "errors=" not in window:
                missing.append(f"{rel}:{index}:{line.strip()}")

    assert not missing, "\n".join(missing)
