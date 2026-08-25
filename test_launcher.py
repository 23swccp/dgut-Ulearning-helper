"""浏览器版启动器的本地回归测试。"""

import json
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from browser_launcher import choose_frontend_port
from web_server import LocalApiHandler, SHUTDOWN_EVENT


class BrowserLauncherTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
