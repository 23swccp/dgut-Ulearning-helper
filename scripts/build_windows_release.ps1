# 构建莞工小皮卡 Windows x64 免安装发行版（PyInstaller onedir）。
# 用法：powershell -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1
#       可选 -SkipNpmInstall 跳过 npm ci（前端依赖未变化时加速本地重跑）。
# 产物：release/dgut-bot-vX.Y.Z-windows-x64.zip 与 release/manifest.json
param(
    [switch]$SkipNpmInstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== 1/7 Clean build/ and dist/ =="
foreach ($name in @("build", "dist")) {
    $path = Join-Path $Root $name
    if (Test-Path $path) {
        Remove-Item $path -Recurse -Force
        Write-Host "Removed $name/"
    }
}

Write-Host "== 2/7 Build frontend =="
Push-Location (Join-Path $Root "web")
try {
    if (-not $SkipNpmInstall) {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
} finally {
    Pop-Location
}

Write-Host "== 3/7 Check PyInstaller =="
python scripts/pyinstaller_run.py --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller not found; installing pyinstaller==6.22.2 ..."
    python -m pip install "pyinstaller==6.22.2"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed" }
}

Write-Host "== 4/7 Build internal updater (onedir) =="
python scripts/pyinstaller_run.py packaging/updater.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "updater.spec build failed" }

Write-Host "== 5/7 Build main application (onedir, windowed) =="
python scripts/pyinstaller_run.py packaging/dgut-bot.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "dgut-bot.spec build failed" }

if (-not (Test-Path (Join-Path $Root "assets/dgut-bot.ico"))) {
    Write-Host "Notice: assets/dgut-bot.ico is missing; building without an icon."
}

Write-Host "== 6/7 Assemble release, ZIP, and manifest.json =="
python scripts/package_release.py
if ($LASTEXITCODE -ne 0) { throw "package_release.py failed" }

Write-Host "== 7/7 Release artifacts =="
$release = Join-Path $Root "release"
Get-ChildItem $release -File | ForEach-Object {
    Write-Host ("{0}  ({1:N1} MB)" -f $_.FullName, ($_.Length / 1MB))
}
$manifest = Get-Content (Join-Path $release "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host ("ZIP SHA-256: {0}" -f $manifest.sha256)
Write-Host ("Release URL: {0}" -f $manifest.url)
