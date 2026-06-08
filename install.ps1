# ============================================================================
# Sidekick Installer for Windows
# v0.7.21 — Real progress bar for PortableGit download
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

param(
    [switch]$NoVenv,
    [switch]$SkipSetup,
    [string]$Branch = "master",
    [string]$SidekickHome = "$env:LOCALAPPDATA\sidekick",
    [string]$InstallDir = "$env:LOCALAPPDATA\sidekick\sidekick-agent"
)

$ErrorActionPreference = "Stop"

# ============================================================================
# Admin rights check
# ============================================================================
# Sidekick installs everything under %LOCALAPPDATA% and does NOT need admin.
# If the user runs elevated, warn them — but continue (some users prefer it).
# If winget (used for Node.js) needs elevation later, it handles that itself.
$script:IsElevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $script:IsElevated) {
    # Running as non-admin — this is the NORMAL case.
    # winget may still work (it can trigger its own UAC prompt).
} else {
    Write-Warn "Sidekick Installer is running as Administrator — this is not required."
    Write-Info "Sidekick installs under %LOCALAPPDATA% and does not need admin rights."
    Write-Info "Proceeding anyway..."
}

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
$script:VenvPath = "$InstallDir\.venv"
$script:PythonExe = "$script:VenvPath\Scripts\python.exe"
$script:SidekickExe = "$script:VenvPath\Scripts\sidekick.exe"

# ============================================================================
# Helper functions
# ============================================================================

