$ErrorActionPreference = "Stop"

# Update repo + reinstall
Set-Location "C:\Users\vboxuser\AppData\Local\sidekick\sidekick-agent"
& git fetch origin master 2>&1 | Out-Null
& git reset --hard origin/master 2>&1 | Out-Null
Write-Host "Repo: $(git rev-parse --short HEAD)"

# Reinstall web extras to get new web_server.py
$env:VIRTUAL_ENV = "C:\Users\vboxuser\AppData\Local\sidekick\sidekick-agent\.venv"
& "C:\Users\vboxuser\.local\bin\uv.exe" pip install --python "C:\Users\vboxuser\AppData\Local\sidekick\sidekick-agent\.venv\Scripts\python.exe" -e ".[web]" 2>&1 | Out-Null

# Kill old dashboard
Get-Process sidekick -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2

# Now run the host-write-and-launch test (mimics what installer does)
$hostsPath = "C:\Windows\System32\drivers\etc\hosts"
$hostsEntry = "127.0.0.1`tsidekick"
$hostsContent = Get-Content $hostsPath -Raw -ErrorAction SilentlyContinue
$hostsOk = $false
if ($hostsContent -match "127\.0\.0\.1\s+sidekick") {
    $hostsOk = $true
} else {
    $tmpHosts = [System.IO.Path]::GetTempFileName()
    try {
        Copy-Item $hostsPath $tmpHosts -Force -ErrorAction Stop
        $newContent = (Get-Content $tmpHosts -Raw) + "`r`n$hostsEntry  # sidekick-installer`r`n"
        Set-Content -Path $tmpHosts -Value $newContent -ErrorAction Stop
        $r = cmd /c copy /Y "$tmpHosts" "$hostsPath" 2>&1
        if ($LASTEXITCODE -eq 0 -and (Get-Content $hostsPath -Raw) -match "127\.0\.0\.1\s+sidekick") {
            $hostsOk = $true
        }
    } catch {
        Write-Host "EXC: $_"
    } finally {
        Remove-Item $tmpHosts -ErrorAction SilentlyContinue
    }
}
Write-Host "Hosts write OK: $hostsOk"

# Start dashboard
Start-Process -FilePath "C:\Users\vboxuser\AppData\Local\sidekick\sidekick-agent\.venv\Scripts\sidekick.exe" -ArgumentList "dashboard" -NoNewWindow
Start-Sleep 5

# Test both URLs
$t1 = Test-NetConnection -ComputerName "127.0.0.1" -Port 8787 -WarningAction SilentlyContinue
$t2 = Test-NetConnection -ComputerName "sidekick" -Port 8787 -WarningAction SilentlyContinue
Write-Host "127.0.0.1:8787 -> $($t1.TcpTestSucceeded)"
Write-Host "sidekick:8787 -> $($t2.TcpTestSucceeded)"