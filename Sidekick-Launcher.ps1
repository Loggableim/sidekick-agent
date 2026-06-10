param(
    [ValidateSet("start", "stop", "restart", "status")]
    [string]$Command = "start",
    [int]$Port = 9119,
    [switch]$NoGateway,
    [switch]$NoBrowser,
    [switch]$NoPause,
    [switch]$ForceRestart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$HomeDir = if ($env:SIDEKICK_HOME) { $env:SIDEKICK_HOME } else { Join-Path $Root "home" }
$LogDir = Join-Path $HomeDir "logs"
$RunDir = Join-Path $HomeDir "run"
$PidFile = Join-Path $RunDir "launcher-pids.json"
$StateFile = Join-Path $RunDir "launcher-state.json"
$LauncherLog = Join-Path $LogDir "launcher.log"

function Write-Line {
    param([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::White)
    Write-Host $Message -ForegroundColor $Color
    try {
        $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Add-Content -Path $LauncherLog -Value "[$timestamp] $Message" -Encoding UTF8
    } catch {}
}

function Write-Header {
    Write-Host ""
    Write-Host "  ============================================================" -ForegroundColor DarkCyan
    Write-Host "   Sidekick Launcher" -ForegroundColor Cyan
    Write-Host "  ------------------------------------------------------------" -ForegroundColor DarkCyan
    Write-Host "   Root : $Root" -ForegroundColor DarkGray
    Write-Host "   Home : $HomeDir" -ForegroundColor DarkGray
    Write-Host "   WebUI: http://127.0.0.1:$Port" -ForegroundColor DarkGray
    Write-Host "  ============================================================" -ForegroundColor DarkCyan
    Write-Host ""
}

function Ensure-Dirs {
    foreach ($dir in @($HomeDir, $LogDir, $RunDir)) {
        if (-not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
        }
    }
}

function Resolve-RepoDir {
    $candidates = @(
        (Join-Path $Root "sidekick"),
        (Join-Path $Root "sidekick-agent"),
        $Root
    )
    foreach ($candidate in $candidates) {
        if ((Test-Path -LiteralPath (Join-Path $candidate "pyproject.toml")) -and
            (Test-Path -LiteralPath (Join-Path $candidate "sidekick_app\__main__.py"))) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Could not find a Sidekick repo under $Root. Expected .\sidekick or .\sidekick-agent."
}

function Resolve-Uv {
    $cmd = Get-Command "uv" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $Root "uv.exe"),
        (Join-Path $HomeDir "uv.exe"),
        (Join-Path $HomeDir "bin\uv.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Test-PythonRuntime {
    param([string]$PythonExe, [string]$RepoDir)
    if (-not (Test-Path -LiteralPath $PythonExe)) { return $false }
    try {
        Push-Location $RepoDir
        & $PythonExe -c "import fastapi, uvicorn, sidekick_app" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        Pop-Location
    }
}

function Ensure-PythonRuntime {
    param([string]$RepoDir)
    $venvPython = Join-Path $RepoDir ".venv\Scripts\python.exe"
    if (Test-PythonRuntime $venvPython $RepoDir) { return $venvPython }

    $uv = Resolve-Uv
    if ($uv) {
        Write-Line "Preparing local Python runtime with uv..." DarkCyan
        Push-Location $RepoDir
        try {
            if (-not (Test-Path -LiteralPath $venvPython)) {
                & $uv venv ".venv" --python 3.11
                if ($LASTEXITCODE -ne 0) {
                    Write-Line "uv could not create a Python 3.11 venv, retrying with default Python..." Yellow
                    & $uv venv ".venv"
                }
                if ($LASTEXITCODE -ne 0) { throw "uv venv failed with exit code $LASTEXITCODE" }
            }
            & $uv pip install --python "$venvPython" -e ".[web,all]"
            if ($LASTEXITCODE -ne 0) { throw "uv pip install failed with exit code $LASTEXITCODE" }
        } finally {
            Pop-Location
        }
        if (Test-PythonRuntime $venvPython $RepoDir) { return $venvPython }
    }

    $pythonCmd = Get-Command "python" -ErrorAction SilentlyContinue
    if (-not $pythonCmd) { throw "Python was not found and uv could not prepare .venv." }
    $python = $pythonCmd.Source

    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Line "Preparing local Python runtime with python -m venv..." DarkCyan
        Push-Location $RepoDir
        try {
            & $python -m venv ".venv"
            if ($LASTEXITCODE -ne 0) { throw "python -m venv failed with exit code $LASTEXITCODE" }
            & $venvPython -m pip install --upgrade pip
            & $venvPython -m pip install -e ".[web,all]"
            if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit code $LASTEXITCODE" }
        } finally {
            Pop-Location
        }
    }
    if (Test-PythonRuntime $venvPython $RepoDir) { return $venvPython }
    throw "Local Python runtime is not usable. See $LauncherLog and pip output."
}

function Set-SidekickEnv {
    param([string]$RepoDir, [string]$PythonExe)
    $env:SIDEKICK_HOME = $HomeDir
    $env:HERMES_HOME = $HomeDir
    $env:SIDEKICK_WEBUI_PORT = "$Port"
    $env:HERMES_WEBUI_PORT = "$Port"
    $env:SIDEKICK_WEBUI_STATE_DIR = Join-Path $HomeDir "webui"
    $env:HERMES_WEBUI_STATE_DIR = $env:SIDEKICK_WEBUI_STATE_DIR
    $env:SIDEKICK_WEBUI_AGENT_DIR = $RepoDir
    $env:HERMES_WEBUI_AGENT_DIR = $RepoDir
    $env:SIDEKICK_WEBUI_PYTHON = $PythonExe
    $env:HERMES_WEBUI_PYTHON = $PythonExe
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"

    $venvScripts = Split-Path -Parent $PythonExe
    $extraPath = @($venvScripts)
    foreach ($candidate in @(
        (Join-Path $HomeDir "git\cmd"),
        (Join-Path $HomeDir "git\bin"),
        (Join-Path $HomeDir "git\usr\bin"),
        (Join-Path $HomeDir "node")
    )) {
        if (Test-Path -LiteralPath $candidate) { $extraPath += $candidate }
    }
    $env:Path = (($extraPath | Select-Object -Unique) -join ";") + ";" + $env:Path
    foreach ($bash in @((Join-Path $HomeDir "git\bin\bash.exe"), (Join-Path $HomeDir "git\usr\bin\bash.exe"))) {
        if (Test-Path -LiteralPath $bash) {
            $env:SIDEKICK_GIT_BASH_PATH = $bash
            $env:HERMES_GIT_BASH_PATH = $bash
            break
        }
    }
}

function Read-Pids {
    if (-not (Test-Path -LiteralPath $PidFile)) { return @{} }
    try {
        $raw = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
        $map = @{}
        foreach ($prop in $raw.PSObject.Properties) { $map[$prop.Name] = [int]$prop.Value }
        return $map
    } catch {
        return @{}
    }
}

function Save-Pids {
    param([hashtable]$Map)
    $obj = [ordered]@{}
    foreach ($key in $Map.Keys) { $obj[$key] = $Map[$key] }
    $obj | ConvertTo-Json | Set-Content -Path $PidFile -Encoding UTF8
}

function Test-ProcessAlive {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    try { return [bool](Get-Process -Id $ProcessId -ErrorAction Stop) } catch { return $false }
}

function Get-PortPids {
    param([int]$TargetPort)
    try {
        return @(Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        return @()
    }
}

function Stop-PortIfUnhealthy {
    param([int]$TargetPort)
    if (Test-DashboardHealth $TargetPort) { return }
    foreach ($portProcId in Get-PortPids $TargetPort) {
        if ($portProcId -gt 0) {
            Write-Line "Stopping stale process on port $TargetPort (PID $portProcId)..." Yellow
            Stop-Process -Id $portProcId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Test-DashboardHealth {
    param([int]$TargetPort)
    try {
        $res = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$TargetPort/health" -TimeoutSec 2
        if ($res.StatusCode -ne 200) { return $false }
        try {
            $json = $res.Content | ConvertFrom-Json
            return ($json.ok -eq $true -and $json.service -eq "sidekick-dashboard")
        } catch {
            return $true
        }
    } catch {
        return $false
    }
}

function Wait-DashboardHealth {
    param([int]$TargetPort, [int]$Seconds = 120)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-DashboardHealth $TargetPort) { return $true }
        Start-Sleep -Milliseconds 1000
    }
    return $false
}

function Start-Child {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [string]$Stdout,
        [string]$Stderr
    )
    $pids = Read-Pids
    if ($pids.ContainsKey($Name) -and (Test-ProcessAlive $pids[$Name])) {
        Write-Line "$Name already running (PID $($pids[$Name]))." DarkGray
        return $pids[$Name]
    }
    $params = @{
        FilePath = $FilePath
        ArgumentList = $Arguments
        WorkingDirectory = $WorkingDirectory
        RedirectStandardOutput = $Stdout
        RedirectStandardError = $Stderr
        WindowStyle = "Hidden"
        PassThru = $true
    }
    $proc = Start-Process @params
    $pids[$Name] = $proc.Id
    Save-Pids $pids
    Write-Line "$Name started (PID $($proc.Id))." Green
    return $proc.Id
}

function Stop-All {
    Ensure-Dirs
    Write-Header
    Write-Line "Stopping Sidekick components..." White
    $pids = Read-Pids
    foreach ($name in @("dashboard", "gateway")) {
        if ($pids.ContainsKey($name)) {
            $procId = $pids[$name]
            if (Test-ProcessAlive $procId) {
                Write-Line "Stopping $name (PID $procId)..." Yellow
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            }
            $pids.Remove($name)
        }
    }
    Save-Pids $pids
    Write-Line "Stopped tracked Sidekick components." Green
}

function Show-Status {
    Ensure-Dirs
    Write-Header
    $pids = Read-Pids
    foreach ($name in @("dashboard", "gateway")) {
        $procId = if ($pids.ContainsKey($name)) { $pids[$name] } else { 0 }
        $state = if ($procId -and (Test-ProcessAlive $procId)) { "running PID $procId" } else { "stopped" }
        Write-Line ("{0,-10} {1}" -f $name, $state) ($(if ($state -like "running*") { "Green" } else { "DarkGray" }))
    }
    if (Test-DashboardHealth $Port) {
        Write-Line "health     OK http://127.0.0.1:$Port/health" Green
    } else {
        Write-Line "health     not ready on http://127.0.0.1:$Port/health" Yellow
    }
    Write-Line "logs       $LogDir" DarkGray
}

function Start-All {
    Ensure-Dirs
    Write-Header
    $repoDir = Resolve-RepoDir
    $pythonExe = Ensure-PythonRuntime $repoDir
    Set-SidekickEnv $repoDir $pythonExe

    Write-Line "Repo: $repoDir" DarkGray
    Write-Line "Python: $pythonExe" DarkGray

    if ($ForceRestart) { Stop-All }
    Stop-PortIfUnhealthy $Port

    if (-not $NoGateway) {
        Start-Child `
            -Name "gateway" `
            -FilePath $pythonExe `
            -Arguments @("-m", "sidekick_app", "gateway", "run", "--replace", "--quiet") `
            -WorkingDirectory $repoDir `
            -Stdout (Join-Path $LogDir "gateway.out.log") `
            -Stderr (Join-Path $LogDir "gateway.err.log") | Out-Null
    } else {
        Write-Line "Gateway skipped by -NoGateway." Yellow
    }

    if (Test-DashboardHealth $Port) {
        Write-Line "Dashboard already healthy on port $Port." Green
    } else {
        Start-Child `
            -Name "dashboard" `
            -FilePath $pythonExe `
            -Arguments @("-m", "sidekick_app", "dashboard", "--host", "127.0.0.1", "--port", "$Port", "--no-open", "--skip-build") `
            -WorkingDirectory $repoDir `
            -Stdout (Join-Path $LogDir "dashboard.out.log") `
            -Stderr (Join-Path $LogDir "dashboard.err.log") | Out-Null
    }

    Write-Line "Waiting for dashboard health check..." DarkCyan
    $ready = Wait-DashboardHealth $Port 150
    $status = [ordered]@{
        time = (Get-Date).ToString("s")
        port = $Port
        ready = $ready
        url = "http://127.0.0.1:$Port"
        health = "http://127.0.0.1:$Port/health"
    }
    $status | ConvertTo-Json | Set-Content -Path $StateFile -Encoding UTF8

    if (-not $ready) {
        Write-Line "Dashboard did not become healthy. Check $LogDir\dashboard.err.log" Red
        throw "Dashboard health check failed on port $Port."
    }

    $url = "http://127.0.0.1:$Port"
    Write-Line "Dashboard ready: $url" Green
    if (-not $NoBrowser) {
        Write-Line "Opening browser after successful health check..." DarkCyan
        Start-Process $url
    }
    Write-Line "Launcher log: $LauncherLog" DarkGray
    Write-Line "Dashboard log: $(Join-Path $LogDir 'dashboard.out.log')" DarkGray
}

try {
    switch ($Command) {
        "start" { Start-All }
        "stop" { Stop-All }
        "restart" { Stop-All; Start-Sleep -Seconds 1; Start-All }
        "status" { Show-Status }
    }
    if (-not $NoPause -and $Command -eq "start") {
        Write-Host ""
        Write-Host "Press Enter to close this launcher window. Sidekick keeps running." -ForegroundColor Yellow
        Write-Host "Use: launcher.bat stop" -ForegroundColor DarkGray
        $null = Read-Host
    }
    exit 0
} catch {
    Write-Line "ERROR: $($_.Exception.Message)" Red
    if (-not $NoPause) {
        Write-Host ""
        Read-Host "Press Enter to close"
    }
    exit 1
}
