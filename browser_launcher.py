"""管理浏览器版所需的本地 API、Vite 和调试浏览器。"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from web_server import CLIENT_CLOSED_EVENT, LocalApiHandler, SHUTDOWN_EVENT, backend, client_last_seen, reset_client_state

if sys.platform == "win32":
    import msvcrt


ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "tauri-react"
LOG_PATH = ROOT / "browser-launcher.log"


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
        print("\nLast frontend log lines:")
        print("\n".join(lines[-20:]))


def poll_terminal_command(buffer: str) -> tuple[str, str | None]:
    """非阻塞读取 Windows 启动器终端，并手动回显输入字符。"""
    if sys.platform != "win32":
        return buffer, None
    while msvcrt.kbhit():
        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            if msvcrt.kbhit():
                msvcrt.getwch()
            continue
        if char in ("\r", "\n"):
            print()
            return "", buffer.strip().lower()
        if char == "\b":
            if buffer:
                buffer = buffer[:-1]
                print("\b \b", end="", flush=True)
            continue
        if char == "\x03":
            raise KeyboardInterrupt
        buffer += char
        print(char, end="", flush=True)
    return buffer, None


def main() -> int:
    check_only = "--check" in sys.argv
    SHUTDOWN_EVENT.clear()
    reset_client_state()
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        print("ERROR: Node.js/npm was not found in PATH.")
        return 1

    if not (FRONTEND / "node_modules" / ".bin" / "vite.cmd").is_file():
        print("Installing frontend dependencies...")
        install = subprocess.run([npm, "ci"], cwd=FRONTEND, check=False)
        if install.returncode != 0:
            print("ERROR: npm ci failed.")
            return install.returncode

    server: ThreadingHTTPServer | None = None
    vite: subprocess.Popen | None = None
    log_file = None
    try:
        web_port = choose_frontend_port()
        web_url = f"http://127.0.0.1:{web_port}"
        health_url = f"{web_url}/api/health"
        server = ThreadingHTTPServer(("127.0.0.1", 8765), LocalApiHandler)
        threading.Thread(target=server.serve_forever, name="local-api", daemon=True).start()

        log_file = LOG_PATH.open("w", encoding="utf-8")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        vite = subprocess.Popen(
            [npm, "run", "web", "--", "--port", str(web_port), "--strictPort"],
            cwd=FRONTEND,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        print("Starting local web UI...")
        wait_for_frontend(vite, health_url)
        print(f"Local web UI is ready: {web_url}")

        if check_only:
            print("Startup check passed.")
            return 0

        if not backend.start_browser(web_url):
            raise RuntimeError("Could not start a Chromium debug browser.")
        print("The UI has been opened in the debug browser.")
        print("Close the frontend tab to stop the local services automatically.")
        print("Type 'kill' here and press Enter, or press Ctrl+C, to stop manually.")
        print("> ", end="", flush=True)

        client_connected = False
        close_requested_at: float | None = None
        terminal_buffer = ""
        while vite.poll() is None and not SHUTDOWN_EVENT.wait(0.2):
            terminal_buffer, terminal_command = poll_terminal_command(terminal_buffer)
            if terminal_command == "kill":
                print("Shutdown requested from the launcher terminal.")
                return 0
            if terminal_command is not None:
                if terminal_command:
                    print(f"Unknown command: {terminal_command}. Type 'kill' to stop.")
                print("> ", end="", flush=True)
            last_seen = client_last_seen()
            if last_seen and not client_connected:
                client_connected = True
                print("\nFrontend tab connected; heartbeat monitoring is active.")
                print(f"> {terminal_buffer}", end="", flush=True)
            if CLIENT_CLOSED_EVENT.is_set():
                close_requested_at = close_requested_at or time.monotonic()
                if time.monotonic() - close_requested_at >= 5:
                    print("\nFrontend tab closed; stopping local services.")
                    return 0
            else:
                close_requested_at = None
            if client_connected and time.monotonic() - last_seen >= 90:
                print("\nFrontend heartbeat timed out; stopping local services.")
                return 0
        if SHUTDOWN_EVENT.is_set():
            print("\nShutdown requested from the web terminal.")
            return 0
        raise RuntimeError(f"Frontend process exited with code {vite.returncode}.")
    except KeyboardInterrupt:
        print("\nStopping local services...")
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
        if server is not None:
            server.shutdown()
            server.server_close()
        stop_process(vite)
        if log_file is not None:
            log_file.close()


if __name__ == "__main__":
    raise SystemExit(main())
