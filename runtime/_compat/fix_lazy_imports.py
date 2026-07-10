#!/usr/bin/env python3
"""
Fix ALL remaining lazy (indented) compat imports in runtime/ files.
The initial copy script only matched line-start imports.
"""
import os
import re
import glob

DST_RUNTIME = r"C:\SidekickPortable\sidekick\runtime"

# Rewrite rules — applied globally with re.sub (matches anywhere in line)
REWRITE_RULES = [
    (r'from sidekick_constants import', 'from runtime._compat.shim_constants import'),
    (r'from sidekick_constants import', 'from runtime._compat.shim_constants import'),
    (r'from utils import', 'from shared.utils import'),
]

# Skip these files (scripts we wrote, generated, or compat shims)
SKIP_FILES = {
    '_compat\\copy_runtime_modules.py',
    '_compat\\copy_dependent_modules.py',
    '_compat\\fix_lazy_imports.py',
    '_compat\\shim_constants_v1.py',
    '_compat\\shim_constants_v2.py',
    '_compat\\shim_logging.py',
    '_compat\\shim_state.py',
    '_compat\\shim_bootstrap.py',
    '_compat\\shim_cli.py',
}

changed_count = 0
for pyfile in glob.glob(os.path.join(DST_RUNTIME, '**', '*.py'), recursive=True):
    rel = os.path.relpath(pyfile, DST_RUNTIME)
    if rel in SKIP_FILES:
        continue
    
    with open(pyfile, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original = content
    for old_pattern, new_pattern in REWRITE_RULES:
        content = re.sub(old_pattern, new_pattern, content)
    
    if content != original:
        with open(pyfile, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"  FIXED: {rel}")
        changed_count += 1

print(f"\nFixed {changed_count} file(s)")
