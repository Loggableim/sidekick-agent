#!/usr/bin/env python3
"""Fix all hardcoded venv paths in install.ps1 — normalize to .venv"""
with open('install.ps1', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Add canonical VenvPath variables
old_config = '$PythonVersion = "3.11"'
new_config = '''$PythonVersion = "3.11"
$script:VenvPath = "$InstallDir\\.venv"
$script:PythonExe = "$script:VenvPath\\Scripts\\python.exe"
$script:SidekickExe = "$script:VenvPath\\Scripts\\sidekick.exe"'''

if old_config in text:
    text = text.replace(old_config, new_config, 1)
    print("1. Added $script:VenvPath, PythonExe, SidekickExe")

# 2. Replace all hardcoded venv references
count = 0
for old_str, new_str in [
    ('$env:VIRTUAL_ENV = "$InstallDir\\venv"', '$env:VIRTUAL_ENV = "$script:VenvPath"'),
    ('"$InstallDir\\venv\\Scripts\\python.exe"', '"$script:PythonExe"'),
    ('& ".\\venv\\Scripts\\python.exe"', '& "$script:PythonExe"'),
    ('$sidekickBin = "$InstallDir\\venv\\Scripts"', '$sidekickBin = "$script:VenvPath\\Scripts"'),
    ('"$InstallDir\\venv\\Scripts\\sidekick.exe"', '"$script:SidekickExe"'),
    ('$InstallDir\\.venv\\Scripts\\sidekick.exe', '"$script:SidekickExe"'),
]:
    n = text.count(old_str)
    if n > 0:
        text = text.replace(old_str, new_str)
        count += n
        print(f"  Replaced {n}x: {old_str[:60]}")

# 3. Fix dependency install to reference script-level variables correctly
# The venv args already use -p / --python with $script:PythonExe
# but the -NoVenv fallback path references $InstallDir\\venv directly
text = text.replace(
    '{ $pythonExe = if (-not $NoVenv) { "$InstallDir\\venv\\Scripts\\python.exe" } else { (& $UvCmd python find $PythonVersion) } }',
    '{ $pythonExe = if (-not $NoVenv) { $script:PythonExe } else { (& $UvCmd python find $PythonVersion) } }'
)
text = text.replace(
    '$pythonExe = "$InstallDir\\venv\\Scripts\\python.exe"',
    '$pythonExe = $script:PythonExe'
)

# 4. Fix sidekick setup command
text = text.replace(
    '& ".\\venv\\Scripts\\python.exe" -m sidekick_cli.main setup',
    '& $script:PythonExe -m sidekick_cli.main setup'
)

# 5. Fix desktop shortcut target path (already referenced .venv but via string concat)
text = text.replace(
    '$shortcut.TargetPath = "$InstallDir\\.venv\\Scripts\\sidekick.exe"',
    '$shortcut.TargetPath = $script:SidekickExe'
)
text = text.replace(
    '$sidekickExe = "$InstallDir\\.venv\\Scripts\\sidekick.exe"',
    '$sidekickExe = $script:SidekickExe'
)

# 6. Remove old $venvPython local variable in Ensure-Venv — use script-level $PythonExe
# Actually keep it as a local alias for clarity, just make sure it matches
text = text.replace(
    '    $venvPython = "$VenvPath\\Scripts\\python.exe"',
    '    $venvPython = "$VenvPath\\Scripts\\python.exe"  # local alias for $script:PythonExe'
)

with open('install.ps1', 'w', encoding='utf-8') as f:
    f.write(text)

# Verify
with open('install.ps1', 'r', encoding='utf-8') as f:
    final = f.read()

import re
bad_lines = []
for i, line in enumerate(final.split('\n'), 1):
    if '\\venv\\' in line and 'function ' not in line and not line.strip().startswith('#'):
        bad_lines.append((i, line.strip()))
        
print(f'\nRemaining \\\\venv\\\\ references: {len(bad_lines)}')
for ln, ll in bad_lines:
    print(f'  L{ln}: {ll[:90]}')
    
print(f'\nBraces: {final.count("{")}/{final.count("}")} {"OK" if final.count("{")==final.count("}") else "FAIL"}')
print(f'Parens: {final.count("(")}/{final.count(")")} {"OK" if final.count("(")==final.count(")") else "FAIL"}')
print(f'$script:VenvPath: {"✅" if "$script:VenvPath" in final else "❌"}')
print(f'$script:PythonExe: {"✅" if "$script:PythonExe" in final else "❌"}')