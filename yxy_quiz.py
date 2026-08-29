"""优学院测验自动作答模块。

依据《测验页面结构调研.md》的实测结构工作：测验渲染在 learnCourse 主文档
（无 iframe），已答判据是 .question-wrapper 的 finished 类，提交按钮是卷尾
.question-operation-area > .btn-submit（整套提交，点一次全部判卷），未答完
翻页会弹 .modal.fade.in 确认框。本模块只通过 CDP Input 域产生真实点击
（isTrusted 事件），不调用任何作答接口。

作答策略是固定占位：选择题点 C、判断题点"错误"、每个填空输入英文逗号。
无法识别的题型会记录并跳过，未答完离开页面由弹窗放行。

QuizHandler 不持有任何连接：通过构造参数注入 evaluate/click/type_text 等
回调，既能被 CourseController 复用，也能用底部的 CLI 后端独立运行。
"""

from __future__ import annotations

import json
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.request import urlopen

from websocket import create_connection

PORT = 9222
TAB_KEYWORD = "ua.dgut.edu.cn/learnCourse"

FORWARD_BUTTON_PATTERN = ("确定离开", "继续下一章", "继续学习", "关闭", "确定", "继续")
STAY_BUTTON_PATTERN = ("留在本页",)


# 页面状态读取脚本：一次 evaluate 拿回题目、坐标、提交按钮与弹窗。
# 只读；坐标为视口坐标，与 Input.dispatchMouseEvent 直接兼容。
QUIZ_STATE_JS = r"""
(function() {
  function visible(el) {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  function center(el) {
    const r = el.getBoundingClientRect();
    return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
  }
  const view = document.querySelector('.question-view');
  if (!view || !visible(view)) return JSON.stringify({present: false});
  const questions = [];
  document.querySelectorAll('.question-wrapper').forEach(function(w) {
    const tagEl = w.querySelector('.question-type-tag');
    const titleEl = w.querySelector('.question-title-html');
    const choices = [];
    w.querySelectorAll('a.choice-item').forEach(function(a) {
      if (!visible(a)) return;
      const label = ((a.querySelector('.option') || {}).innerText || '').trim().replace(/[.。]+\s*$/, '');
      choices.push({label: label, text: (a.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80), pos: center(a)});
    });
    const judgment = [];
    w.querySelectorAll('.checking-type .choice-btn').forEach(function(b) {
      if (!visible(b)) return;
      judgment.push({label: /right-btn/.test(b.className) ? '正确' : '错误', pos: center(b)});
    });
    const blanks = [];
    w.querySelectorAll('.answer-width').forEach(function(s) {
      if (visible(s)) {
        const input = s.matches('input, textarea, [contenteditable="true"]')
          ? s : s.querySelector('input, textarea, [contenteditable="true"]');
        const target = input && visible(input) ? input : s;
        blanks.push({pos: center(target), value: String(input ? input.value || input.innerText || '' : s.innerText || '')});
      }
    });
    let submit = null;
    const area = document.querySelector('.question-operation-area .btn-submit');
    if (area && visible(area)) submit = center(area);
    questions.push({
      qid: w.id,
      finished: w.classList.contains('finished'),
      type: (tagEl ? tagEl.innerText : '').trim(),
      title: (titleEl ? titleEl.innerText : '').trim().slice(0, 300),
      choices: choices,
      judgment: judgment,
      blanks: blanks,
      submit: submit
    });
  });
  let modal = null;
  document.querySelectorAll('.modal').forEach(function(m) {
    if (modal || !visible(m)) return;
    const buttons = [];
    m.querySelectorAll('button, [role=button]').forEach(function(b) {
      if (!visible(b)) return;
      buttons.push({text: (b.innerText || '').trim().replace(/\s+/g, ' '), pos: center(b)});
    });
    if (buttons.length) {
      modal = {text: (m.innerText || '').replace(/\s+/g, ' ').slice(0, 120), buttons: buttons};
    }
  });
  return JSON.stringify({
    present: true,
    viewport: {w: window.innerWidth, h: window.innerHeight},
    questions: questions,
    modal: modal
  });
})()
"""

