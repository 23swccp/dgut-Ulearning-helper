"""本地优学院测验模拟器：用真实 Chromium/CDP 验证 QuizHandler 的题型开关。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from yxy_backend import AppConfig, SignBackend
from yxy_quiz import QuizHandler, StandaloneBackend


ROOT = Path(__file__).resolve().parent
SIMULATOR_ROOT = ROOT / "quiz_simulator"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args) -> None:
        pass


def wait_for_debug_port(port: int, timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1):
                return
        except OSError:
            time.sleep(0.15)
    raise RuntimeError(f"浏览器调试端口 {port} 未能启动")


def load_current_config() -> AppConfig:
    try:
        values = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        values = {}
    return AppConfig.from_mapping(values, ROOT)


def wait_for_simulator_page(backend: StandaloneBackend, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if backend.evaluate("typeof window.getSimulatorResult === 'function'") is True:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("模拟测验页面未能完成加载")


def result_matches(result: dict, *, choice: bool, judgment: bool, blank: bool) -> bool:
    expected_finished = {
        "question-sim-choice": choice,
        "question-sim-judgment": judgment,
        "question-sim-blank": blank,
    }
    return (
        result.get("finished") == expected_finished
        and result.get("choice") == ("C" if choice else "")
        and result.get("judgment") == ("错误" if judgment else "")
        and result.get("blank") == ("," if blank else "")
        and result.get("submitCount") == (1 if any((choice, judgment, blank)) else 0)
        and result.get("trustedClicks", 0) >= sum((choice, judgment, blank)) + (1 if any((choice, judgment, blank)) else 0)
    )


def run_case(
    backend: StandaloneBackend,
    name: str,
    *,
    enabled: bool,
    choice: bool,
    judgment: bool,
    blank: bool,
) -> tuple[bool, dict, dict]:
    backend.evaluate("window.resetQuiz()")
    summary = {"done": 0, "skipped": 0, "failed": 0, "modals": 0}
    if enabled:
        handler = QuizHandler(
            evaluate=backend.evaluate,
            click=backend.click,
            type_text=backend.type_text,
            sleep=lambda _seconds: None,
            log=lambda *_args: None,
            dry_run=False,
            jitter=0,
        )
        summary = handler.answer_all(
            answer_choice=choice,
            answer_judgment=judgment,
            answer_blank=blank,
        )
    result = json.loads(backend.evaluate("JSON.stringify(window.getSimulatorResult())"))
    expected = (choice, judgment, blank) if enabled else (False, False, False)
    passed = summary["failed"] == 0 and result_matches(
        result,
        choice=expected[0],
        judgment=expected[1],
        blank=expected[2],
    )
    detail = json.dumps({"summary": summary, "page": result}, ensure_ascii=False)
    backend.evaluate(
        f"window.renderRunnerResult({json.dumps(name, ensure_ascii=False)}, {str(passed).lower()}, {json.dumps(detail, ensure_ascii=False)})"
    )
    return passed, summary, result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="启动本地模拟测验并用真实浏览器事件验证自动答题")
    parser.add_argument("--show", action="store_true", help="显示测试浏览器窗口")
    parser.add_argument("--hold", type=float, default=15.0, help="--show 时测试结束后保留窗口的秒数")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(SIMULATOR_ROOT)))
    server_thread = threading.Thread(target=server.serve_forever, name="quiz-simulator-http", daemon=True)
    server_thread.start()
    web_port = int(server.server_address[1])

    finder = SignBackend(lambda *_args: None, root=ROOT)
    browser_path, browser_name = finder.find_browser()
    if not browser_path:
        server.shutdown()
        print("未找到 Chromium 浏览器；请先在设置中选择 Edge、Chrome 或其他 Chromium 浏览器。")
        return 2

    backend = None
    process = None
    failures = 0
    with tempfile.TemporaryDirectory(prefix="yxy-quiz-simulator-", ignore_cleanup_errors=True) as profile:
        debug_server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        debug_port = int(debug_server.server_address[1])
        debug_server.server_close()
        url = f"http://127.0.0.1:{web_port}/learnCourse.html"
        command = [
            browser_path,
            f"--remote-debugging-port={debug_port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-mode",
            "--disable-component-update",
            "--window-size=1280,900",
        ]
        if not args.show:
            command.append("--headless=new")
        command.append(url)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if not args.show else 0,
            )
            wait_for_debug_port(debug_port)
            backend = StandaloneBackend(
                port=debug_port,
                dry_run=False,
                log=lambda *_args: None,
                tab_keyword="/learnCourse.html",
            )
            wait_for_simulator_page(backend)

            cases = [
                ("总开关关闭", False, True, True, True),
                ("三类题全部开启", True, True, True, True),
                ("仅选择题", True, True, False, False),
                ("仅判断题", True, False, True, False),
                ("仅填空题", True, False, False, True),
            ]
            config = load_current_config()
            cases.append((
                "当前 config.json 设置",
                bool(config.course_quiz_auto_answer),
                bool(config.course_quiz_choice_enabled),
                bool(config.course_quiz_judgment_enabled),
                bool(config.course_quiz_blank_enabled),
            ))
            print(f"模拟浏览器：{browser_name}")
            for name, enabled, choice, judgment, blank in cases:
                passed, summary, result = run_case(
                    backend,
                    name,
                    enabled=enabled,
                    choice=choice,
                    judgment=judgment,
                    blank=blank,
                )
                failures += 0 if passed else 1
                print(
                    f"{'通过' if passed else '失败'} | {name} | "
                    f"作答={summary['done']} 失败={summary['failed']} | "
                    f"选择={result['choice'] or '-'} 判断={result['judgment'] or '-'} "
                    f"填空={result['blank'] or '-'} 可信点击={result['trustedClicks']}"
                )
            if args.show and args.hold > 0:
                print(f"浏览器窗口保留 {args.hold:g} 秒，页面显示最后一组（当前设置）的结果。")
                time.sleep(args.hold)
        finally:
            if backend is not None:
                backend.close()
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                time.sleep(0.8)
            server.shutdown()
            server.server_close()

    if failures:
        print(f"模拟测试失败：{failures} 组未通过。")
        return 1
    print("模拟测试完成：全部场景通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
