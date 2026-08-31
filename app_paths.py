"""统一的资源与数据路径管理。

冻结（PyInstaller onedir）模式下必须区分两类目录：

- 只读内置资源：随发行包分发，位于 PyInstaller 解包目录（sys._MEIPASS）。
- 用户数据：config.json、auth.json、browser_profile、日志与更新状态等，
  统一写在 dgut-bot.exe 所在目录，绝不写进解包临时目录。

开发模式下两者都是源码目录，行为与历史版本一致。
本模块只依赖标准库，更新器与测试可以安全引用。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """当前进程是否运行在 PyInstaller 冻结环境中。"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """只读内置资源目录：冻结后为 PyInstaller 解包目录，开发时为源码目录。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parent


def data_root() -> Path:
    """用户数据目录：冻结后为 dgut-bot.exe 所在目录，开发时为源码目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def frontend_dist() -> Path:
    """前端构建产物目录；发布包位于程序目录顶层 web/dist。"""
    return data_root() / "web" / "dist"