SCROLL_QUESTION_JS = """
(function(id) {
  const w = document.getElementById(id);
  if (!w) return 'missing';
  w.scrollIntoView({block: 'center', behavior: 'instant'});
  return 'ok';
})(%s)
"""

# 一页卷模式下卷尾提交按钮在全部题目之后，长页时必然在视口外，点击前先滚动
SCROLL_SUBMIT_JS = """
(function() {
  const b = document.querySelector('.question-operation-area .btn-submit');
  if (!b) return 'missing';
  b.scrollIntoView({block: 'center', behavior: 'instant'});
  return 'ok';
})()
"""

SCROLL_BLANK_JS = """
(function(id, index) {
  const w = document.getElementById(id);
  if (!w) return 'missing-question';
  const blanks = w.querySelectorAll('.answer-width');
  const blank = blanks[index];
  if (!blank) return 'missing-blank';
  const input = blank.matches('input, textarea, [contenteditable="true"]')
    ? blank : blank.querySelector('input, textarea, [contenteditable="true"]');
  (input || blank).scrollIntoView({block: 'center', behavior: 'instant'});
  return 'ok';
})(%s, %d)
"""


@dataclass
class Question:
    qid: str
    finished: bool
    type: str
    title: str
    choices: list = field(default_factory=list)
    judgment: list = field(default_factory=list)
    blanks: list = field(default_factory=list)
    submit: dict | None = None


@dataclass
class QuizState:
    present: bool
    viewport: dict = field(default_factory=dict)
    questions: list[Question] = field(default_factory=list)
    modal: dict | None = None


