# -*- mode: python ; coding: utf-8 -*-
"""莞工小皮卡主程序：Windows x64 onedir 免安装发行版。

构建（先执行 packaging/updater.spec 生成内部更新器）：
  python -m PyInstaller packaging/dgut-bot.spec --noconfirm
产物：dist/dgut-bot/dgut-bot.exe + dist/dgut-bot/_internal/

- onedir 模式：更新时被占用的 EXE/DLL 等待进程退出后整体替换。
- windowed 模式：无控制台；启动错误写入程序目录 browser-launcher.log。
- web/dist 不打进 _internal：由 scripts/package_release.py 复制到发行目录
  顶层 web/dist，冻结代码通过 app_paths.frontend_dist() 定位。
- 内部更新器为 onedir（onefile 的引导父子进程会随主程序退出而终止，
  破坏移交流程），位于 _internal/updater/。
- 正式图标放在 assets/dgut-bot.ico；缺失时以无图标构建，并打印明确提示。
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
UPDATER_DIR = ROOT / "dist" / "updater"
ICON = ROOT / "assets" / "dgut-bot.ico"

datas = [
    # 校验新版本时需要读取版本号（updater_installer.verify_new_version）。
    (str(ROOT / "version.py"), "."),
]
if (UPDATER_DIR / "updater.exe").is_file():
    datas.append((str(UPDATER_DIR), "updater"))
else:
    print(
        "警告：未找到 dist/updater/updater.exe。"
        "请先执行 python -m PyInstaller packaging/updater.spec --noconfirm，"
        "否则本构建不具备自动更新安装能力。"
    )

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
