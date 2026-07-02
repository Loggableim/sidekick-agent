#!/usr/bin/env python3
"""Smoke test suite for the Sidekick monorepo — v0.3.0 expanded.

Run with: python tests/smoke_all.py

Exits 0 if all tests pass, non-zero on first failure.
"""
import subprocess
import sys
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS = 0
FAIL = 0

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def test(name: str, cmd: list[str], expect_ok: bool = True, grep: str | None = None, valid_exit_codes: set[int] | None = None):
    global PASS, FAIL
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, cwd=REPO)
        if valid_exit_codes is not None:
            ok = r.returncode in valid_exit_codes
        else:
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


def test_code(name: str, code: str, grep: str | None = None, timeout: int = 15):
    """Run Python code inline and check for expected output."""
    global PASS, FAIL
    try:
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=REPO,
            env={**os.environ, "PYTHONPATH": REPO},
        )
        ok = r.returncode == 0 and (not grep or grep in r.stdout + r.stderr)
        status = "✓" if ok else "✗"
        if ok:
            PASS += 1
        else:
            FAIL += 1
        print(f"  {status} {name}")
        if not ok:
            detail = (r.stderr or r.stdout)[:200]
            print(f"    → {detail}")
    except Exception as e:
        FAIL += 1
        print(f"  ✗ {name}: {e}")


print("=== Sidekick Monorepo Smoke Tests v0.3.0 ===\n")

# ── Core bootstrap ──
print("── Core bootstrap ──")

test("pip install -e .", [sys.executable, "-m", "pip", "install", "-e", "."])

test("sidekick --help", [sys.executable, "-m", "sidekick_app", "--help"], grep="usage: sidekick")

test("sidekick --version", [sys.executable, "-m", "sidekick_app", "--version"], grep="Sidekick Agent")

test("sidekick doctor", [sys.executable, "-m", "sidekick_app", "doctor"], valid_exit_codes={0, 1})

# ── Import smoke ──
print("\n── Import smoke ──")

test_code(
    "CLI import (cli.cli)",
    """from sidekick_app.__main__ import _ensure_self_first, _bootstrap_aliases
_ensure_self_first(); _bootstrap_aliases()
import cli.cli
print('OK')""",
    grep="OK"
)

test_code(
    "run_agent import (AIAgent)",
    """from sidekick_app.__main__ import _ensure_self_first, _bootstrap_aliases
_ensure_self_first(); _bootstrap_aliases()
import run_agent
print(f'AIAgent: {hasattr(run_agent, "AIAgent")}')""",
    grep="AIAgent"
)

test_code(
    "tools registry",
    """from sidekick_app.__main__ import _ensure_self_first, _bootstrap_aliases
_ensure_self_first(); _bootstrap_aliases()
from tools.registry import registry
print(f'tools={len(registry._snapshot_entries())}')""",
    grep="tools="
)

test_code(
    "web.server import",
    """from sidekick_app.__main__ import _ensure_self_first, _bootstrap_aliases
_ensure_self_first(); _bootstrap_aliases()
import web.server
print('OK')""",
    grep="OK"
)

test_code(
    "gateway.run import (0 warnings)",
    """import sys
from sidekick_app.__main__ import _ensure_self_first, _bootstrap_aliases
_ensure_self_first(); _bootstrap_aliases()
import io, logging
log = io.StringIO()
logging.basicConfig(stream=log)
import runtime.gateway.run
warnings = log.getvalue()
if 'Warning' in warnings or 'cannot import' in warnings:
    print('WARNINGS:', warnings[:200])
else:
    print('OK')""",
    grep="OK"
)

# ── Config / Env / Paths ──
print("\n── Config / Env / Paths ──")

test_code(
    "shared.paths: sidekick_home resolution",
    """import os
from shared.paths import sidekick_home
h = sidekick_home()
# Should be an absolute path with 'sidekick' or current SIDEKICK_HOME
print(f'home={h}')""",
    grep="home="
)

test_code(
    "shared.paths: env var priority (SIDEKICK > HERMES)",
    """import os
os.environ['SIDEKICK_HOME'] = '/tmp/sk-prio-test'
os.environ['HERMES_HOME'] = '/tmp/hermes-prio-test'
from shared.paths import sidekick_home
h = sidekick_home()
os.environ.pop('SIDEKICK_HOME', None)
os.environ.pop('HERMES_HOME', None)
# SIDEKICK_HOME should win
assert 'sk-prio-test' in str(h), f'Expected sk-prio-test, got {h}'
print('OK')""",
    grep="OK"
)

