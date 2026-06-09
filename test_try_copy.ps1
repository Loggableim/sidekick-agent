$ErrorActionPreference = "Continue"
$src = "C:\Users\vboxuser\hosts_with_sidekick.txt"
$dst = "C:\Windows\System32\drivers\etc\hosts"

cmd /c copy /Y $src $dst 2>&1 | Out-String
Write-Host "Exit: $LASTEXITCODE"

$content = Get-Content $dst -Raw
if ($content -match "127\.0\.0\.1\s+sidekick") {
    Write-Host "HOSTS WRITE SUCCESS"
} else {
    Write-Host "HOSTS WRITE FAILED"
}