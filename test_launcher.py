"""浏览器版启动器的本地回归测试。"""

import json
import socket
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

import browser_launcher
from app_paths import data_root, frontend_dist, is_frozen, resource_root
from browser_launcher import choose_available_port, choose_frontend_port, service_command
from web_server import (
    CLIENT_CLOSED_EVENT, LocalApiHandler, SHUTDOWN_EVENT, allowed_cors_origin,
    client_last_seen, reset_client_state,
)


class FrozenPathTests(unittest.TestCase):
    """冻结（PyInstaller onedir）模式下的服务启动与路径解析。"""

    def test_frozen_service_command_uses_current_executable(self):
        exe = r"C:\Release\dgut-bot.exe"
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", exe):
            self.assertTrue(is_frozen())
            self.assertEqual(
                service_command(8766, use_static=True, api_port=8766),
                [exe, "--service", "8766", "--api-port", "8766", "--static"],
            )

    def test_dev_service_command_runs_launcher_script_with_interpreter(self):
        self.assertFalse(is_frozen())
        command = service_command(1420, use_static=False, api_port=8766)
        self.assertEqual(command[2:], ["--service", "1420", "--api-port", "8766"])
        self.assertNotIn("--static", command)
        self.assertTrue(Path(command[1]).name == "browser_launcher.py")

    def test_frozen_mode_always_serves_static_frontend(self):
        with patch.object(sys, "frozen", True, create=True):
            self.assertTrue(browser_launcher.static_frontend_available())

    def test_frontend_dist_resolves_under_data_root(self):
        self.assertEqual(frontend_dist(), data_root() / "web" / "dist")

    def test_dev_resource_root_is_source_directory(self):
        self.assertEqual(resource_root(), Path(browser_launcher.__file__).resolve().parent)


