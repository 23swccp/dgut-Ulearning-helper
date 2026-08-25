@echo off
setlocal
set "ROOT=%~dp0"
set "WEB_URL=http://127.0.0.1:1420"

where python >nul 2>nul || (echo Python was not found. Install Python and add it to PATH. & pause & exit /b 1)
where npm >nul 2>nul || (echo Node.js/npm was not found. Install Node.js and add it to PATH. & pause & exit /b 1)

start "YXY Local API" /min /d "%ROOT%" python web_server.py
start "YXY Browser UI" /min /d "%ROOT%tauri-react" npm run web

powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; for ($i=0; $i -lt 30; $i++) { try { Invoke-WebRequest -UseBasicParsing '%WEB_URL%' -TimeoutSec 1 | Out-Null; exit 0 } catch { Start-Sleep -Seconds 1 } }; exit 1"
if errorlevel 1 (echo The local web UI did not start in time. Check the YXY Browser UI window. & pause & exit /b 1)

powershell -NoProfile -Command "Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/command' -Method Post -ContentType 'application/json' -Body '{\"command\":\"start_browser\",\"payload\":{\"url\":\"%WEB_URL%\"}}' | Out-Null"
echo Opened %WEB_URL% in the debug browser.
timeout /t 3 >nul
