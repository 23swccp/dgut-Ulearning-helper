"""组装 onedir 发行目录、更新 ZIP 与 manifest.json。

由 GitHub Actions 在 tag 推送时调用，也可被 scripts/build_windows_release.ps1
在本地调用。用法：python scripts/package_release.py

环境变量：GITHUB_REPOSITORY（形如 owner/repo）、GITHUB_REF_NAME（tag，如 v0.3.0）；
仓库无法解析更新来源时拒绝生成更新包。

输入（须先完成）：
- dist/dgut-bot/         PyInstaller onedir 输出（packaging/dgut-bot.spec）
- dist/updater/          内部更新器（packaging/updater.spec）
- web/dist/              前端构建产物

输出到 release/：
- dgut-bot-vX.Y.Z-windows-x64/   完整发行目录
- dgut-bot-vX.Y.Z-windows-x64.zip
- manifest.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from version import APP_VERSION, resolve_github_repository  # noqa: E402

OUTPUT = ROOT / "release"
APP_OUTPUT = ROOT / "dist" / "dgut-bot"
UPDATER_OUTPUT = ROOT / "dist" / "updater" / "updater.exe"


def release_dir_name(version: str) -> str:
    return f"dgut-bot-v{version}-windows-x64"


def _long_path(path: Path) -> str:
    """Windows 下绕过 MAX_PATH 限制；运行产生的浏览器资料可能出现超长路径。"""
    text = str(path)
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        return "\\\\?\\" + text
    return text


def remove_tree(path: Path) -> None:
    shutil.rmtree(_long_path(path))


def changelog_from_git(tag: str) -> str:
    try:
        previous = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", f"{tag}^"], cwd=ROOT,
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        args = ["git", "log", "--pretty=- %s", f"{previous}..{tag}" if previous else tag]
        lines = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()
        return lines or "本次更新。"
    except OSError:
        return "本次更新。"


def assemble_release_dir(version: str) -> Path:
    """把 PyInstaller onedir 输出、前端构建产物和 README 组装成发行目录。"""
    if not (APP_OUTPUT / "dgut-bot.exe").is_file():
        raise SystemExit("未找到 dist/dgut-bot/dgut-bot.exe；请先执行 PyInstaller 构建 packaging/dgut-bot.spec")
    internal = APP_OUTPUT / "_internal"
    if not internal.is_dir():
        raise SystemExit("PyInstaller 输出缺少 _internal；必须使用 onedir 模式")
    if not UPDATER_OUTPUT.is_file() and not (internal / "updater" / "updater.exe").is_file():
        raise SystemExit("缺少内部更新器 dist/updater/updater.exe；请先构建 packaging/updater.spec")
    web_dist = ROOT / "web" / "dist"
    if not (web_dist / "index.html").is_file():
        raise SystemExit("缺少 web/dist/index.html；请先在 web 目录执行 npm run build")

    dist_dir = OUTPUT / release_dir_name(version)
    if dist_dir.exists():
        remove_tree(dist_dir)
    dist_dir.mkdir(parents=True)
    shutil.copytree(APP_OUTPUT, dist_dir, dirs_exist_ok=True)
    shutil.copytree(web_dist, dist_dir / "web" / "dist")
    shutil.copy2(ROOT / "README.md", dist_dir / "README.md")
    return dist_dir


def main() -> int:
    version = APP_VERSION
    tag = os.environ.get("GITHUB_REF_NAME", f"v{version}")
    repository = resolve_github_repository()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise SystemExit("无法解析更新仓库（owner/repo）；拒绝生成来源不明的更新包。"
                         "可设置 GITHUB_REPOSITORY 或 YXY_UPDATE_REPOSITORY")
    if not tag.startswith("v") or tag.lstrip("v") != version:
        raise SystemExit(f"tag {tag} 与 version.py 中的版本 {version} 不一致；已停止发布")

    OUTPUT.mkdir(exist_ok=True)
    dist_dir = assemble_release_dir(version)
    (dist_dir / "release-source.json").write_text(
        json.dumps({"repository": repository, "version": version}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    zip_name = f"{release_dir_name(version)}.zip"
    zip_path = OUTPUT / zip_name
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(dist_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(OUTPUT))

    size = zip_path.stat().st_size
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    manifest = {
        "version": version,
        "url": f"https://github.com/{repository}/releases/download/{tag}/{zip_name}",
        "size": size,
        "sha256": digest,
        "publishedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "changelog": changelog_from_git(tag),
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"已生成 {zip_path}（{size} 字节，SHA-256: {digest}）")
    print(f"manifest：{OUTPUT / 'manifest.json'}")
    print(f"更新地址：{manifest['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