# ── Session layer ──
print("\n── Session layer ──")

test_code(
    "shared.sessions: create/append/retry/undo",
    """from shared.sessions import *
s = new_session(title='smoke-v030')
s = append_message(s.session_id, role='user', content='hello')
s = append_message(s.session_id, role='assistant', content='world')
s = append_message(s.session_id, role='user', content='question2')
s = append_message(s.session_id, role='assistant', content='answer2')
assert len(s.messages) == 4
# retry removes from last user onwards (2 items: question2 + answer2)
r = retry_last(s.session_id)
assert r['removed_count'] == 2, f'expected 2, got {r[\"removed_count\"]}'
s2 = load_session(s.session_id)
assert len(s2.messages) == 2
# undo removes from last user onwards
r2 = undo_last(s.session_id)
assert r2['removed_count'] == 2
s3 = load_session(s.session_id)
assert len(s3.messages) == 0
print('OK')""",
    grep="OK"
)

test_code(
    "web.api.session_ops import",
    """from sidekick_app.__main__ import _ensure_self_first, _bootstrap_aliases
_ensure_self_first(); _bootstrap_aliases()
import web.api.session_ops as sw
assert hasattr(sw, 'retry_last')
print('OK')""",
    grep="OK"
)

test_code(
    "shared.sessions: legacy migration mock",
    """import os, tempfile
from pathlib import Path
from shared.sessions import migrate_legacy_sessions
n = migrate_legacy_sessions()
print(f'migrated={n}')""",
    grep="migrated="
)

test_code(
    "shared.sessions: list_sessions",
    """from shared.sessions import new_session, list_sessions
s = new_session(title='list-test')
all_s = list_sessions()
print(f'count={len(all_s)}')""",
    grep="count="
)

# ── Session integrity test ──
test_code(
    "shared.sessions: session integrity (JSON on disk)",
    """from shared.sessions import new_session, append_message, sessions_dir
import json
s = new_session(title='integrity-all')
s = append_message(s.session_id, role='user', content='hello integrity')
s = append_message(s.session_id, role='assistant', content='check passed')
sess_dir = sessions_dir()
found = list(sess_dir.glob('*.json'))
ok = False
for f in found:
    if s.session_id in f.name or s.session_id[:8] in f.name:
        with open(f, encoding='utf-8') as fh:
            data = json.load(fh)
        msgs = data.get('messages', data.get('history', []))
        if any('hello integrity' in str(m) for m in msgs) and any('check passed' in str(m) for m in msgs):
            ok = True
            break
print('OK' if ok else f'MISSING: session_id={s.session_id} files={[str(f.name) for f in found[:5]]}')""",
    grep="OK"
)

# ── TUI import smoke ──
print("\n── TUI smoke ──")

test_code(
    "TUI module import (curses_ui)",
    """from sidekick_app.__main__ import _ensure_self_first, _bootstrap_aliases
_ensure_self_first(); _bootstrap_aliases()
import cli.curses_ui
print('OK')""",
    grep="OK"
)

# ── Branding regression: no user-facing Hermes/NousResearch ──
print("\n── Branding audit ──")

test_code(
    "Branding: no user-facing Hermes in help text",
    """import subprocess, sys
r = subprocess.run([sys.executable, '-m', 'sidekick_app', '--help'],
    capture_output=True, text=True, timeout=15)
lines = r.stdout + r.stderr
# Allowed: HERMES_ prefix for env-var docs (legacy compat)
# Blocked: 'Hermes' as product name in subcommand descriptions
import re
hits = [l for l in lines.split('\\n') if 'Hermes' in l and 'HERMES_' not in l]
if hits:
    print('FAIL: user-facing Hermes found:', hits[:3])
    exit(1)
print('OK')""",
    grep="OK"
)

# ── WebUI smoke test ──
print("\n── WebUI http smoke ──")

test_code(
    "webui smoke (tests/smoke_webui.py)",
    """import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath('')))
from tests.smoke_webui import main as webui_smoke
try:
    webui_smoke()
except SystemExit as e:
    sys.exit(e.code)
""",
    grep="passed",
    timeout=45,
)

test_code(
    "dashboard smoke (tests/smoke_dashboard.py)",
    """import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath('')))
from tests.smoke_dashboard import main as dashboard_smoke
try:
    dashboard_smoke()
except SystemExit as e:
    sys.exit(e.code)
""",
    grep="passed",
    timeout=45,
)

# ─── CI output ───
print(f"\n─── Ergebnis: {PASS} passed, {FAIL} failed ───")
raise SystemExit(0 if FAIL == 0 else 1)
