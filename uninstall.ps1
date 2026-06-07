# ============================================================================
# Sidekick Uninstaller for Windows
# ============================================================================
# Removes Sidekick from %LOCALAPPDATA%\sidekick\ and user environment.
# Safe: never touches ProgramFiles, system PATH, or anything outside
# %LOCALAPPDATA%.
#
# Usage:
#   .\uninstall.ps1                    # Remove app files, keep ~/.sidekick config
#   .\uninstall.ps1 -RemoveUserData    # Remove everything including config
# ============================================================================

param(
    [switch]$RemoveUserData
)

$ErrorActionPreference = "Stop"

# ============================================================================
# Exit Code Schema
# ============================================================================
# 0  Success — Sidekick fully removed
# 1  Partial — some items could not be removed (non-fatal)
# ============================================================================

# ============================================================================
# Configuration
# ============================================================================
$SidekickHome = "$env:LOCALAPPDATA\sidekick"
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = "$DesktopPath\Sidekick.lnk"
$UserHome = $env:USERPROFILE
$SidekickConfigDir = "$UserHome\.sidekick"

# ============================================================================
# Color helpers (mirrors install.ps1)
# ============================================================================
function Write-Banner {
    Write-Host ""
    Write-Host "┌─────────────────────────────────────────────────────────┐" -ForegroundColor Cyan
    Write-Host "│              ⚡ Sidekick Uninstaller                       │" -ForegroundColor Cyan
    Write-Host "├─────────────────────────────────────────────────────────┤" -ForegroundColor Cyan
    Write-Host "│  Removing Sidekick from this system.                     │" -ForegroundColor Cyan
    Write-Host "└─────────────────────────────────────────────────────────┘" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Info {
    param([string]$Message)
    Write-Host "→ $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "✓ $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "⚠ $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "✗ $Message" -ForegroundColor Red
}

# ============================================================================
# Helper functions
# ============================================================================

function Test-AdminRights {
    # Check if running as admin — warn but don't block
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
    if ($isAdmin) {
        Write-Warn "Running as Administrator — this is not needed and may leave orphan files."
        Write-Info "Sidekick installs under %LOCALAPPDATA% and does not require elevation."
    }
}

function Remove-SidekickDirectory {
    Write-Info "Removing %LOCALAPPDATA%\sidekick\ ..."
    if (Test-Path $SidekickHome) {
        try {
            Remove-Item -Recurse -Force $SidekickHome -ErrorAction Stop
            Write-Success "Removed $SidekickHome"
        } catch {
            Write-Err "Could not fully remove $SidekickHome : $_"
            Write-Info "Close any programs using files in $SidekickHome (terminals, editors, Sidekick processes) and try again."
            $script:ExitCode = 1
        }
    } else {
        Write-Info "Directory not found — nothing to remove."
    }
}

function Remove-DesktopShortcut {
    Write-Info "Removing desktop shortcut..."
    if (Test-Path $ShortcutPath) {
        try {
            Remove-Item -Force $ShortcutPath -ErrorAction Stop
            Write-Success "Removed desktop shortcut: $ShortcutPath"
        } catch {
            Write-Err "Could not remove desktop shortcut: $_"
            $script:ExitCode = 1
        }
    } else {
        Write-Warn "Desktop shortcut not found at $ShortcutPath"
        $script:ExitCode = 1
    }
}

function Remove-EnvVars {
    Write-Info "Removing SIDEKICK_HOME user environment variable..."
    try {
        $currentHome = [Environment]::GetEnvironmentVariable("SIDEKICK_HOME", "User")
        if ($currentHome) {
            [Environment]::SetEnvironmentVariable("SIDEKICK_HOME", $null, "User")
            Write-Success "Removed SIDEKICK_HOME"
        } else {
            Write-Info "SIDEKICK_HOME not set — nothing to remove."
        }
    } catch {
        Write-Err "Could not remove SIDEKICK_HOME: $_"
        $script:ExitCode = 1
    }

    Write-Info "Removing SIDEKICK_GIT_BASH_PATH user environment variable..."
    try {
        $currentGitBash = [Environment]::GetEnvironmentVariable("SIDEKICK_GIT_BASH_PATH", "User")
        if ($currentGitBash) {
            [Environment]::SetEnvironmentVariable("SIDEKICK_GIT_BASH_PATH", $null, "User")
            Write-Success "Removed SIDEKICK_GIT_BASH_PATH"
        } else {
            Write-Info "SIDEKICK_GIT_BASH_PATH not set — nothing to remove."
        }
    } catch {
        Write-Err "Could not remove SIDEKICK_GIT_BASH_PATH: $_"
        $script:ExitCode = 1
    }
}

function Remove-PathEntries {
    Write-Info "Cleaning PATH entries pointing to %LOCALAPPDATA%\sidekick\git\ ..."
    try {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if (-not $userPath) {
            Write-Info "User PATH is empty — nothing to clean."
            return
        }

        $entries = $userPath -split ";"
        $filtered = $entries | Where-Object {
            $_ -notlike "*$SidekickHome\git*"
        }
        $newPath = $filtered -join ";"

        if ($newPath -ne $userPath) {
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
            Write-Success "Removed Sidekick git PATH entries"
        } else {
            Write-Info "No Sidekick git PATH entries found."
        }
    } catch {
        Write-Err "Could not clean PATH: $_"
        $script:ExitCode = 1
    }
}

function Remove-UserData {
    if (-not $RemoveUserData) {
        Write-Info "Keeping ~/.sidekick configuration directory (-RemoveUserData not specified)."
        Write-Info "  To remove config data later: .\uninstall.ps1 -RemoveUserData"
        return
    }

    Write-Host ""
    Write-Warn "⚠  REMOVE USER DATA — IRREVERSIBLE  ⚠"
    Write-Host ""
    Write-Warn "You requested -RemoveUserData. This will PERMANENTLY DELETE:"
    Write-Host "  $SidekickConfigDir" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "This includes all sessions, config files, API keys, and settings." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Press Ctrl+C to abort." -ForegroundColor Yellow
    Write-Host ""

    # 5-second countdown
    for ($i = 5; $i -ge 1; $i--) {
        Write-Host "  Removing in $i seconds... " -NoNewline -ForegroundColor Red
        if ($i -gt 1) {
            Write-Host "(Ctrl+C to cancel)" -ForegroundColor DarkGray
        } else {
            Write-Host "(last chance!)" -ForegroundColor DarkGray
        }
        Start-Sleep -Seconds 1
    }

    Write-Host ""

    if (Test-Path $SidekickConfigDir) {
        try {
            Remove-Item -Recurse -Force $SidekickConfigDir -ErrorAction Stop
            Write-Success "Removed $SidekickConfigDir"
        } catch {
            Write-Err "Could not remove $SidekickConfigDir : $_"
            Write-Info "Close any programs using files in $SidekickConfigDir and try again."
            $script:ExitCode = 1
        }
    } else {
        Write-Info "~/.sidekick not found — nothing to remove."
    }
}

# ============================================================================
# Main
# ============================================================================

function Main {
    Write-Banner

    Test-AdminRights

    Write-Host ""
    Write-Info "Starting Sidekick uninstall..."
    Write-Host ""

    Remove-SidekickDirectory
    Remove-DesktopShortcut
    Remove-EnvVars
    Remove-PathEntries
    Remove-UserData

    Write-Host ""
    Write-Host "┌─────────────────────────────────────────────────────────┐" -ForegroundColor Green
    if ($script:ExitCode -eq 0) {
        Write-Host "│              ✓ Sidekick Uninstalled!                    │" -ForegroundColor Green
    } else {
        Write-Host "│              ⚠ Partial uninstall (see above)            │" -ForegroundColor Yellow
    }
    Write-Host "└─────────────────────────────────────────────────────────┘" -ForegroundColor Green
    Write-Host ""

    Write-Host "Sidekick has been removed." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "To reinstall, run:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  irm https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/install.ps1 | iex" -ForegroundColor Green
    Write-Host ""

    exit $script:ExitCode
}

# Initialize exit code
$script:ExitCode = 0

# Wrap in try/catch
try {
    Main
} catch {
    Write-Host ""
    Write-Err "Uninstall failed: $_"
    Write-Host ""
    Write-Info "Try running the script directly instead of via iex:"
    Write-Host "  Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/uninstall.ps1' -OutFile uninstall.ps1" -ForegroundColor Yellow
    Write-Host "  .\uninstall.ps1" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
