import os
import subprocess
import sys


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
