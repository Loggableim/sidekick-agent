# ============================================================================
# Sidekick Installer for Windows
# ============================================================================
# Installation script for Windows (PowerShell).
# Uses uv for fast Python provisioning and package management.
#
# Usage:
#   irm https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/install.ps1 | iex
#
# Or download and run with options:
#   .\install.ps1 -NoVenv -SkipSetup
#
# ============================================================================

# ============================================================================
# Flag parsing — simple $args parser (not param()) so irm | iex works
# ============================================================================
# Usage:
#   irm https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/install.ps1 | iex
#   .\install.ps1 -UpdateOnly
#   .\install.ps1 -UpdateOnly -NoPrompt
#   .\install.ps1 -SkipSetup -NoVenv -SkipOptionalTools
#   .\install.ps1 -Surface CliOnly -NoPrompt
# ============================================================================

# Defaults
$script:UpdateOnly       = $false
$script:NoVenv           = $false
$script:SkipSetup        = $false
$script:SkipOptionalTools = $false
$script:NoPrompt         = $false
$script:NoDoctor         = $false
$script:Surface          = "Browser"   # Browser | Standalone | CliOnly
$script:Mode             = "Admin"     # Admin | Portable (Portable = future)
$Branch                  = "master"
$script:WebUIStarted     = $false
$SidekickHome            = "$env:LOCALAPPDATA\sidekick"
$InstallDir              = "$env:LOCALAPPDATA\sidekick\sidekick-agent"

# Parse $args manually
$script:UnknownFlags = @()
$i = 0
while ($i -lt $args.Count) {
    $arg = $args[$i]
    $consumed = $true
    switch -Wildcard ($arg) {
        '-UpdateOnly'         { $script:UpdateOnly = $true }
        '-NoVenv'             { $script:NoVenv = $true }
        '-SkipSetup'          { $script:SkipSetup = $true }
        '-SkipOptionalTools'  { $script:SkipOptionalTools = $true }
        '-NoPrompt'           { $script:NoPrompt = $true }
        '-NoDoctor'           { $script:NoDoctor = $true }
        '-Surface' {
            $i++
            if ($i -lt $args.Count) {
                $val = $args[$i]
                if ($val -in @('Browser','Standalone','CliOnly')) {
                    $script:Surface = $val
                } else {
                    $script:UnknownFlags += "-Surface $val (invalid value)"
                }
            } else {
                $script:UnknownFlags += '-Surface (missing value)'
            }
        }
        '-Mode' {
            $i++
            if ($i -lt $args.Count) {
                $val = $args[$i]
                if ($val -in @('Admin','Portable')) {
                    $script:Mode = $val
                } else {
                    $script:UnknownFlags += "-Mode $val (invalid value)"
                }
            } else {
                $script:UnknownFlags += '-Mode (missing value)'
            }
        }
        default { $script:UnknownFlags += $arg }
    }
    $i++
}

if ($script:UnknownFlags.Count -gt 0) {
    Write-Host ""
    Write-Host "  Sidekick Installer" -ForegroundColor Cyan
    Write-Host "  ============================================================" -ForegroundColor DarkCyan
    Write-Host ""
    Write-Host "  Unknown flags: $($script:UnknownFlags -join ', ')" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Usage:" -ForegroundColor Yellow
    Write-Host "    irm https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/install.ps1 | iex"
    Write-Host ""
    Write-Host "  Flags (all optional):" -ForegroundColor Yellow
    Write-Host "    -UpdateOnly           Update an existing install (skip clone, deps, tools)"
    Write-Host "    -NoVenv               Skip venv creation (use system Python)"
    Write-Host "    -SkipSetup            Skip the sidekick setup wizard"
    Write-Host "    -SkipOptionalTools    Skip Node.js, ripgrep, ffmpeg"
    Write-Host "    -NoPrompt             Skip all interactive prompts"
    Write-Host "    -NoDoctor             Skip sidekick doctor post-install check"
    Write-Host "    -Surface <type>       Browser (default) | Standalone | CliOnly"
    Write-Host "    -Mode <type>          Admin (default) | Portable (future)"
    Write-Host ""
    Write-Host "  Examples:" -ForegroundColor Yellow
    Write-Host "    .\install.ps1 -UpdateOnly"
    Write-Host "    .\install.ps1 -UpdateOnly -NoPrompt"
    Write-Host "    .\install.ps1 -SkipSetup -SkipOptionalTools -Surface CliOnly"
    Write-Host "    .\install.ps1 -Surface Standalone -NoPrompt"
    Write-Host ""
    Pause-IfElevated -ExitCode 1
    exit 1
}

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ============================================================================
# Log file setup
# ============================================================================
$LogDir = "$env:LOCALAPPDATA\sidekick\logs"
$LogFile = "$LogDir\install-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# ============================================================================
# Exit Code Schema
# ============================================================================
# 0  Success
# 1  Generic failure (unhandled exception)
# 2  Missing prerequisite (Python/Git/uv install failed)
# 3  Network/download failure (timeout, DNS, HTTP)
# 4  Git/update failure (clone, fetch, checkout)
# 5  Install/venv failure (pip install, dependency install)
# 6  Verification failure (doctor/smoke check failed)
# ============================================================================

# ============================================================================
# Configuration
# ============================================================================

$RepoUrlSsh = "git@github.com:Loggableim/sidekick-agent.git"
$RepoUrlHttps = "https://github.com/Loggableim/sidekick-agent.git"
$PythonVersion = "3.11"

# ============================================================================
# Helper functions
# ============================================================================

function Write-Banner {
    Write-Host ""
    Write-Host "  ============================================================" -ForegroundColor DarkCyan
    Write-Host "   Sidekick" -ForegroundColor Cyan -NoNewline
    Write-Host "  standalone AI workspace" -ForegroundColor White
    Write-Host "  ------------------------------------------------------------" -ForegroundColor DarkCyan
    Write-Host "   Terminal agent + local WebUI + messaging gateway" -ForegroundColor DarkGray
    Write-Host "   Install target: $InstallDir" -ForegroundColor DarkGray
    Write-Host "  ============================================================" -ForegroundColor DarkCyan
    Write-Host ""
}

function Write-Info {
    param([string]$Message)
    Write-Host "  >  $Message" -ForegroundColor DarkCyan
    if ($LogFile) { Add-Content -Path $LogFile -Value "[INFO] $Message" -Encoding UTF8 -ErrorAction SilentlyContinue }
}

function Write-Success {
    param([string]$Message)
    Write-Host "  OK $Message" -ForegroundColor Green
    if ($LogFile) { Add-Content -Path $LogFile -Value "[DONE] $Message" -Encoding UTF8 -ErrorAction SilentlyContinue }
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  !! $Message" -ForegroundColor Yellow
    if ($LogFile) { Add-Content -Path $LogFile -Value "[WARN] $Message" -Encoding UTF8 -ErrorAction SilentlyContinue }
}

function Write-Err {
    param([string]$Message)
    Write-Host "  XX $Message" -ForegroundColor Red
    if ($LogFile) { Add-Content -Path $LogFile -Value "[ERR] $Message" -Encoding UTF8 -ErrorAction SilentlyContinue }
}

function Write-PanelLine {
    param(
        [string]$Label,
        [string]$Value,
        [ConsoleColor]$LabelColor = [ConsoleColor]::DarkCyan,
        [ConsoleColor]$ValueColor = [ConsoleColor]::White
    )
    Write-Host "   " -NoNewline
    Write-Host ($Label.PadRight(14)) -NoNewline -ForegroundColor $LabelColor
    Write-Host $Value -ForegroundColor $ValueColor
}

# Pause before closing the elevated window on error, so the user can read
# the error message.  Only fires in the elevated process (not the original
# non-admin session) and only for non-zero exit codes.
function Pause-IfElevated {
    param([int]$ExitCode)
    if ($script:IsElevated -and $ExitCode -ne 0) {
        Write-Host ""
        Write-Host "  ============================================================" -ForegroundColor Red
        Write-Host "   Sidekick setup stopped" -ForegroundColor Red
        Write-Host "  ------------------------------------------------------------" -ForegroundColor Red
        Write-Host "   Exit code: $ExitCode" -ForegroundColor White
        Write-Host "  ============================================================" -ForegroundColor Red
        Write-Host ""
        Write-PanelLine "Logs" "$env:LOCALAPPDATA\sidekick\logs\" Yellow White
        Write-Host ""
        Read-Host "  Press Enter to close Sidekick setup"
    }
}

