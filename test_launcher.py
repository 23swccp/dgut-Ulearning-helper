"""浏览器版启动器的本地回归测试。"""

import json
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import Request, urlopen

import browser_launcher
from browser_launcher import choose_frontend_port
from web_server import CLIENT_CLOSED_EVENT, LocalApiHandler, SHUTDOWN_EVENT, client_last_seen, reset_client_state


class BrowserLauncherTests(unittest.TestCase):
    def test_terminal_kill_command_is_echoed_and_parsed(self):
        if not hasattr(browser_launcher, "msvcrt"):
            self.skipTest("Windows console test")
        with patch.object(browser_launcher.msvcrt, "kbhit", return_value=True), patch.object(
            browser_launcher.msvcrt, "getwch", side_effect=list("kill\r")
        ), patch("builtins.print") as output:
            buffer, command = browser_launcher.poll_terminal_command("")
        self.assertEqual(buffer, "")
        self.assertEqual(command, "kill")
        self.assertGreaterEqual(output.call_count, 5)

    def test_chooses_another_port_when_first_port_is_busy(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            occupied_port = listener.getsockname()[1]
            selected = choose_frontend_port(occupied_port, attempts=20)
        self.assertNotEqual(selected, occupied_port)

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
