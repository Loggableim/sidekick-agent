$ErrorActionPreference = "Stop"
$install = Join-Path $env:LOCALAPPDATA "sidekick\sidekick-agent"
$uvPath = "C:\Users\vboxuser\.local\bin\uv.exe"

# List installed packages with uv
& $uvPath pip list 2>&1 | Select-String -Pattern "fastapi|uvicorn|starlette|sidekick"
echo "---ALL TOP---"
& $uvPath pip list 2>&1 | Select-Object -First 50