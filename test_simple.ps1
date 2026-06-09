# Simple, robust test - no git update
$ErrorActionPreference = "Stop"

# Hosts entry
$hostsPath = "C:\Windows\System32\drivers\etc\hosts"
$hostsContent = Get-Content $hostsPath -Raw -ErrorAction SilentlyContinue
Write-Host "Hosts has sidekick: $($hostsContent -match '127\.0\.0\.1\s+sidekick')"

# Start fresh dashboard (it's already running from previous test, just kill and restart)
Get-Process sidekick -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2

# Start dashboard with new web_server.py
$exe = "C:\Users\vboxuser\AppData\Local\sidekick\sidekick-agent\.venv\Scripts\sidekick.exe"
if (Test-Path $exe) {
    Start-Process -FilePath $exe -ArgumentList "dashboard" -NoNewWindow
    Start-Sleep 5
    $t1 = Test-NetConnection -ComputerName "127.0.0.1" -Port 8787 -WarningAction SilentlyContinue
    Write-Host "127.0.0.1:8787 -> $($t1.TcpTestSucceeded)"
}