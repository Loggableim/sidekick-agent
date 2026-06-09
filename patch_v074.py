"""Patch install.ps1 for v0.7.4 — rewrite Python provisioning."""
import re

with open('install.ps1', 'r', encoding='utf-8') as f:
    text = f.read()

# Step 1: Replace Test-Python with Ensure-Venv
old_test = '''function Test-Python {
    Write-Info "Checking Python $PythonVersion..."
    
    # Let uv find or install Python
    try {
        $pythonPath = & $UvCmd python find $PythonVersion 2>$null
        if ($pythonPath) {
            $ver = & $pythonPath --version 2>$null
            Write-Success "Python found: $ver"
            $script:PythonExe = $pythonPath
            return $true
        }
    } catch { }
    
    # Python not found — use uv to install it (no admin needed!)
    Write-Info "Python $PythonVersion not found, installing via uv..."
    try {
        $uvOutput = & $UvCmd python install $PythonVersion 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pythonPath = & $UvCmd python find $PythonVersion 2>$null
            if ($pythonPath) {
                $ver = & $pythonPath --version 2>$null
                Write-Success "Python installed: $ver"
                $script:PythonExe = $pythonPath
                return $true
            }
        } else {
            Write-Warn "uv python install output:"
            Write-Host $uvOutput -ForegroundColor DarkGray
            try {
                $uvVer = & $UvCmd --version 2>&1
                Write-Info "uv version: $uvVer"
            } catch { }
            Write-Info "uv could not download Python $PythonVersion. This is often a network issue or missing prebuilt binary for this Windows version."
        }
    } catch {
        Write-Warn "uv python install error: $_"
    }

    Write-Info "Trying to find any existing Python 3.10+..."
    foreach ($fallbackVer in @("3.12", "3.13", "3.10")) {
        try {
            $pythonPath = & $UvCmd python find $fallbackVer 2>$null
            if ($pythonPath) {
                $ver = & $pythonPath --version 2>$null
                Write-Success "Found fallback: $ver"
                $script:PythonVersion = $fallbackVer
                $script:PythonExe = $pythonPath
                return $true
            }
        } catch { }
    }

    Write-Info "Scanning for system Python installations (excluding Microsoft Store alias)..."
    
    $candidateDirs = @()
    
    $pathDirs = $env:PATH -split ';'
    foreach ($dir in $pathDirs) {
        if ($dir -and $dir -notlike "*Microsoft*WindowsApps*" -and (Test-Path "$dir\\python.exe")) {
            $candidateDirs += $dir
        }
    }
    
    $localAppDataPython = "$env:LOCALAPPDATA\\Programs\\Python"
    if (Test-Path $localAppDataPython) {
        $subdirs = Get-ChildItem -Path $localAppDataPython -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^Python3' }
        foreach ($sub in $subdirs) {
            $candidateDirs += $sub.FullName
        }
    }
    
    $progFiles = "${env:ProgramFiles}\\Python*"
    Get-ChildItem -Path $progFiles -Directory -ErrorAction SilentlyContinue | ForEach-Object { $candidateDirs += $_.FullName }
    $progFiles86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ($progFiles86) {
        Get-ChildItem -Path "$progFiles86\\Python*" -Directory -ErrorAction SilentlyContinue | ForEach-Object { $candidateDirs += $_.FullName }
    }
    
    $seen = @{}
    foreach ($dir in $candidateDirs) {
        $exePath = "$dir\\python.exe"
        if (-not (Test-Path $exePath)) { continue }
        $normPath = (Resolve-Path $exePath).ProviderPath.ToLower()
        if ($seen.ContainsKey($normPath)) { continue }
        $seen[$normPath] = $true
        
        $ver = & $exePath --version 2>$null
        if ($ver -match "Python 3\\.(1[0-9]|[1-9][0-9])\\.\") {
            Write-Success "Found real Python: $ver at $exePath"
            $script:PythonExe = $exePath
            return $true
        }
    }
    
    Write-Err "No usable Python 3.10+ found on this system."
    Write-Info "Install Python 3.11 from https://www.python.org/downloads/"
    Write-Info "Make sure to check 'Add Python to PATH' during installation."
    Write-Info "Or run: winget install Python.Python.3.11"
    Write-Info "Log file: $LogFile"
    return $false
}'''

