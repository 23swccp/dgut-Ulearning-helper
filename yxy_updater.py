"""应用内自动更新的核心逻辑：检查、下载、校验、移交与 CDP 精确关标签。

纯逻辑与线程编排都放在这里；真正替换文件的独立更新器在 updater_installer.py，
它会被复制到临时目录运行，因此必须只依赖标准库。
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import requests

from app_paths import is_frozen, resource_root
from version import APP_VERSION, RELEASE_API

USER_AGENT = "dgut-yxy-assistant-updater"
# 这些相对路径属于用户数据，更新器永远不会覆盖或删除。
PRESERVE_RELATIVE = (
    "config.json",
    "auth.json",
    "account.json",
    "browser_profile",
    "签到记录.md",
    "browser-launcher.log",
    "browser-service.log",
    ".update",
    "update-result.json",
    "update_failures.json",
)

MAX_DOWNLOAD_RETRIES = 3
RETRY_BACKOFF_SECONDS = (2, 5, 10)


class UpdateError(Exception):
    """下载或校验失败。"""


def compare_versions(left: str, right: str) -> int:
    """比较形如 v0.3.0 的版本号：返回 -1/0/1；缺失段视为 0。"""

    def parse(value: str) -> tuple[int, ...]:
        text = value.strip().lstrip("vV")
        parts: list[int] = []
        for chunk in re.split(r"[.\-+]", text):
            digits = re.match(r"\d+", chunk)
            parts.append(int(digits.group()) if digits else 0)
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts)

    a, b = parse(left), parse(right)
    return -1 if a < b else (1 if a > b else 0)


def parse_manifest(raw: Any) -> dict[str, Any]:
    """解析并校验 Release manifest；缺失关键字段时抛出 UpdateError。"""
    data = raw if isinstance(raw, dict) else json.loads(raw)
    version = str(data.get("version", "")).strip()
    url = str(data.get("url", "")).strip()
    sha256 = str(data.get("sha256", "")).strip().lower()
    if not version or not url:
        raise UpdateError("manifest 缺少 version 或 url 字段")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise UpdateError("manifest 缺少有效的 sha256 字段")
    size = int(data.get("size") or 0)
    return {
        "version": version,
        "url": url,
        "sha256": sha256,
        "size": max(0, size),
        "changelog": str(data.get("changelog") or data.get("notes") or ""),
        "publishedAt": str(data.get("publishedAt") or data.get("published_at") or ""),
    }


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_preserved(relative: Path) -> bool:
    """判断相对路径是否属于必须保留的用户数据（支持任意分隔符）。"""
    parts = [part for part in Path(str(relative).replace("\\", "/")).parts if part not in ("", ".")]
    if not parts:
        return True
    for keep in PRESERVE_RELATIVE:
        keep_parts = Path(keep).parts
        if tuple(parts[: len(keep_parts)]) == keep_parts:
            return True
    return False


def _safe_target(dest_dir: Path, arc_name: str) -> Path:
    """把 ZIP 条目名解析为目标路径，拒绝绝对路径与目录穿越（Zip Slip）。"""
    name = arc_name.replace("\\", "/")
    if re.fullmatch(r"[A-Za-z]:.*", name) or name.startswith("/"):
        raise UpdateError(f"ZIP 内出现绝对路径，已拒绝解压：{arc_name}")
    parts = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise UpdateError(f"ZIP 内出现路径穿越，已拒绝解压：{arc_name}")
        parts.append(part)
    if not parts:
        raise UpdateError(f"ZIP 内出现空路径：{arc_name}")
    target = dest_dir.joinpath(*parts).resolve()
    try:
        target.relative_to(dest_dir.resolve())
    except ValueError as error:
        raise UpdateError(f"ZIP 条目逃出目标目录：{arc_name}") from error
    return target


def strip_common_root(names: list[str]) -> tuple[str, Callable[[str], str]]:
    """所有条目都在同一个顶层目录时返回剥离函数，否则原样返回。"""
    tops = {name.replace("\\", "/").split("/", 1)[0] for name in names}
    if len(tops) == 1 and all("/" in name.replace("\\", "/") for name in names):
        root = tops.pop()
        if root not in ("..", "."):  # 顶层目录本身可疑时交给逐条校验拒绝
            return root, lambda name: name.replace("\\", "/").split("/", 1)[1]
    return "", lambda name: name.replace("\\", "/")


def assert_no_traversal(names: list[str]) -> None:
    """在剥离公共目录之前先检查原始条目名，杜绝 ../ 或绝对路径混入。"""
    for name in names:
        normalized = name.replace("\\", "/")
        if re.fullmatch(r"[A-Za-z]:.*", normalized) or normalized.startswith("/"):
            raise UpdateError(f"ZIP 内出现绝对路径，已拒绝解压：{name}")
        if ".." in normalized.split("/"):
            raise UpdateError(f"ZIP 内出现路径穿越，已拒绝解压：{name}")


def extract_zip_safely(zip_path: Path, dest_dir: Path, progress: Callable[[int, int], None] | None = None) -> list[Path]:
    """解压到 dest_dir；禁止绝对路径、`..` 与逃出目标目录的条目。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as archive:
        names = [info.filename for info in archive.infolist() if not info.is_dir()]
        assert_no_traversal(names)
        _, strip = strip_common_root(names)
        total = len(names)
        for index, raw_name in enumerate(names):
            target = _safe_target(dest_dir, strip(raw_name))
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(raw_name) as source, open(target, "wb") as output:
                shutil.copyfileobj(source, output)
            extracted.append(target)
            if progress:
                progress(index + 1, total)
    return extracted


