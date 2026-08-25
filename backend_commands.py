"""浏览器前端与 Python 后端之间的统一命令层。"""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from typing import Any

from yxy_backend import SignBackend


ROOT = Path(__file__).resolve().parent
EVENTS: deque[dict[str, str]] = deque(maxlen=1000)
EVENT_LOCK = threading.Lock()


def emit(message: str, kind: str) -> None:
    with EVENT_LOCK:
        EVENTS.append({"message": message, "kind": kind})


def take_events() -> list[dict[str, str]]:
    with EVENT_LOCK:
        items = list(EVENTS)
        EVENTS.clear()
    return items


backend = SignBackend(emit=emit, root=ROOT)


def courses() -> list[dict[str, Any]]:
    return [{"id": course.id, "name": course.name, "teacherName": course.teacher_name} for course in backend.courses]


def handle(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    if command == "get_events":
        return {"ok": True, "events": take_events()}
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
        return {"ok": backend.load_session_and_courses(), "courses": courses()}
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
        backend.start_monitor()
        return {"ok": True}
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
    if command == "get_settings":
        return {"ok": True, "config": backend.config.to_mapping()}
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
    return {"ok": False, "error": f"未知命令：{command}"}
