"""浏览器版的本机命令服务；仅监听本机回环地址。

除了 /api 命令接口，还直接托管 web/dist 静态前端（发布包模式）；
开发模式下前端仍由 Vite 提供，这里的静态托管不会生效。
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from app_paths import frontend_dist
from backend_commands import backend, handle, update_manager


SHUTDOWN_EVENT = threading.Event()
CLIENT_CLOSED_EVENT = threading.Event()
CLIENT_STATE_LOCK = threading.Lock()
CLIENT_LAST_SEEN = 0.0
STATIC_ROOT = frontend_dist()
DEFAULT_FRONTEND_ORIGIN = "http://127.0.0.1:1420"
ALLOWED_FRONTEND_PORTS = {*range(8765, 8785), *range(1420, 1440)}


def allowed_cors_origin(origin: str | None) -> str:
    """只回显受信任的本机前端 Origin，拒绝包含关键字的恶意域名。"""
    if not origin:
        return DEFAULT_FRONTEND_ORIGIN
    try:
        parsed = urlparse(origin)
        valid = (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and parsed.port in ALLOWED_FRONTEND_PORTS
            and not parsed.username
            and not parsed.password
            and parsed.path in ("", "/")
            and not parsed.params
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        valid = False
    return origin if valid else DEFAULT_FRONTEND_ORIGIN


def mark_client_active() -> None:
    global CLIENT_LAST_SEEN
    with CLIENT_STATE_LOCK:
        CLIENT_LAST_SEEN = time.monotonic()
    CLIENT_CLOSED_EVENT.clear()


def client_last_seen() -> float:
    with CLIENT_STATE_LOCK:
        return CLIENT_LAST_SEEN


def reset_client_state() -> None:
    global CLIENT_LAST_SEEN
    with CLIENT_STATE_LOCK:
        CLIENT_LAST_SEEN = 0.0
    CLIENT_CLOSED_EVENT.clear()


def stop_backend_tasks() -> None:
    """更新退出前停止签到监测与刷课；任何失败都不能阻止移交。"""
    try:
        backend.stop_course_helper()
    except Exception:  # noqa: BLE001
        pass
    try:
        backend.stop_monitor()
    except Exception:  # noqa: BLE001
        pass


class LocalApiHandler(BaseHTTPRequestHandler):
    """把浏览器请求转给统一后端命令层，并托管发布包的静态前端。"""

    server_version = "YxyLocalApi/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if self.path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "未找到接口"})
            return
        self._serve_static()

    def _serve_static(self) -> None:
        index = STATIC_ROOT / "index.html"
        if not index.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "未找到接口"})
            return
        relative = self.path.lstrip("/").split("?", 1)[0].split("#", 1)[0]
        target = (STATIC_ROOT / relative).resolve() if relative else index
        try:
            target.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "未找到接口"})
            return
        if not target.is_file():
            target = index
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        try:
            body = target.read_bytes()
        except OSError:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "读取静态文件失败"})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") or content_type == "application/javascript" else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if target == index else "max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/heartbeat":
            mark_client_active()
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if self.path == "/api/client-closed":
            CLIENT_CLOSED_EVENT.set()
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if self.path != "/api/command":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "未找到接口"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise ValueError("请求过大")
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            command = str(request.get("command", ""))
            payload = request.get("payload") or {}
            if not isinstance(payload, dict):
                raise ValueError("payload 必须是对象")
            mark_client_active()
            if command == "shutdown_app":
                self._send_json(HTTPStatus.OK, {"ok": True})
                SHUTDOWN_EVENT.set()
                return
            if command == "shutdown_for_update":
                # 移交流程：停签到/刷课 → 精确关闭助手标签页 → 停服务退出。
                if not update_manager.snapshot()["readyForExit"]:
                    self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": "独立更新器尚未就绪，已取消退出"})
                    return
                self._send_json(HTTPStatus.OK, {"ok": True})

                def handoff_exit() -> None:
                    update_manager.shutdown_for_update(stop_backend_tasks)
                    SHUTDOWN_EVENT.set()

                threading.Thread(target=handoff_exit, name="update-exit", daemon=True).start()
                return
            self._send_json(HTTPStatus.OK, handle(command, payload))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)})
        except Exception as error:  # 保持服务进程可用，把错误显示在浏览器终端中。
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(error)})

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        origin = allowed_cors_origin(self.headers.get("Origin"))
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        """浏览器轮询不写入控制台，保留启动器输出可读性。"""


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8765), LocalApiHandler)
    print("浏览器版本地服务已启动：http://127.0.0.1:8765", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
