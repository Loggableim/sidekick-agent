#!/usr/bin/env python3
"""Smoke test suite for the Sidekick monorepo baseline.

Run with: python tests/smoke_all.py

Exits 0 if all tests pass, non-zero on first failure.
"""
import subprocess
import sys
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS = 0
FAIL = 0

def test(name: str, cmd: list[str], expect_ok: bool = True, grep: str | None = None):
    global PASS, FAIL
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=REPO)
        ok = (r.returncode == 0) == expect_ok
        if ok and grep:
            ok = grep in r.stdout + r.stderr
        status = "✓" if ok else "✗"
        if ok:
            PASS += 1
        else:
            FAIL += 1
            detail = (r.stderr or r.stdout)[:200]
        print(f"  {status} {name}")
        if not ok:
            print(f"    → {detail}")
    except Exception as e:
        FAIL += 1
        print(f"  ✗ {name}: {e}")

print("=== Sidekick Monorepo Baseline Smoke Tests ===\n")

# 1. pip install -e .
test("pip install", [sys.executable, "-m", "pip", "install", "-e", "."])

# 2. sidekick --help
test("sidekick --help", ["sidekick", "--help"], grep="usage: sidekick")

# 3. sidekick --version
test("sidekick --version", ["sidekick", "--version"], grep="Sidekick Agent")

# 4. sidekick doctor (first 3 lines)
test("sidekick doctor", ["sidekick", "doctor"], grep="Sidekick Doctor")

# 5. CLI module import test
test("CLI import", [sys.executable, "-c", """
import sys; sys.path.insert(0, '.')
import cli.cli
print('cli.cli OK')
"""], grep="cli.cli OK")

# 6. run_agent import (inside bootstrapped context)
test("run_agent import", [sys.executable, "-c", """
from sidekick_app.__main__ import _bootstrap_aliases, _ensure_self_first
_ensure_self_first()
_bootstrap_aliases()
import run_agent
print(f'run_agent AIAgent: {hasattr(run_agent, \"AIAgent\")}')
"""], grep="AIAgent")

# 7. tool registry
test("tools registry", [sys.executable, "-c", """
import sys; sys.path.insert(0, '.')
from runtime._compat import shim_cli, shim_constants, shim_state
sys.modules['sidekick_cli'] = shim_cli
sys.modules['sidekick_constants'] = shim_constants
sys.modules['sidekick_state'] = shim_state
from tools.registry import registry
n = len(registry._snapshot_entries())
print(f'OK tools={n}')
"""], grep="tools=")

# 8. web.server import
test("web.server import", [sys.executable, "-c", """
import sys; sys.path.insert(0, '.')
from runtime._compat import shim_cli, shim_constants, shim_state
sys.modules['sidekick_cli'] = shim_cli
sys.modules['sidekick_constants'] = shim_constants
sys.modules['sidekick_state'] = shim_state
import web.server
print('OK')
"""], grep="OK")

# 9. shared sessions
test("shared sessions", [sys.executable, "-c", """
import sys; sys.path.insert(0, '.')
from shared.sessions import new_session, list_sessions
s = new_session(title='smoke-test')
print(f'OK session={s.session_id}')
"""], grep="session=")

# 10. config load
test("config load", [sys.executable, "-c", """
import sys; sys.path.insert(0, '.')
from sidekick_app.__main__ import main
print('config bootstrap OK')
"""], grep="OK")

print(f"\n=== Ergebnis: {PASS} passed, {FAIL} failed ===")
raise SystemExit(0 if FAIL == 0 else 1)