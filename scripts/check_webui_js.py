#!/usr/bin/env python3
"""Parse-check every WebUI script with ``node --check``.

A single SyntaxError in one of the ``web/static/*.js`` files kills the whole
UI: the browser discards the entire file, so every function it defines becomes
``undefined`` and the symptoms look like "many broken features" (dead
navigation, empty Spaces panel, dead chat) rather than a syntax error. See the
``webui-js-parse-failure-triage`` skill.

This gate runs in CI so a broken file cannot reach master.

Usage:
    python scripts/check_webui_js.py            # check web/static/*.js
    python scripts/check_webui_js.py path ...   # check specific files

Exit code 0 when every file parses, 1 otherwise (with the node error output).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GLOB_DIR = REPO_ROOT / "web" / "static"


def _node_executable() -> str | None:
    return shutil.which("node")


def check_files(paths: list[Path], node: str) -> list[tuple[Path, str]]:
    """Return (path, error_output) for every file that fails ``node --check``."""
    failures: list[tuple[Path, str]] = []
    for path in paths:
        result = subprocess.run(
            [node, "--check", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            failures.append((path, (result.stderr or result.stdout).strip()))
    return failures


def main(argv: list[str]) -> int:
    node = _node_executable()
    if node is None:
        print("ERROR: node is not installed; cannot run the JS parse gate.")
        return 1

    if argv:
        paths = [Path(arg) for arg in argv]
    else:
        paths = sorted(DEFAULT_GLOB_DIR.glob("*.js"))

    if not paths:
        print(f"ERROR: no JS files found under {DEFAULT_GLOB_DIR}")
        return 1

    failures = check_files(paths, node)
    for path, error in failures:
        rel = path.relative_to(REPO_ROOT) if path.is_absolute() else path
        print(f"SYNTAX FAIL: {rel}")
        print(error)
        print()

    checked = len(paths)
    if failures:
        print(f"{len(failures)} of {checked} file(s) failed node --check")
        return 1

    print(f"node --check OK: {checked} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
