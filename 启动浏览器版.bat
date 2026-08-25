@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

where python >nul 2>nul || (echo Python was not found. Install Python and add it to PATH. & pause & exit /b 1)
python browser_launcher.py
if errorlevel 1 (
  echo.
  echo Browser launcher failed. See the error above and browser-launcher.log.
  pause
  exit /b 1
)
