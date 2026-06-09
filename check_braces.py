#!/usr/bin/env python3
"""Count braces in install.ps1, ignoring strings and comments."""
import re

with open('install.ps1', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove everything between double quotes (including $var expansion)
content = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', ' ', content)
# Remove single-quoted strings  
content = re.sub(r"'[^']*'", ' ', content)
# Remove comment lines
lines = content.split('\n')
cleaned = []
for line in lines:
    s = line.strip()
    if s.startswith('#') or s.startswith('<#'):
        continue
    # Remove inline comments (be careful with # in strings - already removed)
    # Simple: remove # that's not in a string
    idx = s.find(' # ')
    if idx > 0:
        s = s[:idx]
    cleaned.append(s)

text = '\n'.join(cleaned)

open_br = text.count('{')
close_br = text.count('}')
print(f'Code-only braces: open={open_br}, close={close_br}, diff={open_br - close_br}')

# Find which opens don't have matching closes
depth = 0
for i, line in enumerate(cleaned, 1):
    opens = line.count('{')
    closes = line.count('}')
    old_depth = depth
    depth += opens - closes
    if old_depth == 0 and depth > 0:
        print(f'  Depth+ at line {i}: {line[:80]}')
    if old_depth > 0 and depth == 0:
        print(f'  Depth- at line {i}: {line[:80]}')

if depth > 0:
    print(f'\nUNMATCHED: depth={depth} at end of file')
    # Backtrack to find where the unmatched open is
    depth = 0
    for i, line in enumerate(cleaned, 1):
        opens = line.count('{')
        closes = line.count('}')
        depth += opens - closes
        if opens > closes:
            print(f'  Net open at line {i}: {line[:80]}')

try_c = sum(1 for l in cleaned if l.strip().startswith('try') and ('{' in l or l.strip() == 'try'))
catch_c = sum(1 for l in cleaned if l.strip().startswith('catch') or l.strip().startswith('} catch') or l.strip() == '} catch {')
finally_c = sum(1 for l in cleaned if l.strip().startswith('finally') or l.strip().startswith('} finally'))

print(f'try: {try_c}, catch: {catch_c}, finally: {finally_c}')
print(f'try - catch - finally = {try_c - catch_c - finally_c}')