new_ensure = '''function Ensure-Venv {
    param([string]$VenvPath)
    
    $venvDir = Split-Path $VenvPath -Parent
    if (-not (Test-Path $venvDir)) { New-Item -ItemType Directory -Path $venvDir -Force | Out-Null }
    
    $venvPython = "$VenvPath\\Scripts\\python.exe"
    
    # ── 1. If venv already exists and has a working python, use it ──
    if (Test-Path $venvPython) {
        $ver = & $venvPython --version 2>$null
        if ($ver -match "Python 3\\.(1[0-9]|[1-9][0-9])") {
            Write-Success "Virtual environment found: $ver"
            $script:PythonExe = $venvPython
            return $true
        }
        Write-Warn "Virtual environment broken at $VenvPath -- recreating..."
        Remove-Item -Recurse -Force $VenvPath -ErrorAction SilentlyContinue
    }
    
    # ── 2. Create venv via uv (handles Python download + venv in one step) ──
    Write-Info "Creating virtual environment with uv (Python $PythonVersion)..."
    Add-Content -Path $LogFile -Value "[INFO] uv venv --python $PythonVersion `"$VenvPath`"" -Encoding UTF8 -ErrorAction SilentlyContinue
    
    try {
        $uvOutput = & $UvCmd venv --python $PythonVersion "$VenvPath" 2>&1
        $exitCode = $LASTEXITCODE
        Add-Content -Path $LogFile -Value "[INFO] uv venv exit code: $exitCode" -Encoding UTF8 -ErrorAction SilentlyContinue
        Add-Content -Path $LogFile -Value "[INFO] uv venv output: $uvOutput" -Encoding UTF8 -ErrorAction SilentlyContinue
        
        if ($exitCode -eq 0 -and (Test-Path $venvPython)) {
            $ver = & $venvPython --version 2>$null
            Write-Success "Virtual environment created: $ver"
            $script:PythonExe = $venvPython
            return $true
        }
        Write-Warn "uv venv failed (exit $exitCode). Trying alternate path..."
    } catch {
        Write-Warn "uv venv error: $_"
        Add-Content -Path $LogFile -Value "[ERR] uv venv exception: $_" -Encoding UTF8 -ErrorAction SilentlyContinue
    }
    
    # ── 3. Fallback: uv python install + python -m venv ──
    Write-Info "Trying uv python install path..."
    $pythonPath = $null
    try { $pythonPath = & $UvCmd python find $PythonVersion 2>$null } catch { }
    
    if (-not $pythonPath) {
        Write-Info "Downloading Python $PythonVersion via uv..."
        try {
            $installOutput = & $UvCmd python install $PythonVersion 2>&1
            $installExit = $LASTEXITCODE
            Add-Content -Path $LogFile -Value "[INFO] uv python install exit code: $installExit" -Encoding UTF8 -ErrorAction SilentlyContinue
            Add-Content -Path $LogFile -Value "[INFO] uv python install output: $installOutput" -Encoding UTF8 -ErrorAction SilentlyContinue
            
            if ($installExit -eq 0) {
                try { $pythonPath = & $UvCmd python find $PythonVersion 2>$null } catch { }
            } else {
                Write-Info "Retrying Python download..."
                Start-Sleep -Seconds 2
                $installOutput = & $UvCmd python install $PythonVersion 2>&1
                $installExit = $LASTEXITCODE
                Add-Content -Path $LogFile -Value "[INFO] uv python install retry exit code: $installExit" -Encoding UTF8 -ErrorAction SilentlyContinue
                Add-Content -Path $LogFile -Value "[INFO] uv python install retry output: $installOutput" -Encoding UTF8 -ErrorAction SilentlyContinue
                if ($installExit -eq 0) {
                    try { $pythonPath = & $UvCmd python find $PythonVersion 2>$null } catch { }
                }
            }
        } catch {
            Write-Warn "uv python install error: $_"
            Add-Content -Path $LogFile -Value "[ERR] uv python install exception: $_" -Encoding UTF8 -ErrorAction SilentlyContinue
        }
    }
    
    if ($pythonPath -and (Test-Path $pythonPath)) {
        $ver = & $pythonPath --version 2>$null
        Write-Success "Python provisioned: $ver"
        Write-Info "Creating virtual environment..."
        & $pythonPath -m venv "$VenvPath"
        if ($LASTEXITCODE -eq 0 -and (Test-Path $venvPython)) {
            $script:PythonExe = $venvPython
            Write-Success "Virtual environment ready"
            return $true
        }
    }
    
    # ── 4. Scan for system Python (excluding Store alias) ──
    Write-Info "Scanning for system Python installations (excluding Microsoft Store alias)..."
    $candidateDirs = @()
    foreach ($dir in ($env:PATH -split ';')) {
        if ($dir -and $dir -notlike "*Microsoft*WindowsApps*" -and (Test-Path "$dir\\python.exe")) {
            $candidateDirs += $dir
        }
    }
    $localPy = "$env:LOCALAPPDATA\\Programs\\Python"
    if (Test-Path $localPy) {
        Get-ChildItem $localPy -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^Python3' } | ForEach-Object { $candidateDirs += $_.FullName }
    }
    Get-ChildItem "${env:ProgramFiles}\\Python*" -Directory -ErrorAction SilentlyContinue | ForEach-Object { $candidateDirs += $_.FullName }
    $pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ($pf86) {
        Get-ChildItem "$pf86\\Python*" -Directory -ErrorAction SilentlyContinue | ForEach-Object { $candidateDirs += $_.FullName }
    }
    
    $seen = @{}
    foreach ($dir in $candidateDirs) {
        $exePath = "$dir\\python.exe"
        if (-not (Test-Path $exePath)) { continue }
        $normPath = (Resolve-Path $exePath).ProviderPath.ToLower()
        if ($seen.ContainsKey($normPath)) { continue }
        $seen[$normPath] = $true
        $ver = & $exePath --version 2>$null
        if ($ver -match "Python 3\\.(1[0-9]|[1-9][0-9])\\.") {
            Write-Success "Found system Python: $ver"
            Write-Info "Creating virtual environment..."
            & $exePath -m venv "$VenvPath"
            if ($LASTEXITCODE -eq 0 -and (Test-Path $venvPython)) {
                $script:PythonExe = $venvPython
                Write-Success "Virtual environment ready"
                return $true
            }
        }
    }
    
    Write-Err "No usable Python 3.10+ found."
    Write-Info "Install Python 3.11 from https://www.python.org/downloads/"
    Write-Info "Make sure to check 'Add Python to PATH' during installation."
    Write-Info "Or run: winget install Python.Python.3.11"
    Write-Info "Log file: $LogFile"
    return $false
}'''

