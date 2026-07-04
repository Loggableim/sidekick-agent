import os
import subprocess
import sys


def test_cli_env_loader_keeps_legacy_load_hermes_alias():
    from cli import env_loader

    assert callable(env_loader.load_sidekick_dotenv)
    assert callable(env_loader.load_hermes_dotenv)
    assert env_loader.load_hermes_dotenv is env_loader.load_sidekick_dotenv


def test_sidekick_cli_main_module_entrypoint_is_available():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-m", "sidekick_cli.main", "--version"],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0
    assert "Sidekick" in result.stdout


def test_sidekick_app_module_entrypoint_is_available():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-m", "sidekick_app", "--version"],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert "Sidekick Agent" in result.stdout
