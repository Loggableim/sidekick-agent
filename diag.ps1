$ErrorActionPreference = "Stop"
$install = Join-Path $env:LOCALAPPDATA "sidekick\sidekick-agent"
Write-Host "Install dir: $install"

# What is in the venv?
$scripts = Join-Path $install ".venv\Scripts"
if (Test-Path $scripts) {
    Write-Host "=== venv Scripts ==="
    Get-ChildItem $scripts -Filter "*.exe" | Select-Object Name | Format-Table
} else {
    Write-Host "NO venv scripts at $scripts"
}

# Test python in venv
$py = Join-Path $scripts "python.exe"
if (Test-Path $py) {
    Write-Host "=== Python test ==="
    & $py -c "import fastapi; print('fastapi', fastapi.__version__)"
    & $py -c "import sidekick_app; print('sidekick_app OK')"
    & $py -c "from web import server; print('web.server OK')"
}