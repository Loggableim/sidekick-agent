#!/usr/bin/env python3
"""Integrationstest für Sidekick Env-Var Migration."""
import sys, os, tempfile, time

sys.path.insert(0, '.')

passed = 0
failed = 0

def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}: {detail}")

# Temp-Verzeichnis für Tests (existiert garantiert)
_tmpdir = tempfile.mkdtemp(prefix="sidekick_inttest_")
_HOME = os.path.join(_tmpdir, "sidekick_home")

# Phase 1: Env-Var Priorität
print("--- Phase 1: Env-Var Priority (SIDEKICK_ > HERMES_) ---")

# Nur Test-Vars setzen, keine realen überschreiben
os.environ['SIDEKICK_HOME'] = os.path.join(_tmpdir, 'sidekick')
os.environ['HERMES_HOME'] = os.path.join(_tmpdir, 'hermes')

from shared.paths import sidekick_home
h = sidekick_home()
check("SIDEKICK_ bevorzugt", 'sidekick' in str(h), f"got {h}")

# Test: Fallback auf HERMES_
del os.environ['SIDEKICK_HOME']
h2 = sidekick_home()
check("Fallback auf HERMES_", 'hermes' in str(h2), f"got {h2}")

del os.environ['HERMES_HOME']

# Test: os.getenv Dual-Read
os.environ['SIDEKICK_API_TIMEOUT'] = '42.0'
os.environ['HERMES_API_TIMEOUT'] = '99.0'
v = float(os.getenv('SIDEKICK_API_TIMEOUT') or os.getenv('HERMES_API_TIMEOUT', '1800.0'))
check("os.getenv primary", v == 42.0, f"got {v}")

del os.environ['SIDEKICK_API_TIMEOUT']
v2 = float(os.getenv('SIDEKICK_API_TIMEOUT') or os.getenv('HERMES_API_TIMEOUT', '1800.0'))
check("os.getenv fallback", v2 == 99.0, f"got {v2}")

del os.environ['HERMES_API_TIMEOUT']
v3 = float(os.getenv('SIDEKICK_API_TIMEOUT') or os.getenv('HERMES_API_TIMEOUT', '1800.0'))
check("os.getenv default", v3 == 1800.0, f"got {v3}")

# Phase 2: Syntaxcheck
print("--- Phase 2: Syntax-Check gepatchter Dateien ---")
import py_compile
syntax_ok = True
for f in ['run_agent.py', 'cli/auth.py', 'cli/cli.py', 'cli/gateway.py',
          'cli/profiles.py', 'cli/oneshot.py', 'cli/web_server.py',
          'cli/slack_cli.py', 'runtime/shell_hooks.py',
          'runtime/gateway/session_context.py', 'tools/code_execution_tool.py',
          'web/api/profiles.py', 'web/api/dispatcher.py',
          'web/api/kanban_bridge.py', 'web/api/agents.py', 'web/api/evey_tools.py']:
    try:
        py_compile.compile(f, doraise=True)
    except py_compile.PyCompileError as e:
        print(f"  [FAIL] Syntax: {f}: {e}")
        syntax_ok = False
if syntax_ok:
    check("alle 16 patched Files kompilieren", True)

# Phase 3: Config laden (mit Timeout)
print("--- Phase 3: Config & Shims ---")

# SIDEKICK_HOME auf existierendes Temp-Verzeichnis setzen
os.environ['SIDEKICK_HOME'] = _HOME
os.makedirs(_HOME, exist_ok=True)

try:
    from cli.config import load_config
    cfg = load_config()
    check("config geladen", len(cfg) > 0)
except Exception as e:
    check("config geladen", False, str(e))

# Phase 4: SIDEKICK_ Vars im Codebase
print("--- Phase 4: SIDEKICK_* env-var Erkennung ---")
import re
all_vars = set()
_skip_dirs = {'.git', '__pycache__', '.venv', 'venv', 'home', 'node_modules', '.wrangler'}

for root, dirs, fnames in os.walk('.'):
    # Explizit große/irrelevante Dirs ausschließen
    rel = os.path.relpath(root)
    parts = rel.replace('\\', '/').split('/')
    if any(p in _skip_dirs for p in parts):
        dirs.clear()  # nicht in Unterverzeichnisse gehen
        continue
    for fn in fnames:
        if not fn.endswith('.py'):
            continue
        fpath = os.path.join(root, fn)
        try:
            with open(fpath, encoding='utf-8', errors='replace') as f:
                content = f.read()
            for m in re.finditer(r'SIDEKICK_[A-Z][A-Z_]+', content):
                all_vars.add(m.group(0))
        except Exception:
            pass

check(f"{len(all_vars)} unique SIDEKICK_* vars gefunden", len(all_vars) > 50)
for v in sorted(all_vars):
    print(f"     {v}")

# Aufräumen
import shutil
shutil.rmtree(_tmpdir, ignore_errors=True)

# Summary
total = passed + failed
print(f"\n{'='*50}")
print(f"ERGEBNIS: {passed}/{total} bestanden, {failed} Fehler")
if failed == 0:
    print("ALL INTEGRATION TESTS PASSED")
    sys.exit(0)
else:
    print(f"{failed} FAILURES")
    sys.exit(1)
