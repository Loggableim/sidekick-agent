# Sidekick install test - skip Node.js to focus on core dependency install
$ErrorActionPreference = "Stop"

# Temporarily disable Node.js check
function global:Test-Node {
    Write-Host "→ Checking Node.js (SKIPPED for test)..."
    $script:HasNode = $false
    return $true
}

# Get fresh install.ps1 and strip its Test-Node function
$url = "https://raw.githubusercontent.com/Loggableim/sidekick-agent/master/install.ps1"
$script = Invoke-WebRequest -UseBasicParsing -Uri $url | Select-Object -ExpandProperty Content

# Replace Test-Node with our skip version
$oldNodeFunc = [regex]::Match($script, '(?s)function Test-Node \{.*?^return \$true\b.*?^}').Value
if ($oldNodeFunc) {
    $script = $script.Replace($oldNodeFunc, @'
function Test-Node {
    Write-Host "→ Checking Node.js (SKIPPED for test)..."
    $script:HasNode = $false
    return $true
}
'@)
}

# Save modified script and run it
$tempFile = "$env:TEMP\sidekick-install-test.ps1"
Set-Content -Path $tempFile -Value $script -Encoding UTF8
& $tempFile
Write-Host "=== EXIT CODE: $LASTEXITCODE ==="