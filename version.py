"""应用版本与更新源解析。

版本号只在本文件维护。更新仓库不绑定项目名称，按以下优先级解析：
YXY_UPDATE_REPOSITORY 环境变量、发布包内 release-source.json、
GitHub Actions 的 GITHUB_REPOSITORY、当前 Git origin。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse


APP_NAME = "优学院助手"
APP_VERSION = "0.2.0"
ROOT = Path(__file__).resolve().parent


def normalize_github_repository(value: str) -> str:
    """把 owner/repo、HTTPS 或 SSH GitHub 地址统一为 owner/repo。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("git@github.com:"):
        text = text.split(":", 1)[1]
    elif "://" in text:
        parsed = urlparse(text)
        if (parsed.hostname or "").lower() != "github.com":
            return ""
        text = parsed.path.lstrip("/")
    text = text.removesuffix(".git").strip("/")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
        return text
    return ""


def _repository_from_source_file() -> str:
    try:
        data = json.loads((ROOT / "release-source.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    return normalize_github_repository(data.get("repository", "")) if isinstance(data, dict) else ""


def _repository_from_git_config() -> str:
    """开发环境兜底；发布包由 release-source.json 提供来源。"""
    git_dir = ROOT / ".git"
    if git_dir.is_file():
        try:
            pointer = git_dir.read_text(encoding="utf-8").strip()
            if pointer.lower().startswith("gitdir:"):
                git_dir = (ROOT / pointer.split(":", 1)[1].strip()).resolve()
        except OSError:
            return ""
    try:
        config = (git_dir / "config").read_text(encoding="utf-8")
    except OSError:
        return ""
    origin = re.search(
        r'(?ms)^\s*\[remote\s+"origin"\]\s*$.*?^\s*url\s*=\s*(\S+)\s*$',
        config,
    )
    return normalize_github_repository(origin.group(1)) if origin else ""


def resolve_github_repository() -> str:
    candidates = (
        os.environ.get("YXY_UPDATE_REPOSITORY", ""),
        _repository_from_source_file(),
        os.environ.get("GITHUB_REPOSITORY", ""),
        _repository_from_git_config(),
    )
    return next((repo for value in candidates if (repo := normalize_github_repository(value))), "")


GITHUB_REPO = resolve_github_repository()
RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest" if GITHUB_REPO else ""