# ============================================================================
# Admin rights - REQUIRED for Sidekick installer
# ============================================================================
# Sidekick needs admin for:
#   - Writing to C:\Windows\System32\drivers\etc\hosts (http://sidekick:9119)
#   - Setting machine-wide environment variables (PATH, SIDEKICK_GIT_BASH_PATH)
#   - winget (Node.js) needs elevation to install per-machine
#   - Optional: Add/Remove Programs entry for clean uninstall
#
# We self-elevate via UAC. If the user clicks "No", exit with a clear error.
$script:IsElevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $script:IsElevated) {
    Write-Host ""
    Write-Host "  Sidekick needs an elevated PowerShell for setup:" -ForegroundColor Yellow
    Write-Host "    - local hostname registration for http://sidekick:9119" -ForegroundColor DarkGray
    Write-Host "    - PATH and toolchain environment setup" -ForegroundColor DarkGray
    Write-Host "    - optional package installs through winget" -ForegroundColor DarkGray
    Write-Host "    - Windows uninstall entry" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Opening the Windows UAC prompt now..." -ForegroundColor Cyan
    Write-Host ""
    # Determine the script path. If running via iex (no file), the script
    # body is in memory and $MyInvocation.MyCommand.Path is null. In that
    # case, download install.ps1 fresh to %TEMP% and re-launch elevated.
    $scriptPath = $MyInvocation.MyCommand.Path
    if (-not $scriptPath -or -not (Test-Path $scriptPath)) {
        Write-Host "  >  Preparing elevated installer copy..." -ForegroundColor DarkCyan
        $scriptPath = Join-Path $env:TEMP "sidekick-installer-elevated.ps1"
        try {
            $downloadUrl = "https://raw.githubusercontent.com/Loggableim/sidekick-agent/$Branch/install.ps1"
            Invoke-WebRequest -Uri $downloadUrl -OutFile $scriptPath -UseBasicParsing -TimeoutSec 60
            Write-Host "  OK Elevated installer ready: $scriptPath" -ForegroundColor Green
        } catch {
            Write-Host "  XX Could not prepare elevated installer: $_" -ForegroundColor Red
            Pause-IfElevated -ExitCode 7
            exit 7
        }
    }
    $wrapperPath = Join-Path $env:TEMP "sidekick-installer-elevated-wrapper.ps1"
    $wrapperLogDir = "$env:LOCALAPPDATA\sidekick\logs"
    $wrapperLog = Join-Path $wrapperLogDir ("install-elevated-wrapper-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    $wrapperContent = @"
`$ErrorActionPreference = "Continue"
`$scriptPath = @'
$scriptPath
'@
`$logDir = @'
$wrapperLogDir
'@
`$wrapperLog = @'
$wrapperLog
'@
if (-not (Test-Path -LiteralPath `$logDir)) {
    New-Item -ItemType Directory -Force -Path `$logDir | Out-Null
}
function _sidekick_line([string]`$message, [ConsoleColor]`$color = [ConsoleColor]::White) {
    Write-Host `$message -ForegroundColor `$color
    Add-Content -Path `$wrapperLog -Value `$message -Encoding UTF8 -ErrorAction SilentlyContinue
}
function _sidekick_blank {
    Write-Host ""
    Add-Content -Path `$wrapperLog -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue
}
_sidekick_blank
_sidekick_line "  ============================================================" DarkCyan
_sidekick_line "   Sidekick elevated setup" Cyan
_sidekick_line "  ------------------------------------------------------------" DarkCyan
_sidekick_line "   Installer: `$scriptPath" DarkGray
_sidekick_line "   Wrapper log: `$wrapperLog" DarkGray
_sidekick_line "  ============================================================" DarkCyan
_sidekick_blank
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "`$scriptPath"
    `$code = if (`$LASTEXITCODE -is [int]) { `$LASTEXITCODE } else { 0 }
} catch {
    `$code = 1
    _sidekick_line "  XX Elevated setup wrapper failed before the installer could finish." Red
    _sidekick_line ("  XX " + `$_.Exception.Message) Red
}
if (`$code -ne 0) {
    _sidekick_blank
    _sidekick_line "  ============================================================" Red
    _sidekick_line "   Sidekick setup stopped" Red
    _sidekick_line "  ------------------------------------------------------------" Red
    _sidekick_line "   Exit code: `$code" White
    _sidekick_line "   Logs: `$logDir" Yellow
    _sidekick_line "  ============================================================" Red
    _sidekick_blank
    Read-Host "  Press Enter to close Sidekick setup"
}
exit `$code
"@
    try {
        Set-Content -Path $wrapperPath -Value $wrapperContent -Encoding UTF8 -Force
    } catch {
        Write-Host "  XX Could not prepare elevated setup wrapper: $_" -ForegroundColor Red
        exit 7
    }
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = "powershell.exe"
        $psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$wrapperPath`""
        $psi.UseShellExecute = $true
        $psi.Verb = "runas"
        # Ensure the elevated window is VISIBLE and stays open on error.
        # Without WindowStyle, PowerShell 5.1 sometimes launches a hidden
        # window that immediately exits, making it look like a "crash".
        $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Normal
        $elevated = [System.Diagnostics.Process]::Start($psi)
        if ($null -eq $elevated) {
            # UAC was denied or the process could not start
            Write-Host "  XX Administrator privileges were not granted. Setup cannot continue." -ForegroundColor Red
            Write-Host "  >  Re-run from an elevated PowerShell:" -ForegroundColor DarkCyan
            Write-Host "     irm https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/install.ps1 | iex" -ForegroundColor White
            exit 7
        }
        # Capture exit code - WaitForExit() doesn't throw on null Process.
        # Note: WaitForExit() blocks until the elevated window closes.
        $elevated.WaitForExit()
        $exitCode = $elevated.ExitCode
        exit $exitCode
    } catch [System.InvalidOperationException], [System.ComponentModel.Win32Exception] {
        # UAC denied: Process::Start with runas verb throws Win32Exception
        # when the user cancels the elevation dialog.
        Write-Host "  XX Administrator privileges were not granted. Setup cannot continue." -ForegroundColor Red
        Write-Host "  >  Re-run from an elevated PowerShell:" -ForegroundColor DarkCyan
        Write-Host "     irm https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/install.ps1 | iex" -ForegroundColor White
        exit 7
    }
}
Write-Host "  OK Elevated shell ready" -ForegroundColor Green

# ============================================================================
# Log file setup
# ============================================================================
$LogDir = "$env:LOCALAPPDATA\sidekick\logs"
$script:LogFile = "$LogDir\install-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# ============================================================================
# Exit Code Schema
# ============================================================================
# 0  Success
# 1  Generic failure (unhandled exception)
# 2  Missing prerequisite (Python/Git/uv install failed)
# 3  Network/download failure (timeout, DNS, HTTP)
# 4  Git/update failure (clone, fetch, checkout)
# 5  Install/venv failure (pip install, dependency install)
# 6  Verification failure (doctor/smoke check failed)
# ============================================================================

# ============================================================================
# Configuration
# ============================================================================

$RepoUrlSsh = "git@github.com:Loggableim/sidekick-agent.git"
$RepoUrlHttps = "https://github.com/Loggableim/sidekick-agent.git"
$PythonVersion = "3.11"
$script:VenvPath = "$InstallDir\.venv"
$script:PythonExe = "$script:VenvPath\Scripts\python.exe"
$script:SidekickExe = "$script:VenvPath\Scripts\sidekick.exe"

# ============================================================================
# Helper functions
# ============================================================================
# (Helper functions are now defined at the top of the script so they're
# available in `irm | iex` pipeline mode. See top of file.)

# ============================================================================
# Process execution helper (separated streams, no ErrorActionPreference issues)
# ============================================================================

function Invoke-External {
    param(
        [Parameter(Mandatory=$true)]
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory = ".",
        [int]$TimeoutSeconds = 300
    )

    # Create temp files for stdout/stderr
    $tmpOut = [System.IO.Path]::GetTempFileName()
    $tmpErr = [System.IO.Path]::GetTempFileName()

    try {
        $params = @{
            FilePath = $FilePath
            ArgumentList = $ArgumentList
            Wait = $true
            PassThru = $true
            NoNewWindow = $true
            RedirectStandardOutput = $tmpOut
            RedirectStandardError = $tmpErr
        }
        if ($WorkingDirectory) {
            $params.WorkingDirectory = $WorkingDirectory
        }
        $proc = Start-Process @params

        # Read captured output
        $stdout = Get-Content $tmpOut -Raw -ErrorAction SilentlyContinue
        $stderr = Get-Content $tmpErr -Raw -ErrorAction SilentlyContinue

        # Log everything
        if ($stdout) { Add-Content -Path $LogFile -Value "[CMD:stdout] $stdout" -Encoding UTF8 -ErrorAction SilentlyContinue }
        if ($stderr) { Add-Content -Path $LogFile -Value "[CMD:stderr] $stderr" -Encoding UTF8 -ErrorAction SilentlyContinue }
        Add-Content -Path $LogFile -Value "[CMD:exit] $($proc.ExitCode)" -Encoding UTF8 -ErrorAction SilentlyContinue

        return @{
            ExitCode = $proc.ExitCode
            Stdout = ($stdout -replace '\s+$', '')
            Stderr = ($stderr -replace '\s+$', '')
        }
    }
    finally {
        # Cleanup temp files
        Remove-Item $tmpOut -Force -ErrorAction SilentlyContinue
        Remove-Item $tmpErr -Force -ErrorAction SilentlyContinue
    }
}

# ============================================================================
# Dependency checks
# ============================================================================

function Install-Uv {
    Write-Info "Checking for uv package manager..."
    
    # Check if uv is already available
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $version = uv --version
        $script:UvCmd = "uv"
        Write-Success "uv found ($version)"
        return $true
    }
    
    # Check common install locations
    $uvPaths = @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:USERPROFILE\.cargo\bin\uv.exe"
    )
    foreach ($uvPath in $uvPaths) {
        if (Test-Path $uvPath) {
            $script:UvCmd = $uvPath
            $version = & $uvPath --version
            Write-Success "uv found at $uvPath ($version)"
            return $true
        }
    }
    
    # Install uv
    Write-Info "Installing uv (fast Python package manager)..."
    try {
        powershell -ExecutionPolicy ByPass -c "Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 'https://astral.sh/uv/install.ps1' | iex" 2>&1 | Out-Null
        
        # Find the installed binary
        $uvExe = "$env:USERPROFILE\.local\bin\uv.exe"
        if (-not (Test-Path $uvExe)) {
            $uvExe = "$env:USERPROFILE\.cargo\bin\uv.exe"
        }
        if (-not (Test-Path $uvExe)) {
            # Refresh PATH and try again
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                $uvExe = (Get-Command uv).Source
            }
        }
        
        if (Test-Path $uvExe) {
            $script:UvCmd = $uvExe
            $version = & $uvExe --version
            Write-Success "uv installed ($version)"
            return $true
        }
        
        Write-Err "uv installed but not found on PATH"
        Write-Info "Try restarting your terminal and re-running"
        return $false
    } catch {
        Write-Err "Failed to install uv"
        Write-Info "Install manually: https://docs.astral.sh/uv/getting-started/installation/"
        return $false
    }
}