def select_targets_to_close(targets: list[dict[str, Any]], base_url: str) -> list[dict[str, Any]]:
    """从 CDP target 列表中挑出助手自己的标签页；必须精确匹配 scheme/host/port。"""
    parsed = urlparse(base_url)
    want_scheme = parsed.scheme or "http"
    want_host = (parsed.hostname or "").lower()
    want_port = parsed.port or (443 if want_scheme == "https" else 80)
    selected: list[dict[str, Any]] = []
    for target in targets:
        if target.get("type") != "page":
            continue
        url = urlparse(str(target.get("url", "")))
        host = (url.hostname or "").lower()
        port = url.port or (443 if url.scheme == "https" else 80)
        if url.scheme == want_scheme and host == want_host and port == want_port:
            selected.append(target)
    return selected


def close_assistant_tabs(
    debug_port: int,
    base_url: str,
    *,
    list_targets: Callable[[int], list[dict[str, Any]]] | None = None,
    browser_socket: Callable[[int], Any] | None = None,
) -> int:
    """通过 CDP Target.closeTarget 精确关闭助手标签页，不结束浏览器进程。"""

    def default_list(port: int) -> list[dict[str, Any]]:
        return requests.get(f"http://127.0.0.1:{port}/json", timeout=2).json()

    def default_socket(port: int) -> Any:
        from websocket import create_connection

        version_info = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2).json()
        ws_url = version_info.get("webSocketDebuggerUrl")
        if not ws_url:
            raise UpdateError("浏览器未提供调试 WebSocket 地址")
        return create_connection(ws_url, timeout=5)

    targets = (list_targets or default_list)(debug_port)
    closing = select_targets_to_close(targets, base_url)
    if not closing:
        return 0
    ws = (browser_socket or default_socket)(debug_port)
    closed = 0
    try:
        for index, target in enumerate(closing, start=1):
            ws.send(json.dumps({"id": index, "method": "Target.closeTarget", "params": {"targetId": target.get("id")}}))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                response = json.loads(ws.recv())
                if response.get("id") == index:
                    if response.get("result", {}).get("success") is not False:
                        closed += 1
                    break
    finally:
        ws.close()
    return closed


