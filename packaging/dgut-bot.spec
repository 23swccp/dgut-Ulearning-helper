# -*- mode: python ; coding: utf-8 -*-
"""莞工小皮卡主程序：交给 Velopack 打包的 Windows x64 onedir 目录。

构建：
  python -m PyInstaller packaging/dgut-bot.spec --noconfirm
产物：dist/dgut-bot/dgut-bot.exe + dist/dgut-bot/_internal/

- onedir 模式：符合 Velopack 官方对 Python/PyInstaller 应用的要求。
- windowed 模式：无控制台；启动错误写入 LocalAppData 数据目录的 browser-launcher.log。
- web/dist 和 release-source.json 作为只读资源打进 _internal；用户数据
  统一存放在 LocalAppData，不进入 Velopack 的 current 目录。
- 正式图标放在 assets/dgut-bot.ico；缺失时以无图标构建，并打印明确提示。
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
ICON = ROOT / "assets" / "dgut-bot.ico"

datas = [
    (str(ROOT / "web" / "dist"), "web/dist"),
    (str(ROOT / "release-source.json"), "."),
    (str(ROOT / "README.md"), "."),
]

if ICON.is_file():
    icon_option: str | None = str(ICON)
else:
    icon_option = None
    print("提示：未找到 assets/dgut-bot.ico，本次以无图标构建；不影响功能。")

a = Analysis(
    [str(ROOT / "browser_launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 测试与开发工具不进入发行包。
        "pytest",
        "test_backend",
        "test_course",
        "test_launcher",
        "test_quiz",
        "test_updater",
        "quiz_simulator",
        "quiz_probe",
        "quiz_walk",
        "run_brush",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dgut-bot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_option,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="dgut-bot",
)