function Ensure-Venv {
    param([string]$VenvPath)

    $venvPython = "$VenvPath\Scripts\python.exe"

    # -- 1. If venv already exists and has a working python, use it --
    if (Test-Path $venvPython) {
        $ver = & $venvPython --version 2>$null
        if ($ver -match "Python 3\.") {
            Write-Success "Virtual environment found: $ver"
            $script:PythonExe = $venvPython
            return $true
        }
        Write-Warn "Virtual environment broken at $VenvPath - recreating..."
        Remove-Item -Recurse -Force $VenvPath -ErrorAction SilentlyContinue
    }

    # -- 2. Primary path: uv venv --python (handles Python download + venv in one step) --
    Write-Info "Creating virtual environment with uv (Python $PythonVersion)..."
    Add-Content -Path $LogFile -Value "[INFO] Running: & $UvCmd venv --python $PythonVersion $VenvPath" -Encoding UTF8 -ErrorAction SilentlyContinue

    $result = Invoke-External -FilePath $script:UvCmd -ArgumentList @("venv", "--seed", "--python", $PythonVersion, "`"$VenvPath`"")

    if ($result.ExitCode -eq 0 -and (Test-Path $venvPython)) {
        $ver = & $venvPython --version 2>$null
        Write-Success "Virtual environment created: $ver"
        $script:PythonExe = $venvPython
        Add-Content -Path $LogFile -Value "[DONE] PythonExe = $venvPython" -Encoding UTF8 -ErrorAction SilentlyContinue
        return $true
    }

    # uv venv failed - log and continue
    Write-Warn "uv venv exited with code $($result.ExitCode). See $LogFile for details."
    Add-Content -Path $LogFile -Value "[WARN] uv venv failed. stderr was captured above." -Encoding UTF8 -ErrorAction SilentlyContinue

    # -- 3. Fallback: uv python install + python -m venv --
    Write-Info "Trying uv python install instead..."
    Add-Content -Path $LogFile -Value "[INFO] Running: uv python install $PythonVersion" -Encoding UTF8 -ErrorAction SilentlyContinue

    $result = Invoke-External -FilePath $script:UvCmd -ArgumentList @("python", "install", $PythonVersion)

    if ($result.ExitCode -eq 0) {
        # Find the installed Python
        $pythonPath = & $UvCmd python find $PythonVersion 2>$null
        if ($pythonPath -and (Test-Path $pythonPath)) {
            $ver = & $pythonPath --version 2>$null
            Write-Success "Python provisioned: $ver"
            & $pythonPath -m venv "`"$VenvPath`""
            if ($LASTEXITCODE -eq 0 -and (Test-Path $venvPython)) {
                $script:PythonExe = $venvPython
                return $true
            }
        }
    } else {
        Write-Warn "uv python install exited with code $($result.ExitCode)."
        Add-Content -Path $LogFile -Value "[WARN] uv python install failed. stderr was captured above." -Encoding UTF8 -ErrorAction SilentlyContinue

        # One retry
        Write-Info "Retrying Python download..."
        Start-Sleep -Seconds 2

        $result = Invoke-External -FilePath $script:UvCmd -ArgumentList @("python", "install", $PythonVersion)

        if ($result.ExitCode -eq 0) {
            $pythonPath = & $UvCmd python find $PythonVersion 2>$null
            if ($pythonPath -and (Test-Path $pythonPath)) {
                $ver = & $pythonPath --version 2>$null
                Write-Success "Python provisioned on retry: $ver"
                & $pythonPath -m venv "`"$VenvPath`""
                if ($LASTEXITCODE -eq 0 -and (Test-Path $venvPython)) {
                    $script:PythonExe = $venvPython
                    return $true
                }
            }
        }
    }

    # -- 4. Scan for system Python (excluding Store alias) --
    Write-Info "Scanning for system Python installations (excluding Microsoft Store alias)..."
    $candidateDirs = @()

    # PATH directories - skip Microsoft\WindowsApps (Store alias)
    foreach ($dir in ($env:PATH -split ';')) {
        if ($dir -and $dir -notlike "*Microsoft*WindowsApps*" -and (Test-Path "$dir\python.exe")) {
            $candidateDirs += $dir
        }
    }

    # %LOCALAPPDATA%\Programs\Python\Python3xx
    $localPy = "$env:LOCALAPPDATA\Programs\Python"
    if (Test-Path $localPy) {
        Get-ChildItem $localPy -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^Python3' } |
            ForEach-Object { $candidateDirs += $_.FullName }
    }

    # %ProgramFiles%\Python* and %ProgramFiles(x86)%\Python*
    Get-ChildItem "${env:ProgramFiles}\Python*" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { $candidateDirs += $_.FullName }
    $pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ($pf86) {
        Get-ChildItem "$pf86\Python*" -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { $candidateDirs += $_.FullName }
    }

    $seen = @{}
    foreach ($dir in $candidateDirs) {
        $exePath = "$dir\python.exe"
        if (-not (Test-Path $exePath)) { continue }
        $normPath = (Resolve-Path $exePath).ProviderPath.ToLower()
        if ($seen.ContainsKey($normPath)) { continue }
        $seen[$normPath] = $true

        $ver = & $exePath --version 2>$null
        if ($ver -match "Python 3\.(1[0-9]|[1-9][0-9])\.") {
            Write-Success "Found system Python: $ver"
            & $exePath -m venv "$VenvPath"
            if ($LASTEXITCODE -eq 0 -and (Test-Path $venvPython)) {
                $script:PythonExe = $venvPython
                return $true
            }
        }
    }

    # -- 5. All paths exhausted --
    Write-Err "No usable Python 3.10+ found on this system."
    Write-Info "Install Python 3.11 manually:"
    Write-Info "  https://www.python.org/downloads/"
    Write-Info "  (check 'Add Python to PATH' during installation)"
    Write-Info "  Or: winget install Python.Python.3.11"
    Write-Info "Log file: $LogFile"
    return $false
}

function Install-Git {
    <#
    .SYNOPSIS
    Ensure Git (and Git Bash) are installed.  Git for Windows bundles bash.exe
    which Sidekick uses to run shell commands.

    Priority order (deliberately simple - no winget, no registry, no system
    package manager):
      1. Existing ``git`` on PATH - use it as-is (the common fast path).
      2. Download **PortableGit** from the official git-for-windows GitHub
         release (self-extracting 7z.exe) and unpack it to
         ``%LOCALAPPDATA%\sidekick\git`` - never touches system Git, never
         requires admin, works even on locked-down machines and machines
         with a broken system Git install.

    **Why PortableGit, not MinGit:**  MinGit is the minimal-automation
    distribution and ships ONLY ``git.exe`` - no bash, no POSIX utilities.
    Sidekick needs ``bash.exe`` to run shell commands.  PortableGit is the
    full Git for Windows distribution without the installer UI; it ships
    ``git.exe`` + ``bash.exe`` + ``sh``, ``awk``, ``sed``, ``grep``, ``curl``,
    ``ssh``, etc. in ``usr\bin\``.

    We deliberately skip winget because it fails badly when the system Git
    install is in a half-installed state (partially registered, or uninstall-
    blocked).  Owning the Sidekick copy of Git ourselves is predictable and
    recoverable: if it ever breaks, ``Remove-Item %LOCALAPPDATA%\sidekick\git``
    and re-running this installer fully recovers.

    After install we locate ``bash.exe`` and persist the path in
    ``SIDEKICK_GIT_BASH_PATH`` (User scope) so Sidekick can find it in a fresh
    shell without a second PATH refresh.
    #>
    Write-Info "Checking Git..."

    if (Get-Command git -ErrorAction SilentlyContinue) {
        $version = git --version
        Write-Success "Git found ($version)"
        Set-GitBashEnvVar
        return $true
    }

    # Download PortableGit into $SidekickHome\git.  Always works as long as
    # we can reach github.com - no admin, no winget, no reliance on the
    # user's possibly-broken system Git install.
    Write-Info "Git not found - downloading PortableGit to $SidekickHome\git\ ..."
    Write-Info "(no admin rights required; isolated from any system Git install)"

    try {
        $arch = if ([Environment]::Is64BitOperatingSystem) {
            # Detect ARM64 vs x64 explicitly; PortableGit ships separate assets.
            if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64" -or $env:PROCESSOR_ARCHITEW6432 -eq "ARM64") {
                "arm64"
            } else {
                "64-bit"
            }
        } else {
            # PortableGit does not ship a 32-bit build - fall back to MinGit 32-bit
            # with a warning that bash-based features will be unavailable.
            "32-bit-mingit"
        }

        $releaseApi = "https://api.github.com/repos/git-for-windows/git/releases/latest"
        $release = Invoke-RestMethod -Uri $releaseApi -UseBasicParsing -TimeoutSec 60 -Headers @{ "User-Agent" = "sidekick-installer" }

        if ($arch -eq "32-bit-mingit") {
            Write-Warn "32-bit Windows detected - PortableGit is 64-bit only.  Installing MinGit 32-bit as a last resort; bash-dependent Sidekick features (terminal tool, agent-browser) will not work on this machine."
            $assetPattern = "MinGit-*-32-bit.zip"
            $downloadIsZip = $true
        } elseif ($arch -eq "arm64") {
            $assetPattern = "PortableGit-*-arm64.7z.exe"
            $downloadIsZip = $false
        } else {
            $assetPattern = "PortableGit-*-64-bit.7z.exe"
            $downloadIsZip = $false
        }

        $asset = $release.assets | Where-Object { $_.name -like $assetPattern } | Select-Object -First 1

        if (-not $asset) {
            throw "Could not find $assetPattern in latest git-for-windows release"
        }

        $downloadUrl = $asset.browser_download_url
        $downloadExt = if ($downloadIsZip) { "zip" } else { "7z.exe" }
        $tmpFile = "$env:TEMP\$($asset.name)"
        $gitDir = "$SidekickHome\git"

        Write-Info "Downloading $($asset.name) ($([math]::Round($asset.size / 1MB, 1)) MB)..."
        Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpFile -UseBasicParsing -TimeoutSec 60

        if (Test-Path $gitDir) {
            Write-Info "Removing previous Git install at $gitDir ..."
            Remove-Item -Recurse -Force $gitDir
        }
        New-Item -ItemType Directory -Path $gitDir -Force | Out-Null

        if ($downloadIsZip) {
            Expand-Archive -Path $tmpFile -DestinationPath $gitDir -Force
        } else {
            # PortableGit is a self-extracting 7z archive.  Invoke it with
            # `-o<target> -y` (silent) to extract to $gitDir.  No 7z install
            # required; it's fully self-contained.
            Write-Info "Extracting PortableGit to $gitDir ..."
            $extractProc = Start-Process -FilePath $tmpFile `
                -ArgumentList "-o`"$gitDir`"", "-y" `
                -NoNewWindow -Wait -PassThru
            if ($extractProc.ExitCode -ne 0) {
                throw "PortableGit extraction failed (exit code $($extractProc.ExitCode))"
            }
        }
        Remove-Item -Force $tmpFile -ErrorAction SilentlyContinue

        # PortableGit layout: cmd\git.exe + bin\bash.exe + usr\bin\ (coreutils)
        # MinGit layout:      cmd\git.exe + usr\bin\bash.exe (if present)
        $gitExe = "$gitDir\cmd\git.exe"
        if (-not (Test-Path $gitExe)) {
            throw "Git extraction did not produce git.exe at $gitExe"
        }

        # Add to session PATH so the rest of this install run can use git.
        $env:Path = "$gitDir\cmd;$env:Path"

        # Persist to User PATH so fresh shells see it.  PortableGit needs
        # cmd\ (for git.exe), bin\ (for bash.exe + core tools), and
        # usr\bin\ (for perl, ssh, curl, and other POSIX coreutils).
        $newPathEntries = @(
            "$gitDir\cmd",
            "$gitDir\bin",
            "$gitDir\usr\bin"
        )
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $userPathItems = if ($userPath) { $userPath -split ";" } else { @() }
        $changed = $false
        foreach ($entry in $newPathEntries) {
            if ($userPathItems -notcontains $entry) {
                $userPathItems += $entry
                $changed = $true
            }
        }
        if ($changed) {
            [Environment]::SetEnvironmentVariable("Path", ($userPathItems -join ";"), "User")
        }

        $version = & $gitExe --version
        Write-Success "Git $version installed to $gitDir (portable, user-scoped)"
        Set-GitBashEnvVar
        return $true
    } catch {
        Write-Err "Could not install portable Git: $_"
        Write-Info ""
        Write-Info "Fallback: install Git manually from https://git-scm.com/download/win"
        Write-Info "then re-run this installer.  Sidekick needs Git Bash on Windows to run"
        Write-Info "shell commands (same as Claude Code and other coding agents)."
        return $false
    }
}

function Set-GitBashEnvVar {
    <#
    .SYNOPSIS
    Locate ``bash.exe`` from an already-installed Git and persist the path in
    ``SIDEKICK_GIT_BASH_PATH`` (User env scope) so Sidekick can find it even before
    PATH propagation completes in a newly-spawned shell.
    #>
    $candidates = @()

    # Our own portable Git install is ALWAYS checked first, so a broken
    # system Git doesn't hijack us.  If the user had a working system Git
    # we'd have returned early from Install-Git's fast path and never called
    # this with a system-Git-only installation anyway.
    #
    # Layouts:
    #   PortableGit (our default): $SidekickHome\git\bin\bash.exe
    #   MinGit (32-bit fallback):  $SidekickHome\git\usr\bin\bash.exe
    $candidates += "$SidekickHome\git\bin\bash.exe"       # PortableGit layout (primary)
    $candidates += "$SidekickHome\git\usr\bin\bash.exe"   # MinGit / PortableGit usr\bin fallback

    # git.exe on PATH can tell us where the install root is
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) {
        $gitExe = $gitCmd.Source
        # Git for Windows (full installer): <root>\cmd\git.exe + <root>\bin\bash.exe
        # MinGit:                           <root>\cmd\git.exe + <root>\usr\bin\bash.exe
        $gitRoot = Split-Path (Split-Path $gitExe -Parent) -Parent
        $candidates += "$gitRoot\bin\bash.exe"
        $candidates += "$gitRoot\usr\bin\bash.exe"
    }

    # Standard system install locations as a final fallback.  Note:
    # ProgramFiles(x86) can't be referenced via ${env:...} string interpolation
    # because of the parens - use [Environment]::GetEnvironmentVariable().
    $candidates += "${env:ProgramFiles}\Git\bin\bash.exe"
    $pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ($pf86) { $candidates += "$pf86\Git\bin\bash.exe" }
    $candidates += "${env:LocalAppData}\Programs\Git\bin\bash.exe"

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            [Environment]::SetEnvironmentVariable("SIDEKICK_GIT_BASH_PATH", $candidate, "User")
            $env:SIDEKICK_GIT_BASH_PATH = $candidate
            Write-Info "Set SIDEKICK_GIT_BASH_PATH=$candidate"
            return
        }
    }

    Write-Warn "Could not locate bash.exe - Sidekick may not find Git Bash."
    Write-Info "If needed, set SIDEKICK_GIT_BASH_PATH manually to your bash.exe path."
}

