$ErrorActionPreference = "Stop"
$install = Join-Path $env:LOCALAPPDATA "sidekick\sidekick-agent"
$py = Join-Path $install ".venv\Scripts\python.exe"

# What's installed?
& $py -m pip list 2>&1 | Select-String -Pattern "fastapi|uvicorn|starlette|sidekick|web"
echo "---"
# What does the sidekick_web module need?
& $py -c "import cli.web_server as server; import inspect; src = inspect.getsourcefile(server); print('cli.web_server at:', src)" 2>&1
echo "---"
# Is it the wrong venv?
& $py -c "import sys; print('\\n'.join(sys.path))" 2>&1 | head -5
