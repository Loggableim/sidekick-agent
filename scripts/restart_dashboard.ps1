# Detached dashboard restart after a code change.
#
# The dashboard process serves the live WebUI (and the agent sessions running
# inside it), so it cannot restart itself mid-turn. This script waits until no
# agent turn is running, then kills the old dashboard, starts a fresh one, and
# health-checks it. Outcome is appended to the log file.
#
# The wait is a POLL LOOP, not a fixed sleep. An earlier version slept a fixed
# 90 s and then killed unconditionally, which murdered every turn that ran
# longer than 90 s (long multi-tool turns are the normal case, not the
# exception; verified 2026-09-18: two WebUI sessions died mid-turn). The loop
# polls /health for the live worker counter (active_runs) and only proceeds
# after several consecutive idle polls.
#
# Idle detection, in order of preference:
#   1. /health exposes active_runs (new servers) -> idle when active_runs == 0.
#   2. /health answers without counters (old servers) -> session-file scan:
#      a recently written session file with a non-null active_stream_id means
#      a turn is running.
#   3. /health does not answer but the port is listening -> treated as BUSY
#      (a blocked event loop during a tool call looks exactly like that), so
#      the script never kills a turn it cannot see.
#   4. Port not listening -> the server is already down; proceed to start.
#
# Launched detached by the agent; safe to kill before it proceeds.
#
# Usage:
#   powershell -File restart_dashboard.ps1
#   powershell -File restart_dashboard.ps1 -Force                # skip the idle wait
#   powershell -File restart_dashboard.ps1 -MaxWaitSeconds 600
#   powershell -File restart_dashboard.ps1 -DryRun               # wait, then stop

[CmdletBinding()]
param(
    [int]$Port = 9119,
    [int]$MaxWaitSeconds = 1800,
    [int]$PollSeconds = 5,
    [int]$IdleConfirmPolls = 3,
    [int]$StaleWindowSeconds = 600,
    [string]$SessionsDir = "C:\sidekick\home\webui\sessions",
    [string]$LogPath = "C:\sidekick\home\logs\dashboard_restart.log",
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = "SilentlyContinue"
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null

function Write-RestartLog([string]$Message) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message" | Out-File -Append $LogPath
}

