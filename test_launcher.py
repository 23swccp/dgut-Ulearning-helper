"""浏览器版启动器的本地回归测试。"""

import socket
import unittest

from browser_launcher import choose_frontend_port


class BrowserLauncherTests(unittest.TestCase):
    def test_chooses_another_port_when_first_port_is_busy(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            occupied_port = listener.getsockname()[1]
            selected = choose_frontend_port(occupied_port, attempts=20)
        self.assertNotEqual(selected, occupied_port)


if __name__ == "__main__":
    unittest.main()
