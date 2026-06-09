with open('install.ps1') as f:
    t = f.read()

checks = [
    ('VenvPath outside repo', '$script:VenvPath = "$SidekickHome\.venv"' in t),
    ('uv pip install uses $pipArgs', 'uv pip install $pipArgs' in t),
    ('pipArgs = --python', '$pipArgs = "--python"' in t),
    ('No $VIRTUAL_ENV in Install-Dependencies', True), # verify manually
    ('Braces balanced', t.count('{') == t.count('}')),
    ('Parens balanced', t.count('(') == t.count(')')),
]
# Check no VIRTUAL_ENV in Install-Dependencies
deps_section = t.split('function Install-Dependencies')[1].split('function')[0] if 'function Install-Dependencies' in t else ''
checks.append(('No $VIRTUAL_ENV in Install-Dependencies', 'VIRTUAL_ENV' not in deps_section))

for name, ok in checks:
    print(f'  {"✅" if ok else "❌"} {name}')