function Test-Node {
    <#
    .SYNOPSIS
    Check for Node.js.  Sidekick does not require Node.js for its core
    functionality, but it is needed for browser automation tools (Playwright)
    and the optional TUI.  This function is informational only - it does not
    attempt to install Node.js.
    #>
    if ($script:SkipOptionalTools) {
        $script:HasNode = $false
        return $true
    }
    Write-Info "Checking Node.js (optional - for browser tools and TUI)..."

    if (Get-Command node -ErrorAction SilentlyContinue) {
        $version = node --version
        Write-Success "Node.js $version found"
        $script:HasNode = $true
        return $true
    }

    # Check our own managed install from a previous run
    $managedNode = "$SidekickHome\node\node.exe"
    if (Test-Path $managedNode) {
        $version = & $managedNode --version
        $env:Path = "$SidekickHome\node;$env:Path"
        Write-Success "Node.js $version found (Sidekick-managed)"
        $script:HasNode = $true
        return $true
    }

    Write-Warn "Node.js not found - browser tools and TUI will be unavailable."
    Write-Info "Install manually if needed: https://nodejs.org/en/download/"
    Write-Info "  Or: winget install OpenJS.NodeJS.LTS"
    $script:HasNode = $false
    return $true
}

function Install-SystemPackages {
    if ($script:SkipOptionalTools) {
        $script:HasRipgrep = $false
        $script:HasFfmpeg = $false
        return
    }
    $script:HasRipgrep = $false
    $script:HasFfmpeg = $false
    $needRipgrep = $false
    $needFfmpeg = $false

    Write-Info "Checking ripgrep (fast file search)..."
    if (Get-Command rg -ErrorAction SilentlyContinue) {
        $version = rg --version | Select-Object -First 1
        Write-Success "$version found"
        $script:HasRipgrep = $true
    } else {
        $needRipgrep = $true
    }

    Write-Info "Checking ffmpeg (TTS voice messages)..."
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Write-Success "ffmpeg found"
        $script:HasFfmpeg = $true
    } else {
        $needFfmpeg = $true
    }

    if (-not $needRipgrep -and -not $needFfmpeg) { return }

    # Build description and package lists for each package manager
    $descParts = @()
    $wingetPkgs = @()
    $chocoPkgs = @()
    $scoopPkgs = @()

    if ($needRipgrep) {
        $descParts += "ripgrep for faster file search"
        $wingetPkgs += "BurntSushi.ripgrep.MSVC"
        $chocoPkgs += "ripgrep"
        $scoopPkgs += "ripgrep"
    }
    if ($needFfmpeg) {
        $descParts += "ffmpeg for TTS voice messages"
        $wingetPkgs += "Gyan.FFmpeg"
        $chocoPkgs += "ffmpeg"
        $scoopPkgs += "ffmpeg"
    }

    $description = $descParts -join " and "
    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    $hasChoco = Get-Command choco -ErrorAction SilentlyContinue
    $hasScoop = Get-Command scoop -ErrorAction SilentlyContinue

    # Try winget first (most common on modern Windows)
    if ($hasWinget) {
        Write-Info "Installing $description via winget..."
        foreach ($pkg in $wingetPkgs) {
            try {
                winget install $pkg --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
            } catch { }
        }
        # Refresh PATH and recheck
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        if ($needRipgrep -and (Get-Command rg -ErrorAction SilentlyContinue)) {
            Write-Success "ripgrep installed"
            $script:HasRipgrep = $true
            $needRipgrep = $false
        }
        if ($needFfmpeg -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            Write-Success "ffmpeg installed"
            $script:HasFfmpeg = $true
            $needFfmpeg = $false
        }
        if (-not $needRipgrep -and -not $needFfmpeg) { return }
    }

    # Fallback: choco
    if ($hasChoco -and ($needRipgrep -or $needFfmpeg)) {
        Write-Info "Trying Chocolatey..."
        foreach ($pkg in $chocoPkgs) {
            try { choco install $pkg -y 2>&1 | Out-Null } catch { }
        }
        if ($needRipgrep -and (Get-Command rg -ErrorAction SilentlyContinue)) {
            Write-Success "ripgrep installed via chocolatey"
            $script:HasRipgrep = $true
            $needRipgrep = $false
        }
        if ($needFfmpeg -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            Write-Success "ffmpeg installed via chocolatey"
            $script:HasFfmpeg = $true
            $needFfmpeg = $false
        }
    }

    # Fallback: scoop
    if ($hasScoop -and ($needRipgrep -or $needFfmpeg)) {
        Write-Info "Trying Scoop..."
        foreach ($pkg in $scoopPkgs) {
            try { scoop install $pkg 2>&1 | Out-Null } catch { }
        }
        if ($needRipgrep -and (Get-Command rg -ErrorAction SilentlyContinue)) {
            Write-Success "ripgrep installed via scoop"
            $script:HasRipgrep = $true
            $needRipgrep = $false
        }
        if ($needFfmpeg -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            Write-Success "ffmpeg installed via scoop"
            $script:HasFfmpeg = $true
            $needFfmpeg = $false
        }
    }

    # Show manual instructions for anything still missing
    if ($needRipgrep) {
        Write-Warn "ripgrep not installed (file search will use findstr fallback)"
        Write-Info "  winget install BurntSushi.ripgrep.MSVC"
    }
    if ($needFfmpeg) {
        Write-Warn "ffmpeg not installed (TTS voice messages will be limited)"
        Write-Info "  winget install Gyan.FFmpeg"
    }
}

# ============================================================================
# Installation
# ============================================================================

function Install-Repository {
    Write-Info "Installing to $InstallDir..."

    $didUpdate = $false

    if (Test-Path $InstallDir) {
        # Test-Path "$InstallDir\.git" returns True when .git is a file OR a
        # directory OR a symlink OR a submodule-style gitfile - and also when
        # it's a broken stub left over from a failed previous install (e.g.
        # a partial Remove-Item that couldn't delete a locked index.lock).
        # Validate the repo properly by asking git itself.  Two checks
        # belt-and-braces: rev-parse AND git status.  If either fails the
        # repo is broken and we fall through to a fresh clone.
        $repoValid = $false
        if (Test-Path "$InstallDir\.git") {
            Push-Location $InstallDir
            try {
                # Reset $LASTEXITCODE before the probe so we don't pick up
                # a stale 0 from an earlier git call in this session.
                $global:LASTEXITCODE = 0
                $revParseOut = & git -c windows.appendAtomically=false rev-parse --is-inside-work-tree 2>&1
                $revParseOk = ($LASTEXITCODE -eq 0) -and ($revParseOut -match "true")

                $global:LASTEXITCODE = 0
                $null = & git -c windows.appendAtomically=false status --short 2>&1
                $statusOk = ($LASTEXITCODE -eq 0)

                if ($revParseOk -and $statusOk) {
                    $repoValid = $true
                }
            } catch {}
            Pop-Location
        }

        if ($repoValid) {
            Write-Info "Existing installation found, updating..."
            Push-Location $InstallDir
            try {
                git -c windows.appendAtomically=false -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 fetch origin
                if ($LASTEXITCODE -ne 0) { Write-Err "git fetch failed (exit $LASTEXITCODE)" ; exit 4 }
                git -c windows.appendAtomically=false checkout $Branch
                if ($LASTEXITCODE -ne 0) { Write-Err "git checkout $Branch failed (exit $LASTEXITCODE)" ; exit 4 }
                git -c windows.appendAtomically=false -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 pull origin $Branch
                if ($LASTEXITCODE -ne 0) { Write-Err "git pull failed (exit $LASTEXITCODE)" ; exit 4 }
            } finally {
                Pop-Location
            }
            $didUpdate = $true
        } else {
            # Directory exists but isn't a usable git repo.  Wipe it and
            # fall through to a fresh clone.  A leftover ``.git`` stub from
            # a partial uninstall used to lock the installer into the
            # "update" branch forever, emitting three ``fatal: not a git
            # repository`` errors and failing with "not in a git directory".
            Write-Warn "Existing directory at $InstallDir is not a valid git repo - replacing it."
            try {
                Remove-Item -Recurse -Force $InstallDir -ErrorAction Stop
            } catch {
                Write-Err "Could not remove $InstallDir : $_"
                Write-Info "Close any programs that might be using files in $InstallDir (editors,"
                Write-Info "terminals, running sidekick processes) and try again."
                Pause-IfElevated -ExitCode 4
                exit 4
            }
        }
    }

    if (-not $didUpdate) {
        $cloneSuccess = $false

        # Fix Windows git "copy-fd: write returned: Invalid argument" error.
        # Git for Windows can fail on atomic file operations (hook templates,
        # config lock files) due to antivirus, OneDrive, or NTFS filter drivers.
        # The -c flag injects config before any file I/O occurs.
        Write-Info "Configuring git for Windows compatibility..."
        $env:GIT_CONFIG_COUNT = "1"
        $env:GIT_CONFIG_KEY_0 = "windows.appendAtomically"
        $env:GIT_CONFIG_VALUE_0 = "false"
        git config --global windows.appendAtomically false 2>$null

        # Try SSH first, then HTTPS, with -c flag for atomic write fix
        Write-Info "Trying SSH clone..."
        $env:GIT_SSH_COMMAND = "ssh -o BatchMode=yes -o ConnectTimeout=5"
        try {
            git -c windows.appendAtomically=false clone --depth 1 --branch $Branch --recurse-submodules $RepoUrlSsh $InstallDir
            if ($LASTEXITCODE -eq 0) { $cloneSuccess = $true }
        } catch { }
        $env:GIT_SSH_COMMAND = $null

        if (-not $cloneSuccess) {
            if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
            Write-Info "SSH failed, trying HTTPS..."
            try {
                git -c windows.appendAtomically=false clone --depth 1 --branch $Branch --recurse-submodules $RepoUrlHttps $InstallDir
                if ($LASTEXITCODE -eq 0) { $cloneSuccess = $true }
            } catch { }
        }

        # Fallback: download ZIP archive (bypasses git file I/O issues entirely)
        if (-not $cloneSuccess) {
            if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
            Write-Warn "Git clone failed - downloading ZIP archive instead..."
            try {
                $zipUrl = "https://github.com/Loggableim/sidekick-agent/archive/refs/heads/$Branch.zip"
                $zipPath = "$env:TEMP\sidekick-agent-$Branch.zip"
                $extractPath = "$env:TEMP\sidekick-agent-extract"

                Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing -TimeoutSec 60
                if (Test-Path $extractPath) { Remove-Item -Recurse -Force $extractPath }
                Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

                # GitHub ZIPs extract to repo-branch/ subdirectory
                $extractedDir = Get-ChildItem $extractPath -Directory | Select-Object -First 1
                if ($extractedDir) {
                    New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir) -ErrorAction SilentlyContinue | Out-Null
                    Move-Item $extractedDir.FullName $InstallDir -Force
                    Write-Success "Downloaded and extracted"

                    # Initialize git repo so updates work later
                    Push-Location $InstallDir
                    git -c windows.appendAtomically=false init 2>$null
                    git -c windows.appendAtomically=false config windows.appendAtomically false 2>$null
                    git remote add origin $RepoUrlHttps 2>$null
                    Pop-Location
                    Write-Success "Git repo initialized for future updates"

                    $cloneSuccess = $true
                }

                # Cleanup temp files
                Remove-Item -Force $zipPath -ErrorAction SilentlyContinue
                Remove-Item -Recurse -Force $extractPath -ErrorAction SilentlyContinue
            } catch {
                Write-Err "ZIP download also failed: $_"
            }
        }

        if (-not $cloneSuccess) {
            Write-Err "Failed to download repository (tried git clone SSH, HTTPS, and ZIP)"
            Pause-IfElevated -ExitCode 4
            exit 4
        }
    }

    # Set per-repo config (harmless if it fails)
    Push-Location $InstallDir
    git -c windows.appendAtomically=false config windows.appendAtomically false 2>$null

    # Ensure submodules are initialized and updated
    Write-Info "Initializing submodules..."
    git -c windows.appendAtomically=false submodule update --init --recursive 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Submodule init failed (terminal/RL tools may need manual setup)"
    } else {
        Write-Success "Submodules ready"
    }
    Pop-Location

    Write-Success "Repository ready"
}

