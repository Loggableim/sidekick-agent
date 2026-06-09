<#
.SYNOPSIS
    Sidekick Launcher — Startet Sidekick mit WebUI, Gateway, Dispatcher & Worker.
.DESCRIPTION
    Zentraler Starter für die gesamte Sidekick-Umgebung:
    - Gateway (Messaging + WebUI auf Port 9119)
    - WebUI Dashboard
    - Dispatcher & Worker (Kanban)
    - Lokales LLM (Ollama)
.PARAMETER Command
    start     (Default) Startet alle Komponenten
    stop      Stoppt alle Komponenten
    restart   Restartet alle Komponenten
    status    Zeigt Status aller Komponenten
    webui     Nur WebUI starten
    gateway   Nur Gateway starten
    worker    Nur Worker starten
.PARAMETER Profile
    Sidekick-Profil (default: webui)
.PARAMETER OllamaPort
    Port für lokales LLM (default: 11434)
.EXAMPLE
    .\sidekick.ps1 start
    .\sidekick.ps1 status
    .\sidekick.ps1 stop
#>

param(
    [ValidateSet("start", "stop", "restart", "status", "webui", "gateway", "worker", "help")]
    [string]$Command = "start",

    [string]$Profile = "webui",

    [int]$OllamaPort = 11434
)

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = "python"
$LogDir = Join-Path $RepoRoot "logs"
$PidDir = Join-Path $RepoRoot ".pids"

# Stelle sicher, dass Verzeichnisse existieren
foreach ($dir in @($LogDir, $PidDir)) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}

function Write-Status {
    param([string]$Component, [string]$Status, [string]$Color = "White")
    $symbol = switch ($Status) {
        "RUNNING" { "▶" }
        "STOPPED" { "■" }
        "ERROR"   { "⚠" }
        "WARN"    { "⚡" }
        default   { "?" }
    }
    Write-Host "  $symbol $Component" -NoNewline
    Write-Host "  $Status" -ForegroundColor $Color
}

function Get-PidFile {
    param([string]$Name)
    $path = Join-Path $PidDir "$Name.pid"
    if (Test-Path $path) {
        try { return [int](Get-Content $path -Raw).Trim() } catch { return $null }
    }
    return $null
}

function Write-PidFile {
    param([string]$Name, [int]$Pid)
    $path = Join-Path $PidDir "$Name.pid"
    Set-Content -Path $path -Value $Pid
}

function Remove-PidFile {
    param([string]$Name)
    $path = Join-Path $PidDir "$Name.pid"
    if (Test-Path $path) { Remove-Item $path -Force }
}

function Is-ProcessAlive {
    param([int]$Pid)
    if (-not $Pid) { return $false }
    try { return (Get-Process -Id $Pid -ErrorAction Stop) -ne $null } catch { return $false }
}

function Get-ComponentStatus {
    param([string]$Name)
    $pid = Get-PidFile $Name
    if ($pid -and (Is-ProcessAlive $pid)) { return "RUNNING", $pid }
    return "STOPPED", $null
}

function Start-ProcessLogged {
    param(
        [string]$Name,
        [string]$Command,
        [string]$Arguments,
        [string]$WorkDir = $RepoRoot
    )
    $logFile = Join-Path $LogDir "${Name}.log"
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Command
    $startInfo.Arguments = $Arguments
    $startInfo.WorkingDirectory = $WorkDir
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.CreateNoWindow = $true
    
    # Umgebungsvariablen setzen
    $startInfo.EnvironmentVariables["SIDEKICK_PROFILE"] = $Profile
    $startInfo.EnvironmentVariables["PYTHONUNBUFFERED"] = "1"
    
    try {
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        $process.Start() | Out-Null
        
        # Output asynchron lesen
        $outTask = $process.StandardOutput.ReadToEndAsync()
        $errTask = $process.StandardError.ReadToEndAsync()
        
        Write-PidFile $Name $process.Id
        Write-Host "    PID $($process.Id) | Log: $logFile" -ForegroundColor DarkGray
        
        return $process
    } catch {
        Write-Host "    FEHLER: $_" -ForegroundColor Red
        return $null
    }
}

function Stop-Component {
    param([string]$Name, [string]$Signal = "CTRL_C")
    $pid = Get-PidFile $Name
    if ($pid -and (Is-ProcessAlive $pid)) {
        Write-Host "    Stoppe $Name (PID $pid)..." -NoNewline
        try {
            Stop-Process -Id $pid -Force -ErrorAction Stop
            Write-Host " OK" -ForegroundColor Green
        } catch {
            Write-Host " Fehler: $_" -ForegroundColor Red
        }
    } else {
        Write-Host "    $Name läuft nicht" -ForegroundColor DarkGray
    }
    Remove-PidFile $Name
}

# ====== KOMPONENTEN ======

function Start-Gateway {
    Write-Host "`n[Gateway] WebUI + Messaging" -ForegroundColor Cyan
    $pid, $null = Get-ComponentStatus "gateway"
    if ($pid) { Write-Status "Gateway" "bereits gestartet (PID $pid)" "Yellow"; return }
    
    $proc = Start-ProcessLogged -Name "gateway" -Command $PythonExe -Arguments "-m sidekick_app gateway run --verbose"
    if ($proc) {
        Write-Status "Gateway" "startet (Port 9119)" "Green"
    }
}

