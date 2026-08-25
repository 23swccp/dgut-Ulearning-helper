@echo off
setlocal
set "ROOT=%~dp0"
set "WEB_URL=http://127.0.0.1:1420"

where python >nul 2>nul || (echo 未找到 Python。请先安装 Python 并加入 PATH。 & pause & exit /b 1)
where npm >nul 2>nul || (echo 未找到 Node.js/npm。请先安装 Node.js 并加入 PATH。 & pause & exit /b 1)

start "优学院本地服务" /min /d "%ROOT%" python web_server.py
start "优学院浏览器前端" /min /d "%ROOT%tauri-react" npm run web

powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; for ($i=0; $i -lt 30; $i++) { try { Invoke-WebRequest -UseBasicParsing '%WEB_URL%' -TimeoutSec 1 | Out-Null; exit 0 } catch { Start-Sleep -Seconds 1 } }; exit 1"
if errorlevel 1 (echo 本地前端启动超时，请查看“优学院浏览器前端”窗口。 & pause & exit /b 1)

powershell -NoProfile -Command "Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/command' -Method Post -ContentType 'application/json' -Body '{\"command\":\"start_browser\",\"payload\":{\"url\":\"%WEB_URL%\"}}' | Out-Null"
echo 已在调试模式浏览器中打开 %WEB_URL%
timeout /t 3 >nul