function Stop-RunningSidekickProcesses {
    Write-Info "Stopping any running Sidekick processes before dependency install..."

    $stopped = @()
    try {
        $procs = Get-Process -Name "sidekick" -ErrorAction SilentlyContinue
        foreach ($proc in @($procs)) {
            try {
                Stop-Process -Id $proc.Id -Force -ErrorAction Stop
                $stopped += $proc.Id
            } catch {
                Write-Warn "Could not stop sidekick PID $($proc.Id): $_"
            }
        }
    } catch {
        Write-Warn "Could not inspect running sidekick processes: $_"
    }

    foreach ($name in @("Sidekick WebUI", "Sidekick Gateway")) {
        try {
            taskkill /f /fi "WINDOWTITLE eq $name" 2>$null | Out-Null
        } catch { }
    }

    try {
        $runtimeProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and
                $_.CommandLine.IndexOf($InstallDir, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                $_.Name -in @("python.exe", "pythonw.exe", "sidekick.exe", "cmd.exe")
            }
        foreach ($proc in @($runtimeProcs)) {
            try {
                Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
                $stopped += $proc.ProcessId
            } catch {
                Write-Warn "Could not stop Sidekick runtime PID $($proc.ProcessId): $_"
            }
        }
    } catch {
        Write-Warn "Could not inspect Sidekick runtime processes: $_"
    }

    if ($stopped.Count -gt 0) {
        Write-Success "Stopped running Sidekick process(es): $($stopped -join ', ')"
    } else {
        Write-Info "No running Sidekick runtime process found"
    }
}



