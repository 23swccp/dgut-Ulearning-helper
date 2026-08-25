"""不访问学校接口的基础回归测试。运行：python -m unittest -v test_backend.py"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yxy_backend import Activity, AppConfig, Course, SignBackend


class BackendTests(unittest.TestCase):
    def make_backend(self, root: Path) -> SignBackend:
        return SignBackend(lambda _text, _kind: None, root=root)

    def test_config_accepts_known_values_only(self):
        config = AppConfig.from_mapping({"poll_interval": 8, "unexpected": "ignored"}, Path("."))
        self.assertEqual(config.poll_interval, 8)
        self.assertFalse(hasattr(config, "unexpected"))

    def test_course_selection_accepts_one_exact_course_only(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self.make_backend(Path(directory))
            backend.courses = [Course(101, "数据结构"), Course(202, "决策分析")]
            selected = backend.select_course("101")
            self.assertEqual(selected.id, 101)
            self.assertIsNone(backend.select_course("101，决策"))
            backend.clear_selected_course()
            self.assertIsNone(backend.selected_course)

    def test_log_uses_one_append_only_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = self.make_backend(root)
            log_path = root / "签到记录.md"
            backend.update_settings(log_path=str(log_path))
            backend._write_sign_log("测试课程", "一键签到", ["HTTP/status: 200"])
            backend._write_sign_log("测试课程", "数字码签到", ["HTTP/status: 201"])
            content = log_path.read_text(encoding="utf-8")
            self.assertEqual(content.count("测试课程"), 2)
            self.assertIn("HTTP/status: 200", content)

    def test_activity_model_keeps_raw_extra_fields(self):
        activity = Activity.from_api({"relationId": 1, "scoreType": 3, "custom": "kept"})
        self.assertEqual(activity.score_type, 3)
        self.assertEqual(activity.raw["custom"], "kept")

    def test_browser_launch_uses_debug_mode_and_opens_requested_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = root / "msedge.exe"
            browser.touch()
            backend = self.make_backend(root)
            backend.config.browser_name = "Microsoft Edge"
            backend.config.browser_path = str(browser)
            url = "http://127.0.0.1:1420"

            with patch.object(backend, "_open_debug_tab", return_value=False), patch("yxy_backend.subprocess.Popen") as popen:
                self.assertTrue(backend.start_browser(url))

            command = popen.call_args.args[0]
            self.assertIn("--remote-debugging-port=9222", command)
            self.assertIn(url, command)

    def test_optional_account_login_is_saved_separately_and_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = self.make_backend(root)
            self.assertFalse(backend.update_account_login("", "", True))
            self.assertTrue(backend.update_account_login("20260001", "test-password", True))
            self.assertEqual(backend.account_login_status(), {"enabled": True, "username": "20260001", "has_password": True})
            self.assertTrue((root / "account.json").is_file())
            self.assertTrue(backend.update_account_login("", "", True))
            self.assertTrue(backend.update_account_login("", "", False))
            self.assertFalse((root / "account.json").exists())


if __name__ == "__main__":
    unittest.main()