function Start-WebUI {
    Write-Host "`n[WebUI] Dashboard" -ForegroundColor Cyan
    $pid, $null = Get-ComponentStatus "webui"
    if ($pid) { Write-Status "WebUI" "bereits gestartet (PID $pid)" "Yellow"; return }
    
    # WebUI wird vom Gateway mitgestartet — check ob Gateway läuft
    $gpid, $null = Get-ComponentStatus "gateway"
    if (-not $gpid) {
        Write-Status "WebUI" "Gateway nicht gestartet — WebUI separat via Gateway" "Yellow"
        Start-Gateway
    } else {
        Write-Status "WebUI" "läuft via Gateway (http://127.0.0.1:9119)" "Green"
    }
}

function Start-Dispatcher {
    Write-Host "`n[Dispatcher] Kanban-Aufgaben" -ForegroundColor Cyan
    $pid, $null = Get-ComponentStatus "dispatcher"
    if ($pid) { Write-Status "Dispatcher" "bereits gestartet (PID $pid)" "Yellow"; return }
    
    $proc = Start-ProcessLogged -Name "dispatcher" -Command $PythonExe -Arguments "-m sidekick_app kanban dispatch"
    if ($proc) {
        Write-Status "Dispatcher" "startet" "Green"
    }
}

function Start-Worker {
    Write-Host "`n[Worker] Kanban-Ausführung" -ForegroundColor Cyan
    $pid, $null = Get-ComponentStatus "worker"
    if ($pid) { Write-Status "Worker" "bereits gestartet (PID $pid)" "Yellow"; return }
    
    $proc = Start-ProcessLogged -Name "worker" -Command $PythonExe -Arguments "-m sidekick_app kanban worker"
    if ($proc) {
        Write-Status "Worker" "startet" "Green"
    }
}

function Start-Ollama {
    Write-Host "`n[Local LLM] Ollama" -ForegroundColor Cyan
    # Prüfe ob ollama installiert ist
    $ollamaPath = Get-Command "ollama" -ErrorAction SilentlyContinue
    if (-not $ollamaPath) {
        Write-Status "Ollama" "nicht installiert (überspringe)" "DarkGray"
        Write-Host "    Install: https://ollama.com/download/windows" -ForegroundColor DarkGray
        return
    }
    
    $pid, $null = Get-ComponentStatus "ollama"
    if ($pid) { Write-Status "Ollama" "bereits gestartet (PID $pid)" "Yellow"; return }
    
    $proc = Start-ProcessLogged -Name "ollama" -Command "ollama" -Arguments "serve"
    if ($proc) {
        Write-Status "Ollama" "startet (Port $OllamaPort)" "Green"
    }
}

# ====== HAUPTLOGIK ======

function Show-Header {
    Clear-Host
    Write-Host "╔═══════════════════════════════════════════╗" -ForegroundColor DarkCyan
    Write-Host "║       ⚡ Sidekick Agent Launcher ⚡       ║" -ForegroundColor Cyan
    Write-Host "╚═══════════════════════════════════════════╝" -ForegroundColor DarkCyan
    Write-Host "  Repo: $RepoRoot" -ForegroundColor DarkGray
    Write-Host "  Profil: $Profile" -ForegroundColor DarkGray
    Write-Host "`n"
}

function Show-Status {
    Show-Header
    Write-Host "Status aller Komponenten:" -ForegroundColor White
    
    $components = @(
        @{Name="Gateway"; Key="gateway"},
        @{Name="Dispatcer"; Key="dispatcher"},
        @{Name="Worker"; Key="worker"},
        @{Name="Ollama"; Key="ollama"}
    )
    
    foreach ($c in $components) {
        $status, $pid = Get-ComponentStatus $c.Key
        $color = if ($status -eq "RUNNING") { "Green" } else { "Red" }
        Write-Status $c.Name $status $color
    }
    
    # WebUI Port-Check
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.ConnectAsync("127.0.0.1", 9119).Wait(500) | Out-Null
        if ($tcp.Connected) {
            Write-Status "WebUI (Port 9119)" "ERREICHBAR" "Green"
            $tcp.Close()
            Write-Host "    http://127.0.0.1:9119" -ForegroundColor DarkGray
        } else {
            Write-Status "WebUI (Port 9119)" "NICHT ERREICHBAR" "Red"
        }
    } catch {
        Write-Status "WebUI (Port 9119)" "NICHT ERREICHBAR" "Red"
    }
}

function Start-All {
    Show-Header
    Write-Host "Starte Sidekick Umgebung..." -ForegroundColor White
    
    Start-Ollama
    Start-Gateway
    Start-WebUI
    Start-Dispatcher
    Start-Worker
    
    Write-Host "`n"
    Write-Host "✅ Start-Befehle gesendet" -ForegroundColor Green
    Write-Host "  Status prüfen: .\sidekick.ps1 status" -ForegroundColor DarkGray
    Write-Host "  Stoppen:       .\sidekick.ps1 stop" -ForegroundColor DarkGray
    Write-Host "  WebUI:         http://127.0.0.1:9119" -ForegroundColor Cyan
}

function Stop-All {
    Show-Header
    Write-Host "Stoppe Sidekick Umgebung..." -ForegroundColor White
    
    foreach ($comp in @("worker", "dispatcher", "gateway", "ollama")) {
        Stop-Component $comp
    }
    
    Write-Host "`n✅ Alle Komponenten gestoppt" -ForegroundColor Green
}

# ====== MAIN ======

switch ($Command) {
    "start"   { Start-All }
    "stop"    { Stop-All }
    "restart" { Stop-All; Start-Sleep 2; Start-All }
    "status"  { Show-Status }
    "webui"   { Show-Header; Start-WebUI; Show-Status }
    "gateway" { Show-Header; Start-Gateway; Show-Status }
    "worker"  { Show-Header; Start-Worker; Show-Status }
    "help"    {
        Get-Help $MyInvocation.MyCommand.Path -Detailed
    }
    default   { Show-Status }
}