function Test-DashboardPortListening {
    param([int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return [bool]$conn
}

function Get-DashboardHealth {
    # Returns the parsed /health payload, or $null when the server does not
    # answer (busy event loop, hung process, or no server at all).
    param([int]$Port)
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($r.StatusCode -eq 200) { return ($r.Content | ConvertFrom-Json) }
    } catch {}
    return $null
}

function Test-SessionFilesBusy {
    # Fallback for servers that predate the /health counters: a session file
    # with a non-null active_stream_id that was written recently means a turn
    # is (or was until seconds ago) running. Stale active_stream_id values from
    # old crashes are ignored because their files are old.
    param([string]$SessionsDir, [int]$StaleWindowSeconds)
    if (-not (Test-Path -LiteralPath $SessionsDir)) { return $false }
    $cutoff = (Get-Date).AddSeconds(-1 * $StaleWindowSeconds)
    foreach ($file in Get-ChildItem -LiteralPath $SessionsDir -Filter '*.json' -File) {
        if ($file.Name -like '*.tail.json' -or $file.Name -like '_*') { continue }
        if ($file.LastWriteTime -lt $cutoff) { continue }
        $head = Get-Content -LiteralPath $file.FullName -TotalCount 40
        if (($head -join "`n") -match '"active_stream_id"\s*:\s*"') { return $true }
    }
    return $false
}

function Get-ActiveTurnState {
    # Returns @{ Busy = <bool>; Reason = <string> }. Conservative on ambiguity:
    # an unanswered probe on a listening port counts as busy.
    param([int]$Port, [string]$SessionsDir, [int]$StaleWindowSeconds)

    $health = Get-DashboardHealth -Port $Port
    if ($null -ne $health) {
        $names = @($health.PSObject.Properties.Name)
        if ($names -contains 'active_runs') {
            $runs = [int]$health.active_runs
            $streams = 0
            if ($names -contains 'active_streams') { $streams = [int]$health.active_streams }
            return @{
                Busy = ($runs -gt 0)
                Reason = "health: active_runs=$runs active_streams=$streams"
            }
        }
        $busy = Test-SessionFilesBusy -SessionsDir $SessionsDir -StaleWindowSeconds $StaleWindowSeconds
        return @{
            Busy = $busy
            Reason = "health without counters; session-file scan says busy=$busy"
        }
    }

    if (-not (Test-DashboardPortListening -Port $Port)) {
        return @{ Busy = $false; Reason = "port $Port not listening (server already down)" }
    }
    return @{ Busy = $true; Reason = "health probe timed out (event loop busy or server hung)" }
}

function Wait-ForIdle {
    param([int]$Port, [int]$MaxWaitSeconds, [int]$PollSeconds, [int]$IdleConfirmPolls, [string]$SessionsDir, [int]$StaleWindowSeconds)

    $deadline = (Get-Date).AddSeconds($MaxWaitSeconds)
    $idleStreak = 0
    $lastReason = ''
    $polls = 0
    while ((Get-Date) -lt $deadline) {
        $polls++
        $state = Get-ActiveTurnState -Port $Port -SessionsDir $SessionsDir -StaleWindowSeconds $StaleWindowSeconds
        if ($state.Reason -ne $lastReason) {
            Write-RestartLog "wait: $($state.Reason) (poll $polls)"
            $lastReason = $state.Reason
        } elseif ($polls % 12 -eq 0) {
            Write-RestartLog "wait: still $($state.Reason) (poll $polls)"
        }
        if ($state.Busy) {
            $idleStreak = 0
        } else {
            $idleStreak++
            if ($idleStreak -ge $IdleConfirmPolls) {
                Write-RestartLog "idle confirmed after $polls polls ($IdleConfirmPolls consecutive)"
                return $true
            }
        }
        Start-Sleep -Seconds $PollSeconds
    }
    Write-RestartLog "TIMEOUT: still busy after ${MaxWaitSeconds}s ($lastReason) - NOT restarting"
    return $false
}

function Invoke-DashboardRestart {
    param([int]$Port, [int]$MaxWaitSeconds, [int]$PollSeconds, [int]$IdleConfirmPolls, [string]$SessionsDir, [int]$StaleWindowSeconds, [switch]$Force, [switch]$DryRun)

    Write-RestartLog "scheduled at $(Get-Date) (port $Port, max wait ${MaxWaitSeconds}s)"

    if ($Force) {
        Write-RestartLog "force mode: skipping idle wait"
    } else {
        $idle = Wait-ForIdle -Port $Port -MaxWaitSeconds $MaxWaitSeconds -PollSeconds $PollSeconds -IdleConfirmPolls $IdleConfirmPolls -SessionsDir $SessionsDir -StaleWindowSeconds $StaleWindowSeconds
        if (-not $idle) { exit 1 }
    }

    if ($DryRun) {
        Write-RestartLog "dry run: would restart now (kill + start skipped)"
        return
    }

    # 1. Kill the dashboard processes for THIS port (parent wrapper + re-exec
    #    child). Only real server processes match: a python interpreter
    #    invoked with `-m sidekick_app dashboard`. A shell that merely
    #    mentions the string (bash -lic "...", powershell -Command "...")
    #    must NOT match — killing those would murder unrelated tooling.
    #    Processes on other ports (e.g. a throwaway verification instance on
    #    9121) are left alone. A dashboard started without an explicit
    #    --port flag only counts when we are restarting the default port,
    #    since that is the port it would have bound.
    $killed = @()
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and
        $_.CommandLine -match '-m\s+sidekick_app\s+dashboard' -and
        (
            $_.CommandLine -match "--port[=\s]+$Port\b" -or
            ($Port -eq 9119 -and $_.CommandLine -notmatch '--port[=\s]+\d+')
        )
    } | ForEach-Object {
        $killed += $_.ProcessId
        Stop-Process -Id $_.ProcessId -Force
    }
    Write-RestartLog "killed PIDs: $($killed -join ', ')"

    # 2. Wait for the port to free
    for ($i = 0; $i -lt 15; $i++) {
        if (-not (Test-DashboardPortListening -Port $Port)) { break }
        Start-Sleep -Seconds 1
    }

    # 3. Start a fresh dashboard + health check (with one retry)
    $ok = $false
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        Start-Process -FilePath "C:\sidekick\sidekick\.venv\Scripts\python.exe" `
            -ArgumentList @("-m","sidekick_app","dashboard","--host","127.0.0.1","--port","$Port","--no-open","--skip-build") `
            -WorkingDirectory "C:\sidekick\sidekick" -WindowStyle Hidden
        Write-RestartLog "start attempt $attempt at $(Get-Date)"

        for ($i = 0; $i -lt 15; $i++) {
            Start-Sleep -Seconds 3
            try {
                $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
                if ($r.StatusCode -eq 200) { $ok = $true; break }
            } catch {}
        }
        if ($ok) { break }
        Write-RestartLog "attempt $attempt did not become healthy"
    }

    if ($ok) { Write-RestartLog "OK: dashboard healthy at $(Get-Date)" }
    else { Write-RestartLog "FAIL: dashboard did not become healthy at $(Get-Date)" }
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-DashboardRestart -Port $Port -MaxWaitSeconds $MaxWaitSeconds -PollSeconds $PollSeconds -IdleConfirmPolls $IdleConfirmPolls -SessionsDir $SessionsDir -StaleWindowSeconds $StaleWindowSeconds -Force:$Force -DryRun:$DryRun
}