function Write-Banner {
    Write-Host ""
    Write-Host "┌─────────────────────────────────────────────────────────┐" -ForegroundColor Cyan
    Write-Host "│             ⚡ Sidekick Installer                         │" -ForegroundColor Cyan
    Write-Host "├─────────────────────────────────────────────────────────┤" -ForegroundColor Cyan
    Write-Host "│  An open source AI agent for your terminal.              │" -ForegroundColor Cyan
    Write-Host "└─────────────────────────────────────────────────────────┘" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Info {
    param([string]$Message)
    Write-Host "→ $Message" -ForegroundColor Cyan
    if ($LogFile) { Add-Content -Path $LogFile -Value "[INFO] → $Message" -Encoding UTF8 -ErrorAction SilentlyContinue }
}

function Write-Success {
    param([string]$Message)
    Write-Host "✓ $Message" -ForegroundColor Green
    if ($LogFile) { Add-Content -Path $LogFile -Value "[OK]   ✓ $Message" -Encoding UTF8 -ErrorAction SilentlyContinue }
}

function Write-Warn {
    param([string]$Message)
    Write-Host "⚠ $Message" -ForegroundColor Yellow
    if ($LogFile) { Add-Content -Path $LogFile -Value "[WARN] ⚠ $Message" -Encoding UTF8 -ErrorAction SilentlyContinue }
}

function Write-Err {
    param([string]$Message)
    Write-Host "✗ $Message" -ForegroundColor Red
    if ($LogFile) { Add-Content -Path $LogFile -Value "[ERR]  ✗ $Message" -Encoding UTF8 -ErrorAction SilentlyContinue }
}

# ============================================================================
# Process execution helper (separated streams, no ErrorActionPreference issues)
# ============================================================================

function Invoke-External {
    param(
        [Parameter(Mandatory=$true)]
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory = (Get-Location).ProviderPath,
        [int]$TimeoutSeconds = 300
    )

    # Save and temporarily override ErrorActionPreference so stderr from native
    # commands (e.g. uv download progress) does NOT become a terminating error.
    $savedEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    # Create temp files for stdout/stderr
    $tmpOut = [System.IO.Path]::GetTempFileName()
    $tmpErr = [System.IO.Path]::GetTempFileName()

    try {
        $proc = Start-Process -FilePath $FilePath `
            -ArgumentList $ArgumentList `
            -Wait -PassThru -NoNewWindow `
            -RedirectStandardOutput $tmpOut `
            -RedirectStandardError $tmpErr `
            -WorkingDirectory $WorkingDirectory

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
        # Restore ErrorActionPreference
        $ErrorActionPreference = $savedEAP
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
        $script:UvCmd = (Get-Command uv).Source  # Full path required for Start-Process
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

    $venvPython = "$VenvPath\Scripts\python.exe"  # local alias for $script:PythonExe

    # ── 1. If venv already exists and has a working python, use it ──
    if (Test-Path $venvPython) {
        $ver = & $venvPython --version 2>$null
        if ($ver -match "Python 3\.") {
            Write-Success "Virtual environment found: $ver"
            $script:PythonExe = $venvPython
            return $true
        }
        Write-Warn "Virtual environment broken at $VenvPath — recreating..."
        Remove-Item -Recurse -Force $VenvPath -ErrorAction SilentlyContinue
    }

    # ── 2. Primary path: uv venv --python (handles Python download + venv in one step) ──
    Write-Info "Creating virtual environment with uv (Python $PythonVersion)..."
    Add-Content -Path $LogFile -Value "[INFO] Running: uv venv --python $PythonVersion $VenvPath" -Encoding UTF8 -ErrorAction SilentlyContinue

    $result = Invoke-External -FilePath $script:UvCmd -ArgumentList @("venv", "--python", $PythonVersion, "$VenvPath")

    if ($result.ExitCode -eq 0 -and (Test-Path $venvPython)) {
        $ver = & $venvPython --version 2>$null
        Write-Success "Virtual environment created: $ver"
        $script:PythonExe = $venvPython
        Add-Content -Path $LogFile -Value "[OK] PythonExe = $venvPython" -Encoding UTF8 -ErrorAction SilentlyContinue
        return $true
    }

    # uv venv failed — log and continue
    Write-Warn "uv venv exited with code $($result.ExitCode). See $LogFile for details."
    Add-Content -Path $LogFile -Value "[WARN] uv venv failed. stderr was captured above." -Encoding UTF8 -ErrorAction SilentlyContinue

    # ── 3. Fallback: uv python install + python -m venv ──
    Write-Info "Trying uv python install instead..."
    Add-Content -Path $LogFile -Value "[INFO] Running: uv python install $PythonVersion" -Encoding UTF8 -ErrorAction SilentlyContinue

    $result = Invoke-External -FilePath $script:UvCmd -ArgumentList @("python", "install", $PythonVersion)

    if ($result.ExitCode -eq 0) {
        # Find the installed Python
        $pythonPath = & $UvCmd python find $PythonVersion 2>$null
        if ($pythonPath -and (Test-Path $pythonPath)) {
            $ver = & $pythonPath --version 2>$null
            Write-Success "Python provisioned: $ver"
            & $pythonPath -m venv "$VenvPath"
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
                & $pythonPath -m venv "$VenvPath"
                if ($LASTEXITCODE -eq 0 -and (Test-Path $venvPython)) {
                    $script:PythonExe = $venvPython
                    return $true
                }
            }
        }
    }

    # ── 4. Scan for system Python (excluding Store alias) ──
    Write-Info "Scanning for system Python installations (excluding Microsoft Store alias)..."
    $candidateDirs = @()

    # PATH directories — skip Microsoft\WindowsApps (Store alias)
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

    # ── 5. All paths exhausted ──
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

    Priority order (deliberately simple — no winget, no registry, no system
    package manager):
      1. Existing ``git`` on PATH — use it as-is (the common fast path).
      2. Download **PortableGit** from the official git-for-windows GitHub
         release (self-extracting 7z.exe) and unpack it to
         ``%LOCALAPPDATA%\sidekick\git`` — never touches system Git, never
         requires admin, works even on locked-down machines and machines
         with a broken system Git install.

    **Why PortableGit, not MinGit:**  MinGit is the minimal-automation
    distribution and ships ONLY ``git.exe`` — no bash, no POSIX utilities.
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
        $version = git --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -match "git version") {
            Write-Success "Git found ($version)"
            Set-GitBashEnvVar
            return $true
        }
        Write-Warn "Git found on PATH but returned error (corrupt config?) — downloading PortableGit"
    }

    # Download PortableGit into $SidekickHome\git.  Always works as long as
    # we can reach github.com — no admin, no winget, no reliance on the
    # user's possibly-broken system Git install.
    Write-Info "Git not found — downloading PortableGit to $SidekickHome\git\ ..."
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
            # PortableGit does not ship a 32-bit build — fall back to MinGit 32-bit
            # with a warning that bash-based features will be unavailable.
            "32-bit-mingit"
        }

        $releaseApi = "https://api.github.com/repos/git-for-windows/git/releases/latest"
        $release = Invoke-RestMethod -Uri $releaseApi -UseBasicParsing -TimeoutSec 60 -Headers @{ "User-Agent" = "sidekick-installer" }

        if ($arch -eq "32-bit-mingit") {
            Write-Warn "32-bit Windows detected — PortableGit is 64-bit only.  Installing MinGit 32-bit as a last resort; bash-dependent Sidekick features (terminal tool, agent-browser) will not work on this machine."
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
        # Use Invoke-WebRequest -OutFile (native PowerShell progress via Write-Progress in
        # PowerShell 7+). For PowerShell 5.1 we supplement with a custom polling-based
        # progress bar that reads the on-disk file size every 200ms.
        try {
            $request = [System.Net.HttpWebRequest]::Create($downloadUrl)
            $request.UserAgent = "sidekick-installer"
            $response = $request.GetResponse()
            $totalSize = [int64]$response.ContentLength
            $response.Close()

            if ($totalSize -gt 0) {
                # Polling-based progress: launch IWR in background job, watch file size
                $iwrJob = Start-Job -ScriptBlock {
                    param($u, $o) Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing -TimeoutSec 600 2>&1
                } -ArgumentList $downloadUrl, $tmpFile

                $lastPct = -1
                while ($iwrJob.State -eq "Running") {
                    Start-Sleep -Milliseconds 250
                    if (Test-Path $tmpFile) {
                        $current = (Get-Item $tmpFile).Length
                        $pct = [int](($current / $totalSize) * 100)
                        if ($pct -ne $lastPct) {
                            $lastPct = $pct
                            $mb = [math]::Round($current / 1MB, 1)
                            $totalMb = [math]::Round($totalSize / 1MB, 1)
                            $bar = [string]::new([char]0x2588, [math]::Floor($pct / 5)) + [string]::new([char]0x2591, [math]::Ceiling((100 - $pct) / 5))
                            Write-Progress -Activity "Downloading PortableGit" -Status "$mb MB / $totalMb MB ($pct%)" -PercentComplete $pct
                            Write-Host "`r  ⏳ $($bar) $pct% ($mb MB / $totalMb MB)" -NoNewline -ForegroundColor DarkYellow
                        }
                    }
                }
                Receive-Job $iwrJob -Wait | Out-Null
                Remove-Job $iwrJob -Force
                Write-Host ""
                Write-Progress -Activity "Downloading PortableGit" -Completed
            } else {
                # Unknown size — fall back to plain download
                Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpFile -UseBasicParsing -TimeoutSec 600
            }
        } catch {
            # Final fallback: plain Invoke-WebRequest
            Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpFile -UseBasicParsing -TimeoutSec 600
        }

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
    # because of the parens — use [Environment]::GetEnvironmentVariable().
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

    Write-Warn "Could not locate bash.exe — Sidekick may not find Git Bash."
    Write-Info "If needed, set SIDEKICK_GIT_BASH_PATH manually to your bash.exe path."
}