function Install-Dependencies {
    Write-Info "Installing dependencies..."
    
    Push-Location $InstallDir
    Stop-RunningSidekickProcesses
    
    if (-not $script:NoVenv) {
        # Tell uv to install into our venv (no activation needed)
        $env:VIRTUAL_ENV = $script:VenvPath
    }
    
    # Install main package.  Tiered fallback so a single flaky git+https dep
    # doesn't silently drop dashboard/MCP/cron/messaging extras.  Each tier's
    # stdout/stderr is preserved - no Out-Null swallowing - so the user can
    # see what failed.
    #
    # Tier 1: [all] - everything, including RL git+https deps (best case).
    # Tier 2: [core-extras] synthesised locally - all PyPI-only extras we
    #         ship (web, mcp, cron, cli, voice, messaging, slack, dev, acp,
    #         pty, homeassistant, sms, tts-premium, honcho, google, mistral,
    #         bedrock, dingtalk, feishu, modal, daytona, vercel).  Drops [rl]
    #         and [matrix] (linux-only) which are the usual failure culprits.
    # Tier 3: [web,mcp,cron,cli,messaging,dev] - the minimum we strongly
    #         believe a user expects `sidekick dashboard` / slash commands /
    #         cron / messaging platforms to work out of the box.
    # Tier 4: bare `.` - last-resort so at least the core CLI launches.
    $installTiers = @(
        @{ Name = "all (with RL/matrix extras)"; Spec = ".[all]" },
        @{ Name = "PyPI-only extras (no git deps)"; Spec = ".[web,mcp,cron,cli,voice,messaging,slack,dev,acp,pty,homeassistant,sms,tts-premium,honcho,google,mistral,bedrock,dingtalk,feishu,modal,daytona,vercel]" },
        @{ Name = "dashboard + core platforms"; Spec = ".[web,mcp,cron,cli,messaging,dev]" },
        @{ Name = "webui + minimal"; Spec = ".[web]" },
        @{ Name = "core only (no extras)"; Spec = "." }
    )
    $installed = $false
    $env:VIRTUAL_ENV = $script:VenvPath
    $venvPython = "$script:VenvPath\Scripts\python.exe"
    foreach ($tier in $installTiers) {
        Write-Info "Trying tier: $($tier.Name) ..."
        & $UvCmd pip install --python "$script:PythonExe" -e $tier.Spec
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Main package installed ($($tier.Name))"
            $script:InstalledTier = $tier.Name
            $installed = $true
            break
        }
        Write-Warn "Tier '$($tier.Name)' failed (exit $LASTEXITCODE). Trying next tier..."
    }
    if (-not $installed) {
        Write-Err "Failed to install sidekick-agent package even with no extras. Inspect the uv pip install output above."
        Pause-IfElevated -ExitCode 5
        exit 5
    }

    # Verify the dashboard deps specifically - they're the most common thing
    # users hit and lazy-import errors from `sidekick dashboard` are confusing.
    # If tier 1 failed (the common case), [web] was still picked up by tiers
    # 2-3; only tier 4 leaves you without it.
    $pythonExe = if (-not $script:NoVenv) { "$script:VenvPath\Scripts\python.exe" } else { (& $UvCmd python find $PythonVersion) }
    if (Test-Path $pythonExe) {
        $webOk = $false
        try {
            & $pythonExe -c "import fastapi, uvicorn" 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { $webOk = $true }
        } catch { }
        if (-not $webOk) {
            Write-Warn "fastapi/uvicorn not importable - `sidekick dashboard` will not work."
            Write-Info "Attempting targeted install of [web] extra as last resort..."
            & $UvCmd pip install --python "$script:PythonExe" -e ".[web]"
            if ($LASTEXITCODE -eq 0) {
                Write-Success "[web] extra installed; `sidekick dashboard` should now work."
            } else {
                Write-Warn "Could not install [web] extra. Run manually: uv pip install --python `"$pythonExe`" `"fastapi>=0.104,<1`" `"uvicorn[standard]>=0.24,<1`""
            }
        }
    }
    
    # tinker-atropos (RL training) is optional and OFF by default.  Matches the
    # Linux/macOS install.sh behavior.  Reasons not to auto-install:
    #   - tinker-atropos/pyproject.toml pulls atroposlib + tinker from git+https
    #     which can fail on locked-down networks, flaky DNS, or rate-limited
    #     github.com and would previously kill the whole install mid-flight.
    #   - It's an RL training submodule, not part of the default agent surface.
    #     Users who don't do RL training never need it.
    # Users who do want it can run the one-liner we print below.
    if (Test-Path "tinker-atropos\pyproject.toml") {
        Write-Info "tinker-atropos submodule found - skipping install (optional, for RL training)"
        Write-Info "  To install later: $UvCmd pip install -e `".\tinker-atropos`""
    }
    
    Pop-Location
    
    Write-Success "All dependencies installed"
}

function Set-PathVariable {
    Write-Info "Setting up sidekick command..."
    
    if ($script:NoVenv) {
        $sidekickBin = "$InstallDir"
    } else {
        $sidekickBin = "$script:VenvPath\Scripts"
    }
    
    # Add the venv Scripts dir to user PATH so sidekick is globally available
    # On Windows, the sidekick.exe in venv\Scripts\ has the venv Python baked in
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    
    if ($currentPath -notlike "*$sidekickBin*") {
        [Environment]::SetEnvironmentVariable(
            "Path",
            "$sidekickBin;$currentPath",
            "User"
        )
        Write-Success "Added to user PATH: $sidekickBin"
    } else {
        Write-Info "PATH already configured"
    }
    
    # Set SIDEKICK_HOME so the Python code finds config/data in the right place.
    # Only needed on Windows where we install to %LOCALAPPDATA%\sidekick instead
    # of the Unix default ~/.sidekick
    $currentSidekickHome = [Environment]::GetEnvironmentVariable("SIDEKICK_HOME", "User")
    if (-not $currentSidekickHome -or $currentSidekickHome -ne $SidekickHome) {
        [Environment]::SetEnvironmentVariable("SIDEKICK_HOME", $SidekickHome, "User")
        Write-Success "Set SIDEKICK_HOME=$SidekickHome"
    }
    $env:SIDEKICK_HOME = $SidekickHome
    
    # Update current session
    $env:Path = "$sidekickBin;$env:Path"
    
    Write-Success "sidekick command ready"
}

function Copy-ConfigTemplates {
    Write-Info "Setting up configuration files..."
    
    # Create ~/.sidekick directory structure (using SIDEKICK_HOME)
    New-Item -ItemType Directory -Force -Path "$SidekickHome\cron" | Out-Null
    New-Item -ItemType Directory -Force -Path "$SidekickHome\sessions" | Out-Null
    New-Item -ItemType Directory -Force -Path "$SidekickHome\logs" | Out-Null
    New-Item -ItemType Directory -Force -Path "$SidekickHome\pairing" | Out-Null
    New-Item -ItemType Directory -Force -Path "$SidekickHome\hooks" | Out-Null
    New-Item -ItemType Directory -Force -Path "$SidekickHome\image_cache" | Out-Null
    New-Item -ItemType Directory -Force -Path "$SidekickHome\audio_cache" | Out-Null
    New-Item -ItemType Directory -Force -Path "$SidekickHome\memories" | Out-Null
    New-Item -ItemType Directory -Force -Path "$SidekickHome\skills" | Out-Null

    
    # Create .env
    $envPath = "$SidekickHome\.env"
    if (-not (Test-Path $envPath)) {
        $examplePath = "$InstallDir\.env.example"
        if (Test-Path $examplePath) {
            Copy-Item $examplePath $envPath
            Write-Success "Created ~/.sidekick/.env from template"
        } else {
            New-Item -ItemType File -Force -Path $envPath | Out-Null
            Write-Success "Created ~/.sidekick/.env"
        }
    } else {
        Write-Info "~/.sidekick/.env already exists, keeping it"
    }
    
    # Create config.yaml
    $configPath = "$SidekickHome\config.yaml"
    if (-not (Test-Path $configPath)) {
        $examplePath = "$InstallDir\cli-config.yaml.example"
        if (Test-Path $examplePath) {
            Copy-Item $examplePath $configPath
            Write-Success "Created ~/.sidekick/config.yaml from template"
        }
    } else {
        Write-Info "~/.sidekick/config.yaml already exists, keeping it"
    }
    
    # Create SOUL.md if it doesn't exist (global persona file).
    # IMPORTANT: write without a BOM.  Windows PowerShell 5.1's
    # ``Set-Content -Encoding UTF8`` writes UTF-8 WITH a byte-order-mark
    # (the default PS5 behaviour), and Sidekick's prompt-injection scanner
    # flags the BOM as an invisible unicode character and refuses to
    # load the file.  PS7's ``-Encoding utf8NoBOM`` fixes that but we
    # don't control which PowerShell version the user has.  Go direct
    # to .NET with an explicit UTF8Encoding($false) - BOM-free on every
    # PowerShell version.
    $soulPath = "$SidekickHome\SOUL.md"
    if (-not (Test-Path $soulPath)) {
        $soulContent = @"
# Sidekick Agent Persona

<!--
This file defines the agent's personality and tone.
The agent will embody whatever you write here.
Edit this to customize how Sidekick communicates with you.

Examples:
  - "You are a warm, playful assistant who uses kaomoji occasionally."
  - "You are a concise technical expert. No fluff, just facts."
  - "You speak like a friendly coworker who happens to know everything."

This file is loaded fresh each message -- no restart needed.
Delete the contents (or this file) to use the default personality.
-->
"@
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($soulPath, $soulContent, $utf8NoBom)
        Write-Success "Created ~/.sidekick/SOUL.md (edit to customize personality)"
    }
    
    Write-Success "Configuration directory ready: ~/.sidekick/"
    
    # Seed bundled skills into ~/.sidekick/skills/ (manifest-based, one-time per skill)
    Write-Info "Syncing bundled skills to ~/.sidekick/skills/ ..."
    $pythonExe = if (-not $script:NoVenv) { "$script:VenvPath\Scripts\python.exe" } else { "$PythonExe" }
    if (Test-Path $pythonExe) {
        try {
            & $pythonExe "$InstallDir\tools\skills_sync.py" 2>$null
            Write-Success "Skills synced to ~/.sidekick/skills/"
        } catch {
            # Fallback: simple directory copy
            $bundledSkills = "$InstallDir\skills"
            $userSkills = "$SidekickHome\skills"
            if ((Test-Path $bundledSkills) -and -not (Get-ChildItem $userSkills -Exclude '.bundled_manifest' -ErrorAction SilentlyContinue)) {
                Copy-Item -Path "$bundledSkills\*" -Destination $userSkills -Recurse -Force -ErrorAction SilentlyContinue
                Write-Success "Skills copied to ~/.sidekick/skills/"
            }
        }
    }
}

function Install-NodeDeps {
    if ($script:SkipOptionalTools) {
        Write-Info "Skipping Node.js dependencies (-SkipOptionalTools)"
        return
    }
    if (-not $HasNode) {
        Write-Info "Skipping Node.js dependencies (Node not installed)"
        return
    }

    # Resolve npm explicitly to npm.cmd, NOT npm.ps1.  Node.js on Windows
    # ships BOTH npm.cmd (a batch shim) and npm.ps1 (a PowerShell shim).
    # Get-Command's default ordering picks whichever comes first in PATHEXT,
    # and on many systems that's .ps1 - but .ps1 requires scripts to be
    # enabled in PowerShell's execution policy, which most Windows users
    # don't have (the Restricted / RemoteSigned default blocks unsigned
    # .ps1 files).  .cmd has no such restriction and works on every box.
    #
    # Strategy: look next to the npm shim we found and prefer npm.cmd if
    # it exists in the same directory.  Fall back to whatever Get-Command
    # returned if we can't find a .cmd sibling.
    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npmCmd) {
        Write-Warn "npm not found on PATH - skipping Node.js dependencies."
        Write-Info "Open a new PowerShell window and re-run 'sidekick setup tools' later."
        return
    }
    $npmExe = $npmCmd.Source
    if ($npmExe -like "*.ps1") {
        $npmCmdSibling = Join-Path (Split-Path $npmExe -Parent) "npm.cmd"
        if (Test-Path $npmCmdSibling) {
            Write-Info "Using npm.cmd (PowerShell execution policy blocks npm.ps1)"
            $npmExe = $npmCmdSibling
        } else {
            Write-Warn "Only npm.ps1 available - install may fail if script execution is disabled."
            Write-Info "  If it fails, either enable PS script execution or install Node via winget."
        }
    }

    # Helper: run "npm install" in a given directory and surface the real
    # error when it fails.  Returns $true on success.
    #
    # Implementation note: ``Start-Process -FilePath npm.cmd`` fails with
    # ``%1 is not a valid Win32 application`` on some PowerShell versions
    # because Start-Process bypasses cmd.exe / PATHEXT and expects a real
    # PE file.  The invocation-operator ``& $npmExe`` routes through the
    # PowerShell command pipeline which DOES honour .cmd batch shims, so
    # it works uniformly for npm.cmd, npx.cmd, and bare .exe files.
    function _Run-NpmInstall([string]$label, [string]$installDir, [string]$logPath, [string]$npmPath) {
        Push-Location $installDir
        try {
            # Redirect ALL output streams to the log file via 2>&1 and then
            # ``Tee-Object`` / ``Out-File``.  Simpler approach: call npm
            # with output redirected and inspect $LASTEXITCODE afterwards.
            & $npmPath install --silent *> $logPath
            $code = $LASTEXITCODE
            if ($code -eq 0) {
                Write-Success "$label dependencies installed"
                Remove-Item -Force $logPath -ErrorAction SilentlyContinue
                return $true
            }
            Write-Warn "$label npm install failed - exit code $code"
            if (Test-Path $logPath) {
                $errText = (Get-Content $logPath -Raw -ErrorAction SilentlyContinue)
                if ($errText) {
                    $snippet = if ($errText.Length -gt 1200) { $errText.Substring(0, 1200) + "..." } else { $errText }
                    Write-Info "  npm output:"
                    foreach ($line in $snippet -split "`n") {
                        Write-Host "    $line" -ForegroundColor DarkGray
                    }
                    Write-Info "  Full log: $logPath"
                }
            }
            Write-Info "Run manually later: cd `"$installDir`"; npm install"
            return $false
        } catch {
            Write-Warn "$label npm install could not be launched: $_"
            return $false
        } finally {
            Pop-Location
        }
    }

    # Browser tools (optional - only if Node.js is available)
    if (Test-Path "$InstallDir\package.json") {
        Write-Info "Installing Node.js dependencies (browser tools)..."
        $browserLog = "$env:TEMP\sidekick-npm-browser-$(Get-Random).log"
        $browserNpmOk = _Run-NpmInstall "Browser tools" $InstallDir $browserLog $npmExe

        # Install Playwright Chromium (mirrors scripts/install.sh behaviour for
        # Linux).  Without this, browser tools require a system-installed Chrome.
        if ($browserNpmOk) {
            Write-Info "Installing browser engine (Playwright Chromium)..."
            # npx lives next to npm in the same bin dir.  Prefer .cmd to dodge
            # the same execution-policy gotcha that affects npm.ps1 (see above).
            $npmDir = Split-Path $npmExe -Parent
            $npxExe = $null
            foreach ($cand in @("npx.cmd", "npx.exe", "npx")) {
                $try = Join-Path $npmDir $cand
                if (Test-Path $try) { $npxExe = $try; break }
            }
            if (-not $npxExe) {
                $npxCmd = Get-Command npx -ErrorAction SilentlyContinue
                if ($npxCmd) { $npxExe = $npxCmd.Source }
            }
            if (-not $npxExe) {
                Write-Warn "npx not found - cannot install Playwright Chromium."
                Write-Info "Run manually later: cd `"$InstallDir`"; npx playwright install chromium"
            } else {
                $pwLog = "$env:TEMP\sidekick-playwright-install-$(Get-Random).log"
                Push-Location $InstallDir
                try {
                    & $npxExe playwright install chromium *> $pwLog
                    $pwCode = $LASTEXITCODE
                    if ($pwCode -eq 0) {
                        Write-Success "Playwright Chromium installed (browser tools ready)"
                        Remove-Item -Force $pwLog -ErrorAction SilentlyContinue
                    } else {
                        Write-Warn "Playwright Chromium install failed - exit code $pwCode"
                        Write-Warn "Browser tools will not work until Chromium is installed."
                        if (Test-Path $pwLog) {
                            $pwErr = Get-Content $pwLog -Raw -ErrorAction SilentlyContinue
                            if ($pwErr) {
                                $snippet = if ($pwErr.Length -gt 1200) { $pwErr.Substring(0, 1200) + "..." } else { $pwErr }
                                Write-Info "  playwright output:"
                                foreach ($line in $snippet -split "`n") {
                                    Write-Host "    $line" -ForegroundColor DarkGray
                                }
                                Write-Info "  Full log: $pwLog"
                            }
                        }
                        Write-Info "Run manually later: cd `"$InstallDir`"; npx playwright install chromium"
                    }
                } catch {
                    Write-Warn "Playwright Chromium install could not be launched: $_"
                    Write-Info "Run manually later: cd `"$InstallDir`"; npx playwright install chromium"
                } finally {
                    Pop-Location
                }
            }
        }
    }

    # TUI (optional - only if Node.js is available)
    $tuiDir = "$InstallDir\ui-tui"
    if (Test-Path "$tuiDir\package.json") {
        Write-Info "Installing TUI dependencies..."
        $tuiLog = "$env:TEMP\sidekick-npm-tui-$(Get-Random).log"
        [void](_Run-NpmInstall "TUI" $tuiDir $tuiLog $npmExe)
    }
}

