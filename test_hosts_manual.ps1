# Use the VM's Python API to run an admin command via UAC
$ErrorActionPreference = "Stop"

# Write the host entry to a file in the user profile
$userHosts = "C:\Users\vboxuser\hosts_with_sidekick.txt"
$content = Get-Content "C:\Windows\System32\drivers\etc\hosts" -Raw
$entry = "127.0.0.1`tsidekick  # sidekick-installer"
if ($content -match "127\.0\.0\.1\s+sidekick") {
    Write-Host "Hosts file already has sidekick entry"
} else {
    # Write modified hosts to a user-accessible location
    $newContent = $content + "`r`n$entry`r`n"
    Set-Content -Path $userHosts -Value $newContent -Encoding UTF8
    Write-Host "Wrote modified hosts to $userHosts"
    Write-Host ""
    Write-Host "=== To enable http://sidekick:8787 manually, run as admin: ==="
    Write-Host "  Copy-Item '$userHosts' 'C:\Windows\System32\drivers\etc\hosts' -Force"
    Write-Host "  (or open as admin: notepad C:\Windows\System32\drivers\etc\hosts and paste)"
    Write-Host ""
    Write-Host "After that, restart the dashboard and http://sidekick:8787 will work"
}

# For now, show the working URL
Write-Host ""
Write-Host "Currently working URL: http://127.0.0.1:8787"