class BrowserLauncherTests(unittest.TestCase):
    def test_missing_heartbeat_stops_service_after_background_grace_period(self):
        self.assertFalse(browser_launcher.client_connection_expired(False, 0, now=100))
        timeout = browser_launcher.CLIENT_HEARTBEAT_TIMEOUT
        self.assertFalse(browser_launcher.client_connection_expired(True, 100 - timeout + 0.1, now=100))
        self.assertTrue(browser_launcher.client_connection_expired(True, 100 - timeout, now=100))

    @patch("browser_launcher.time.sleep")
    @patch("builtins.print")
    def test_duplicate_instance_notice_stays_visible(self, output, sleep):
        browser_launcher.show_already_running_notice()
        self.assertIn("请勿重复打开", output.call_args_list[0].args[0])
        self.assertIn("4 秒后", output.call_args_list[1].args[0])
        sleep.assert_called_once_with(browser_launcher.DUPLICATE_NOTICE_SECONDS)

    def test_cors_origin_requires_exact_loopback_host_and_allowed_port(self):
        self.assertEqual(allowed_cors_origin("http://localhost:1420"), "http://localhost:1420")
        self.assertEqual(allowed_cors_origin("http://127.0.0.1:8765"), "http://127.0.0.1:8765")
        self.assertEqual(allowed_cors_origin("http://127.0.0.1:8766"), "http://127.0.0.1:8766")
        self.assertEqual(allowed_cors_origin("http://127.0.0.1.evil.example:1420"), "http://127.0.0.1:1420")
        self.assertEqual(allowed_cors_origin("http://localhost.evil.example:1420"), "http://127.0.0.1:1420")
        self.assertEqual(allowed_cors_origin("http://localhost:9999"), "http://127.0.0.1:1420")

    @patch("builtins.print")
    def test_startup_help_precedes_two_separator_lines(self, output):
        browser_launcher.print_startup_help()
        lines = [call.args[0] for call in output.call_args_list]
        self.assertIn("Edge → Chrome → 其他 Chromium", lines[0])
        self.assertEqual(lines[-2], "-" * 72)
        self.assertEqual(lines[-1], "-" * 72)

    @patch("browser_launcher.backend.find_browser", return_value=(None, None))
    def test_frontend_requests_terminal_setup_when_detection_fails(self, _find_browser):
        self.assertEqual(browser_launcher.open_frontend("http://127.0.0.1:1420"), "manual")

    @patch("browser_launcher.backend.start_browser", return_value=True)
    @patch("browser_launcher.backend.update_settings")
    @patch("browser_launcher.backend.find_browser", return_value=(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "Microsoft Edge"))
    def test_frontend_reports_and_saves_detected_browser(self, _find_browser, update_settings, start_browser):
        self.assertEqual(browser_launcher.open_frontend("http://127.0.0.1:1420"), "debug")
        update_settings.assert_called_once_with(
            browser_name="Microsoft Edge",
            browser_path=r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        )
        start_browser.assert_called_once_with("http://127.0.0.1:1420")

    @patch("browser_launcher.backend.start_browser")
    @patch("browser_launcher.backend.update_settings")
    @patch("browser_launcher.backend.find_browser", return_value=(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "Microsoft Edge"))
    def test_browser_can_be_prepared_without_opening_a_page(self, _find_browser, _update_settings, start_browser):
        self.assertEqual(browser_launcher.open_frontend(""), "debug")
        start_browser.assert_not_called()

    @patch("browser_launcher.backend.update_settings")
    def test_terminal_accepts_a_quoted_chrome_executable(self, update_settings):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "Chrome Folder" / "chrome.exe"
            executable.parent.mkdir()
            executable.touch()
            self.assertTrue(browser_launcher.configure_browser_path(f'"{executable}"'))
        update_settings.assert_called_once_with(browser_name="Google Chrome", browser_path=str(executable.resolve()))

    @patch("browser_launcher.backend.update_settings")
    def test_terminal_accepts_a_portable_chromium_executable(self, update_settings):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "PortableBrowser" / "browser.exe"
            executable.parent.mkdir()
            executable.touch()
            self.assertTrue(browser_launcher.configure_browser_path(str(executable)))
        update_settings.assert_called_once_with(browser_name="自定义浏览器", browser_path=str(executable.resolve()))

    @patch("browser_launcher.backend.update_settings")
    def test_terminal_setup_only_accepts_a_browser_executable_path(self, update_settings):
        self.assertFalse(browser_launcher.configure_browser_path("edge"))
        self.assertFalse(browser_launcher.configure_browser_path(r"Z:\\Browser Folder\\browser.exe"))
        update_settings.assert_not_called()

    @patch("browser_launcher.configure_browser_path", side_effect=[False, True])
    @patch("builtins.input", side_effect=['"Z:\\Browser Folder\\browser.exe"', '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"'])
    def test_terminal_prompt_accepts_quoted_paths_and_retries(self, _input, configure):
        self.assertTrue(browser_launcher.prompt_for_browser("http://127.0.0.1:1420"))
        self.assertEqual(configure.call_args_list[0].args[0], '"Z:\\Browser Folder\\browser.exe"')
        self.assertEqual(configure.call_args_list[1].args[0], '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"')

    def test_chooses_another_port_when_first_port_is_busy(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            occupied_port = listener.getsockname()[1]
            selected = choose_frontend_port(occupied_port, attempts=20)
        self.assertNotEqual(selected, occupied_port)

    def test_service_port_moves_when_8765_is_busy(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            occupied_port = listener.getsockname()[1]
            selected = choose_available_port(occupied_port, attempts=20)
        self.assertNotEqual(selected, occupied_port)
        self.assertLess(selected, occupied_port + 20)

    def test_shutdown_command_sets_launcher_event_after_reply(self):
        SHUTDOWN_EVENT.clear()
        server = ThreadingHTTPServer(("127.0.0.1", 0), LocalApiHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        request = Request(
            f"http://127.0.0.1:{port}/api/command",
            data=json.dumps({"command": "shutdown_app", "payload": {}}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.assertTrue(result["ok"])
            self.assertTrue(SHUTDOWN_EVENT.wait(1))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            SHUTDOWN_EVENT.clear()

    def test_heartbeat_recovers_from_page_refresh_close_signal(self):
        reset_client_state()
        server = ThreadingHTTPServer(("127.0.0.1", 0), LocalApiHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            closed = Request(f"http://127.0.0.1:{port}/api/client-closed", data=b"", method="POST")
            with urlopen(closed, timeout=3):
                pass
            self.assertTrue(CLIENT_CLOSED_EVENT.is_set())

            heartbeat = Request(f"http://127.0.0.1:{port}/api/heartbeat", data=b"", method="POST")
            with urlopen(heartbeat, timeout=3):
                pass
            self.assertFalse(CLIENT_CLOSED_EVENT.is_set())
            self.assertGreater(client_last_seen(), 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            reset_client_state()


if __name__ == "__main__":
    unittest.main()