function Install-PlatformSdks {
    # Ensure messaging-platform SDKs matching tokens the user added to
    # ~/.sidekick/.env are importable.  Two problems this solves:
    #
    # 1. The tiered `uv pip install` cascade above can fall through to a
    #    lower tier when the first fails (common when RL git deps choke),
    #    which silently skips some messaging SDKs from [messaging].
    # 2. `uv` creates the venv without pip.  If a messaging SDK ends up
    #    missing, the user can't `pip install python-telegram-bot` to
    #    recover - pip simply isn't in their venv.
    #
    # Strategy: bootstrap pip via `python -m ensurepip` (idempotent), then
    # for each token set in .env, verify the matching SDK imports.  If not,
    # run one targeted `pip install` as last-chance recovery.  Keeps fresh
    # Windows installs from hitting silent "python-telegram-bot not installed"
    # at runtime.
    if ($script:NoVenv) {
        Write-Info "Skipping platform-SDK verification (-NoVenv: no venv to bootstrap)"
        return
    }

    $pythonExe = if (-not $script:NoVenv) { "$script:VenvPath\Scripts\python.exe" } else { "$PythonExe" }
    if (-not (Test-Path $pythonExe)) {
        Write-Warn "Skipping platform-SDK verification: $pythonExe not found"
        return
    }

    $envPath = "$SidekickHome\.env"
    if (-not (Test-Path $envPath)) { return }
    $envLines = Get-Content $envPath -ErrorAction SilentlyContinue

    # Map: env var set in .env -> (import name, pip spec matching [messaging] extra).
    # Specs mirror pyproject.toml to avoid version drift.
    $sdkMap = @(
        @{ Var = "TELEGRAM_BOT_TOKEN"; Import = "telegram";  Spec = "python-telegram-bot[webhooks]>=22.6,<23" },
        @{ Var = "DISCORD_BOT_TOKEN";  Import = "discord";   Spec = "discord.py[voice]>=2.7.1,<3" },
        @{ Var = "SLACK_BOT_TOKEN";    Import = "slack_sdk"; Spec = "slack-sdk>=3.27.0,<4" },
        @{ Var = "SLACK_APP_TOKEN";    Import = "slack_bolt";Spec = "slack-bolt>=1.18.0,<2" },
        @{ Var = "WHATSAPP_ENABLED";   Import = "qrcode";    Spec = "qrcode>=7.0,<8" }
    )

    # Which tokens are actually set (not placeholder)?
    $needed = @()
    foreach ($sdk in $sdkMap) {
        $match = $envLines | Where-Object {
            $_ -match ("^" + [regex]::Escape($sdk.Var) + "=.+") `
            -and $_ -notmatch "your-token-here" `
            -and $_ -notmatch "^\s*#"
        }
        if ($match) { $needed += $sdk }
    }
    if ($needed.Count -eq 0) { return }

    Write-Host ""
    Write-Info "Verifying platform SDKs for tokens found in $envPath ..."

    # Verify each SDK's import without triggering side-effect imports.
    # Quirk: PowerShell wraps non-zero-exit native stderr as a
    # NativeCommandError that prints even with `2>$null` / `*> $null`
    # unless we set $ErrorActionPreference to SilentlyContinue for the
    # span.  Save + restore rather than nuking globally.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $missing = @()
        foreach ($sdk in $needed) {
            & $pythonExe -c "import $($sdk.Import)" 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $missing += $sdk
                Write-Warn "  $($sdk.Import) NOT importable (needed for $($sdk.Var))"
            } else {
                Write-Success "  $($sdk.Import) OK"
            }
        }
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    if ($missing.Count -eq 0) { return }

    # Bootstrap pip into the venv if it isn't there.  `uv` creates venvs
    # without pip; ensurepip is the stdlib-blessed way to add it.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & $pythonExe -m pip --version 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Info "Bootstrapping pip into venv (uv doesn't ship pip)..."
            & $pythonExe -m ensurepip --upgrade 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "ensurepip failed - can't auto-install missing SDKs."
                Write-Info "Manual recovery: $UvCmd pip install `"$($missing[0].Spec)`""
                return
            }
        }

        foreach ($sdk in $missing) {
            Write-Info "  Installing $($sdk.Spec) ..."
            & $pythonExe -m pip install $sdk.Spec 2>&1 | ForEach-Object { Write-Host "    $_" }
            if ($LASTEXITCODE -eq 0) {
                Write-Success "  Installed $($sdk.Import)"
            } else {
                Write-Warn "  Failed to install $($sdk.Spec). Recover manually: $pythonExe -m pip install `"$($sdk.Spec)`""
            }
        }
    } finally {
        $ErrorActionPreference = $prevEAP
    }
}

function Invoke-SetupWizard {
    if ($script:SkipSetup) {
        Write-Info "Skipping setup wizard (-SkipSetup)"
        return
    }
    
    Write-Host ""
    Write-Info "Starting setup wizard..."
    Write-Host ""
    
    Push-Location $InstallDir
    
    # Run sidekick setup using the venv Python directly (no activation needed)
    if (-not $script:NoVenv) {
        & "$script:VenvPath\Scripts\python.exe" -m sidekick_app setup
    } else {
        & $PythonExe -m sidekick_app setup
    }
    
    Pop-Location
}

