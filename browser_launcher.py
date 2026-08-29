"""管理浏览器版所需的本地 API、Vite 和调试浏览器。"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from web_server import CLIENT_CLOSED_EVENT, LocalApiHandler, SHUTDOWN_EVENT, backend, client_last_seen, reset_client_state, update_manager
from yxy_mutex import APP_MUTEX, NamedMutex, app_mutex_exists, updating_mutex_exists

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "web"
LOG_PATH = ROOT / "browser-launcher.log"
SERVICE_LOG_PATH = ROOT / "browser-service.log"
DUPLICATE_NOTICE_SECONDS = 4.0
CLIENT_HEARTBEAT_TIMEOUT = 10.0


def client_connection_expired(client_connected: bool, last_seen: float, now: float | None = None) -> bool:
    """关闭通知丢失时，根据心跳在短时间内回收本地服务。"""
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


def choose_frontend_port(start: int = 1420, attempts: int = 20) -> int:
    """选择空闲端口，避免旧 Vite 进程导致新启动器误判成功。"""
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No free frontend port found in {start}-{start + attempts - 1}.")


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


def show_update_in_progress_window() -> None:
    """更新互斥锁存在时弹出的小窗口；tkinter 不可用则退回终端提示。"""
    message = "优学院助手正在更新\n\n更新完成后程序将自动重新启动，请勿重复打开。"
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title("优学院助手正在更新")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        tk.Label(root, text=message, justify="left", padx=24, pady=16).pack()
        root.after(10000, root.destroy)
        root.mainloop()
        return
    except Exception:  # noqa: BLE001 - 无桌面环境时只打印提示
        pass
    print(message)


def show_already_running_notice() -> None:
    """重复启动时让终端提示停留片刻，避免批处理窗口一闪而过。"""
    print("优学院助手已在运行，请勿重复打开。")
    print(f"此窗口将在 {int(DUPLICATE_NOTICE_SECONDS)} 秒后自动关闭……")
    time.sleep(DUPLICATE_NOTICE_SECONDS)


def static_frontend_available() -> bool:
    """发布包模式：有构建产物且没有 node_modules 时，由本地 API 服务直接托管前端。"""
    return (FRONTEND / "dist" / "index.html").is_file() and not (FRONTEND / "node_modules" / ".bin" / "vite.cmd").is_file()


def run_background_service(web_port: int, use_static: bool = False) -> int:
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
        server = ThreadingHTTPServer(("127.0.0.1", 8765), LocalApiHandler)
        threading.Thread(target=server.serve_forever, name="local-api", daemon=True).start()
        log_file = LOG_PATH.open("w", encoding="utf-8")
        if use_static:
            print(f"发布包模式：由本地服务直接托管 web/dist，前端地址 http://127.0.0.1:{web_port}", flush=True)
        else:
            vite = subprocess.Popen(
                [npm, "run", "dev", "--", "--port", str(web_port), "--strictPort"],
                cwd=FRONTEND,
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


def start_background_service(web_port: int, use_static: bool = False) -> subprocess.Popen:
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    service_log = SERVICE_LOG_PATH.open("w", encoding="utf-8")
    service_python = Path(sys.base_prefix) / ("pythonw.exe" if sys.platform == "win32" else "bin/python")
    if not service_python.is_file():
        service_python = Path(sys.executable)
    command = [str(service_python), str(Path(__file__).resolve()), "--service", str(web_port)]
    if use_static:
        command.append("--static")
    try:
        return subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=service_log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            close_fds=True,
        )
    finally:
        service_log.close()


def main() -> int:
    if "--service" in sys.argv:
        index = sys.argv.index("--service")
        return run_background_service(int(sys.argv[index + 1]), "--static" in sys.argv)
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
            web_port = 8765
        else:
            web_port = choose_frontend_port()
        web_url = f"http://127.0.0.1:{web_port}"
        health_url = f"{web_url}/api/health"

        # 浏览器是网页界面的入口，必须先确认可用，再启动本地网页服务。
        browser_mode = open_frontend("")
        if browser_mode == "manual" and not prompt_for_browser(""):
            print("未配置可用浏览器，程序已取消启动。")
            return 0

        print("浏览器准备完成，开始启动网页程序。")
        service = start_background_service(web_port, use_static)
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
    configure_console_encoding()
    raise SystemExit(main())
