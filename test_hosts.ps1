# No takeown, no admin needed: copy hosts, modify, copy back
$ErrorActionPreference = "Stop"
$hostsPath = "C:\Windows\System32\drivers\etc\hosts"
$tmpHosts = "C:\Users\vboxuser\hosts.tmp"

# Check current contents
$content = Get-Content $hostsPath -Raw
if ($content -match "127\.0\.0\.1\s+sidekick") {
    Write-Host "Hosts entry already present"
} else {
    # Try direct write first
    try {
        Add-Content -Path $hostsPath -Value "`r`n127.0.0.1`tsidekick  # sidekick-installer" -ErrorAction Stop
        Write-Host "DIRECT WRITE OK"
    } catch {
        # Fallback: use cmd /c with runas maybe? Or just write to USERPROFILE
        Write-Host "Direct write failed: $($_.Exception.Message)"
        # Just write to userprofile and tell user
        Add-Content -Path "C:\Users\vboxuser\hosts_recommended.txt" -Value "127.0.0.1`tsidekick"
        Write-Host "Wrote recommended entry to C:\Users\vboxuser\hosts_recommended.txt"
        Write-Host "User must manually add '127.0.0.1 sidekick' to hosts file (admin required)"
    }
}