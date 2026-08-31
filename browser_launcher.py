"""管理浏览器版所需的本地 API、Vite 和调试浏览器。

冻结（PyInstaller onedir）模式下本文件同时是 dgut-bot.exe 的入口：
无参数时作为启动器查找浏览器并拉起后台服务；带 --service 参数时
作为后台服务进程运行，全程不需要系统安装 Python。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from app_paths import data_root, frontend_dist, is_frozen
from backend_commands import backend, emit_event
from web_server import CLIENT_CLOSED_EVENT, LocalApiHandler, SHUTDOWN_EVENT, client_last_seen, reset_client_state, update_manager
from yxy_mutex import APP_MUTEX, NamedMutex, app_mutex_exists, updating_mutex_exists

ROOT = data_root()
FRONTEND = ROOT / "web"
LOG_PATH = ROOT / "browser-launcher.log"
SERVICE_LOG_PATH = ROOT / "browser-service.log"
APP_TITLE = "莞工小皮卡"
DUPLICATE_NOTICE_SECONDS = 4.0
# 助手页在用户切到优学院课件页后会成为后台标签；Chromium 会明显降低
# 后台定时器频率。真正关闭标签页有 pagehide/sendBeacon 这条快速路径，
# 心跳仅作 Beacon 丢失时的兜底，因此保留较长的后台容忍时间。
CLIENT_HEARTBEAT_TIMEOUT = 120.0


def client_connection_expired(client_connected: bool, last_seen: float, now: float | None = None) -> bool:
    """关闭通知丢失时，根据心跳延迟回收本地服务。"""
    current = time.monotonic() if now is None else now
    return bool(client_connected and current - last_seen >= CLIENT_HEARTBEAT_TIMEOUT)


def configure_console_encoding() -> str:
    """终端优先 UTF-8；旧控制台不支持时仅将终端降级为 GBK。"""
    encoding = os.environ.get("YXY_CONSOLE_ENCODING", "utf-8").lower()
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            utf8_ready = bool(kernel32.SetConsoleOutputCP(65001)) and bool(kernel32.SetConsoleCP(65001))
            if not utf8_ready or encoding == "gbk":
                kernel32.SetConsoleOutputCP(936)
                kernel32.SetConsoleCP(936)
                encoding = "gbk"
        except (AttributeError, OSError):
            encoding = "gbk"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding=encoding, errors="replace")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding=encoding, errors="replace")
    return encoding


def print_startup_help() -> None:
    print("启动说明：程序会优先使用有效的已保存路径；路径失效时按 Edge → Chrome → 其他 Chromium 浏览器重新查找。")
    print("若浏览器被移动到自定义位置，自动查找失败后请按提示填写 msedge.exe 或 chrome.exe 的完整地址，支持英文双引号。")
    print("-" * 72)
    print("-" * 72)


def choose_available_port(start: int, attempts: int = 20) -> int:
    """从指定端口开始选择空闲端口，避免与其它本机程序冲突。"""
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No free local port found in {start}-{start + attempts - 1}.")


def choose_frontend_port(start: int = 1420, attempts: int = 20) -> int:
    """兼容旧调用；开发前端默认在 1420-1439 中选择。"""
    return choose_available_port(start, attempts)


def wait_for_frontend(process: subprocess.Popen, health_url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Frontend process exited with code {process.returncode}.")
        try:
            with urlopen(health_url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("Frontend startup timed out after 30 seconds.")


def stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def show_log_tail() -> None:
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    if lines:
        print("\n前端日志末尾：")
        print("\n".join(lines[-20:]))


def open_frontend(url: str) -> str:
    """查找并保存浏览器；传入网址时才真正打开浏览器。"""
    print("正在寻找 Chromium 浏览器：")
    path, name = backend.find_browser(progress=lambda candidate: print(f"  {candidate}"), timeout_seconds=10)
    if not path or not name:
        return "manual"
    print(f"找到了 {name}：{path}")
    backend.update_settings(browser_name=name, browser_path=path)
    print(f"已保存浏览器配置地址：{path}")
    if not url:
        return "debug"
    if backend.start_browser(url):
        return "debug"
    print(f"浏览器启动失败：{path}")
    return "manual"


def configure_browser_path(value: str) -> bool:
    """首次自动检测失败时，仅由终端接收 Chromium 可执行文件地址。"""
    candidate = Path(value.strip().strip('"')).expanduser()
    if not candidate.is_file() or candidate.suffix.lower() != ".exe":
        print(f"格式或地址错误：{candidate}")
        print("请填写 Chromium 浏览器 .exe 的完整地址，可使用英文双引号包裹。")
        return False
    executable = candidate.name.lower()
    browser_names = {
        "msedge.exe": "Microsoft Edge",
        "chrome.exe": "Google Chrome",
        "brave.exe": "Brave",
        "vivaldi.exe": "Vivaldi",
        "launcher.exe": "Opera",
        "opera.exe": "Opera",
        "360chromex.exe": "360 极速浏览器",
    }
    browser_name = browser_names.get(executable, "自定义浏览器")
    browser_path = str(candidate.resolve())
    backend.update_settings(browser_name=browser_name, browser_path=browser_path)
    print(f"已保存 {browser_name} 配置：{browser_path}")
    return True


def prompt_for_browser(web_url: str) -> bool:
    """自动检测失败后，在终端循环引导用户输入浏览器程序地址。"""
    print("未找到 Chromium 浏览器 :(")
    print('请填写 Chromium 浏览器的 .exe 所在位置（可使用英文双引号）：')
    print(r'例如："C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"')
    while True:
        try:
            value = input("浏览器地址> ").strip()
        except (EOFError, OSError):
            print(f"无法读取终端输入。本地程序地址：{web_url}")
            return False
        if not value:
            print("填写内容为空，请重新输入。")
            continue
        if configure_browser_path(value):
            return True


def request_service_shutdown(web_url: str) -> None:
    request = Request(
        f"{web_url}/api/command",
        data=b'{"command":"shutdown_app","payload":{}}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2):
            pass
    except OSError:
        pass


def log_line(path: Path, message: str) -> None:
    """无控制台的冻结进程把关键启动信息追加到日志文件。"""
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


def show_notice_window(message: str, title: str = APP_TITLE, auto_close_ms: int | None = None) -> None:
    """无控制台的冻结进程用小窗口提示；tkinter 不可用时静默返回。"""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title(title)
        root.attributes("-topmost", True)
        root.resizable(False, False)
        tk.Label(root, text=message, justify="left", padx=24, pady=16, wraplength=380).pack()
        if auto_close_ms:
            root.after(auto_close_ms, root.destroy)
        root.mainloop()
        return
    except Exception:  # noqa: BLE001 - 无桌面环境时放弃提示
        pass


def show_update_in_progress_window() -> None:
    """更新互斥锁存在时弹出的小窗口。"""
    show_notice_window(
        "优学院助手正在更新\n\n更新完成后程序将自动重新启动，请勿重复打开。",
        title="优学院助手正在更新",
        auto_close_ms=10000,
    )


def show_already_running_notice() -> None:
    """重复启动时提示片刻，避免窗口一闪而过；冻结模式改用图形提示。"""
    if is_frozen():
        show_notice_window(f"{APP_TITLE}已在运行，请勿重复打开。", auto_close_ms=int(DUPLICATE_NOTICE_SECONDS * 1000))
        return
    print("优学院助手已在运行，请勿重复打开。")
    print(f"此窗口将在 {int(DUPLICATE_NOTICE_SECONDS)} 秒后自动关闭……")
    time.sleep(DUPLICATE_NOTICE_SECONDS)


def static_frontend_available() -> bool:
    """发布包模式：有构建产物且没有 node_modules 时，由本地 API 服务直接托管前端。"""
    if is_frozen():
        return True
    if not (frontend_dist() / "index.html").is_file():
        return False
    return not (FRONTEND / "node_modules" / ".bin" / "vite.cmd").is_file()


def run_background_service(web_port: int, use_static: bool = False, api_port: int = 8765) -> int:
    """独立持有 API 与前端；启动终端被关闭后此进程仍继续运行。"""
    SHUTDOWN_EVENT.clear()
    reset_client_state()
    app_mutex = NamedMutex(APP_MUTEX)
    if not app_mutex.try_acquire():
        print("另一个优学院助手实例正在运行，本服务退出。")
        return 1
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm and not use_static:
        app_mutex.release()
        return 1
    server: ThreadingHTTPServer | None = None
    vite: subprocess.Popen | None = None
    log_file = None
    try:
        server_port = web_port if use_static else api_port
        server = ThreadingHTTPServer(("127.0.0.1", server_port), LocalApiHandler)
        threading.Thread(target=server.serve_forever, name="local-api", daemon=True).start()
        if use_static:
            print(f"发布包模式：由本地服务直接托管 web/dist，前端地址 http://127.0.0.1:{web_port}", flush=True)
        else:
            log_file = LOG_PATH.open("w", encoding="utf-8")
            vite_env = os.environ.copy()
            vite_env["YXY_API_PORT"] = str(api_port)
            vite = subprocess.Popen(
                [npm, "run", "dev", "--", "--port", str(web_port), "--strictPort"],
                cwd=FRONTEND,
                env=vite_env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        update_manager.set_ports(f"http://127.0.0.1:{web_port}", web_port)
        update_manager.start_auto_check()
        client_connected = False
        close_requested_at: float | None = None
        while not SHUTDOWN_EVENT.is_set():
            if vite is not None and vite.poll() is not None:
                return 1
            last_seen = client_last_seen()
            client_connected = client_connected or bool(last_seen)
            if CLIENT_CLOSED_EVENT.is_set():
                close_requested_at = close_requested_at or time.monotonic()
                if time.monotonic() - close_requested_at >= 5:
                    return 0
            else:
                close_requested_at = None
            if client_connection_expired(client_connected, last_seen):
                return 0
            SHUTDOWN_EVENT.wait(0.2)
        return 0
    finally:
        update_manager.stop()
        if server is not None:
            server.shutdown()
            server.server_close()
        stop_process(vite)
        if log_file is not None:
            log_file.close()
        app_mutex.release()


def service_command(web_port: int, use_static: bool, api_port: int = 8765) -> list[str]:
    """后台服务命令行：冻结模式通过当前 EXE 自启动，开发模式使用当前解释器。"""
    if is_frozen():
        command = [sys.executable, "--service", str(web_port), "--api-port", str(api_port)]
    else:
        service_python = Path(sys.base_prefix) / ("pythonw.exe" if sys.platform == "win32" else "bin/python")
        if not service_python.is_file():
            service_python = Path(sys.executable)
        command = [str(service_python), str(Path(__file__).resolve()), "--service", str(web_port), "--api-port", str(api_port)]
    if use_static:
        command.append("--static")
    return command


def start_background_service(web_port: int, use_static: bool = False, api_port: int = 8765) -> subprocess.Popen:
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if is_frozen():
        # 冻结的服务进程把输出写进自己的日志文件，不需要继承句柄。
        return subprocess.Popen(
            service_command(web_port, use_static, api_port),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    service_log = SERVICE_LOG_PATH.open("w", encoding="utf-8")
    try:
        return subprocess.Popen(
            service_command(web_port, use_static, api_port),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=service_log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            close_fds=True,
        )
    finally:
        service_log.close()


def redirect_frozen_output() -> None:
    """无控制台的冻结进程把 stdout/stderr 落到日志文件，避免诊断信息丢失。"""
    if sys.platform != "win32":
        return
    target = SERVICE_LOG_PATH if "--service" in sys.argv else LOG_PATH
    try:
        handle = target.open("w", encoding="utf-8", buffering=1)
        sys.stdout = handle
        sys.stderr = handle
    except OSError:
        pass


def main_frozen() -> int:
    """免安装发行版入口：无控制台，浏览器缺失时通过网页设置处理。"""
    if updating_mutex_exists():
        show_update_in_progress_window()
        return 0
    if app_mutex_exists():
        show_already_running_notice()
        return 0
    SHUTDOWN_EVENT.clear()
    reset_client_state()
    try:
        web_port = choose_available_port(8765)
        web_url = f"http://127.0.0.1:{web_port}"
        # 浏览器检测失败不再依赖终端输入：照常启动服务，由网页设置接管。
        browser_mode = open_frontend("")
        if browser_mode == "manual":
            log_line(LOG_PATH, "未找到 Chromium 浏览器；请在网页设置的浏览器模块中选择或填写浏览器路径。")
            emit_event("BROWSER_NOT_FOUND", "warning", "browser", "未找到 Chromium 浏览器，请在设置中手动选择浏览器程序。")
        log_line(LOG_PATH, "正在启动本地网页服务…")
        service = start_background_service(web_port, use_static=True, api_port=web_port)
        wait_for_frontend(service, f"{web_url}/api/health")
        if browser_mode == "manual":
            try:
                # 没有调试浏览器时用系统默认浏览器打开界面，让用户在设置中配置。
                os.startfile(web_url)  # noqa: S606
            except OSError:
                log_line(LOG_PATH, f"系统默认浏览器打开失败；请手动访问 {web_url}")
        else:
            backend.start_browser(web_url)
        return 0
    except Exception as error:  # noqa: BLE001 - 无控制台：所有启动错误必须落盘
        log_line(LOG_PATH, f"启动失败：{error!r}")
        show_notice_window(f"{APP_TITLE}启动失败：{error}\n详情见程序目录中的 browser-launcher.log。")
        return 1


def main() -> int:
    if "--service" in sys.argv:
        index = sys.argv.index("--service")
        web_port = int(sys.argv[index + 1])
        api_port = int(sys.argv[sys.argv.index("--api-port") + 1]) if "--api-port" in sys.argv else 8765
        return run_background_service(web_port, "--static" in sys.argv, api_port)
    if is_frozen():
        return main_frozen()
    # 更新互斥锁优先：更新器接管期间拒绝一切重复启动请求。
    if updating_mutex_exists():
        show_update_in_progress_window()
        return 0
    if app_mutex_exists():
        show_already_running_notice()
        return 0
    check_only = "--check" in sys.argv
    print_startup_help()
    SHUTDOWN_EVENT.clear()
    reset_client_state()
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    use_static = static_frontend_available()
    if not use_static and not npm:
        print("ERROR: Node.js/npm was not found in PATH.")
        return 1

    if not use_static and not (FRONTEND / "node_modules" / ".bin" / "vite.cmd").is_file():
        print("Installing frontend dependencies...")
        install = subprocess.run([npm, "ci"], cwd=FRONTEND, check=False)
        if install.returncode != 0:
            print("ERROR: npm ci failed.")
            return install.returncode

    service: subprocess.Popen | None = None
    try:
        if use_static:
            web_port = choose_available_port(8765)
            api_port = web_port
        else:
            web_port = choose_frontend_port()
            api_port = choose_available_port(8765)
        web_url = f"http://127.0.0.1:{web_port}"
        health_url = f"{web_url}/api/health"

        # 浏览器是网页界面的入口，必须先确认可用，再启动本地网页服务。
        browser_mode = open_frontend("")
        if browser_mode == "manual" and not prompt_for_browser(""):
            print("未配置可用浏览器，程序已取消启动。")
            return 0

        print("浏览器准备完成，开始启动网页程序。")
        service = start_background_service(web_port, use_static, api_port)
        print("正在启动本地网页程序…")
        wait_for_frontend(service, health_url)
        print(f"本地网页程序已就绪：{web_url}")

        if check_only:
            print("启动检查通过。")
            request_service_shutdown(web_url)
            service.wait(timeout=10)
            return 0

        if backend.start_browser(web_url):
            print("已在找到的浏览器中打开网页程序。")
        else:
            print("已保存的浏览器启动失败，请重新填写浏览器地址。")
            if not prompt_for_browser("") or not backend.start_browser(web_url):
                print("浏览器仍无法启动，程序已取消。")
                request_service_shutdown(web_url)
                return 0
        print("网页已打开，后台服务将继续运行；此启动窗口现在会自动关闭。")
        return 0
    except KeyboardInterrupt:
        print("\n启动已取消；若网页已经打开，后台服务会继续运行。")
        return 0
    except OSError as error:
        print(f"ERROR: Could not start a local service: {error}")
        show_log_tail()
        return 1
    except RuntimeError as error:
        print(f"ERROR: {error}")
        show_log_tail()
        return 1
    finally:
        # 后台服务是独立进程；退出或关闭此终端时不得清理它。
        pass


if __name__ == "__main__":
    if is_frozen():
        # 冻结进程没有控制台：先落日志，再跳过面向终端的编码配置。
        redirect_frozen_output()
    else:
        configure_console_encoding()
    raise SystemExit(main())
