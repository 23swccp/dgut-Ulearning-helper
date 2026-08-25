"""Tauri 开发期桥接：通过标准输入/输出把 React 命令转给现有签到后端。"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import threading
import traceback
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any

# 开发目录结构：Codex_Output/tauri-react/sidecar/bridge.py。
# 打包后不能往 Program Files 写配置，因此改用当前用户的 AppData 目录。
if getattr(sys, "frozen", False):
    ROOT = Path(os.environ.get("APPDATA", Path.home())) / "优学院签到助手"
    ROOT.mkdir(parents=True, exist_ok=True)
else:
    ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(ROOT))

from yxy_backend import SignBackend  # noqa: E402


DEBUG_LOG = ROOT / "sidecar-debug.log"
EVENTS: deque[dict[str, str]] = deque(maxlen=1000)
EVENT_LOCK = threading.Lock()


def diagnostic(message: str) -> None:
    """仅记录本地启动诊断，不写入账号、密码或登录令牌。"""
    try:
        with DEBUG_LOG.open("a", encoding="utf-8") as file:
            file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


diagnostic(f"sidecar 启动：executable={sys.executable} cwd={Path.cwd()}")


def send(payload: dict[str, Any]) -> None:
    # 发布版的 stdout 仅用于可选日志转发；某些 Windows 环境中它会在
    # 后台线程提前失效。日志转发失败不能影响签到或浏览器启动。
    try:
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    except OSError as error:
        diagnostic(f"stdout 日志转发失败（已忽略）：{error}")


def emit(message: str, kind: str) -> None:
    diagnostic(f"[{kind}] {message}")
    with EVENT_LOCK:
        EVENTS.append({"message": message, "kind": kind})
    send({"type": "log", "message": message, "kind": kind})


def take_events() -> list[dict[str, str]]:
    """供发布版前端轮询读取日志，避免依赖不稳定的 stdout 管道。"""
    with EVENT_LOCK:
        items = list(EVENTS)
        EVENTS.clear()
    return items


backend = SignBackend(emit=emit, root=ROOT)


def courses() -> list[dict[str, Any]]:
    return [{"id": course.id, "name": course.name, "teacherName": course.teacher_name} for course in backend.courses]


def handle(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    diagnostic(f"收到命令：{command}")
    if command == "get_events":
        return {"ok": True, "events": take_events()}
    if command == "load_saved_courses":
        return {"ok": backend.load_saved_courses(), "courses": courses()}
    if command == "start_browser":
        def launch() -> None:
            diagnostic("开始执行浏览器启动流程")
            try:
                result = backend.start_browser(str(payload.get("url", "")))
                diagnostic(f"浏览器启动流程结束：{result}")
            except Exception as error:
                diagnostic(f"浏览器启动异常：{error}\n{traceback.format_exc()}")
                emit(f"浏览器启动异常：{error}", "warn")
        threading.Thread(target=launch, daemon=False).start()
        return {"ok": True}
    if command == "load_session_and_courses":
        return {"ok": backend.load_session_and_courses(), "courses": courses()}
    if command == "select_course":
        course = backend.select_course(str(payload.get("query", "")))
        return {"ok": course is not None, "course": {"id": course.id, "name": course.name, "teacherName": course.teacher_name} if course else None}
    if command == "clear_selected_course":
        backend.clear_selected_course()
        return {"ok": True}
    if command == "start_monitor":
        if backend.selected_course is None:
            return {"ok": False, "error": "尚未选择课程"}
        backend.start_monitor()
        return {"ok": True}
    if command == "stop_monitor":
        backend.stop_monitor()
        return {"ok": True}
    if command == "get_settings":
        return {"ok": True, "config": backend.config.to_mapping()}
    if command == "update_settings":
        backend.update_settings(**payload)
        return {"ok": True, "config": backend.config.to_mapping()}
    return {"ok": False, "error": f"未知命令：{command}"}


def main() -> None:
    emit("Python 签到后端已连接。", "success")
    for line in sys.stdin:
        try:
            request = json.loads(line)
            result = handle(str(request.get("command", "")), request.get("payload") or {})
            send({"type": "result", "id": request.get("id"), "result": result})
        except Exception as error:  # 保持通信进程可用，并将错误显示在终端中
            send({"type": "result", "id": request.get("id"), "result": {"ok": False, "error": str(error)}})


def serve(port: int) -> None:
    """发布版使用本机回环端口通信，避免 Windows 子进程 stdin 管道被提前关闭。"""
    emit("Python 签到后端已连接。", "success")
    diagnostic(f"监听本机端口：{port}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port))
        server.listen()
        while True:
            connection, _ = server.accept()
            with connection:
                raw = b""
                while chunk := connection.recv(65536):
                    raw += chunk
                try:
                    request = json.loads(raw.decode("utf-8"))
                    result = handle(str(request.get("command", "")), request.get("payload") or {})
                except Exception as error:
                    result = {"ok": False, "error": str(error)}
                connection.sendall(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--server":
        serve(int(sys.argv[2]))
    else:
        main()
