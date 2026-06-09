#!/usr/bin/env python3
"""Fix install.ps1: replace 2>&1 with temp-file stderr capture to avoid ErrorActionPreference=Stop issues."""
import sys, os

os.chdir("/c/HermesPortable/sidekick")

with open('install.ps1', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Add _Invoke helper before Ensure-Venv
OLD_DEF = 'function Ensure-Venv {'
NEW_HELPER = '''# Helper: run external command safely under ErrorActionPreference=Stop
function _Invoke {
    param([scriptblock]$Command)
    $stderrFile = [System.IO.Path]::GetTempFileName()
    try {
        $output = & $Command 2>$stderrFile
        $ec = $LASTEXITCODE
        $errText = Get-Content -Path $stderrFile -Raw -ErrorAction SilentlyContinue
        return @{ Output = $output; ExitCode = $ec; Stderr = $errText }
    } finally {
        Remove-Item -Path $stderrFile -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-Venv {'''

if OLD_DEF in text:
    text = text.replace(OLD_DEF, NEW_HELPER, 1)
    print("1. Added _Invoke helper")
else:
    print("1. ERROR: Ensure-Venv not found!")
    sys.exit(1)

# 2. Replace uv venv 2>&1
OLD1 = '$uvOut = & $UvCmd venv --python $PythonVersion "$VenvPath" 2>&1'
NEW1 = '$_r = _Invoke { & $UvCmd venv --python $PythonVersion "$VenvPath" }; $uvOut = $_r.Output; $uvExit = $_r.ExitCode; $_uvErr = $_r.Stderr'
if OLD1 in text:
    text = text.replace(OLD1, NEW1, 1)
    print("2. Replaced uv venv 2>&1")
    # Remove duplicate $uvExit assignment after it
    text = text.replace("$uvExit = $LASTEXITCODE\n", "", 1)
else:
    print("2. WARNING: uv venv 2>&1 pattern not found")

# 3. Replace uv python install 2>&1 (first occurrence)
OLD2 = '$installOut = & $UvCmd python install $PythonVersion 2>&1'
NEW2 = '$_r = _Invoke { & $UvCmd python install $PythonVersion }; $installOut = $_r.Output; $installExit = $_r.ExitCode'
if OLD2 in text:
    text = text.replace(OLD2, NEW2, 1)
    print("3. Replaced uv python install 2>&1")
    # Remove first $installExit = $LASTEXITCODE
    text = text.replace("$installExit = $LASTEXITCODE\n", "", 1)
else:
    print("3. WARNING: uv python install 2>&1 not found")

# 4. Replace retry uv python install 2>&1 (second occurrence)
OLD3 = '$installOut = & $UvCmd python install $PythonVersion 2>&1'
# If still present, replace again (retry version)
count = text.count(OLD3)
if count == 1:
    text = text.replace(OLD3, NEW2, 1)
    print("4. Replaced retry uv python install 2>&1")
elif count > 1:
    print(f"4. WARNING: {count} occurrences still remain")
else:
    print("4. Retry pattern already replaced")

# 5. Fix log output to also log stderr
# Add stderr to the uv venv log
text = text.replace(
    'if ($uvOut) {',
    'if ($_uvErr) { Add-Content -Path $LogFile -Value "[STDERR] uv venv: $_uvErr" -Encoding UTF8 -ErrorAction SilentlyContinue }\n        if ($uvOut) {'
)

# Add stderr to install log (find the right places - first install)
text = text.replace(
    'if ($installOut) {',
    'if ($_installErr) { Add-Content -Path $LogFile -Value "[STDERR] uv python install: $_installErr" -Encoding UTF8 -ErrorAction SilentlyContinue }\n        if ($installOut) {',
    1  # first install
)

# Actually the retry also needs stderr var. Let me check the retry block.
# The retry block already replaces the pattern above.

# 6. Final verification
b = [text.count(c) for c in '{}']
p = [text.count(c) for c in '()']
print(f"Braces: {b[0]}/{b[1]} {'OK' if b[0]==b[1] else 'FAIL'}")
print(f"Parens: {p[0]}/{p[1]} {'OK' if p[0]==p[1] else 'FAIL'}")

with open('install.ps1', 'w', encoding='utf-8') as f:
    f.write(text)

print("Written OK")