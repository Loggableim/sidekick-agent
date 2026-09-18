"""Tests for the WebUI JS parse gate (backlog item 39).

A single SyntaxError in one of the web/static/*.js files kills the whole UI,
so CI must reject it. These tests pin the gate's behaviour and its CI wiring.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_webui_js.py"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
STATIC_DIR = REPO_ROOT / "web" / "static"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required for the parse gate"
)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )


def test_gate_passes_on_the_current_tree() -> None:
    result = _run([])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "node --check OK" in result.stdout


def test_gate_fails_on_a_syntax_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.js"
    broken.write_text("function broken( { return 1; }\n", encoding="utf-8")

    result = _run([str(broken)])
    assert result.returncode == 1
    assert "SYNTAX FAIL" in result.stdout


def test_gate_covers_every_static_script() -> None:
    expected = sorted(path.name for path in STATIC_DIR.glob("*.js"))
    assert expected, "no scripts found in web/static"

    result = _run([])
    assert f"{len(expected)} file(s)" in result.stdout


def test_ci_runs_the_gate() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "check_webui_js.py" in workflow, "CI does not run the JS parse gate"