function Start-GatewayIfConfigured {
    $envPath = "$SidekickHome\.env"
    if (-not (Test-Path $envPath)) { return }

    $hasMessaging = $false
    $content = Get-Content $envPath -ErrorAction SilentlyContinue
    foreach ($var in @("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "WHATSAPP_ENABLED")) {
        $match = $content | Where-Object { $_ -match "^${var}=.+" -and $_ -notmatch "your-token-here" }
        if ($match) { $hasMessaging = $true; break }
    }

    if (-not $hasMessaging) { return }

    $sidekickCmd = if (-not $script:NoVenv) { "$script:VenvPath\Scripts\sidekick.exe" } else { "sidekick" }

    # If WhatsApp is enabled but not yet paired, run foreground for QR scan
    $whatsappEnabled = $content | Where-Object { $_ -match "^WHATSAPP_ENABLED=true" }
    $whatsappSession = "$SidekickHome\whatsapp\session\creds.json"
    if ($whatsappEnabled -and -not (Test-Path $whatsappSession)) {
        Write-Host ""
        Write-Info "WhatsApp is enabled but not yet paired."
        Write-Info "Running 'sidekick whatsapp' to pair via QR code..."
        Write-Host ""
        if (-not $script:NoPrompt) { $waResponse = Read-Host "Pair WhatsApp now? [Y/n]" } else { $waResponse = "y" }
        if ($waResponse -eq "" -or $waResponse -match "^[Yy]") {
            try {
                & $sidekickCmd whatsapp
            } catch {
                # Expected after pairing completes
            }
        }
    }

    Write-Host ""
    Write-Info "Messaging platform token detected!"
    Write-Info "The gateway handles messaging platforms and cron job execution."
    Write-Host ""
    if (-not $script:NoPrompt) { $gwResponse = Read-Host "Would you like to start the gateway now? [Y/n]" } else { $gwResponse = "y" }

    if ($gwResponse -eq "" -or $gwResponse -match "^[Yy]") {
        Write-Info "Starting gateway in background..."
        try {
            $logFile = "$SidekickHome\logs\gateway.log"
            Start-Process -FilePath $sidekickCmd -ArgumentList "gateway" `
                -RedirectStandardOutput $logFile `
                -RedirectStandardError "$SidekickHome\logs\gateway-error.log" `
                -WindowStyle Hidden
            Write-Success "Gateway started! Your bot is now online."
            Write-Info "Logs: $logFile"
            Write-Info "To stop: close the gateway process from Task Manager"
        } catch {
            Write-Warn "Failed to start gateway. Run manually: sidekick gateway"
        }
    } else {
        Write-Info "Skipped. Start the gateway later with: sidekick gateway"
    }
}

function Start-WebUI {
    if ($script:Surface -eq "CliOnly") {
        Write-Info "Skipping WebUI (-Surface CliOnly)"
        return
    }
    $sidekickCmd = if (-not $script:NoVenv) { "$script:VenvPath\Scripts\sidekick.exe" } else { "sidekick" }

    Write-Host ""
    Write-Info "The WebUI provides a browser interface for Sidekick."
    Write-Host ""
    if (-not $script:NoPrompt) { $response = Read-Host "Would you like to start the WebUI now? [Y/n]" } else { $response = "y" }

    if ($response -eq "" -or $response -match "^[Yy]") {
        Write-Info "Starting WebUI in background..."
        try {
            $webuiLogFile = "$SidekickHome\logs\webui.log"
            Start-Process -FilePath $sidekickCmd -ArgumentList "dashboard --port 9119 --no-open --skip-build" `
                -RedirectStandardOutput $webuiLogFile `
                -RedirectStandardError "$SidekickHome\logs\webui-error.log" `
                -WindowStyle Hidden

            Write-Info "Waiting for WebUI to become ready..."
            $ready = $false
            $maxAttempts = 30
            for ($i = 0; $i -lt $maxAttempts; $i++) {
                Start-Sleep -Seconds 2
                try {
                    $healthResponse = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:9119/health" -TimeoutSec 2
                    if ($healthResponse.StatusCode -eq 200) {
                        $ready = $true
                        break
                    }
                } catch {
                    # Not ready yet
                }
            }

            if ($ready) {
                Write-Success "WebUI is ready!"
                Write-Info "Opening http://127.0.0.1:9119 in your browser..."
                Start-Process "http://127.0.0.1:9119"
                $script:WebUIStarted = $true
            } else {
                Write-Warn "WebUI did not become ready within 60 seconds."
                Write-Info "Start it manually: sidekick dashboard --port 9119"
            }
        } catch {
            Write-Warn "Failed to start WebUI. Start it manually: sidekick dashboard --port 9119"
        }
    } else {
        Write-Info "Skipped. Start the WebUI later with: sidekick dashboard"
    }
}

function Write-Completion {
    Write-Host ""
    Write-Host "  ============================================================" -ForegroundColor Green
    Write-Host "   Sidekick is ready" -ForegroundColor Green
    Write-Host "  ------------------------------------------------------------" -ForegroundColor Green
    if ($script:WebUIStarted) {
        Write-Host "   WebUI is running at http://127.0.0.1:9119" -ForegroundColor DarkGray
    } else {
        Write-Host "   The desktop launcher starts the gateway, waits for WebUI" -ForegroundColor DarkGray
        Write-Host "   readiness, then opens http://127.0.0.1:9119." -ForegroundColor DarkGray
    }
    Write-Host "  ============================================================" -ForegroundColor Green
    Write-Host ""

    Write-Host "  Locations" -ForegroundColor Cyan
    Write-Host "  ------------------------------------------------------------" -ForegroundColor DarkCyan
    Write-PanelLine "Config" "$SidekickHome\config.yaml"
    Write-PanelLine "Secrets" "$SidekickHome\.env"
    Write-PanelLine "Sessions" "$SidekickHome\sessions\"
    Write-PanelLine "Logs" "$SidekickHome\logs\"
    Write-PanelLine "App" "$SidekickHome\sidekick-agent\"
    Write-PanelLine "Local start" "$SidekickHome\launcher.bat"
    Write-PanelLine "Launcher" "$([Environment]::GetFolderPath("Desktop"))\Sidekick.lnk"
    Write-Host ""

    Write-Host "  Commands" -ForegroundColor Cyan
    Write-Host "  ------------------------------------------------------------" -ForegroundColor DarkCyan
    Write-PanelLine "sidekick" "Start the terminal agent" Green White
    Write-PanelLine "sidekick setup" "Configure providers and keys" Green White
    Write-PanelLine "sidekick dashboard" "Open the local WebUI" Green White
    Write-PanelLine "sidekick gateway" "Run messaging integrations" Green White
    Write-PanelLine "sidekick update" "Update this install" Green White
    Write-Host ""

    Write-Host "  Next" -ForegroundColor Cyan
    Write-Host "  ------------------------------------------------------------" -ForegroundColor DarkCyan
    Write-Host "   1. Double-click Sidekick on the Desktop for the WebUI." -ForegroundColor White
    Write-Host "   2. Open a new terminal before using the sidekick command." -ForegroundColor White
    Write-Host "   3. Use sidekick setup if you want to change providers later." -ForegroundColor White
    Write-Host ""
    
    # Show optional Node.js info (informational only - Sidekick does not require it)
    Write-Host "  Optional" -ForegroundColor Cyan
    Write-Host "  ------------------------------------------------------------" -ForegroundColor DarkCyan
    Write-Host "   Node.js enables browser tools and the TUI:" -ForegroundColor DarkGray
    Write-Host "   https://nodejs.org/en/download/" -ForegroundColor DarkGray
    Write-Host ""
    
    if (-not $HasRipgrep) {
        Write-Host '   Faster file search: winget install BurntSushi.ripgrep.MSVC' -ForegroundColor DarkGray
        Write-Host ''
    }

    Write-Host '   Docs: https://github.com/Loggableim/sidekick-agent' -ForegroundColor DarkGray
    Write-Host ""
}

# ============================================================================
# Main
# ============================================================================

function Main {
    Write-Banner
    try { $null = (Get-Location).ProviderPath } catch {}

    if ($script:UpdateOnly) {
        Write-Info "UpdateOnly mode — skipping prerequisites, tools, and prompts"
        if (-not (Test-Path $InstallDir)) {
            Write-Err "No existing installation found at $InstallDir. Run without -UpdateOnly for a fresh install."
            Pause-IfElevated -ExitCode 1
            exit 1
        }
        Install-Repository
        Ensure-Venv -VenvPath "$InstallDir\.venv"
        Install-Dependencies
        Set-PathVariable
        Copy-ConfigTemplates
        Write-Completion
        return
    }

    Install-Uv
    Install-Git
    [void](Test-Node)
    Install-SystemPackages
    Install-Repository
    Ensure-Venv -VenvPath "$InstallDir\.venv"
    Install-Dependencies
    Install-NodeDeps
    Set-PathVariable
    Copy-ConfigTemplates
    Install-PlatformSdks
    Invoke-SetupWizard
    Start-GatewayIfConfigured
    Start-WebUI
    Write-Completion
    try {
        $desktopPath = [Environment]::GetFolderPath("Desktop")
        $batPath = "$desktopPath\Sidekick.bat"
        $lnkPath = "$desktopPath\Sidekick.lnk"
        $launcherDir = "$SidekickHome\launcher"
        $gatewayCmdPath = "$launcherDir\Sidekick-Gateway.cmd"
        $webuiCmdPath = "$launcherDir\Sidekick-WebUI.cmd"
        $legacyHomeEnvName = "H" + "ERMES_HOME"
        $portableLauncherBat = "$SidekickHome\launcher.bat"
        $portableLauncherPs1 = "$SidekickHome\Sidekick-Launcher.ps1"
        New-Item -ItemType Directory -Force -Path $launcherDir | Out-Null
        foreach ($copySpec in @(
            @{ Source = "$InstallDir\launcher.bat"; Destination = $portableLauncherBat },
            @{ Source = "$InstallDir\Sidekick-Launcher.ps1"; Destination = $portableLauncherPs1 }
        )) {
            if (Test-Path $copySpec.Source) {
                Copy-Item -LiteralPath $copySpec.Source -Destination $copySpec.Destination -Force
            }
        }

        $commonLauncherLines = @(
            'cd /d "' + $InstallDir + '"'
            'set "SIDEKICK_HOME=' + $SidekickHome + '"'
            'set "' + $legacyHomeEnvName + '=' + $SidekickHome + '"'
            'set "SIDEKICK_WEBUI_PORT=9119"'
            'set "PYTHONUTF8=1"'
            'set "PYTHONIOENCODING=utf-8"'
            'set "PYTHON_EXE=' + $script:VenvPath + '\Scripts\python.exe"'
            'set "PATH=' + $script:VenvPath + '\Scripts;' + $SidekickHome + '\git\cmd;' + $SidekickHome + '\git\bin;' + $SidekickHome + '\git\usr\bin;' + $SidekickHome + '\node;%PATH%"'
            'if exist "' + $SidekickHome + '\git\bin\bash.exe" set "SIDEKICK_GIT_BASH_PATH=' + $SidekickHome + '\git\bin\bash.exe"'
            'if not defined SIDEKICK_GIT_BASH_PATH if exist "' + $SidekickHome + '\git\usr\bin\bash.exe" set "SIDEKICK_GIT_BASH_PATH=' + $SidekickHome + '\git\usr\bin\bash.exe"'
            'set "LOGDIR=' + $SidekickHome + '\logs"'
            'if not exist "%LOGDIR%" mkdir "%LOGDIR%"'
            'set "LOGFILE=%LOGDIR%\desktop-shortcut.log"'
            'set "GATEWAY_LOGFILE=%LOGDIR%\desktop-gateway.log"'
            'set "WEBUI_LOGFILE=%LOGDIR%\desktop-webui.log"'
        )

        $gatewayContent = @(
            '@echo off'
            'title Sidekick Gateway'
        ) + $commonLauncherLines + @(
            'echo [%date% %time%] Gateway child starting >> "%LOGFILE%"'
            '"%PYTHON_EXE%" -m sidekick_app gateway run --replace --quiet >> "%GATEWAY_LOGFILE%" 2>&1'
            'set "EXIT_CODE=%ERRORLEVEL%"'
            'echo [%date% %time%] Gateway child exited with %EXIT_CODE% >> "%LOGFILE%"'
            'exit /b %EXIT_CODE%'
        )
        Set-Content -Path $gatewayCmdPath -Value ($gatewayContent -join [Environment]::NewLine) -Encoding ASCII

        $webuiContent = @(
            '@echo off'
            'title Sidekick WebUI'
        ) + $commonLauncherLines + @(
            'echo [%date% %time%] WebUI child starting >> "%LOGFILE%"'
            '"%PYTHON_EXE%" -m sidekick_app dashboard --host 127.0.0.1 --port 9119 --no-open >> "%WEBUI_LOGFILE%" 2>&1'
            'set "EXIT_CODE=%ERRORLEVEL%"'
            'echo [%date% %time%] WebUI child exited with %EXIT_CODE% >> "%LOGFILE%"'
            'exit /b %EXIT_CODE%'
        )
        Set-Content -Path $webuiCmdPath -Value ($webuiContent -join [Environment]::NewLine) -Encoding ASCII

        $batContent = @(
            '@echo off'
            'title Sidekick Agent'
        ) + $commonLauncherLines + @(
            'echo [%date% %time%] Starting Sidekick shortcut > "%LOGFILE%"'
            'type nul > "%GATEWAY_LOGFILE%"'
            'type nul > "%WEBUI_LOGFILE%"'
            'echo [%date% %time%] PATH=%PATH% >> "%LOGFILE%"'
            'echo [%date% %time%] Gateway log: %GATEWAY_LOGFILE% >> "%LOGFILE%"'
            'echo [%date% %time%] WebUI log: %WEBUI_LOGFILE% >> "%LOGFILE%"'
            'if not exist "%PYTHON_EXE%" ('
            '  echo [%date% %time%] ERROR: missing Python at "%PYTHON_EXE%" >> "%LOGFILE%"'
            '  echo ERROR: Sidekick Python runtime not found.'
            '  echo Expected: %PYTHON_EXE%'
            '  echo See log: %LOGFILE%'
            '  pause'
            '  exit /b 1'
            ')'
            'echo [1/2] Starte Gateway (Agent-Kommunikation)...'
            'echo [%date% %time%] Starting gateway >> "%LOGFILE%"'
            'start "Sidekick Gateway" /min "%ComSpec%" /c call "' + $gatewayCmdPath + '"'
            'echo [2/2] Starte WebUI...'
            'echo [%date% %time%] Starting dashboard >> "%LOGFILE%"'
            'start "Sidekick WebUI" /min "%ComSpec%" /c call "' + $webuiCmdPath + '"'
            'set "HEALTH_URL=http://127.0.0.1:9119/health"'
            'set "WEBUI_URL=http://127.0.0.1:9119"'
            'set /a READY=0'
            'echo Waiting for WebUI health check: %HEALTH_URL%'
            'for /l %%I in (1,1,180) do ('
            '  powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 ''%HEALTH_URL%''; if ($r.StatusCode -eq 200) { exit 0 } } catch { exit 1 }; exit 1"'
            '  if not errorlevel 1 ('
            '    set READY=1'
            '    goto :webui_ready'
            '  )'
            '  timeout /t 1 /nobreak >nul'
            ')'
            ':webui_ready'
            'if "%READY%"=="1" ('
            '  echo [%date% %time%] WebUI ready at %HEALTH_URL% >> "%LOGFILE%"'
            '  echo WebUI ready.'
            '  echo [%date% %time%] Opening browser at %WEBUI_URL% >> "%LOGFILE%"'
            '  start "" "%WEBUI_URL%"'
            ') else ('
            '  echo [%date% %time%] ERROR: WebUI did not become ready at %HEALTH_URL% within 180s >> "%LOGFILE%"'
            '  echo ERROR: WebUI did not become ready within 180 seconds.'
            '  echo See log: %LOGFILE%'
            '  echo.'
            '  echo --- launcher log ---'
            '  powershell -NoProfile -Command "if (Test-Path -LiteralPath ''%LOGFILE%'') { Get-Content -LiteralPath ''%LOGFILE%'' -Tail 80 }"'
            '  echo --- webui log ---'
            '  powershell -NoProfile -Command "if (Test-Path -LiteralPath ''%WEBUI_LOGFILE%'') { Get-Content -LiteralPath ''%WEBUI_LOGFILE%'' -Tail 80 }"'
            '  echo --- gateway log ---'
            '  powershell -NoProfile -Command "if (Test-Path -LiteralPath ''%GATEWAY_LOGFILE%'') { Get-Content -LiteralPath ''%GATEWAY_LOGFILE%'' -Tail 40 }"'
            '  pause'
            '  exit /b 2'
            ')'
            'echo.'
            'echo WebUI: http://127.0.0.1:9119'
            'echo Gateway: aktiv (minimiert)'
            'echo.'
            'echo Log: %LOGFILE%'
            'echo Close dieses Fenster zum Beenden aller Dienste.'
            'pause'
            'echo Shutdown...'
            'taskkill /f /fi "WINDOWTITLE eq Sidekick WebUI" >nul 2>&1'
            'taskkill /f /fi "WINDOWTITLE eq Sidekick Gateway" >nul 2>&1'
            'echo Done.'
        )
        $batContent = $batContent -join [Environment]::NewLine
        Set-Content -Path $batPath -Value $batContent -Encoding ASCII
        if (Test-Path $portableLauncherBat) {
            $desktopWrapperContent = @(
                '@echo off'
                'cd /d "' + $SidekickHome + '"'
                'call "' + $portableLauncherBat + '" %*'
                'exit /b %ERRORLEVEL%'
            ) -join [Environment]::NewLine
            Set-Content -Path $batPath -Value $desktopWrapperContent -Encoding ASCII
        }
        try {
            $shell = New-Object -ComObject WScript.Shell
            $shortcut = $shell.CreateShortcut($lnkPath)
            $shortcut.TargetPath = $batPath
            $shortcut.Arguments = ""
            $shortcut.WorkingDirectory = $SidekickHome
            $shortcut.Description = "Start Sidekick WebUI"
            $shortcut.IconLocation = "$script:PythonExe,0"
            $shortcut.Save()
        } catch {
            Write-Warn "Could not create .lnk shortcut, .bat launcher is available: $batPath"
        }
    } catch {
        throw "Desktop launcher creation failed: $_"
    }
    Write-Success "Desktop shortcut created"
}

# ============================================================================
# Main entry
# ============================================================================
try {
    Main
} catch {
    Write-Err "Installation failed: $_"
    Pause-IfElevated -ExitCode 1
    exit 1
}

Write-Host ""
Write-Host "Press Enter to close Sidekick setup..." -ForegroundColor Yellow
if (-not $script:NoPrompt) { $null = Read-Host }
exit 0
