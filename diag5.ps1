$install = "C:\Users\vboxuser\AppData\Local\sidekick\sidekick-agent"
$uvPath = "C:\Users\vboxuser\.local\bin\uv.exe"

Set-Location $install
$env:VIRTUAL_ENV = "$install\.venv"
& $uvPath pip install --python "$install\.venv\Scripts\python.exe" -e ".[web]" 2>&1 | Out-File C:\Users\vboxuser\Desktop\pip_out.log -Encoding utf8
$ec = $LASTEXITCODE
"Exit: $ec"
"---"
Get-Content C:\Users\vboxuser\Desktop\pip_out.log | Select-Object -First 30
"---"
# Check fastapi
& "$install\.venv\Scripts\python.exe" -c "import fastapi; print('fastapi', fastapi.__version__)" 2>&1