"""Publish the user-facing Windows installer to a Gitee Release.

The script is intentionally a no-op when GITEE_TOKEN is absent, so forks and
local builds do not fail merely because they cannot publish a mirror release.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import requests


API_ROOT = "https://gitee.com/api/v5"
DEFAULT_ASSET_NAME = "DgutBot-win-Setup-Qing-Zhi-Xia-Zai-Zhe-Ge.exe"


class GiteeReleaseError(RuntimeError):
    pass


def _json(response: requests.Response, action: str) -> Any:
    if not response.ok:
        detail = response.text.strip()[:500]
        raise GiteeReleaseError(
            f"Gitee API {action} failed: HTTP {response.status_code} {detail}"
        )
    if response.status_code == 204 or not response.text:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise GiteeReleaseError(f"Gitee API {action} returned invalid JSON") from exc


def publish_release(
    *,
    token: str,
    owner: str,
    repo: str,
    tag: str,
    target: str,
    asset: Path,
    asset_name: str = DEFAULT_ASSET_NAME,
    session: requests.Session | None = None,
) -> str:
    if not token:
        print("GITEE_TOKEN is not configured; skipping Gitee Release.")
        return ""
    if not asset.is_file():
        raise GiteeReleaseError(f"installer not found: {asset}")

    client = session or requests.Session()
    client.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "dgut-bot-release-workflow",
        }
    )
    releases_url = f"{API_ROOT}/repos/{owner}/{repo}/releases"
    tag_url = f"{releases_url}/tags/{tag}"

    response = client.get(tag_url, timeout=30)
    release = None if response.status_code == 404 else _json(response, "find release")
    # Gitee currently answers HTTP 200 with a JSON null for an unknown tag.
    if release is None:
        release = _json(
            client.post(
                releases_url,
                data={
                    "tag_name": tag,
                    "target_commitish": target,
                    "name": tag,
                    "body": (
                        "普通用户请只下载 "
                        f"**{asset_name}**。\n\n"
                        "此页面是 GitHub 下载不畅时使用的国内备用下载源。"
                    ),
                    "prerelease": False,
                },
                timeout=30,
            ),
            "create release",
        )
        print(f"Created Gitee Release {tag}.")
    else:
        print(f"Using existing Gitee Release {tag}.")

    release_id = release.get("id")
    if release_id is None:
        raise GiteeReleaseError("Gitee Release response does not contain an id")

    attachments_url = f"{releases_url}/{release_id}/attach_files"
    attachments = _json(client.get(attachments_url, timeout=30), "list attachments")
    for attachment in attachments:
        if attachment.get("name") == asset_name:
            attachment_id = attachment.get("id")
            if attachment_id is None:
                raise GiteeReleaseError("matching Gitee attachment has no id")
            _json(
                client.delete(
                    f"{attachments_url}/{attachment_id}",
                    timeout=30,
                ),
                "delete old attachment",
            )
            print(f"Removed old attachment {asset_name}.")

    with asset.open("rb") as installer:
        uploaded = _json(
            client.post(
                attachments_url,
                files={
                    "file": (
                        asset_name,
                        installer,
                        "application/vnd.microsoft.portable-executable",
                    )
                },
                timeout=300,
            ),
            "upload installer",
        )

    download_url = uploaded.get("browser_download_url") or uploaded.get("download_url") or ""
    print(f"Uploaded {asset_name} to Gitee Release {tag}.")
    if download_url:
        print(f"Download: {download_url}")
    return str(download_url)


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload Setup.exe to a Gitee Release")
    parser.add_argument("--owner", default=os.getenv("GITEE_OWNER", "swccq23"))
    parser.add_argument("--repo", default=os.getenv("GITEE_REPO", "dgut-bot"))
    parser.add_argument("--tag", default=os.getenv("GITEE_TAG", ""))
    parser.add_argument("--target", default=os.getenv("GITEE_TARGET_COMMITISH", "main"))
    parser.add_argument("--asset", default=os.getenv("GITEE_ASSET", ""))
    parser.add_argument(
        "--asset-name", default=os.getenv("GITEE_ASSET_NAME", DEFAULT_ASSET_NAME)
    )
    args = parser.parse_args()

    token = os.getenv("GITEE_TOKEN", "").strip()
    if not token:
        print("GITEE_TOKEN is not configured; skipping Gitee Release.")
        return 0
    if not args.tag:
        parser.error("--tag or GITEE_TAG is required when GITEE_TOKEN is configured")
    if not args.asset:
        parser.error("--asset or GITEE_ASSET is required when GITEE_TOKEN is configured")

    publish_release(
        token=token,
        owner=args.owner,
        repo=args.repo,
        tag=args.tag,
        target=args.target,
        asset=Path(args.asset),
        asset_name=args.asset_name,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
