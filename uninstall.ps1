# ============================================================================
# Sidekick Uninstaller for Windows
# ============================================================================
# Removes Sidekick from %LOCALAPPDATA%\sidekick and user environment.
# Safe default: keeps ~/.sidekick config, sessions, and keys.
#
# Usage:
#   .\uninstall.ps1
#   .\uninstall.ps1 -RemoveUserData
# ============================================================================

param(
    [switch]$RemoveUserData
)

$ErrorActionPreference = "Stop"

# Exit codes:
#   0  Success
#   1  Partial uninstall; one or more cleanup steps failed

$SidekickHome = "$env:LOCALAPPDATA\sidekick"
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopPath "Sidekick.lnk"
$UserHome = $env:USERPROFILE
$SidekickConfigDir = Join-Path $UserHome ".sidekick"

function Write-Banner {
    Write-Host ""
    Write-Host "  ============================================================" -ForegroundColor DarkCyan
    Write-Host "   Sidekick uninstall" -ForegroundColor Cyan
    Write-Host "  ------------------------------------------------------------" -ForegroundColor DarkCyan
    Write-Host "   Removes the local Sidekick install from this Windows user." -ForegroundColor Gray
    Write-Host "  ============================================================" -ForegroundColor DarkCyan
    Write-Host ""
}

function Write-Info {
    param([string]$Message)
    Write-Host "  >  $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "  OK $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  !! $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "  XX $Message" -ForegroundColor Red
}

function Test-AdminRights {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
    if ($isAdmin) {
        Write-Warn "Administrator rights are not required for this uninstall."
        Write-Info "Sidekick installs under %LOCALAPPDATA% for the current user."
    }
}

function Stop-SidekickProcesses {
    Write-Info "Stopping Sidekick processes from the install directory..."

    if (-not (Test-Path -LiteralPath $SidekickHome)) {
        Write-Info "Install directory not found; no running install-local processes expected."
        return
    }

    try {
        $processes = Get-Process -ErrorAction SilentlyContinue | Where-Object {
            try {
                $_.Path -and ($_.Path -like "$SidekickHome*")
            } catch {
                $false
            }
        }

        if (-not $processes) {
            Write-Info "No Sidekick processes found."
            return
        }

        foreach ($proc in $processes) {
            try {
                Write-Info "Stopping $($proc.ProcessName) (pid $($proc.Id))"
                Stop-Process -Id $proc.Id -Force -ErrorAction Stop
            } catch {
                Write-Err "Could not stop process $($proc.Id): $_"
                $script:ExitCode = 1
            }
        }
    } catch {
        Write-Err "Could not inspect running processes: $_"
        $script:ExitCode = 1
    }
}

function Remove-SidekickDirectory {
    Write-Info "Removing %LOCALAPPDATA%\sidekick..."
    if (Test-Path -LiteralPath $SidekickHome) {
        try {
            Remove-Item -LiteralPath $SidekickHome -Recurse -Force -ErrorAction Stop
            Write-Success "Removed $SidekickHome"
        } catch {
            Write-Err "Could not fully remove ${SidekickHome}: $($_.Exception.Message)"
            Write-Info "Close terminals, editors, browser windows, and Sidekick processes, then retry."
            $script:ExitCode = 1
        }
    } else {
        Write-Info "Directory not found; nothing to remove."
    }
}

function Remove-DesktopShortcut {
    Write-Info "Removing desktop shortcut..."
    if (Test-Path -LiteralPath $ShortcutPath) {
        try {
            Remove-Item -LiteralPath $ShortcutPath -Force -ErrorAction Stop
            Write-Success "Removed $ShortcutPath"
        } catch {
            Write-Err "Could not remove desktop shortcut: $_"
            $script:ExitCode = 1
        }
    } else {
        Write-Info "Desktop shortcut not found."
    }
}

function Remove-EnvVars {
    foreach ($name in @("SIDEKICK_HOME", "SIDEKICK_GIT_BASH_PATH")) {
        Write-Info "Removing $name user environment variable..."
        try {
            $current = [Environment]::GetEnvironmentVariable($name, "User")
            if ($current) {
                [Environment]::SetEnvironmentVariable($name, $null, "User")
                Write-Success "Removed $name"
            } else {
                Write-Info "$name is not set."
            }
        } catch {
            Write-Err "Could not remove ${name}: $($_.Exception.Message)"
            $script:ExitCode = 1
        }
    }
}

function Remove-PathEntries {
    Write-Info "Cleaning Sidekick entries from the user PATH..."
    try {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if (-not $userPath) {
            Write-Info "User PATH is empty."
            return
        }

        $entries = $userPath -split ";"
        $filtered = $entries | Where-Object {
            $_ -and
            $_ -notlike "$SidekickHome*" -and
            $_ -notlike "*\sidekick\git*" -and
            $_ -notlike "*\sidekick\sidekick-agent*"
        }
        $newPath = ($filtered -join ";").Trim(";")

        if ($newPath -ne $userPath) {
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
            Write-Success "Removed Sidekick PATH entries"
        } else {
            Write-Info "No Sidekick PATH entries found."
        }
    } catch {
        Write-Err "Could not clean PATH: $_"
        $script:ExitCode = 1
    }
}

function Remove-UserData {
    if (-not $RemoveUserData) {
        Write-Info "Keeping ~/.sidekick config, sessions, and keys."
        Write-Info "Run .\uninstall.ps1 -RemoveUserData to remove user data too."
        return
    }

    Write-Host ""
    Write-Warn "Remove user data requested. This is irreversible."
    Write-Host "  $SidekickConfigDir" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Press Ctrl+C now to abort." -ForegroundColor Yellow
    Write-Host ""

    for ($i = 5; $i -ge 1; $i--) {
        Write-Host "  Removing in $i seconds..." -ForegroundColor Red
        Start-Sleep -Seconds 1
    }

    if (Test-Path -LiteralPath $SidekickConfigDir) {
        try {
            Remove-Item -LiteralPath $SidekickConfigDir -Recurse -Force -ErrorAction Stop
            Write-Success "Removed $SidekickConfigDir"
        } catch {
            Write-Err "Could not remove ${SidekickConfigDir}: $($_.Exception.Message)"
            $script:ExitCode = 1
        }
    } else {
        Write-Info "~/.sidekick not found."
    }
}

function Main {
    Write-Banner
    Test-AdminRights

    Write-Info "Starting Sidekick uninstall..."
    Write-Host ""

    Stop-SidekickProcesses
    Remove-SidekickDirectory
    Remove-DesktopShortcut
    Remove-EnvVars
    Remove-PathEntries
    Remove-UserData

    Write-Host ""
    if ($script:ExitCode -eq 0) {
        Write-Success "Sidekick has been removed."
    } else {
        Write-Warn "Partial uninstall completed. Review the messages above."
    }

    Write-Host ""
    Write-Host "  Reinstall:" -ForegroundColor Cyan
    Write-Host "  irm https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/install.ps1 | iex" -ForegroundColor Green
    Write-Host ""

    exit $script:ExitCode
}

$script:ExitCode = 0

try {
    Main
} catch {
    Write-Host ""
    Write-Err "Uninstall failed: $_"
    Write-Host ""
    Write-Info "Try running the script directly:"
    Write-Host "  Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/uninstall.ps1' -OutFile uninstall.ps1" -ForegroundColor Yellow
    Write-Host "  .\uninstall.ps1" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
