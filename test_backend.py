"""不访问学校接口的基础回归测试。运行：python -m unittest -v test_backend.py"""

import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
