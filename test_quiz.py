"""yxy_quiz.QuizHandler 的离线单元测试；不连接真实浏览器。"""

import json
import unittest

from yxy_quiz import QuizHandler


def make_state(questions, modal=None, viewport=None):
    return json.dumps(
        {
            "present": True,
            "viewport": viewport or {"w": 1528, "h": 779},
            "questions": questions,
            "modal": modal,
        },
        ensure_ascii=False,
    )


def choice(label, text, x=900, y=300):
    return {"label": label, "text": f"{label}. {text}", "pos": {"x": x, "y": y}}


def question(qid, qtype, title, choices=None, judgment=None, finished=False, submit=None):
    return {
        "qid": qid,
        "finished": finished,
        "type": qtype,
        "title": title,
        "choices": choices or [],
        "judgment": judgment or [],
        "blanks": [],
        "submit": submit or {"x": 914, "y": 527},
    }


class FakePage:
    """点击驱动的假页面：读取始终返回当前状态，每次点击推进到下一个状态。"""

    def __init__(self, states):
        self.states = list(states)
        self.index = 0
        self.clicks = []

    def evaluate(self, _expression):
        if "scrollIntoView" in _expression:
            return "ok"
        return self.states[self.index]

    def click(self, x, y):
        self.clicks.append((round(x), round(y)))
        if self.index < len(self.states) - 1:
            self.index += 1
        return True


class ModalTests(unittest.TestCase):
    def test_forward_button_preferred_for_advance(self):
        modal = {
            "text": "本页面还有题目没有完成",
            "buttons": [
                {"text": "留在本页", "pos": {"x": 100, "y": 100}},
                {"text": "确定离开", "pos": {"x": 200, "y": 200}},
            ],
        }
        page = FakePage([])
        handler = QuizHandler(evaluate=page.evaluate, click=page.click, dry_run=False, jitter=0)
        summary = {"modals": 0}
        self.assertTrue(handler.handle_modal(modal, summary, advance=True))
        self.assertEqual(page.clicks, [(200, 200)])

    def test_chapter_stat_modal_forward_is_next_chapter(self):
        modal = {
            "text": "本章成绩 0分",
            "buttons": [
                {"text": "<< 留在本页", "pos": {"x": 100, "y": 100}},
                {"text": "继续下一章 >>", "pos": {"x": 200, "y": 200}},
            ],
        }
        page = FakePage([])
        handler = QuizHandler(evaluate=page.evaluate, click=page.click, dry_run=False, jitter=0)
        self.assertTrue(handler.handle_modal(modal, {"modals": 0}, advance=True))
        self.assertEqual(page.clicks, [(200, 200)])

    def test_unknown_buttons_are_rejected(self):
        modal = {"text": "?", "buttons": [{"text": "随便", "pos": {"x": 1, "y": 1}}]}
        page = FakePage([])
        handler = QuizHandler(evaluate=page.evaluate, click=page.click, dry_run=False)
        self.assertFalse(handler.handle_modal(modal, {"modals": 0}))
        self.assertEqual(page.clicks, [])

    def test_suspend_modal_single_button_is_clicked(self):
        # 走神检测弹窗：唯一按钮「继续学习」，必须被当作前进方向
        modal = {
            "text": "计时学习已暂停 走神太久啦",
            "buttons": [{"text": "继续学习", "pos": {"x": 764, "y": 514}}],
        }
        page = FakePage([])
        handler = QuizHandler(evaluate=page.evaluate, click=page.click, dry_run=False, jitter=0)
        self.assertTrue(handler.handle_modal(modal, {"modals": 0}, advance=True))
        self.assertEqual(page.clicks, [(764, 514)])


class AnswerFlowTests(unittest.TestCase):
    def test_dry_run_lists_every_question_without_clicking(self):
        state = make_state(
            [
                question(
                    "q1",
                    "判断题",
                    "题干一",
                    judgment=[
                        {"label": "正确", "pos": {"x": 900, "y": 300}},
                        {"label": "错误", "pos": {"x": 900, "y": 360}},
                    ],
                ),
                question("q2", "单选题", "题干二", choices=[choice("A", "甲"), choice("C", "丙")]),
            ]
        )
        page = FakePage([state])
        handler = QuizHandler(evaluate=page.evaluate, click=page.click, dry_run=True)
        summary = handler.answer_all()
        self.assertEqual(summary["done"], 2)
        self.assertEqual(page.clicks, [])

    def test_judgment_placeholder_defaults_to_wrong(self):
        state = make_state(
            [
                question(
                    "q1",
                    "判断题",
                    "题干一",
                    judgment=[
                        {"label": "正确", "pos": {"x": 900, "y": 300}},
                        {"label": "错误", "pos": {"x": 900, "y": 360}},
                    ],
                )
            ]
        )
        page = FakePage([state])
        handler = QuizHandler(evaluate=page.evaluate, click=page.click, dry_run=True)
        handler.answer_all()
        # dry-run 不点击，验证计划目标：直接检查选择逻辑选中的是「错误」
        fresh = handler.read_state()
        target = "错误"
        entry = next(j for j in fresh.questions[0].judgment if j["label"] == target)
        self.assertEqual(entry["pos"], {"x": 900, "y": 360})

    def test_choice_question_answers_with_placeholder_and_submits(self):
        unanswered = make_state(
            [
                question(
                    "q1",
                    "单选题",
                    "题干",
                    choices=[choice("A", "甲"), choice("B", "乙"), choice("C", "丙"), choice("D", "丁")],
                )
            ]
        )
        answered = make_state(
            [
                question(
                    "q1",
                    "单选题",
                    "题干",
                    choices=[choice("A", "甲"), choice("B", "乙"), choice("C", "丙"), choice("D", "丁")],
                    finished=True,
                )
            ]
        )
        page = FakePage([unanswered, answered])
        handler = QuizHandler(evaluate=page.evaluate, click=page.click, dry_run=False, jitter=0)
        summary = handler.answer_all(option_label="C")
        self.assertEqual(summary["done"], 1)
        # 至少两次点击：选项 C（y=300） + 卷尾提交（y=527）
        self.assertIn((900, 300), page.clicks)
        self.assertIn((914, 527), page.clicks)

    def test_blank_question_is_filled_and_submitted(self):
        state = make_state([question("q1", "填空题", "题干")])
        payload = json.loads(state)
        payload["questions"][0]["blanks"] = [{"pos": {"x": 900, "y": 300}, "value": ""}]
        answered = json.loads(make_state([question("q1", "填空题", "题干", finished=True)]))
        answered["questions"][0]["blanks"] = [{"pos": {"x": 900, "y": 300}, "value": ","}]
        page = FakePage([json.dumps(payload, ensure_ascii=False), json.dumps(answered, ensure_ascii=False)])
        typed = []
        handler = QuizHandler(
            evaluate=page.evaluate,
            click=page.click,
            type_text=lambda text: typed.append(text) or True,
            dry_run=False,
            jitter=0,
        )
        summary = handler.answer_all()
        self.assertEqual(summary["skipped"], 0)
        self.assertEqual(summary["done"], 1)
        self.assertEqual(typed, [","])
        self.assertIn((900, 300), page.clicks)

    def test_stops_when_no_quiz_present(self):
        page = FakePage(['{"present": false}'])
        handler = QuizHandler(evaluate=page.evaluate, click=page.click, dry_run=False)
        summary = handler.answer_all()
        self.assertEqual(
            summary, {"done": 0, "skipped": 0, "failed": 0, "modals": 0}
        )


if __name__ == "__main__":
    unittest.main()
