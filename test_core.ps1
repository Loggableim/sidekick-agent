$ErrorActionPreference = "Stop"
Write-Host "=== REPAIR TEST: re-install with v0.7.25 ==="

# Get fresh install.ps1
$content = Invoke-WebRequest -UseBasicParsing -TimeoutSec 30 "https://raw.githubusercontent.com/Loggableim/sidekick-agent/HEAD/install.ps1" | Select-Object -ExpandProperty Content

# Clean everything
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\sidekick" -ErrorAction SilentlyContinue
Start-Sleep 2

# Run installer
$env:SKIP_NODE_AUTOINSTALL = "1"
$content | iex
$ec = $LASTEXITCODE
Write-Host "=== EXIT: $ec ==="
if ($ec -eq 0) {
    # Try to start the dashboard
    Write-Host "=== Starting dashboard ==="
    $sidekickExe = "$env:LOCALAPPDATA\sidekick\sidekick-agent\.venv\Scripts\sidekick.exe"
    if (Test-Path $sidekickExe) {
        Write-Host "sidekick.exe found"
        # Start in background
        Start-Process -FilePath $sidekickExe -ArgumentList "dashboard" -NoNewWindow
        Start-Sleep 5
        # Check if it's listening on 8787
        $conn = Test-NetConnection -ComputerName 127.0.0.1 -Port 8787 -WarningAction SilentlyContinue
        Write-Host "Port 8787: $($conn.TcpTestSucceeded)"
    } else {
        Write-Host "sidekick.exe NOT found"
    }
}