class QuizHandler:
    """测验作答核心；全部页面动作通过注入的回调完成，便于复用与测试。"""

    def __init__(
        self,
        *,
        evaluate: Callable[[str], Any],
        click: Callable[[float, float], bool],
        is_running: Callable[[], bool] = lambda: True,
        type_text: Callable[[str], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[..., None] = print,
        dry_run: bool = True,
        jitter: float = 3.0,
    ) -> None:
        self._evaluate = evaluate
        self._click = click
        self._is_running = is_running
        self._type_text = type_text
        self._sleep = sleep
        self._log = log
        self.dry_run = dry_run
        self._jitter = max(0.0, jitter)

    # ---- 页面读取 ----

    def read_state(self) -> QuizState:
        value = self._evaluate(QUIZ_STATE_JS)
        if not isinstance(value, str):
            return QuizState(present=False)
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            self._log("读取测验页面状态失败，稍后重试", "warn")
            return QuizState(present=False)
        questions = [
            Question(**{k: item[k] for k in (
                "qid", "finished", "type", "title", "choices", "judgment", "blanks", "submit",
            )})
            for item in data.get("questions", [])
        ]
        return QuizState(
            present=bool(data.get("present")),
            viewport=data.get("viewport") or {},
            questions=questions,
            modal=data.get("modal"),
        )

    def _scroll_question_into_view(self, qid: str) -> bool:
        return self._evaluate(SCROLL_QUESTION_JS % json.dumps(qid)) == "ok"

    # ---- 点击原语 ----

    def _click_point(self, pos: dict, label: str = "") -> bool:
        x = float(pos["x"]) + random.uniform(-self._jitter, self._jitter)
        y = float(pos["y"]) + random.uniform(-self._jitter, self._jitter)
        return self._click(x, y)

    def _click_in_viewport(self, pos: dict, viewport: dict, label: str = "") -> bool:
        vh = float(viewport.get("h", 0))
        if vh and not (0 <= float(pos["y"]) <= vh):
            self._log(f"  {label}不在视口内（y={pos['y']}），放弃", "warn")
            return False
        return self._click_point(pos, label)

    # ---- 弹窗 ----

    def handle_modal(self, modal: dict, summary: dict, advance: bool = True) -> bool:
        """点掉 .modal.fade.in。advance=True 点前进按钮（确定离开/继续下一章）。"""
        forward, stay = None, None
        for button in modal.get("buttons", []):
            text = button.get("text", "")
            if any(pattern in text for pattern in FORWARD_BUTTON_PATTERN):
                forward = forward or button
            elif any(pattern in text for pattern in STAY_BUTTON_PATTERN):
                stay = stay or button
        target = forward if advance else stay
        if target is None:
            self._log(f"  弹窗无可识别按钮，文本：{modal.get('text', '')[:60]}", "warn")
            return False
        summary["modals"] += 1
        if self.dry_run:
            self._log(f"  [dry-run] 将点击弹窗按钮「{target['text']}」{target['pos']}")
            return True
        self._log(f"  弹窗：点击「{target['text']}」")
        if not self._click_point(target["pos"], f"弹窗按钮{target['text']}"):
            return False
        self._sleep(random.uniform(0.8, 1.6))
        return True

    # ---- 单题选择（不提交） ----

    def _fresh_question(self, qid: str) -> Question | None:
        return next((q for q in self.read_state().questions if q.qid == qid), None)

    def _select_answer(self, state: QuizState, q: Question, option_label: str, judgment_label: str) -> str:
        """点击一道未完成题的选项（不提交）。返回 'done' / 'skipped' / 'failed' / 'planned'。"""
        target = judgment_label if q.type == "判断题" else option_label
        if q.judgment:
            entry = next((j for j in q.judgment if j["label"] == target), None)
        else:
            entry = next((c for c in q.choices if c["label"].upper() == target.upper()), None)
        if entry is None:
            self._log(f"  题{q.qid} 找不到选项「{target}」，跳过")
            return "skipped"

        if self.dry_run:
            self._log(f"  [dry-run] 题{q.qid} {q.type} 将点「{target}」")
            return "planned"

        # 滚动到题目并等待坐标稳定；组件刚挂载时框架可能重置滚动位置，需重试
        fresh = None
        entry = None
        for attempt in range(3):
            if not self._scroll_question_into_view(q.qid):
                self._log(f"  题{q.qid} 滚动失败，跳过", "warn")
                return "skipped"
            self._sleep(random.uniform(0.4, 0.9) * (attempt + 1))
            fresh = self._fresh_question(q.qid)
            if fresh is None:
                return "failed"
            if fresh.finished:
                return "done"
            if fresh.judgment:
                entry = next((j for j in fresh.judgment if j["label"] == target), None)
            else:
                entry = next((c for c in fresh.choices if c["label"].upper() == target.upper()), None)
            if entry is not None and 0 <= float(entry["pos"]["y"]) <= float(state.viewport.get("h", 0)):
                break
            self._log(f"  题{q.qid} 第{attempt + 1}次滚动后选项仍在视口外，重试")
            entry = None
        if entry is None:
            self._log(f"  题{q.qid} 多次滚动后仍无法进入视口，跳过该题", "warn")
            return "skipped"
        if not self._click_in_viewport(entry["pos"], state.viewport, f"题{q.qid}选项{target}"):
            return "failed"
        self._sleep(random.uniform(0.5, 1.2))
        self._log(f"  题{q.qid} 已选择「{target}」")
        return "done"

    def _fill_blanks(self, state: QuizState, q: Question, answers: list[str]) -> str:
        if self._type_text is None or not q.blanks:
            self._log(f"  题{q.qid} 未找到可输入的填空控件，跳过", "warn")
            return "skipped"
        if self.dry_run:
            self._log(f"  [dry-run] 题{q.qid} {q.type} 将填写 {len(q.blanks)} 个空")
            return "planned"
        if not self._scroll_question_into_view(q.qid):
            return "skipped"
        self._sleep(random.uniform(0.3, 0.8))
        fresh = self._fresh_question(q.qid)
        if fresh is None:
            return "failed"
        blank_count = len(fresh.blanks)
        for index in range(blank_count):
            if index >= len(answers):
                break
            if self._evaluate(SCROLL_BLANK_JS % (json.dumps(q.qid), index)) != "ok":
                self._log(f"  题{q.qid} 第{index + 1}空无法滚动到视口", "warn")
                return "skipped"
            self._sleep(random.uniform(0.3, 0.7))
            fresh = self._fresh_question(q.qid)
            if fresh is None or index >= len(fresh.blanks):
                return "failed"
            blank = fresh.blanks[index]
            pos = blank.get("pos") if isinstance(blank, dict) else blank
            if not isinstance(pos, dict) or not self._click_in_viewport(pos, state.viewport, f"题{q.qid}空{index + 1}"):
                return "failed"
            self._sleep(random.uniform(0.2, 0.5))
            if not self._type_text(str(answers[index])):
                self._log(f"  题{q.qid} 第{index + 1}空输入失败", "warn")
                return "failed"
            self._sleep(random.uniform(0.3, 0.8))
        self._log(f"  题{q.qid} 已填写 {min(blank_count, len(answers))} 个空")
        return "done"

    # ---- 提交与验证 ----

    def _submit_and_wait(self, state: QuizState, qid_sample: str, attempted_qids: set[str]) -> str:
        """点卷尾提交并等待本轮已填写/选择的题 finished。"""
        if self._evaluate(SCROLL_SUBMIT_JS) != "ok":
            return "failed"
        self._sleep(random.uniform(0.4, 0.9))
        fresh = self._fresh_question(qid_sample)
        if fresh is None or fresh.submit is None:
            return "failed"
        latest = self.read_state()
        if not self._click_in_viewport(fresh.submit, latest.viewport, "卷尾提交按钮"):
            return "failed"
        for _ in range(60):
            self._sleep(0.5)
            if not self._is_running():
                return "failed"
            check = self.read_state()
            if not check.present:
                return "done"
            if check.modal is not None:
                self.handle_modal(check.modal, {"modals": 0}, advance=True)
                continue
            by_id = {q.qid: q for q in check.questions}
            if attempted_qids and all(by_id.get(qid) is None or by_id[qid].finished for qid in attempted_qids):
                return "done"
        return "nomove"

    # ---- 主循环 ----

    def answer_all(
        self,
        *,
        option_label: str = "C",
        judgment_label: str = "错误",
        blank_text: str = ",",
        max_questions: int = 30,
        advance_on_modal: bool = True,
    ) -> dict:
        """处理当前页面的整套测验：选择全部可答题 → 卷尾统一提交。

        实测（调研 §5）：卷尾 submitQuiz 是整套提交，点一次全部判卷；
        因此本方法先逐题选择、最后只提交一次。
        """
        summary = {"done": 0, "skipped": 0, "failed": 0, "modals": 0}

        # 弹窗优先清空（可能残留上一页的章节统计弹窗）
        for _ in range(4):
            state = self.read_state()
            if not state.present or state.modal is None:
                break
            if not self.handle_modal(state.modal, summary, advance=advance_on_modal):
                break

        state = self.read_state()
        if not state.present:
            return summary

        # 阶段1：逐题选择（不提交）
        attempted: set[str] = set()
        planned_done = False
        for _ in range(max(1, max_questions)):
            if not self._is_running():
                return summary
            state = self.read_state()
            if not state.present:
                break
            if state.modal is not None:
                if not self.handle_modal(state.modal, summary, advance=advance_on_modal):
                    break
                continue
            pending = next((q for q in state.questions if not q.finished and q.qid not in attempted), None)
            if pending is None:
                break
            attempted.add(pending.qid)
            if pending.type == "填空题":
                outcome = self._fill_blanks(state, pending, [blank_text] * len(pending.blanks))
            else:
                outcome = self._select_answer(state, pending, option_label, judgment_label)
            if outcome in ("done", "planned"):
                summary["done"] += 1
                if outcome == "planned":
                    planned_done = True
                    for q in state.questions:
                        if not q.finished and q.qid not in attempted:
                            self._select_answer(state, q, option_label, judgment_label)
                            summary["done"] += 1
                    break
            elif outcome == "skipped":
                summary["skipped"] += 1
            else:
                summary["failed"] += 1
                break
            self._sleep(random.uniform(1.2, 3.0))

        if planned_done or self.dry_run:
            return summary
        if summary["done"] == 0:
            # 没有任何可执行的选择或输入动作时，不提交空卷。
            return summary

        # 阶段2：卷尾统一提交
        state = self.read_state()
        if not state.present or not state.questions:
            return summary
        submitted_qids = {qid for qid in attempted if qid}
        result = self._submit_and_wait(state, state.questions[0].qid, submitted_qids)
        if result == "failed":
            summary["failed"] += 1
            self._log("提交失败，请人工检查", "warn")
            return summary
        if result == "nomove":
            summary["failed"] += 1
            self._log("提交后未确认到全部完成，请人工检查（可能仍有未答题）", "warn")
        return summary


# ---- 独立 CLI 后端：自带一条 CDP 连接，便于单独验证 ----


class StandaloneBackend:
    """quiz_probe 同款的最小 CDP 客户端；仅主文档，不附加 iframe。"""

    def __init__(self, port: int = PORT, dry_run: bool = True, log: Callable[..., None] = print) -> None:
        targets = json.loads(urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5).read())
        pages = [t for t in targets if t.get("type") == "page" and TAB_KEYWORD in (t.get("url") or "")]
        if not pages:
            raise RuntimeError("未找到 learnCourse 标签页，请先在浏览器里打开课程页面。")
        self.ws = create_connection(pages[0]["webSocketDebuggerUrl"], timeout=15)
        self._msg_id = 0
        self._dry_run = dry_run
        self._log = log

    def evaluate(self, expression: str):
        self._msg_id += 1
        self.ws.send(json.dumps({
            "id": self._msg_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        }))
        self.ws.settimeout(15)
        while True:
            raw = json.loads(self.ws.recv())
            if raw.get("id") == self._msg_id:
                if raw.get("error"):
                    raise RuntimeError(f"CDP 错误：{raw['error']}")
                return (raw.get("result") or {}).get("result", {}).get("value")

    def click(self, x: float, y: float) -> bool:
        if self._dry_run:
            self._log(f"  [dry-run] 点击 ({x:.0f}, {y:.0f})")
            return True
        events = (
            {"type": "mouseMoved", "x": x, "y": y},
            {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
            {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
        )
        for params in events:
            self._msg_id += 1
            self.ws.send(json.dumps({"id": self._msg_id, "method": "Input.dispatchMouseEvent", "params": params}))
            self.ws.settimeout(10)
            while True:
                raw = json.loads(self.ws.recv())
                if raw.get("id") == self._msg_id:
                    break
        return True

    def type_text(self, text: str) -> bool:
        if self._dry_run:
            self._log(f"  [dry-run] 输入文本「{text}」")
            return True
        self._msg_id += 1
        self.ws.send(json.dumps({
            "id": self._msg_id,
            "method": "Input.insertText",
            "params": {"text": text},
        }))
        self.ws.settimeout(10)
        while True:
            raw = json.loads(self.ws.recv())
            if raw.get("id") == self._msg_id:
                return raw.get("error") is None


def main() -> int:
    args = sys.argv[1:]
    dry_run = "--commit" not in args
    option_label = "C"
    judgment_label = "错误"
    port = PORT
    for arg in args:
        if arg.startswith("--option="):
            option_label = arg.split("=", 1)[1].upper()
        elif arg.startswith("--judgment="):
            judgment_label = arg.split("=", 1)[1]
        elif arg.startswith("--port="):
            port = int(arg.split("=", 1)[1])

    backend = StandaloneBackend(port=port, dry_run=dry_run, log=print)
    handler = QuizHandler(
        evaluate=backend.evaluate,
        click=backend.click,
        type_text=backend.type_text,
        log=print,
        dry_run=dry_run,
    )
    state = handler.read_state()
    if not state.present:
        print("当前页面没有检测到测验 (.question-view)")
        return 1
    print(f"检测到 {len(state.questions)} 道题：")
    for q in state.questions:
        flag = "已答" if q.finished else "未答"
        print(f"  [{flag}] {q.type} {q.title[:50]}" + (f" (空×{len(q.blanks)})" if q.blanks else ""))
    if state.modal:
        print(f"检测到弹窗：{state.modal['text'][:60]}")
    print()
    summary = handler.answer_all(option_label=option_label, judgment_label=judgment_label)
    print(
        f"完成：done={summary['done']} skipped={summary['skipped']} "
        f"failed={summary['failed']} modals={summary['modals']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
