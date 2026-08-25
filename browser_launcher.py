"""管理浏览器版所需的本地 API、Vite 和调试浏览器。"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from web_server import LocalApiHandler, backend


ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "tauri-react"
WEB_URL = "http://127.0.0.1:1420"
HEALTH_URL = f"{WEB_URL}/api/health"
LOG_PATH = ROOT / "browser-launcher.log"


def wait_for_frontend(process: subprocess.Popen, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Frontend process exited with code {process.returncode}.")
        try:
            with urlopen(HEALTH_URL, timeout=1) as response:
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


def main() -> int:
    check_only = "--check" in sys.argv
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
        server = ThreadingHTTPServer(("127.0.0.1", 8765), LocalApiHandler)
        threading.Thread(target=server.serve_forever, name="local-api", daemon=True).start()

        log_file = LOG_PATH.open("w", encoding="utf-8")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        vite = subprocess.Popen(
            [npm, "run", "web"],
            cwd=FRONTEND,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        print("Starting local web UI...")
        wait_for_frontend(vite)
        print(f"Local web UI is ready: {WEB_URL}")

        if check_only:
            print("Startup check passed.")
            return 0

        if not backend.start_browser(WEB_URL):
            raise RuntimeError("Could not start a Chromium debug browser.")
        print("The UI has been opened in the debug browser.")
        print("Keep this window open. Press Ctrl+C to stop the local services.")

        while vite.poll() is None:
            time.sleep(0.5)
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
