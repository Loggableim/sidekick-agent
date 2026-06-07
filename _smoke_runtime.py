"""Smoke test: verify all runtime modules can be imported."""
import sys
import os
import importlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Wire up sidekick_cli alias
from runtime._compat import shim_cli
sys.modules["sidekick_cli"] = shim_cli

import glob

runtime_files = sorted(glob.glob("runtime/[!_]*.py") + glob.glob("runtime/_compat/shim_*.py"))

runtime_mods = []
for f in runtime_files:
    mod = os.path.splitext(f.replace(os.sep, "."))[0]
    if mod.endswith("__init__") or "copy_" in mod or "fix_" in mod:
        continue
    runtime_mods.append(mod)

success = []
fail = []
for mod_name in runtime_mods:
    try:
        importlib.import_module(mod_name)
        success.append(mod_name)
    except Exception as e:
        fail.append((mod_name, str(e)))

print(f"OK: {len(success)}/{len(success) + len(fail)}")
for m in success:
    print(f"  [+] {m}")
if fail:
    print(f"\nFAIL: {len(fail)}")
    for m, e in fail:
        print(f"  [!] {m}: {e}")