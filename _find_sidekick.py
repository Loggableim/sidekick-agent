"""Find remaining SIDEKICK_ references in non-test Python code."""
import os, re

remaining = {}
for root, dirs, files in os.walk('.'):
    if any(p in root for p in ['__pycache__', '.git', 'node_modules', 'venv', '.venv']):
        continue
    for f in files:
        if not f.endswith('.py'):
            continue
        if f.startswith('test_') or f == '_find_sidekick.py':
            continue
        path = os.path.join(root, f)
        if '/tests/' in path.replace('\\', '/') or '\\tests\\' in path:
            continue
        with open(path, 'r', encoding='utf-8') as fh:
            for i, line in enumerate(fh, 1):
                if 'SIDEKICK_' not in line:
                    continue
                stripped = line.strip()
                if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith('*'):
                    continue
                if 'backward compat' in line:
                    continue
                remaining.setdefault(path, []).append((i, line.rstrip()[:150]))

if remaining:
    print(f'{len(remaining)} files with SIDEKICK_ code references:')
    for path, lines in sorted(remaining.items()):
        print(f'\n  {path}:')
        for i, l in lines:
            print(f'    L{i}: {l}')
else:
    print('NO remaining SIDEKICK_ code references!')
