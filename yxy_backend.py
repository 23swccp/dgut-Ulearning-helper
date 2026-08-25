"""桌面 UI 的本地后端：浏览器登录、课程读取和课堂活动轮询。"""

from __future__ import annotations

import json
import os
import threading
import time
from urllib.parse import quote
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable

import requests
from websocket import create_connection


LMS_BASE = "https://lms.dgut.edu.cn/courseapi"
APP_BASE = "https://application.dgut.edu.cn/classroomapi"


@dataclass
class AppConfig:
    browser_path: str = ""
    browser_name: str = ""
    debug_port: int = 9222
    poll_interval: int = 5
    save_log: bool = True
    log_path: str = ""
    lat: float = 23.0432
    lng: float = 113.3993
    address: str = "东莞理工学院"
    # 课件学习辅助：仅控制播放、文档阅读与章节衔接；测验由用户自行完成。
    course_playback_rate: float = 8.0
    course_auto_dismiss_dialog: bool = True
    course_document_scroll_enabled: bool = True
    course_document_scroll_interval: float = 3.0
    course_document_scroll_speed: float = 3.0

    @classmethod
    def from_mapping(cls, values: dict, root: Path) -> "AppConfig":
        defaults = cls(log_path=str(root / "签到记录.md"))
        allowed = {key: value for key, value in values.items() if key in defaults.__dataclass_fields__}
        return cls(**(asdict(defaults) | allowed))

    def to_mapping(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Course:
    id: int
    name: str
    teacher_name: str = "未知教师"
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_api(cls, data: dict) -> "Course":
        return cls(id=int(data["id"]), name=data.get("name", "未命名课程"), teacher_name=data.get("teacherName", "未知教师"), raw=data)


@dataclass(frozen=True)
class Classroom:
    id: int
    title: str
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_api(cls, data: dict) -> "Classroom":
        return cls(id=int(data["id"]), title=data.get("title", "未命名课堂"), raw=data)


@dataclass(frozen=True)
class Activity:
    relation_id: int
    title: str
    relation_type: int | None
    score_type: int | None
    state: int | None
    status: int | None
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_api(cls, data: dict) -> "Activity":
        return cls(relation_id=int(data["relationId"]), title=data.get("title", "未命名活动"), relation_type=data.get("relationType"), score_type=data.get("scoreType"), state=data.get("state"), status=data.get("status"), raw=data)


class MonitorState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"


class ApiClient:
    """集中处理 HTTP 超时、状态码与 JSON 错误。"""

    def __init__(self, headers: dict) -> None:
        self.headers = headers

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("headers", self.headers)
        kwargs.setdefault("timeout", 10)
        response = requests.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def json(self, method: str, url: str, **kwargs) -> dict:
        try:
            return self.request(method, url, **kwargs).json()
        except requests.RequestException as error:
            raise RuntimeError(f"网络或认证错误：{error}") from error
        except ValueError as error:
            raise RuntimeError("服务返回了无法解析的数据") from error

# 开源源码不保存任何个人登录信息。发布版会在当前用户的 AppData 中保存本地缓存。
# 开发时如需临时令牌，请通过环境变量 YXY_TOKEN 提供，切勿提交到仓库。
TOKEN = os.environ.get("YXY_TOKEN", "")
USER_ID = None


class SignBackend:
    def __init__(self, emit: Callable[[str, str], None], root: Path | None = None) -> None:
        self.emit = emit
        self.root = root or Path(__file__).resolve().parent
        self.config_path = self.root / "config.json"
        self.config: AppConfig = self._load_config()
        credentials = self._load_credentials()
        self.token = credentials.get("token") or TOKEN
        cached_user_id = credentials.get("user_id")
        self.user_id: int | None = cached_user_id if isinstance(cached_user_id, int) else USER_ID
        self.courses: list[Course] = []
        self.selected_course: Course | None = None
        self.stop_event = threading.Event()
        self.browser_start_lock = threading.Lock()
        self.monitor_state = MonitorState.IDLE
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Content-Type": "application/json;charset=UTF-8", "Origin": "https://lms.dgut.edu.cn", "Referer": "https://lms.dgut.edu.cn/"}
        if self.token:
            self.headers["Authorization"] = self.token
        self.api = ApiClient(self.headers)
        self._course_controller = None

    def _load_config(self) -> AppConfig:
        values: dict = {}
        try:
            values = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        return AppConfig.from_mapping(values, self.root)

    def _load_credentials(self) -> dict:
        """读取当前用户本地保存的登录缓存，不从源码读取任何个人凭据。"""
        try:
            values = json.loads((self.root / "auth.json").read_text(encoding="utf-8"))
            return values if isinstance(values, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_config(self) -> None:
        self.config_path.write_text(json.dumps(self.config.to_mapping(), ensure_ascii=False, indent=2), encoding="utf-8")

    def update_settings(self, **values) -> None:
        for key, value in values.items():
            if key in self.config.__dataclass_fields__:
                setattr(self.config, key, value)
        self.save_config()

    def _fetch_courses(self) -> list[Course]:
        data = self.api.json("GET", f"{LMS_BASE}/courses/students", params={"keyword": "", "publishStatus": 1, "type": 1, "pn": 1, "ps": 50})
        raw_courses = data.get("courseList") or data.get("result", {}).get("courseList", [])
        return [Course.from_api(course) for course in raw_courses]

    def _log(self, text: str, kind: str = "muted") -> None:
        self.emit(text, kind)

    @property
    def course_controller(self):
        """延迟创建课程页控制器，避免普通签到流程依赖浏览器课件页。"""
        if self._course_controller is None:
            from yxy_course import CourseController
            self._course_controller = CourseController(self._log)
        return self._course_controller

    def start_course_helper(self) -> bool:
        """启动课件学习辅助；需用户先在调试浏览器打开课件学习页。"""
        from yxy_course import CourseConfig
        config = CourseConfig(
            playback_rate=float(self.config.course_playback_rate),
            auto_dismiss_dialog=bool(self.config.course_auto_dismiss_dialog),
            document_scroll_enabled=bool(self.config.course_document_scroll_enabled),
            document_scroll_interval=float(self.config.course_document_scroll_interval),
            document_scroll_speed=float(self.config.course_document_scroll_speed),
        )
        return self.course_controller.start(config)

    def stop_course_helper(self) -> None:
        """停止课程页控制器，不关闭用户的浏览器标签页。"""
        self.course_controller.stop()

    def set_course_speed(self, rate: float) -> None:
        """在运行中调整视频播放倍速。"""
        self.course_controller.set_speed(float(rate))

    def _persist_credentials(self) -> None:
        """把最近一次有效凭据保存到本地 auth.json，绝不修改或污染源码。"""
        try:
            (self.root / "auth.json").write_text(
                json.dumps({"token": self.token, "user_id": self.user_id}, ensure_ascii=False),
                encoding="utf-8",
            )
            self._log("已更新本地应用登录缓存。", "success")
        except OSError as error:
            self._log(f"保存本地登录缓存失败：{error}", "warn")

    def load_saved_courses(self) -> bool:
        """优先使用本地 auth.json 中保存的 Token，不启动浏览器。"""
        if not self.token:
            self._log("本地脚本中没有已保存的登录信息。", "muted")
            return False
        self._log("正在使用本地保存的登录信息读取课程列表…", "info")
        try:
            self.courses = self._fetch_courses()
        except Exception as error:
            self._log(f"本地登录信息已失效：{error}", "warn")
            return False
        if not self.courses:
            self._log("本地登录信息已失效或未读取到课程，需要重新登录。", "warn")
            return False
        self._log(f"已读取 {len(self.courses)} 门课程：", "success")
        for index, course in enumerate(self.courses, 1):
            self._log(f"  {index:>2}. [{course.id}] {course.name} - {course.teacher_name}", "input")
        return True

    def find_browser(self) -> tuple[str | None, str | None]:
        manual = self.config.browser_path
        expected_executables = {
            "Microsoft Edge": "msedge.exe",
            "Google Chrome": "chrome.exe",
            "Brave": "brave.exe",
        }
        # 明确选择浏览器时，不能让之前遗留的其他浏览器路径反客为主。
        manual_matches_choice = (
            not self.config.browser_name
            or Path(manual).name.lower() == expected_executables.get(self.config.browser_name, "")
        )
        if manual and Path(manual).is_file() and manual_matches_choice:
            return manual, self.config.browser_name or Path(manual).stem
        candidates = [
            ("Microsoft Edge", [os.path.expandvars(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe"), os.path.expandvars(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe")]),
            ("Google Chrome", [os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"), os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"), os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe")]),
            ("Brave", [os.path.expandvars(r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe"), os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe")]),
            ("360 极速浏览器", [os.path.expandvars(r"%PROGRAMFILES(X86)%\360ChromeX\Chrome\Application\360ChromeX.exe")]),
        ]
        if self.config.browser_name:
            candidates.sort(key=lambda item: item[0] != self.config.browser_name)
        for name, paths in candidates:
            for path in paths:
                if Path(path).is_file():
                    return path, name
        return None, None

    def start_browser(self, url: str = "") -> bool:
        if not self.browser_start_lock.acquire(blocking=False):
            self._log("浏览器登录正在启动，请稍候。", "muted")
            return True
        try:
            return self._start_browser_impl(url)
        finally:
            self.browser_start_lock.release()

    def _start_browser_impl(self, url: str = "") -> bool:
        url = url.strip()
        if url and self._open_debug_tab(url):
            return True
        path, name = self.find_browser()
        if not path:
            self._log("未找到可用浏览器。请打开设置并手动选择浏览器程序。", "warn")
            return False
        port = int(self.config.debug_port)
        self._log(f"正在以远程调试模式启动 {name}…", "info")
        debug_profile = self.root / "browser_profile"
        command = [path, f"--remote-debugging-port={port}", "--remote-allow-origins=*", f"--user-data-dir={debug_profile}", "--no-first-run", "--no-default-browser-check"]
        if url:
            command.append(url)
        try:
            # Tauri 会接管 sidecar 的标准输出；若 Edge 继承到其中无效的
            # Windows 句柄，会在 Popen 阶段报 Errno 22。浏览器无需终端输入输出，
            # 因此明确丢弃这些句柄。
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            self._log(f"浏览器启动失败：{error}", "warn")
            return False
        self.config.browser_path, self.config.browser_name = path, name
        self.save_config()
        self._log(f"已启动 {name}。请自行进入优学院并完成登录后回到本程序。", "success")
        return True

    def _open_debug_tab(self, url: str) -> bool:
        """调试浏览器已运行时，在同一配置中新增标签页而不是关闭所有窗口。"""
        try:
            port = int(self.config.debug_port)
            response = requests.put(f"http://127.0.0.1:{port}/json/new?{quote(url, safe='')}", timeout=2)
            if response.ok:
                self._log("已在调试浏览器中新开页面。", "success")
                return True
        except requests.RequestException:
            pass
        return False

    def _get_ws_url(self) -> str | None:
        try:
            targets = requests.get(f"http://127.0.0.1:{self.config.debug_port}/json", timeout=2).json()
            page = next((item for item in targets if item.get("type") == "page"), None)
            return page.get("webSocketDebuggerUrl") if page else None
        except (requests.RequestException, ValueError):
            return None

    def _cookies(self, ws_url: str) -> list[dict]:
        ws = create_connection(ws_url, timeout=10)
        try:
            ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
            ws.recv()
            ws.send(json.dumps({"id": 2, "method": "Network.getAllCookies"}))
            while True:
                response = json.loads(ws.recv())
                if response.get("id") == 2:
                    return response.get("result", {}).get("cookies", [])
        finally:
            ws.close()

    def load_session_and_courses(self, wait_seconds: int = 20, automatic: bool = False) -> bool:
        self._log("正在自动检查浏览器登录状态…" if automatic else "正在连接浏览器远程调试端口…", "info")
        ws_url = None
        for _ in range(max(1, wait_seconds)):
            ws_url = self._get_ws_url()
            if ws_url:
                break
            time.sleep(1)
        if not ws_url:
            self._log("未发现浏览器远程调试端口。" if automatic else "连接浏览器失败：未发现远程调试端口。", "warn")
            return False
        try:
            cookies = self._cookies(ws_url)
        except Exception as error:
            self._log(f"读取浏览器登录信息失败：{error}", "warn")
            return False
        dgut = [item for item in cookies if "dgut.edu.cn" in item.get("domain", "")]
        self.token = next((item.get("value", "") for item in dgut if item.get("name") == "AUTHORIZATION"), "")
        user_id = next((item.get("value") for item in dgut if item.get("name") == "userid"), None)
        if not self.token:
            self._log("未检测到有效登录状态，请在浏览器中完成优学院登录。", "warn")
            return False
        self.headers["Authorization"] = self.token
        self.user_id = int(user_id) if user_id and user_id.isdigit() else None
        self._persist_credentials()
        self._log("登录信息读取成功，正在获取课程列表…", "success")
        try:
            self.courses = self._fetch_courses()
        except Exception as error:
            self._log(f"获取课程列表失败：{error}", "warn")
            return False
        if not self.courses:
            self._log("没有读取到课程，请确认登录账号与网络状态。", "warn")
            return False
        self._log(f"已读取 {len(self.courses)} 门课程：", "success")
        for index, course in enumerate(self.courses, 1):
            self._log(f"  {index:>2}. [{course.id}] {course.name} - {course.teacher_name}", "input")
        return True

    def select_course(self, query: str) -> Course | None:
        """只允许选择一门课程；模糊匹配有歧义时要求用户细化输入。"""
        keyword = query.strip()
        if not keyword or "," in keyword or "，" in keyword:
            return None
        matches = [course for course in self.courses if keyword == str(course.id) or keyword.lower() in course.name.lower()]
        if len(matches) != 1:
            return None
        self.selected_course = matches[0]
        return self.selected_course

    def clear_selected_course(self) -> None:
        self.selected_course = None

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        return self.api.request(method, url, **kwargs)

    def _classrooms(self, course_id: int) -> list[Classroom]:
        response = self._request("GET", f"{LMS_BASE}/wisdomClassroom/student/getClassroomList", params={"ocId": course_id, "status": "", "pageNum": 1, "pageSize": 10, "order": 0, "lang": "zh"})
        data = response.json()
        raw_classrooms = data.get("result", {}).get("list", []) if data.get("code") == 1 else []
        return [Classroom.from_api(classroom) for classroom in raw_classrooms]

    def _activities(self, classroom_id: int) -> list[Activity]:
        response = self._request("GET", f"{APP_BASE}/wisdomClassroom/student/classroomActivitys", params={"classroomId": classroom_id, "pageNum": 1, "pageSize": 999})
        data = response.json()
        raw_activities = data.get("result", {}).get("list", []) if data.get("code") == 1 else []
        return [Activity.from_api(activity) for activity in raw_activities]

    @staticmethod
    def _kind(score_type: int | None) -> str:
        return {0: "选人点名", 1: "二维码签到", 2: "数字码签到", 3: "一键签到"}.get(score_type, "未知签到")

    @staticmethod
    def _attendance_code(activity: Activity) -> str:
        return next((str(activity.raw[key]) for key in ("attendanceCode", "code", "codeStr", "signCode", "checkInCode") if activity.raw.get(key)), "")

    def _write_sign_log(self, course: str, kind: str, details: list[str]) -> None:
        if not self.config.save_log:
            return
        path = Path(self.config.log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        with path.open("a", encoding="utf-8") as file:
            file.write(f"{now:%Y-%m-%d}-{now.hour}:{now:%M} | {course} | {kind} |\n")
            for item in details:
                file.write(f"  - {item}\n")
            file.write("\n")

    def _sign(self, course: Course, classroom_id: int, activity: Activity) -> bool:
        score_type = activity.score_type
        kind = self._kind(score_type)
        activity_id = activity.relation_id
        code = self._attendance_code(activity) if score_type == 1 else ""
        if score_type == 1 and not code:
            message = "未找到二维码签到码，已跳过"
            self._log(f"[{course.name}] {kind}：{message}", "warn")
            self._write_sign_log(course.name, kind, [f"attendanceID: {activity_id}", "result: skipped", f"reason: {message}"])
            return True
        if score_type not in (1, 2, 3):
            self._log(f"[{course.name}] {kind}：当前类型不支持自动处理，已跳过", "warn")
            self._write_sign_log(course.name, kind, [f"attendanceID: {activity_id}", "result: skipped", "reason: unsupported scoreType"])
            return True
        payload = {"attendanceID": activity_id, "classID": classroom_id, "userID": self.user_id, "location": f"{self.config.lat},{self.config.lng}", "address": self.config.address, "enterWay": 1, "attendanceCode": code}
        try:
            response = self._request("POST", f"{APP_BASE}/newAttendance/signByStu", json=payload)
            result = response.json()
            status, message = result.get("status"), result.get("msg", result)
        except Exception as error:
            status, message = "exception", str(error)
        if status == 200:
            self._log(f"✓ [{course.name}] {kind}：签到成功", "success")
        elif status == 201:
            self._log(f"• [{course.name}] {kind}：已签到过", "muted")
        else:
            self._log(f"× [{course.name}] {kind}：{message}", "warn")
        self._write_sign_log(course.name, kind, [f"attendanceID: {activity_id}", f"HTTP/status: {status}", f"response: {message}"])
        return status in (200, 201)

    def _poll_once(self, checked: set[str]) -> None:
        today = datetime.now().strftime("%m-%d")
        course = self.selected_course
        if course is None:
            self._log("尚未选择课程，轮询已跳过。", "warn")
            return
        classrooms = [item for item in self._classrooms(course.id) if today in item.title]
        if not classrooms:
            self._log(f"[{course.name}] 本轮完成：今天没有课堂，无需签到。", "muted")
            return
        active_count = 0
        new_count = 0
        for classroom in classrooms:
            for activity in self._activities(classroom.id):
                if activity.relation_type != 1 or activity.state != 1 or activity.status != 0:
                    continue
                active_count += 1
                key = f"{activity.relation_id}_{classroom.id}"
                if key in checked:
                    continue
                new_count += 1
                self._log(f"[{course.name}] 发现 {self._kind(activity.score_type)}，正在处理…", "info")
                if self._sign(course, classroom.id, activity):
                    checked.add(key)
        if active_count == 0:
            self._log(f"[{course.name}] 本轮完成：未发现进行中的签到。", "muted")
        elif new_count == 0:
            self._log(f"[{course.name}] 本轮完成：签到活动已处理，继续等待。", "muted")

    def start_monitor(self) -> None:
        self.stop_event.clear()
        self.monitor_state = MonitorState.RUNNING
        threading.Thread(target=self._monitor, daemon=True).start()

    def stop_monitor(self) -> None:
        self.stop_event.set()
        self.monitor_state = MonitorState.STOPPED

    def _monitor(self) -> None:
        checked: set[str] = set()
        interval = max(2, int(self.config.poll_interval))
        self._log(f"开始轮询，每 {interval} 秒检查一次。", "success")
        round_number = 0
        while not self.stop_event.is_set():
            round_number += 1
            try:
                course_name = self.selected_course.name if self.selected_course else "未选择课程"
                self._log(f"[{time.strftime('%H:%M:%S')}] 第 {round_number} 轮：正在检查《{course_name}》…", "info")
                self._poll_once(checked)
            except Exception as error:
                self._log(f"轮询出错：{error}", "warn")
            self.stop_event.wait(interval)
