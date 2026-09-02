"""Windows 浏览器路径清理与系统注册位置查询，不启动浏览器。"""

from __future__ import annotations

import os
from pathlib import Path


BROWSER_NAMES = {
    "msedge.exe": "Microsoft Edge",
    "chrome.exe": "Google Chrome",
    "brave.exe": "Brave",
    "vivaldi.exe": "Vivaldi",
    "launcher.exe": "Opera",
    "opera.exe": "Opera",
    "360chromex.exe": "360 极速浏览器",
}


def normalize_browser_path(value: str) -> str:
    # Explorer 的“复制为路径”会带引号，部分复制来源还会带方向标记。
    value = value.translate(dict.fromkeys(map(ord, "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff")))
    value = value.strip().strip('"\'“”').strip()
    return os.path.expanduser(os.path.expandvars(value)) if value else ""


def resolve_browser_path(value: str) -> str:
    value = normalize_browser_path(value)
    if not value:
        return ""
    candidate = Path(value)
    try:
        if candidate.is_dir():
            # 也接受浏览器 Application 文件夹，但不递归扫描或猜测其他程序。
            for executable in BROWSER_NAMES:
                path = candidate / executable
                if path.is_file():
                    return str(path.resolve())
        if candidate.suffix.lower() == ".exe" and candidate.is_file():
            return str(candidate.resolve())
    except (OSError, ValueError):
        pass
    return ""


def registered_browser_paths(executable: str) -> list[str]:
    """同时读取当前用户/整机和 32/64 位 App Paths 注册项。"""
    try:
        import winreg
    except ImportError:
        return []
    paths = []
    key_name = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                if isinstance(value, str) and value.strip():
                    paths.append(normalize_browser_path(value))
            except OSError:
                continue
    return list(dict.fromkeys(paths))


def extra_browser_candidates() -> dict[str, list[str]]:
    paths: dict[str, list[str]] = {}
    for executable, name in BROWSER_NAMES.items():
        paths.setdefault(name, []).extend(registered_browser_paths(executable))
    # 环境变量缺失或由 32 位宿主启动时，仍检查系统盘的标准安装位置。
    drive = os.environ.get("SystemDrive") or "C:"
    roots = [
        os.environ.get("ProgramW6432", ""),
        str(Path(drive + "\\") / "Program Files"),
        str(Path(drive + "\\") / "Program Files (x86)"),
    ]
    try:
        roots.append(str(Path.home() / "AppData" / "Local"))
    except RuntimeError:
        pass
    for name, relative in (
        ("Microsoft Edge", Path("Microsoft/Edge/Application/msedge.exe")),
        ("Google Chrome", Path("Google/Chrome/Application/chrome.exe")),
    ):
        paths[name].extend(str(Path(root) / relative) for root in roots if root)
    return paths
