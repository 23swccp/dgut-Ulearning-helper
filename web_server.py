"""浏览器版的本机命令服务；仅监听本机回环地址。"""

from __future__ import annotations

import json
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SIDECAR_DIR = ROOT / "tauri-react" / "sidecar"
sys.path.insert(0, str(SIDECAR_DIR))

from bridge import backend, handle  # noqa: E402,F401


SHUTDOWN_EVENT = threading.Event()
CLIENT_CLOSED_EVENT = threading.Event()
CLIENT_STATE_LOCK = threading.Lock()
CLIENT_LAST_SEEN = 0.0


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


class LocalApiHandler(BaseHTTPRequestHandler):
    """把浏览器命令转给同一套 sidecar 命令处理逻辑。"""

    server_version = "YxyLocalApi/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "未找到接口"})

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
            self._send_json(HTTPStatus.OK, handle(command, payload))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)})
        except Exception as error:  # 保持服务进程可用，把错误显示在浏览器终端中。
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(error)})

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:1420")
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