function Test-Node {
    <#
    .SYNOPSIS
    Check for Node.js. Sidekick core works without it. Browser tools and TUI need it.
    Skip auto-install if SKIP_NODE_AUTOINSTALL=1 is set (saves ~5min on slow winget).
    #>
    $NodeVersion = "22"

    # Already available
    if (Get-Command node -ErrorAction SilentlyContinue) {
        $version = node --version
        Write-Success "Node.js $version found"
        $script:HasNode = $true
        return $true
    }

    # Already managed
    $managedNode = "$SidekickHome\node\node.exe"
    if (Test-Path $managedNode) {
        $version = & $managedNode --version
        $env:Path = "$SidekickHome\node;$env:Path"
        Write-Success "Node.js $version found (Sidekick-managed)"
        $script:HasNode = $true
        return $true
    }

    # Skip auto-install if requested
    if ($env:SKIP_NODE_AUTOINSTALL -eq "1") {
        Write-Info "Skipping Node.js auto-install (SKIP_NODE_AUTOINSTALL=1)"
        $script:HasNode = $false
        return $true
    }

    # Install via winget
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "Installing Node.js $NodeVersion via winget (may take several minutes)..."
        try {
            # Show a simple activity spinner while winget runs
            $wingetJob = Start-Job -ScriptBlock { param($v) winget install OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null } -ArgumentList $NodeVersion
            $spinner = @("|", "/", "-", "\\")
            $i = 0
            while ($wingetJob.State -eq "Running") {
                Write-Host "`r  ⏳ Installing Node.js $NodeVersion $($spinner[$i])" -NoNewline -ForegroundColor DarkYellow
                $i = ($i + 1) % 4
                Start-Sleep -Milliseconds 200
            }
            Write-Host "`r  " -NoNewline
            Receive-Job $wingetJob -ErrorAction SilentlyContinue | Out-Null
            Remove-Job $wingetJob -ErrorAction SilentlyContinue
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
            if (Get-Command node -ErrorAction SilentlyContinue) {
                $version = node --version
                Write-Success "Node.js $version installed via winget"
                $script:HasNode = $true
                return $true
            }
        } catch {
            Write-Warn "winget install failed: $_"
        }
    }

    # Fallback: download binary zip
    Write-Info "Downloading Node.js $NodeVersion binary..."
    try {
        $arch = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
        $indexUrl = "https://nodejs.org/dist/latest-v${NodeVersion}.x/"
        $indexPage = Invoke-WebRequest -Uri $indexUrl -UseBasicParsing -TimeoutSec 30
        $zipName = ($indexPage.Content | Select-String -Pattern "node-v${NodeVersion}\\.\\d+\\.\\d+-win-${arch}\\.zip" -AllMatches).Matches[0].Value

        if ($zipName) {
            $downloadUrl = "${indexUrl}${zipName}"
            $tmpZip = "$env:TEMP\\$zipName"
            $tmpDir = "$env:TEMP\\sidekick-node-extract"

            Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpZip -UseBasicParsing -TimeoutSec 120
            if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
            Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force

            $extractedDir = Get-ChildItem $tmpDir -Directory | Select-Object -First 1
            if ($extractedDir) {
                if (Test-Path "$SidekickHome\\node") { Remove-Item -Recurse -Force "$SidekickHome\\node" }
                Move-Item $extractedDir.FullName "$SidekickHome\\node"
                $env:Path = "$SidekickHome\\node;$env:Path"
                $version = & "$SidekickHome\\node\\node.exe" --version
                Write-Success "Node.js $version installed to $SidekickHome\\node\\"
                $script:HasNode = $true
                Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue
                Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
                return $true
            }
        }
    } catch {
        Write-Warn "Node.js download failed: $_"
    }

    Write-Warn "Could not auto-install Node.js — browser tools and TUI will be unavailable."
    Write-Info "Install manually: https://nodejs.org/en/download/"
    $script:HasNode = $false
    return $true
}

