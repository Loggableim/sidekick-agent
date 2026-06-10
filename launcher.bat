@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PS1=%~dp0Sidekick-Launcher.ps1"
if not exist "%PS1%" (
  echo ERROR: Sidekick-Launcher.ps1 not found next to launcher.bat.
  echo Expected: %PS1%
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Sidekick launcher failed with exit code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
