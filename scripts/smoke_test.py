"""发行包启动冒烟测试：只验证本地服务、静态资源与生命周期。

- 不访问真实课程、不提交签到、不执行真实刷课。
- 用法：python scripts/smoke_test.py [发行目录]
  不传参数时自动选择 release/ 下唯一的 dgut-bot-v*-windows-x64 目录。

验证项：
1. 从发行目录直接启动 dgut-bot.exe（无源码参与）。
2. 本地后端服务在 127.0.0.1:8765 就绪。
3. 首页 HTML 可加载；web/dist 的 JS/CSS 正常返回。
4. 前后端 API 可通信（get_app_info/get_settings）。
5. 能检测到 Edge 或 Chrome。
6. 配置写入发行目录而不是 PyInstaller 临时目录。
7. 关闭后端口释放；再次启动不会因“已有实例”卡死。
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8765"
HEALTH_TIMEOUT = 90.0
PORT_CLOSE_TIMEOUT = 20.0


class SmokeError(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)
    print(f"  [OK] {message}")


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def wait_health(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError as error:
            last_error = error
        time.sleep(0.5)
    raise SmokeError(f"本地服务未在 {timeout:.0f} 秒内就绪：{last_error}")


def http_get(path: str) -> tuple[int, bytes, dict[str, str]]:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as response:
        return response.status, response.read(), dict(response.headers)


def post_command(command: str, payload: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"{BASE}/api/command",
        data=json.dumps({"command": command, "payload": payload or {}}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def launch(exe: Path) -> subprocess.Popen:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(
        [str(exe)],
        cwd=str(exe.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


def close_debug_browser(config: dict, port_was_free_before_launch: bool) -> None:
    """通过 CDP 关闭小皮卡启动的调试浏览器，只影响独立 profile。

    启动前 9222 已被占用时不做关闭，避免误关用户自己的调试浏览器。
    """
    if not port_was_free_before_launch:
        print("  （调试端口在启动前已被占用，跳过调试浏览器关闭）")
        return
    import requests

    port = int(config.get("debug_port", 9222))
    try:
        info = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2).json()
        ws_url = info.get("webSocketDebuggerUrl")
        if not ws_url:
            return
        from websocket import create_connection

        ws = create_connection(ws_url, timeout=5)
        try:
            ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
            try:
                ws.recv()
            except Exception:  # noqa: BLE001 - 浏览器可能直接断开
                pass
        finally:
            ws.close()
        time.sleep(2)
    except Exception:  # noqa: BLE001 - 调试浏览器不存在时静默跳过
        pass


def wait_port_closed(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_open(8765):
            return
        time.sleep(0.5)
    raise SmokeError("关闭命令后本地服务端口仍未释放，后台服务可能残留")


def shutdown_service() -> None:
    try:
        post_command("shutdown_app")
    except OSError:
        pass
    wait_port_closed(PORT_CLOSE_TIMEOUT)


def expected_version() -> str:
    sys.path.insert(0, str(ROOT))
    from version import APP_VERSION

    return APP_VERSION


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        release_dir = Path(argv[1]).resolve()
    else:
        candidates = sorted((ROOT / "release").glob("dgut-bot-v*-windows-x64"))
        if len(candidates) != 1:
            raise SystemExit(f"无法定位唯一的发行目录（找到 {len(candidates)} 个）；请传入路径")
        release_dir = candidates[0].resolve()
    print(f"发行目录：{release_dir}")

    exe = release_dir / "dgut-bot.exe"
    check(exe.is_file(), "dgut-bot.exe 存在")
    check((release_dir / "_internal").is_dir(), "_internal 资源目录存在")
    index = release_dir / "web" / "dist" / "index.html"
    check(index.is_file(), "web/dist/index.html 存在")

    try:
        debug_port_was_free = not port_open(int(9222))
        print("首次启动…")
        launch(exe)
        wait_health(HEALTH_TIMEOUT)
        print("  [OK] 本地后端服务启动")

        status, body, _headers = http_get("/")
        check(status == 200 and b'id="root"' in body, "首页 HTML 可加载")

        assets = sorted((release_dir / "web" / "dist" / "assets").iterdir())
        js = next((a for a in assets if a.suffix == ".js"), None)
        css = next((a for a in assets if a.suffix == ".css"), None)
        check(js is not None and css is not None, "web/dist 存在 JS 与 CSS 资源")
        for asset in (js, css):
            status, _body, headers = http_get(f"/assets/{asset.name}")
            content_type = headers.get("Content-Type", "")
            ok_type = "javascript" in content_type or "css" in content_type
            check(status == 200 and ok_type, f"静态资源 /assets/{asset.name} 返回 {content_type}")

        info = post_command("get_app_info")
        check(info.get("ok") and info["info"]["version"] == expected_version(), "API 通信正常且版本一致")
        settings = post_command("get_settings")
        check(settings.get("ok"), "get_settings 可用")

        detected = post_command("detect_browsers").get("browsers", [])
        check(bool(detected), f"检测到 Chromium 浏览器：{[item['name'] for item in detected]}")

        check((release_dir / "config.json").is_file(), "config.json 写入发行目录（而非临时目录）")
        check((release_dir / "browser-launcher.log").is_file(), "启动日志写入发行目录")

        print("关闭服务…")
        close_debug_browser(settings.get("config", {}), debug_port_was_free)
        shutdown_service()
        print("  [OK] 后台服务已退出，端口 8765 已释放")

        print("再次启动（验证不误报已有实例）…")
        launch(exe)
        wait_health(HEALTH_TIMEOUT)
        print("  [OK] 第二次启动正常")
        shutdown_service()
    finally:
        if port_open(8765):
            try:
                post_command("shutdown_app")
            except OSError:
                pass
            try:
                wait_port_closed(PORT_CLOSE_TIMEOUT)
            except SmokeError:
                pass

    print("冒烟测试全部通过。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except SmokeError as error:
        print(f"冒烟测试失败：{error}", file=sys.stderr)
        raise SystemExit(1)