def wait_for_ready(ready_path: Path, timeout: float, poll: Callable[[float], None] = time.sleep) -> dict[str, Any] | None:
    """等待独立更新器写出 ready 信号；超时返回 None。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = json.loads(ready_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            poll(0.2)
            continue
        if data.get("ok"):
            return data
        poll(0.2)
    return None


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def download_to_file(
    url: str,
    dest: Path,
    *,
    expected_size: int = 0,
    headers: dict[str, str] | None = None,
    progress: Callable[[int, int], None] | None = None,
    stop: threading.Event | None = None,
) -> None:
    """流式下载到 dest；支持 Range 断点续传，HTTP 状态或长度异常时抛出 UpdateError。"""
    start_from = dest.stat().st_size if dest.is_file() else 0
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if start_from > 0:
        request_headers["Range"] = f"bytes={start_from}-"
    request = Request(url, headers=request_headers)
    try:
        response = urlopen(request, timeout=30)
    except OSError as error:
        raise UpdateError(f"网络连接失败：{error}") from error
    status = getattr(response, "status", 0) or response.getcode()
    if status not in (200, 206):
        response.close()
        raise UpdateError(f"下载地址返回异常状态：HTTP {status}")
    if start_from > 0 and status != 206:
        # 服务器不支持续传时必须从头下载。
        response.close()
        dest.unlink(missing_ok=True)
        download_to_file(url, dest, expected_size=expected_size, headers=headers, progress=progress, stop=stop)
        return
    total = expected_size or int(response.headers.get("Content-Length") or 0)
    if start_from > 0 and total and start_from >= total:
        response.close()
        if progress:
            progress(total, total)
        return
    received = start_from if status == 206 else 0
    mode = "ab" if status == 206 and start_from > 0 else "wb"
    with open(dest, mode) as output:
        while True:
            if stop is not None and stop.is_set():
                response.close()
                raise UpdateError("下载已取消")
            block = response.read(256 * 1024)
            if not block:
                break
            output.write(block)
            received += len(block)
            if progress:
                progress(received, total)
    response.close()
    if total and received != total:
        raise UpdateError(f"下载不完整：已下载 {received} / {total} 字节")


class RequestsTransport:
    """UpdateManager 的默认网络层；测试时可注入假实现。"""

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        response = requests.get(url, headers={"User-Agent": USER_AGENT, **(headers or {})}, timeout=15)
        response.raise_for_status()
        return response.json()

    def download(
        self,
        url: str,
        dest: Path,
        *,
        expected_size: int = 0,
        progress: Callable[[int, int], None] | None = None,
        stop: threading.Event | None = None,
    ) -> None:
        download_to_file(url, dest, expected_size=expected_size, progress=progress, stop=stop)


DEFAULT_STATE: dict[str, Any] = {
    "state": "idle",
    "latestVersion": "",
    "publishedAt": "",
    "changelog": "",
    "downloadUrl": "",
    "sha256": "",
    "size": 0,
    "downloaded": 0,
    "total": 0,
    "error": "",
    "messages": [],
    "lastMessageId": 0,
    "handledResultKey": "",
}

HANDOFF_STATES = {"handoff", "waiting_for_exit"}


class UpdateManager:
    """更新状态机：状态持久化到 .update/state.json，页面刷新或重启后可恢复。"""

    def __init__(
        self,
        root: Path,
        *,
        version: str = APP_VERSION,
        release_api: str = RELEASE_API,
        transport: Any | None = None,
        emit_event: Callable[..., dict[str, Any]] | None = None,
        debug_port: Callable[[], int] | None = None,
        frontend_port: Callable[[], int] | None = None,
        now: Callable[[], str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.root = Path(root)
        self.version = version
        self.release_api = release_api
        self.transport = transport or RequestsTransport()
        self.emit_event = emit_event or (lambda *args, **kwargs: {})
        self.debug_port = debug_port or (lambda: 9222)
        self.frontend_port = frontend_port or (lambda: 8765)
        self._now = now or (lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
        self._sleep = sleep
        self.update_dir = self.root / ".update"
        self.state_path = self.update_dir / "state.json"
        self.zip_path = self.update_dir / "package.zip"
        self.part_path = self.update_dir / "package.zip.part"
        self.manifest_path = self.update_dir / "manifest.json"
        self.result_path = self.root / "update-result.json"
        self.base_url = "http://127.0.0.1:8765"
        self._frontend_port_override: int | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._download_thread: threading.Thread | None = None
        self._install_thread: threading.Thread | None = None
        self._state = json.loads(json.dumps(DEFAULT_STATE))
        self._last_persist = 0.0

    # ---- 状态持久化 -------------------------------------------------

    def _persist(self, force: bool = False) -> None:
        with self._lock:
            self._state["updatedAt"] = self._now()
            payload = json.dumps(self._state, ensure_ascii=False, indent=1)
        now = time.monotonic()
        if not force and now - self._last_persist < 0.5:
            return
        self._last_persist = now
        self.update_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.state_path)

    def _mutate(self, **changes: Any) -> None:
        with self._lock:
            self._state.update(changes)

    def _add_message(self, kind: str, title: str, body: str = "") -> None:
        with self._lock:
            next_id = int(self._state["lastMessageId"]) + 1
            self._state["lastMessageId"] = next_id
            self._state["messages"].insert(0, {"id": next_id, "kind": kind, "title": title, "body": body, "time": self._now(), "read": False})
            self._state["messages"] = self._state["messages"][:50]

    def _set_state(self, state: str) -> None:
        with self._lock:
            self._state["state"] = state
        self.emit_event("UPDATE_STATE", "info", "update", f"更新状态：{state}", data={"state": state})

    # ---- 对前端暴露 -------------------------------------------------

    def set_base_url(self, url: str) -> None:
        self.base_url = url

    def set_ports(self, base_url: str, frontend_port: int) -> None:
        self.base_url = base_url
        self._frontend_port_override = int(frontend_port)

    def mark_read(self) -> None:
        with self._lock:
            for message in self._state["messages"]:
                message["read"] = True
        self._persist(force=True)

    def failure_count(self) -> int:
        try:
            return int(json.loads((self.root / "update_failures.json").read_text(encoding="utf-8")).get("count", 0))
        except (OSError, ValueError):
            return 0

    def ack_failure(self) -> None:
        try:
            result = json.loads(self.result_path.read_text(encoding="utf-8"))
            result["acknowledged"] = True
            self.result_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        except (OSError, ValueError):
            pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            messages = [dict(item) for item in state["messages"]]
        unread = sum(1 for item in messages if not item["read"])
        downloading = state["state"] == "downloading"
        percent = 0
        if state["total"]:
            percent = min(100, round(state["downloaded"] * 100 / state["total"]))
        elif state["state"] in ("verifying", "ready_to_install"):
            percent = 100
        return {
            "currentVersion": self.version,
            "state": state["state"],
            "latestVersion": state["latestVersion"],
            "publishedAt": state["publishedAt"],
            "changelog": state["changelog"],
            "downloaded": state["downloaded"],
            "total": state["total"],
            "percent": percent,
            "error": state["error"],
            "messages": messages,
            "unreadCount": unread,
            "downloading": downloading,
            "handoff": state["state"] in HANDOFF_STATES,
            # handoff 仅表示移交流程已开始；独立更新器写入 ready 文件后
            # 才能安全退出主程序，否则守护安装线程会随主进程一起终止。
            "readyForExit": state["state"] == "waiting_for_exit",
            "canInstall": state["state"] == "ready_to_install",
            "canRetryDownload": state["state"] in ("available", "download_failed"),
            "pendingFailureDialog": self.pending_failure_dialog(),
        }

    def pending_failure_dialog(self) -> dict[str, Any] | None:
        """安装失败且尚未确认时返回一次性对话框内容；确认后不再出现。"""
        try:
            result = json.loads(self.result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if result.get("result") != "failed" or result.get("acknowledged"):
            return None
        if result.get("rolledBack") is False:
            # 回滚都失败时不启动主程序，也就不会走到这个对话框。
            return None
        return {
            "title": "更新未完成",
            "restoredVersion": result.get("from", self.version),
            "failedVersion": result.get("to", ""),
            "stage": result.get("stage", ""),
            "error": result.get("error", ""),
            "advice": result.get("advice", ""),
        }

    # ---- 启动恢复与自动检查 -----------------------------------------

    def restore(self) -> None:
        """进程启动时读取持久化状态，恢复真实进度并续传/校验。"""
        self.update_dir.mkdir(parents=True, exist_ok=True)
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        with self._lock:
            self._state = json.loads(json.dumps(DEFAULT_STATE))
            self._state["messages"] = list(saved.get("messages") or [])[:50]
            self._state["lastMessageId"] = int(saved.get("lastMessageId") or 0)
            self._state["handledResultKey"] = str(saved.get("handledResultKey") or "")
        state = str(saved.get("state") or "idle")
        if state == "downloading":
            # 上次下载被打断：保留 .part 静默续传，不打扰用户。
            self._mutate(
                latestVersion=saved.get("latestVersion", ""),
                changelog=saved.get("changelog", ""),
                downloadUrl=saved.get("downloadUrl", ""),
                sha256=saved.get("sha256", ""),
                size=int(saved.get("size") or 0),
            )
            self._mutate(downloaded=self.part_path.stat().st_size if self.part_path.is_file() else 0)
            self._set_state("downloading")
            self._start_download_thread()
            return
        if state == "ready_to_install":
            self._mutate(
                latestVersion=saved.get("latestVersion", ""),
                changelog=saved.get("changelog", ""),
                downloadUrl=saved.get("downloadUrl", ""),
                sha256=saved.get("sha256", ""),
                size=int(saved.get("size") or 0),
                total=int(saved.get("size") or 0),
                downloaded=int(saved.get("size") or 0),
            )
            if self.zip_path.is_file() and self._verify_zip():
                self._set_state("ready_to_install")
            else:
                self._mutate(error="更新包校验失败，请重新下载")
                self._set_state("download_failed")
                self._add_message("error", f"⚠ {saved.get('latestVersion') or '新版本'} 更新包校验失败", "文件缺失或已损坏，请重新下载。")
                self.zip_path.unlink(missing_ok=True)
            self._persist(force=True)
            return
        if state in ("available", "download_failed"):
            self._mutate(
                latestVersion=saved.get("latestVersion", ""),
                changelog=saved.get("changelog", ""),
                downloadUrl=saved.get("downloadUrl", ""),
                sha256=saved.get("sha256", ""),
                size=int(saved.get("size") or 0),
            )
            self._set_state("available" if state == "available" else "download_failed")
            self._persist(force=True)
            if state == "available":
                self._start_download_thread()
            return
        if state in (*HANDOFF_STATES, "backing_up", "installing", "restarting", "completed", "failed_rolled_back", "failed_recovery_required"):
            # 上一次安装由独立更新器完成，结果以 update-result.json 为准。
            try:
                result = json.loads(self.result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result = None
            result_key = json.dumps(
                {key: result.get(key) for key in ("result", "from", "to", "stage", "error", "rolledBack", "time")},
                ensure_ascii=False,
                sort_keys=True,
            ) if result else ""
            already_handled = bool(result_key and result_key == self._state["handledResultKey"])
            if result and result.get("result") == "success" and result.get("to") == self.version:
                if not already_handled:
                    self._add_message("success", f"✓ 已成功更新到 v{self.version}", "助手已在新版本上重新启动。")
                self._set_state("completed")
            elif result and result.get("result") == "failed" and result.get("rolledBack") is False:
                if not already_handled:
                    self._add_message("error", "更新未完成且未能自动恢复", f"{result.get('error', '')}\n请查看日志或手动恢复备份。")
                self._set_state("failed_recovery_required")
            elif result and result.get("result") == "failed":
                if not already_handled:
                    self._add_message("error", f"⚠ v{result.get('to', '')} 安装失败", f"已恢复到 v{result.get('from', self.version)}。{result.get('error', '')}")
                self._set_state("failed_rolled_back")
            else:
                self._set_state("idle")
            if result_key:
                self._mutate(handledResultKey=result_key)
            self._persist(force=True)
            return
        self._persist(force=True)

    def start_auto_check(self, delay: float = 5.0) -> None:
        def delayed() -> None:
            self._sleep(delay)
            if not self._stop.is_set():
                self.check(manual=False)

        threading.Thread(target=delayed, name="update-auto-check", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    # ---- 检查更新 ---------------------------------------------------

    def check(self, manual: bool = False) -> dict[str, Any]:
        prior_state = self._state["state"]
        prior_latest = self._state.get("latestVersion", "")
        if self._state_in("checking", "downloading", "verifying") or self._state_in(*HANDOFF_STATES):
            return {"ok": True, "skipped": True}
        self._set_state("checking")
        self._persist(force=True)
        try:
            if not self.release_api:
                raise UpdateError("未配置更新仓库；请设置 YXY_UPDATE_REPOSITORY 或使用正式发布包")
            release = self.transport.get_json(self.release_api, {"Accept": "application/vnd.github+json"})
            asset = next(item for item in release.get("assets", []) if str(item.get("name", "")).lower() == "manifest.json")
            manifest = parse_manifest(self.transport.get_json(asset["browser_download_url"]))
        except (StopIteration, UpdateError, ValueError, requests.RequestException, OSError, KeyError) as error:
            # 检查失败时保留此前的 download_failed / available 状态，避免丢失失败记录；
            # 已下载待安装的包同样必须保留，网络波动不能孤儿化用户的更新。
            self._set_state(prior_state if prior_state in ("download_failed", "available", "idle", "ready_to_install") else "idle")
            self._mutate(error=f"检查更新失败：{error}")
            self._persist(force=True)
            if manual:
                self._add_message("error", "检查更新失败", str(error))
                self._persist(force=True)
            return {"ok": False, "error": str(error)}
        newer = compare_versions(manifest["version"], self.version) > 0
        self._mutate(
            latestVersion=manifest["version"],
            changelog=manifest["changelog"],
            publishedAt=manifest["publishedAt"],
            downloadUrl=manifest["url"],
            sha256=manifest["sha256"],
            size=manifest["size"],
            error="",
        )
        if not newer:
            self._set_state("idle")
            self._persist(force=True)
            if manual:
                self._add_message("info", f"已是最新版本 v{self.version}", "当前没有可用的更新。")
                self._persist(force=True)
            return {"ok": True, "updateAvailable": False}
        if prior_state == "ready_to_install" and manifest["version"] == prior_latest:
            # 待安装的正是这个版本，不要因为重复检查而丢弃已下载的更新包。
            self._set_state("ready_to_install")
            return {"ok": True, "updateAvailable": True, "version": manifest["version"], "skipped": True}
        self._set_state("available")
        self._add_message("info", f"发现新版本 v{manifest['version']}", manifest["changelog"][:200])
        self._persist(force=True)
        self._start_download_thread()
        return {"ok": True, "updateAvailable": True, "version": manifest["version"]}

    def _state_in(self, *states: str) -> bool:
        with self._lock:
            return self._state["state"] in states

    # ---- 下载 -------------------------------------------------------

    def start_download(self) -> dict[str, Any]:
        if self._state_in("downloading", "verifying") or self._state_in(*HANDOFF_STATES):
            return {"ok": True, "skipped": True}
        if not self._state.get("downloadUrl"):
            threading.Thread(target=lambda: self.check(manual=True), name="update-check", daemon=True).start()
            return {"ok": True, "checking": True}
        if self._state_in("download_failed"):
            self._set_state("available")
        self._start_download_thread()
        return {"ok": True}

    def _start_download_thread(self) -> None:
        with self._lock:
            if self._download_thread and self._download_thread.is_alive():
                return
            self._download_thread = threading.Thread(target=self._download_worker, name="update-download", daemon=True)
        self._download_thread.start()

    def _download_worker(self) -> None:
        url = self._state.get("downloadUrl", "")
        expected_sha = self._state.get("sha256", "")
        expected_size = int(self._state.get("size") or 0)
        latest = self._state.get("latestVersion", "")
        attempts = 0
        self._set_state("downloading")
        self._persist(force=True)
        while True:
            attempts += 1
            try:
                self.transport.download(
                    url,
                    self.part_path,
                    expected_size=expected_size,
                    progress=self._download_progress,
                    stop=self._stop,
                )
            except UpdateError as error:
                if self._stop.is_set():
                    return
                if attempts >= MAX_DOWNLOAD_RETRIES:
                    self._fail_download(latest, str(error))
                    return
                wait = RETRY_BACKOFF_SECONDS[min(attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                self._mutate(error=f"下载失败（{attempts}/{MAX_DOWNLOAD_RETRIES}），{wait} 秒后自动重试：{error}")
                self._persist(force=True)
                for _ in range(int(wait * 5)):
                    if self._stop.is_set():
                        return
                    self._sleep(0.2)
                continue
            self._set_state("verifying")
            self._persist(force=True)
            if self._verify_zip(part=True):
                self.part_path.replace(self.zip_path)
                self._mutate(downloaded=self.zip_path.stat().st_size, total=self.zip_path.stat().st_size, error="")
                self._set_state("ready_to_install")
                self._add_message("success", f"✓ v{latest} 已下载并通过完整性校验", "可以随时安装；安装时程序会自动重启。")
                self._persist(force=True)
                return
            if self._stop.is_set():
                return
            self.part_path.unlink(missing_ok=True)
            self._fail_download(latest, "SHA-256 校验失败，更新包已损坏")
            return

    def _fail_download(self, latest: str, error: str) -> None:
        self._set_state("download_failed")
        self._mutate(error=error)
        self._add_message(
            "error",
            f"⚠ v{latest or '?'} 下载失败",
            f"{error}\n已下载：{self._format_bytes(self._state.get('downloaded', 0))}"
            + (f" / {self._format_bytes(self._state.get('size', 0))}" if self._state.get("size") else "")
            + f"\n失败时间：{self._now()[11:16]}",
        )
        self._persist(force=True)

    def _verify_zip(self, part: bool = False) -> bool:
        path = self.part_path if part else self.zip_path
        if not path.is_file():
            return False
        expected = self._state.get("sha256", "")
        if not expected:
            return False
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    return False
        except (zipfile.BadZipFile, OSError):
            return False
        return sha256_file(path) == expected

    @staticmethod
    def _format_bytes(value: float) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} GB"

    def _download_progress(self, received: int, total: int) -> None:
        self._mutate(downloaded=received, total=total or self._state.get("size", 0))
        self._persist()

    # ---- 安装移交 ---------------------------------------------------

    def _installer_command(self, workdir: Path) -> list[str]:
        """独立更新器的启动命令。

        冻结模式不假设系统存在 python.exe/pythonw.exe：把打包在 _internal
        中的更新器目录（onedir，单进程、无引导父子耦合）整体复制到临时
        目录后运行——复制是为了避免更新器替换 _internal 时占用自身文件。
        开发模式把 updater_installer.py 复制到临时目录后用当前解释器运行。
        """
        if is_frozen():
            source = resource_root() / "updater"
            exe = source / "updater.exe"
            if not exe.is_file():
                raise UpdateError("发行包缺少内部更新器（_internal/updater/updater.exe），无法安装更新")
            target = workdir / "updater"
            shutil.copytree(source, target)
            return [str(target / "updater.exe")]
        installer = workdir / "updater_installer.py"
        shutil.copyfile(Path(__file__).resolve().parent / "updater_installer.py", installer)
        python = Path(sys.base_prefix) / "pythonw.exe" if sys.platform == "win32" else Path(sys.executable)
        if not python.is_file():
            python = Path(sys.executable)
        return [str(python), str(installer)]

    def install(self) -> dict[str, Any]:
        if self._state_in(*HANDOFF_STATES):
            return {"ok": True, "skipped": True}
        if self._state["state"] != "ready_to_install":
            return {"ok": False, "error": "当前没有已下载待安装的更新包"}
        if self.failure_count() >= 3:
            return {"ok": False, "error": "已连续失败 3 次，自动安装已停止；请查看日志或重新下载"}
        with self._lock:
            self._install_thread = threading.Thread(target=self._install_worker, name="update-install", daemon=True)
        self._install_thread.start()
        return {"ok": True}

    def _install_worker(self) -> None:
        self._set_state("handoff")
        self._persist(force=True)
        payload = {
            "installDir": str(self.root),
            "zip": str(self.zip_path),
            "sha256": self._state.get("sha256", ""),
            "expectedVersion": self._state.get("latestVersion", ""),
            "fromVersion": self.version,
            "readyFile": "",
            "ports": [8765, self._frontend_port_override or self.frontend_port()],
            "appMutex": "Local\\YxyAssistant.App",
            "updatingMutex": "Local\\YxyAssistant.Updating",
        }
        workdir = Path(tempfile.mkdtemp(prefix="yxy-updater-"))
        try:
            ready_file = workdir / "updater-ready.json"
            progress_file = workdir / "updater-progress.json"
            payload["readyFile"] = str(ready_file)
            payload["progressFile"] = str(progress_file)
            payload["installerLog"] = str(self.update_dir / "updater.log")
            (workdir / "payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            command = self._installer_command(workdir)
            process = subprocess.Popen(
                [*command, "--payload", str(workdir / "payload.json")],
                cwd=str(workdir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, UpdateError) as error:
            self._set_state("ready_to_install")
            self._mutate(error=f"无法启动独立更新器：{error}")
            self._persist(force=True)
            self.emit_event("UPDATE_INSTALL_FAILED", "error", "update", f"无法启动独立更新器：{error}")
            return
        ready = wait_for_ready(ready_file, timeout=30)
        if ready is None:
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
            except OSError:
                pass
            self._set_state("ready_to_install")
            self._mutate(error="更新器未在预期时间内就绪，已取消本次安装")
            self._persist(force=True)
            self.emit_event("UPDATE_INSTALL_FAILED", "error", "update", "更新器未就绪，已取消本次安装")
            return
        self._set_state("waiting_for_exit")
        self._persist(force=True)
        self.emit_event("UPDATE_HANDOFF", "success", "update", "正在移交给更新器……")
        # 前端收到 handoff 状态后调用 shutdown_for_update；若前端失联，
        # 由看门狗兜底触发同样的关闭流程。
        threading.Thread(target=self._exit_watchdog, name="update-exit-watchdog", daemon=True).start()

    def _exit_watchdog(self) -> None:
        for _ in range(150):
            if self._stop.is_set():
                return
            self._sleep(1)
        self.shutdown_for_update(lambda: None)

    def shutdown_for_update(self, stop_backend: Callable[[], None]) -> dict[str, Any]:
        """移交完成后的主程序退出流程：关标签页 → 停签到/刷课 → 停服务。"""
        if not self._state_in("waiting_for_exit"):
            return {"ok": False, "error": "独立更新器尚未就绪，已取消退出"}
        try:
            closed = close_assistant_tabs(self.debug_port(), self.base_url)
            self.emit_event("UPDATE_TAB_CLOSED", "info", "update", f"已关闭助手标签页（{closed} 个）")
        except Exception as error:  # noqa: BLE001 - 关标签失败不能阻止退出
            self.emit_event("UPDATE_TAB_CLOSE_FAILED", "warning", "update", f"关闭助手标签页失败：{error}")
        try:
            stop_backend()
        except Exception as error:  # noqa: BLE001
            self.emit_event("UPDATE_STOP_FAILED", "warning", "update", f"停止后台任务失败：{error}")
        return {"ok": True}
