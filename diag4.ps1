$ErrorActionPreference = "Stop"
$install = "C:\Users\vboxuser\AppData\Local\sidekick\sidekick-agent"
$uvPath = "C:\Users\vboxuser\.local\bin\uv.exe"

Set-Location $install
Write-Host "CWD: $PWD"
Write-Host "Venv exists: $(Test-Path .venv\Scripts\python.exe)"

# Use --python explicit and VIRTUAL_ENV
$env:VIRTUAL_ENV = "$install\.venv"
& $uvPath pip install --python "$install\.venv\Scripts\python.exe" -e ".[web]" 2>&1 | Select-Object -First 30