function Install-SystemPackages {
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
        # directory OR a symlink OR a submodule-style gitfile — and also when
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
git -c windows.appendAtomically=false -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 pull --ff-only origin $Branch
                if ($LASTEXITCODE -ne 0) {
                    Write-Warn "git pull --ff-only failed (exit $LASTEXITCODE) — trying reset..."
                    git -c windows.appendAtomically=false reset --hard origin/$Branch
                    if ($LASTEXITCODE -ne 0) { Write-Err "git reset failed (exit $LASTEXITCODE)" ; exit 4 }
                }
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
            Write-Warn "Existing directory at $InstallDir is not a valid git repo — replacing it."
            try {
                Remove-Item -Recurse -Force $InstallDir -ErrorAction Stop
            } catch {
                Write-Err "Could not remove $InstallDir : $_"
                Write-Info "Close any programs that might be using files in $InstallDir (editors,"
                Write-Info "terminals, running sidekick processes) and try again."
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

        # FIRST: check for corrupt .gitconfig BEFORE calling any git config --global
        # Must use a self-contained try/catch because $ErrorActionPreference=Stop
        # converts native-command non-zero exits into terminating exceptions.
        try {
            $gitConfigPath = "$env:USERPROFILE\.gitconfig"
            if (Test-Path $gitConfigPath) {
                # 2>$null avoids ErrorActionPreference issues on stderr-only output,
                # but a non-zero exit still triggers Stop.  Catch that here.
                $null = git config --list --global 2>$null
                if ($LASTEXITCODE -ne 0) {
                    Write-Warn "Corrupt .gitconfig found at $gitConfigPath — replacing with clean config"
                    $backupPath = "$env:USERPROFILE\.gitconfig.sidekick-backup"
                    Copy-Item -Path $gitConfigPath -Destination $backupPath -Force -ErrorAction SilentlyContinue
                    Remove-Item -Path $gitConfigPath -Force -ErrorAction SilentlyContinue
                    Write-Info "Backed up old config to $backupPath"
                }
            }
        } catch {
            # If git config --list itself throws despite 2>$null, remove corrupt file
            Write-Warn "Could not check .gitconfig: $_ — removing corrupt file"
            $gitConfigPath = "$env:USERPROFILE\.gitconfig"
            if (Test-Path $gitConfigPath) {
                try {
                    $backupPath = "$env:USERPROFILE\.gitconfig.sidekick-backup"
                    Copy-Item -Path $gitConfigPath -Destination $backupPath -Force -ErrorAction SilentlyContinue
                    Remove-Item -Path $gitConfigPath -Force -ErrorAction SilentlyContinue
                    Write-Info "Removed corrupt .gitconfig (backup at $backupPath)"
                } catch {
                    Write-Warn "Could not remove corrupt .gitconfig: $_"
                }
            }
        }

        # Now safe to call git config --global — .gitconfig is clean or we already
        # set GIT_CONFIG_COUNT/GIT_CONFIG_KEY/GIT_CONFIG_VALUE env vars as override
        $env:GIT_CONFIG_COUNT = "1"
        $env:GIT_CONFIG_KEY_0 = "windows.appendAtomically"
        $env:GIT_CONFIG_VALUE_0 = "false"
        # Also set GIT_CONFIG_NOSYSTEM=1 to fully bypass any system-level gitconfig
        # wrap in try/catch because $ErrorActionPreference=Stop can fire on stderr
        try { git config --global windows.appendAtomically false 2>$null } catch { }

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
            Write-Warn "Git clone failed — downloading ZIP archive instead..."
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



function Install-Dependencies {
Write-Info "Installing dependencies..."

    Push-Location $InstallDir

    if (-not $NoVenv) {
        $env:VIRTUAL_ENV = "$script:VenvPath"
        $pipPython = "--python", "$script:PythonExe"
    } else {
        $pipPython = @()
    }

    $installTiers = @(
        @{ Name = "all (with RL/matrix extras)"; Spec = ".[all]" },
        @{ Name = "PyPI-only extras (no git deps)"; Spec = ".[web,mcp,cron,cli,voice,messaging,slack,dev,acp,pty,homeassistant,sms,tts-premium,honcho,google,mistral,bedrock,dingtalk,feishu,modal,daytona,vercel]" },
        @{ Name = "dashboard + core platforms"; Spec = ".[web,mcp,cron,cli,messaging,dev]" },
        @{ Name = "core only (no extras)"; Spec = "." }
    )
    $installed = $false
    foreach ($tier in $installTiers) {
        Write-Info "Trying tier: $($tier.Name) ..."
        $spec = $tier.Spec
        if ($spec -eq ".") {
            & $UvCmd pip install $pipPython -e "$InstallDir"
        } else {
            $extras = $spec.Substring(1)  # ".[all]" -> "[all]"
            & $UvCmd pip install $pipPython -e "$InstallDir$extras"
        }
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
        exit 5
    }

    # Verify the dashboard deps specifically — they're the most common thing
    # users hit and lazy-import errors from `sidekick dashboard` are confusing.
    # If tier 1 failed (the common case), [web] was still picked up by tiers
    # 2-3; only tier 4 leaves you without it.
    $pythonExe = if (-not $NoVenv) { "$script:PythonExe" } else { (& $UvCmd python find $PythonVersion) }
    if (Test-Path $pythonExe) {
        $webOk = $false
        try {
            & $pythonExe -c "import fastapi, uvicorn" 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { $webOk = $true }
        } catch { }
        if (-not $webOk) {
            Write-Warn "fastapi/uvicorn not importable — `sidekick dashboard` will not work."
            Write-Info "Attempting targeted install of [web] extra as last resort..."
            & $UvCmd pip install $pipArgs -e ".[web]"
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
        Write-Info "tinker-atropos submodule found — skipping install (optional, for RL training)"
        Write-Info "  To install later: $UvCmd pip install -e `".\tinker-atropos`""
    }
    
    Pop-Location
    
    Write-Success "All dependencies installed"
}

function Set-PathVariable {
    Write-Info "Setting up sidekick command..."
    
    if ($NoVenv) {
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
    # to .NET with an explicit UTF8Encoding($false) — BOM-free on every
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
    $pythonExe = "$script:PythonExe"
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
    if (-not $HasNode) {
        Write-Info "Skipping Node.js dependencies (Node not installed)"
        return
    }

    # Resolve npm explicitly to npm.cmd, NOT npm.ps1.  Node.js on Windows
    # ships BOTH npm.cmd (a batch shim) and npm.ps1 (a PowerShell shim).
    # Get-Command's default ordering picks whichever comes first in PATHEXT,
    # and on many systems that's .ps1 — but .ps1 requires scripts to be
    # enabled in PowerShell's execution policy, which most Windows users
    # don't have (the Restricted / RemoteSigned default blocks unsigned
    # .ps1 files).  .cmd has no such restriction and works on every box.
    #
    # Strategy: look next to the npm shim we found and prefer npm.cmd if
    # it exists in the same directory.  Fall back to whatever Get-Command
    # returned if we can't find a .cmd sibling.
    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npmCmd) {
        Write-Warn "npm not found on PATH — skipping Node.js dependencies."
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
            Write-Warn "Only npm.ps1 available — install may fail if script execution is disabled."
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
            Write-Warn "$label npm install failed — exit code $code"
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

    # Browser tools (optional — only if Node.js is available)
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
                Write-Warn "npx not found — cannot install Playwright Chromium."
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
                        Write-Warn "Playwright Chromium install failed — exit code $pwCode"
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

    # TUI (optional — only if Node.js is available)
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
    #    recover — pip simply isn't in their venv.
    #
    # Strategy: bootstrap pip via `python -m ensurepip` (idempotent), then
    # for each token set in .env, verify the matching SDK imports.  If not,
    # run one targeted `pip install` as last-chance recovery.  Keeps fresh
    # Windows installs from hitting silent "python-telegram-bot not installed"
    # at runtime.
    if ($NoVenv) {
        Write-Info "Skipping platform-SDK verification (-NoVenv: no venv to bootstrap)"
        return
    }

    $pythonExe = "$script:PythonExe"
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
                Write-Warn "ensurepip failed — can't auto-install missing SDKs."
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
    if ($SkipSetup) {
        Write-Info "Skipping setup wizard (-SkipSetup)"
        return
    }
    
    Write-Host ""
    Write-Info "Starting setup wizard..."
    Write-Host ""
    
    Push-Location $InstallDir
    
    # Run sidekick setup using the venv Python directly (no activation needed)
    if (-not $NoVenv) {
        & "$script:PythonExe" -m sidekick_cli.main setup
    } else {
        & $PythonExe -m sidekick_cli.main setup
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

    $sidekickCmd = "$script:SidekickExe"
    if (-not (Test-Path $sidekickCmd)) {
        $sidekickCmd = "sidekick"
    }

    # If WhatsApp is enabled but not yet paired, run foreground for QR scan
    $whatsappEnabled = $content | Where-Object { $_ -match "^WHATSAPP_ENABLED=true" }
    $whatsappSession = "$SidekickHome\whatsapp\session\creds.json"
    if ($whatsappEnabled -and -not (Test-Path $whatsappSession)) {
        Write-Host ""
        Write-Info "WhatsApp is enabled but not yet paired."
        Write-Info "Running 'sidekick whatsapp' to pair via QR code..."
        Write-Host ""
        $response = Read-Host "Pair WhatsApp now? [Y/n]"
        if ($response -eq "" -or $response -match "^[Yy]") {
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
    $response = Read-Host "Would you like to start the gateway now? [Y/n]"

    if ($response -eq "" -or $response -match "^[Yy]") {
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

function Write-Completion {
    Write-Host ""
    Write-Host "┌─────────────────────────────────────────────────────────┐" -ForegroundColor Green
    Write-Host "│              ✓ Installation Complete!                   │" -ForegroundColor Green
    Write-Host "└─────────────────────────────────────────────────────────┘" -ForegroundColor Green
    Write-Host ""
    
    # Show file locations
    Write-Host "📁 Your files:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   Config:    " -NoNewline -ForegroundColor Yellow
    Write-Host "$SidekickHome\config.yaml"
    Write-Host "   API Keys:  " -NoNewline -ForegroundColor Yellow
    Write-Host "$SidekickHome\.env"
    Write-Host "   Data:      " -NoNewline -ForegroundColor Yellow
    Write-Host "$SidekickHome\cron\, sessions\, logs\"
    Write-Host "   Code:      " -NoNewline -ForegroundColor Yellow
    Write-Host "$SidekickHome\sidekick-agent\"
    Write-Host ""
    
    Write-Host "─────────────────────────────────────────────────────────" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "🚀 Commands:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   sidekick              " -NoNewline -ForegroundColor Green
    Write-Host "Start chatting with Sidekick"
    Write-Host "   sidekick setup        " -NoNewline -ForegroundColor Green
    Write-Host "Configure API keys & settings"
    Write-Host "   sidekick config       " -NoNewline -ForegroundColor Green
    Write-Host "View/edit configuration"
    Write-Host "   sidekick config edit  " -NoNewline -ForegroundColor Green
    Write-Host "Open config in editor"
    Write-Host "   sidekick gateway      " -NoNewline -ForegroundColor Green
    Write-Host "Start messaging gateway (Telegram, Discord, etc.)"
    Write-Host "   sidekick update       " -NoNewline -ForegroundColor Green
    Write-Host "Update to latest version"
    Write-Host "   sidekick dashboard    " -NoNewline -ForegroundColor Green
    Write-Host "Open the Sidekick web dashboard"
    Write-Host "   sidekick --tui        " -NoNewline -ForegroundColor Green
    Write-Host "Launch the terminal TUI"
    Write-Host ""
    
    Write-Host "─────────────────────────────────────────────────────────" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "⚡ Try Sidekick now:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   Restart your terminal, then run:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   sidekick" -ForegroundColor Green
    Write-Host ""
    Write-Host "   Or open the web dashboard in your browser:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   sidekick dashboard" -ForegroundColor Green
    Write-Host ""
    Write-Host "─────────────────────────────────────────────────────────" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "⚡ Restart your terminal for PATH changes to take effect" -ForegroundColor Yellow
    Write-Host ""
    
    # Show optional Node.js info (informational only — Sidekick does not require it)
    Write-Host "Note: Node.js is optional for Sidekick." -ForegroundColor Yellow
    Write-Host "Browser tools and TUI need Node.js. Install if desired:" -ForegroundColor Yellow
    Write-Host "  https://nodejs.org/en/download/" -ForegroundColor Yellow
    Write-Host ""
    
    if (-not $HasRipgrep) {
        Write-Host "Note: ripgrep (rg) was not installed. For faster file search:" -ForegroundColor Yellow
        Write-Host "  winget install BurntSushi.ripgrep.MSVC" -ForegroundColor Yellow
        Write-Host ""
    }
    
    # Quick-start: open docs
    Write-Host "📖 Documentation: https://docs.sidekick-agent.dev" -ForegroundColor Cyan
    Write-Host ""
}

# ============================================================================
# Main
# ============================================================================

function Main {
    Write-Banner

    # Windows refuses to delete a directory any shell is currently cd'd
    # inside — and silently leaves orphan files behind, which then wedge
    # "is this a valid git repo" probes on re-install.  If the current
    # working dir is under $InstallDir, step out to the user's home
    # BEFORE doing anything else.  Harmless when the user ran the
    # installer from somewhere else.
    try {
        $currentResolved = (Get-Location).ProviderPath
        $installResolved = $null
        if (Test-Path $InstallDir) {
            $installResolved = (Resolve-Path $InstallDir -ErrorAction SilentlyContinue).ProviderPath
        }
        if ($installResolved -and $currentResolved.ToLower().StartsWith($installResolved.ToLower())) {
            Write-Info "Stepping out of $InstallDir so Windows can replace files there if needed..."
            Set-Location $env:USERPROFILE
        }
    } catch {}

if (-not (Install-Uv)) { Write-Err "uv installation failed — cannot continue" ; exit 2 }
    if (-not (Install-Git)) { Write-Err "Git not available and auto-install failed — install from https://git-scm.com/download/win then re-run" ; exit 2 }
    # Test-Node always returns $true (sets $script:HasNode on success, emits a
    # warning on failure and continues so non-browser installs still work).
    # Cast to [void] so the bare return value doesn't print "True" to the
    # console between the "Node found" line and the next installer step.
    [void](Test-Node)
    Install-SystemPackages  # ripgrep + ffmpeg in one step

Install-Repository
    if (-not (Ensure-Venv -VenvPath "$InstallDir\.venv")) { Write-Err "Python/venv provisioning failed — cannot continue" ; exit 2 }
    Install-Dependencies
    Install-NodeDeps
    Set-PathVariable
    Copy-ConfigTemplates
    Install-PlatformSdks
    Invoke-SetupWizard
    Start-GatewayIfConfigured

    Write-Completion
    
    # ── Desktop shortcut ──────────────────────────────────────────
    Write-Info "Creating desktop shortcut..."
    try {
        $desktopPath = [Environment]::GetFolderPath("Desktop")
        $shortcutPath = "$desktopPath\Sidekick.lnk"
        $iconPath = "$InstallDir\web\static\sidekick-taskbar.ico"
        if (-not (Test-Path $iconPath)) {
            $iconPath = "$InstallDir\web\static\favicon.ico"
        }
        
        $wshell = New-Object -ComObject WScript.Shell
        $shortcut = $wshell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $script:SidekickExe
        $shortcut.Arguments = "dashboard"
        $shortcut.Description = "Sidekick WebUI Dashboard"
        $shortcut.WorkingDirectory = "$InstallDir"
        if (Test-Path $iconPath) {
            $shortcut.IconLocation = "$iconPath, 0"
        }
        $shortcut.Save()
        Write-Success "Desktop shortcut created: $shortcutPath"
    } catch {
        Write-Warn "Could not create desktop shortcut: $_"
    }
    
    # ── Auto-open WebUI ────────────────────────────────────────────
        Write-Info "Opening Sidekick WebUI in your browser..."
        try {
            $sidekickExe = $script:SidekickExe
            if (Test-Path $sidekickExe) {
                # Try to add 'sidekick' to Windows hosts file so http://sidekick:8787 works.
                # On most Windows installs the file is owned by SYSTEM, so a non-admin
                # PowerShell can't write it. We attempt the write and gracefully fall
                # back to telling the user how to do it manually.
                $hostsPath = "$env:windir\System32\drivers\etc\hosts"
                $hostsEntry = "127.0.0.1`tsidekick"
                $hostsContent = if (Test-Path $hostsPath) { Get-Content $hostsPath -Raw } else { "" }
                $hostsOk = $false
                if ($hostsContent -match "(?m)^\s*127\.0\.0\.1\s+sidekick\s*$") {
                    Write-Info "'sidekick' already in hosts file"
                    $hostsOk = $true
                } else {
                    $tmpHosts = [System.IO.Path]::GetTempFileName()
                    try {
                        Copy-Item $hostsPath $tmpHosts -Force -ErrorAction Stop
                        $newContent = (Get-Content $tmpHosts -Raw) + "`r`n$hostsEntry  # sidekick-installer`r`n"
                        Set-Content -Path $tmpHosts -Value $newContent -ErrorAction Stop
                        # Atomic-ish replace: copy via cmd /c which runs in the user's context
                        $copyResult = cmd /c copy /Y "$tmpHosts" "$hostsPath" 2>&1
                        if ($LASTEXITCODE -eq 0 -and (Get-Content $hostsPath -Raw) -match "127\.0\.0\.1\s+sidekick") {
                            Write-Info "Added 'sidekick' to hosts — http://sidekick:8787 now works"
                            $hostsOk = $true
                        } else {
                            Write-Info "Could not write to hosts file (admin needed for http://sidekick:8787)"
                        }
                    } catch {
                        Write-Info "Could not write to hosts file (admin needed for http://sidekick:8787): $_"
                    } finally {
                        Remove-Item $tmpHosts -ErrorAction SilentlyContinue
                    }
                }
                $proc = Start-Process -FilePath $sidekickExe -ArgumentList "dashboard" -NoNewWindow -PassThru
                Start-Sleep -Seconds 3
                if ($hostsOk) {
                    Start-Process "http://sidekick:8787"
                } else {
                    Start-Process "http://127.0.0.1:8787"
                }
                Write-Success "WebUI dashboard started — open http://sidekick:8787 (or http://127.0.0.1:8787 if hosts-file is locked)"
        } else {
            Write-Warn "sidekick.exe not found — start dashboard manually with: .\start.ps1 dashboard"
        }
    } catch {
        Write-Warn "Could not auto-start WebUI: $_"
        Write-Info "Start it manually: sidekick dashboard"
    }

    exit 0
}

# Wrap in try/catch so errors don't kill the terminal when run via:
#   irm https://...install.ps1 | iex
# (exit/throw inside iex kills the entire PowerShell session)
try {
    Main
} catch {
    Write-Host ""
    Write-Err "Installation failed: $_"
    Write-Host ""
    Write-Info "If the error is unclear, try downloading and running the script directly:"
    Write-Host "  Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/install.ps1' -OutFile install.ps1" -ForegroundColor Yellow
    Write-Host "  .\\install.ps1" -ForegroundColor Yellow
    Write-Host ""
    Write-Info "→ Log file: $LogFile"
    Write-Host ""
    exit 1
}
