"""浏览器前端与 Python 后端之间的统一命令层。"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime
from typing import Any
from uuid import uuid4

from app_paths import data_root
from yxy_backend import SignBackend
from yxy_updater import UpdateManager
from version import APP_NAME, APP_VERSION, GITHUB_REPO, RELEASE_API


# 配置、登录缓存、浏览器资料、日志与更新状态都属于用户数据，统一写入程序目录。
ROOT = data_root()
class EventBuffer:
    """线程安全的有限事件流；读取使用游标，不会消费事件。"""

    def __init__(self, maxlen: int = 1500) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0
        self._default_session_id = f"app-{datetime.now().astimezone():%Y%m%d}-{uuid4().hex[:8]}"

    def emit_event(
        self,
        code: str,
        level: str,
        category: str,
        message: str,
        *,
        session_id: str = "",
        page: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                "sessionId": session_id or self._default_session_id,
                "code": str(code),
                "level": str(level),
                "category": str(category),
                "message": str(message),
                "page": dict(page or {}),
                "data": dict(data or {}),
            }
            self._events.append(event)
            return dict(event)

    def get_events(self, after_seq: int = 0) -> dict[str, Any]:
        with self._lock:
            events = [dict(event) for event in self._events if int(event["seq"]) > after_seq]
            return {"events": events, "latestSeq": self._seq}


EVENT_BUFFER = EventBuffer()


def emit(message: str, kind: str) -> None:
    """旧字符串日志兼容入口；课程内部日志默认归入详细日志。"""
    is_course = kind.startswith("course:") or message.startswith(("[刷课]", "[yxy]"))
    raw_level = kind.split(":", 1)[-1]
    level = {"muted": "info", "warn": "warning"}.get(raw_level, raw_level)
    EVENT_BUFFER.emit_event(
        "DEBUG_LOG" if is_course else "LEGACY_LOG",
        level if level in {"info", "success", "warning", "error"} else "info",
        "debug" if is_course else "general",
        message,
        data={"legacyKind": kind},
    )


def emit_event(code: str, level: str, category: str, message: str, **kwargs: Any) -> dict[str, Any]:
    return EVENT_BUFFER.emit_event(code, level, category, message, **kwargs)


backend = SignBackend(emit=emit, emit_event=emit_event, root=ROOT)

# 应用内自动更新：状态持久化在 .update/state.json，前端通过 get_update_status 轮询。
update_manager = UpdateManager(
    ROOT,
    version=APP_VERSION,
    release_api=RELEASE_API,
    emit_event=emit_event,
    debug_port=lambda: backend.config.debug_port,
)
update_manager.restore()


def courses() -> list[dict[str, Any]]:
    return [{"id": course.id, "name": course.name, "teacherName": course.teacher_name} for course in backend.courses]


def handle(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    if command == "get_events":
        try:
            after_seq = max(0, int(payload.get("afterSeq", 0)))
        except (TypeError, ValueError):
            after_seq = 0
        return {"ok": True, **EVENT_BUFFER.get_events(after_seq)}
    if command == "load_saved_courses":
        return {"ok": backend.load_saved_courses(), "courses": courses()}
    if command == "start_browser":
        url = str(payload.get("url", ""))

        def launch() -> None:
            try:
                backend.start_browser(url)
            except Exception as error:
                emit(f"浏览器启动异常：{error}", "warn")

        threading.Thread(target=launch, name="browser-launch", daemon=True).start()
        return {"ok": True}
    if command == "load_session_and_courses":
        try:
            wait_seconds = max(1, min(5, int(payload.get("waitSeconds", 1))))
        except (TypeError, ValueError):
            wait_seconds = 1
        automatic = bool(payload.get("automatic", False))
        return {
            "ok": backend.load_session_and_courses(wait_seconds=wait_seconds, automatic=automatic),
            "courses": courses(),
        }
    if command == "select_course":
        course = backend.select_course(str(payload.get("query", "")))
        value = {"id": course.id, "name": course.name, "teacherName": course.teacher_name} if course else None
        return {"ok": course is not None, "course": value}
    if command == "clear_selected_course":
        backend.clear_selected_course()
        return {"ok": True}
    if command == "start_monitor":
        if backend.selected_course is None:
            return {"ok": False, "error": "尚未选择课程"}
        started = backend.start_monitor()
        return {"ok": started, "error": "签到监测已在运行" if not started else ""}
    if command == "stop_monitor":
        backend.stop_monitor()
        return {"ok": True}
    if command == "start_course_helper":
        return {"ok": backend.start_course_helper()}
    if command == "stop_course_helper":
        backend.stop_course_helper()
        return {"ok": True}
    if command == "set_course_speed":
        try:
            backend.set_course_speed(float(payload.get("rate", backend.config.course_playback_rate)))
            return {"ok": True}
        except (TypeError, ValueError) as error:
            return {"ok": False, "error": str(error)}
    if command == "get_course_helper_status":
        return {"ok": True, "status": backend.course_helper_status()}
    if command == "get_settings":
        return {"ok": True, "config": backend.config.to_mapping()}
    if command == "detect_browsers":
        return {"ok": True, "browsers": backend.detect_browsers()}
    if command == "get_account_login_status":
        return {"ok": True, "account": backend.account_login_status()}
    if command == "update_account_login":
        ok = backend.update_account_login(
            str(payload.get("username", "")),
            str(payload.get("password", "")),
            bool(payload.get("enabled")),
        )
        return {"ok": ok, "account": backend.account_login_status()}
    if command == "update_settings":
        backend.update_settings(**payload)
        return {"ok": True, "config": backend.config.to_mapping()}
    if command == "open_log":
        try:
            path = backend.open_log_file(str(payload.get("path", "")))
            return {"ok": True, "path": str(path)}
        except OSError as error:
            return {"ok": False, "error": f"打开日志失败：{error}"}
    if command == "get_app_info":
        return {"ok": True, "info": {"appName": APP_NAME, "version": APP_VERSION, "repo": GITHUB_REPO}}
    if command == "get_update_status":
        return {"ok": True, "update": update_manager.snapshot()}
    if command == "check_update":
        def check() -> None:
            update_manager.check(manual=True)

        threading.Thread(target=check, name="update-check", daemon=True).start()
        return {"ok": True}
    if command == "download_update":
        return update_manager.start_download()
    if command == "install_update":
        return update_manager.install()
    if command == "mark_update_read":
        update_manager.mark_read()
        return {"ok": True}
    if command == "ack_update_failure":
        update_manager.ack_failure()
        return {"ok": True}
    return {"ok": False, "error": f"未知命令：{command}"}