assert old_test in text, "Test-Python not found!"
text = text.replace(old_test, new_ensure)

# Step 2: Remove Install-Venv function
match = re.search(r'function Install-Venv \{[^}]*?\n\}', text, re.DOTALL)
if match:
    text = text[:match.start()] + text[match.end():]
    print("Removed Install-Venv")

# Step 3: Update Main calls
text = text.replace(
    '    if (-not (Test-Python)) { Write-Err "Python $PythonVersion not available -- cannot continue" ; exit 2 }',
    '    if (-not (Ensure-Venv -VenvPath "$InstallDir\\venv")) { Write-Err "Python $PythonVersion not available -- cannot continue" ; exit 2 }'
)
text = text.replace(
    '    Install-Venv\n',
    ''
)

# Fix $venvPath references in desktop shortcut
text = text.replace(
    '$shortcut.TargetPath = "$venvPath\\Scripts\\sidekick.exe"',
    '$shortcut.TargetPath = "$InstallDir\\venv\\Scripts\\sidekick.exe"'
)
text = text.replace(
    '$sidekickExe = "$venvPath\\Scripts\\sidekick.exe"',
    '$sidekickExe = "$InstallDir\\venv\\Scripts\\sidekick.exe"'
)

with open('install.ps1', 'w', encoding='utf-8') as f:
    f.write(text)

# Verify
b = [text.count(c) for c in '{}']
p = [text.count(c) for c in '()']
print(f"Brace: {'OK' if b[0]==b[1] else 'MISMATCH'} ({{:{b[0]}}} }}:{b[1]})")
print(f"Paren: {'OK' if p[0]==p[1] else 'MISMATCH'} (:{p[0]} :{p[1]})")
print(f"Ensure-Venv present: {'function Ensure-Venv' in text}")
print(f"Test-Python present: {'function Test-Python' in text}")
print(f"Install-Venv present: {'function Install-Venv' in text}")
iwr = [l for l in text.split('\n') if 'Invoke-WebRequest' in l and 'Write-Host' not in l]
print(f"Invoke-WebRequest without UseBasicParsing: {len([l for l in iwr if '-UseBasicParsing' not in l])}")
print(f"$venvPath references fixed: ${'$venvPath'} references 0" if '$venvPath' not in text else f"WARNING: $venvPath still present!")
print(f"File size: {len(text)} chars")
