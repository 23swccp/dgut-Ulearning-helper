# -*- mode: python ; coding: utf-8 -*-
"""内部更新器：onedir、仅标准库，由主程序复制到临时目录运行。

必须使用 onedir：onefile 的引导父进程与实际工作进程存在父子耦合，
PyInstaller 的 onefile 子进程会监视引导父进程，父进程退出时子进程
跟着退出——而更新流程恰好要求主程序退出后更新器继续运行。

构建：python -m PyInstaller packaging/updater.spec --noconfirm
产物：dist/updater/updater.exe（整个目录会被 dgut-bot.spec 收集进
_internal/updater/，安装时整体复制到临时目录后运行）。
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(ROOT / "updater_installer.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="updater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="updater",
)
