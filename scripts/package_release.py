"""构建发布 ZIP 与 manifest.json；由 GitHub Actions 在 tag 推送时调用。

用法：python scripts/package_release.py
环境变量：GITHUB_REPOSITORY（形如 owner/repo）、GITHUB_REF_NAME（tag，如 v0.3.0）。
产物输出到 release/ 目录：yxy-assistant-vX.Y.Z.zip 与 manifest.json。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "release"

# 发布包内容：可直接运行的 Python 源码 + 前端构建产物，不含源码开发目录。
INCLUDE_FILES = (
    "requirements.txt", "README.md", "启动浏览器版.bat", "web/package.json",
)
EXCLUDE_DIRS = {
    ".git", "__pycache__", ".update", "browser_profile", "release", "node_modules",
    "优学院手机端源码", ".pytest_cache", "web/src", "web/node_modules",
}
EXCLUDE_FILE_PATTERNS = (r"^test_.*\.py$", r"^update-result\.json$", r"^update_failures\.json$")


def read_version() -> str:
    text = (ROOT / "version.py").read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("version.py 中找不到 APP_VERSION")
    return match.group(1)


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


def is_excluded(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    parts = set(relative.parts)
    if parts & {part.rstrip("/") for part in EXCLUDE_DIRS}:
        return True
    return any(re.match(pattern, relative.name) for pattern in EXCLUDE_FILE_PATTERNS)


def collect_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.iterdir():
        if path.is_file() and path.suffix == ".py" and not is_excluded(path):
            files.append(path)
    for path in (ROOT / "web" / "dist").rglob("*") if (ROOT / "web" / "dist").is_dir() else []:
        if path.is_file():
            files.append(path)
    for name in INCLUDE_FILES:
        path = ROOT / name
        if path.is_file():
            files.append(path)
        else:
            print(f"警告：发布包缺少 {name}", file=sys.stderr)
    return sorted(set(files))


def main() -> int:
    version = read_version()
    tag = os.environ.get("GITHUB_REF_NAME", f"v{version}")
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise SystemExit("缺少有效的 GITHUB_REPOSITORY（应为 owner/repo）；拒绝生成来源不明的更新包")
    if not tag.startswith("v") or tag.lstrip("v") != version:
        raise SystemExit(f"tag {tag} 与 version.py 中的版本 {version} 不一致；已停止发布")
    OUTPUT.mkdir(exist_ok=True)
    zip_name = f"yxy-assistant-{tag}.zip"
    zip_path = OUTPUT / zip_name
    files = collect_files()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT))
        archive.writestr(
            "release-source.json",
            json.dumps({"repository": repository}, ensure_ascii=False, indent=1),
        )
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
    print(f"已生成 {zip_path}（{size} 字节，{len(files)} 个文件）")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
