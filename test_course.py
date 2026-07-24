"""课程页辅助的离线单元测试；不连接真实浏览器。"""

import json
import unittest
from unittest.mock import patch

from yxy_course import COURSE_TAB_URL_KEYWORD, INJECT_JS, CourseConfig, CourseController


class CourseConfigTests(unittest.TestCase):
    def test_config_serializes_supported_controls_only(self):
        config = CourseConfig(playback_rate=4.0, document_scroll_speed=2.0)
        values = json.loads(config.to_js())
        self.assertEqual(values["playback_rate"], 4.0)
        self.assertEqual(values["document_scroll_speed"], 2.0)
        self.assertNotIn("auto_answer", values)


class InjectScriptTests(unittest.TestCase):
    def test_video_recovery_and_chapter_dialog_are_present(self):
        self.assertIn("loadedmetadata", INJECT_JS)
        self.assertIn("ratechange", INJECT_JS)
        self.assertIn("继续下一章", INJECT_JS)
        self.assertIn("恭喜你完成本章", INJECT_JS)

    def test_script_has_no_answer_fetch_or_submission(self):
        for forbidden in ("questionAnswer", "correctAnswerList", "fetchAndAnswer", "btn-submit"):
            self.assertNotIn(forbidden, INJECT_JS)


class CdpDocumentTests(unittest.TestCase):
    def test_finds_and_attaches_oopif_document(self):
        controller = CourseController(lambda _text, _kind: None)
        with patch.object(
            controller,
            "_cdp_call",
            side_effect=[
                {"targetInfos": [{"targetId": "doc-1", "type": "iframe", "url": "https://docs.ulearning.cn/view/1"}]},
                {"sessionId": "session-1"},
            ],
        ):
            targets = controller._document_targets()
        self.assertEqual(
            targets,
            [{"id": "doc-1", "url": "https://docs.ulearning.cn/view/1", "sessionId": "session-1", "kind": "oopif"}],
        )

    def test_document_completes_only_after_real_scroll(self):
        events = []
        controller = CourseController(lambda text, _kind: events.append(text))
        item = {"id": "doc-1", "url": "https://docs.ulearning.cn/view/1"}
        with patch.object(controller, "_cdp_eval") as evaluate:
            controller._handle_document_scroll_state(item, {"state": "complete"})
            evaluate.assert_not_called()
            controller._handle_document_scroll_state(item, {"state": "scrolled"})
            controller._handle_document_scroll_state(item, {"state": "complete"})
        evaluate.assert_called_once_with("window.__yxy_go_next && window.__yxy_go_next()", timeout=5.0)
        self.assertTrue(any("文档已滚动至末尾" in event for event in events))


class CourseTabTests(unittest.TestCase):
    @patch("yxy_course.requests.get")
    def test_finds_course_tab(self, mock_get):
        mock_get.return_value.json.return_value = [
            {"type": "page", "url": f"https://{COURSE_TAB_URL_KEYWORD}/learnCourse.html", "webSocketDebuggerUrl": "ws://course"},
            {"type": "page", "url": "https://lms.dgut.edu.cn/", "webSocketDebuggerUrl": "ws://lms"},
        ]
        self.assertEqual(CourseController(lambda _text, _kind: None).find_course_tab(), "ws://course")


if __name__ == "__main__":
    unittest.main()
