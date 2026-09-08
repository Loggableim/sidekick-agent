"""Execute browser navigation races against the shipped JavaScript with Node."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_space_navigation_races():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the WebUI navigation tests")
    test_file = Path(__file__).with_name("space_navigation.test.cjs")
    result = subprocess.run(
        [node, "--test", str(test_file)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
