"""临时探测脚本：连上调试端口，扫描测验页 DOM 结构，确定选择器用。

用法：python quiz_probe.py [URL关键字]
默认找 ua.dgut.edu.cn 的标签页，把主文档和每个 iframe 里疑似题目/选项的
元素打印出来。只读不点。
"""

from __future__ import annotations

import json
import sys
import time
from urllib.request import urlopen

from websocket import create_connection

PORT = 9222


def http_json(path: str):
    with urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


class TabConnection:
    """一个标签页的所有 CDP 会话共用一条 websocket（flattened 模式）。"""

    def __init__(self, ws_url: str) -> None:
        self.ws = create_connection(ws_url, timeout=15)
        self._next_id = 0

    def send(self, method: str, params: dict | None = None, session_id: str | None = None, timeout: float = 10.0):
        self._next_id += 1
        msg: dict = {"id": self._next_id, "method": method}
        if params is not None:
            msg["params"] = params
        if session_id:
            msg["sessionId"] = session_id
        self.ws.send(json.dumps(msg))
        deadline = time.monotonic() + timeout
        while True:
            self.ws.settimeout(max(0.05, deadline - time.monotonic()))
            raw = json.loads(self.ws.recv())
            if raw.get("id") == self._next_id:
                return raw.get("result"), raw.get("error")
            # 其他响应/事件暂存子会话登记
            if raw.get("method") == "Target.attachedToTarget":
                info = raw["params"]["targetInfo"]
                ATTACHED[info["targetId"]] = raw["params"]["sessionId"]


ATTACHED: dict[str, str] = {}  # target_id -> session_id


def collect_sessions(conn: TabConnection) -> None:
    conn.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
    deadline = time.monotonic() + 3.0
    before = -1
    while time.monotonic() < deadline:
        if len(ATTACHED) == before and before >= 0:
            break
        try:
            conn.ws.settimeout(0.3)
            raw = json.loads(conn.ws.recv())
            if raw.get("method") == "Target.attachedToTarget":
                info = raw["params"]["targetInfo"]
                ATTACHED[info["targetId"]] = raw["params"]["sessionId"]
                # 子会话内部可能还有 iframe，逐层开启自动附加
                sid = raw["params"]["sessionId"]
                conn.send(
                    "Target.setAutoAttach",
                    {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
                    session_id=sid,
                )
        except Exception:
            pass


SCANNER_JS = r"""
(function() {
  const out = {
    href: location.href,
    title: document.title,
    inIframe: window !== window.top,
    candidates: [],
    radios: 0,
    buttons: []
  };
  const seen = new Set();
  function push(el, reason) {
    if (!el || seen.has(el)) return;
    seen.add(el);
    const r = el.getBoundingClientRect();
    const cls = typeof el.className === 'string' ? el.className : '';
    out.candidates.push({
      reason,
      tag: el.tagName.toLowerCase(),
      cls: cls.slice(0, 80),
      id: el.id ? el.id.slice(0, 40) : '',
      text: (el.innerText || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 60),
      checked: !!el.checked,
      rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}
    });
  }
  // 类名/id 含题目相关关键词的元素
  for (const el of document.querySelectorAll('[class],[id]')) {
    const sig = ((typeof el.className === 'string' ? el.className : '') + ' ' + el.id);
    if (/question|option|answer|quiz|exam|topic|stem|choice|题|answerCard|paper/i.test(sig)) push(el, 'keyword');
  }
  // 单选/多选输入及其容器
  for (const el of document.querySelectorAll('input[type=radio],input[type=checkbox]')) {
    out.radios++;
    push(el, 'input');
    push(el.closest('label') || el.parentElement, 'input-parent');
  }
  // 导航/提交类按钮
  for (const el of document.querySelectorAll('a,button,[role=button]')) {
    const t = (el.innerText || '').trim();
    if (/下一[题页步]|上一[题页步]|提交|确定|交卷/.test(t)) push(el, 'nav-button');
  }
  out.candidates = out.candidates.slice(0, 150);
  return JSON.stringify(out);
})()
"""


def evaluate_in(conn: TabConnection, session_id: str | None):
    result, error = conn.send("Runtime.evaluate", {"expression": SCANNER_JS, "returnByValue": True}, session_id)
    if error:
        return None, error
    value = (result or {}).get("result", {}).get("value")
    if not isinstance(value, str):
        return None, f"非字符串返回: {value}"
    return json.loads(value), None


def walk(conn: TabConnection, session_id: str | None, depth: int, path: str, lines: list[str]) -> None:
    data, err = evaluate_in(conn, session_id)
    ind = "    " * depth
    if err is not None:
        lines.append(f"{ind}[{path}] evaluate 失败: {err}")
        return
    lines.append(
        f"{ind}[{path}] {data['href'][:100]} | title={data['title'][:30]} | inIframe={data['inIframe']}"
        f" | radios={data['radios']}"
    )
    for c in data["candidates"]:
        vis = "*" if c["rect"]["w"] > 0 else " "
        lines.append(
            f"{ind}  {vis}{c['reason']:<11} <{c['tag']} class='{c['cls']}' id='{c['id']}'>"
            f" text='{c['text']}' checked={c['checked']} rect={c['rect']}"
        )


def main() -> int:
    keyword = sys.argv[1] if len(sys.argv) > 1 else "ua.dgut.edu.cn"
    targets = [t for t in http_json("/json/list") if t.get("type") == "page"]
    print(f"共 {len(targets)} 个标签页:")
    for i, t in enumerate(targets):
        print(f"  [{i}] {t['title'][:50]} | {t['url'][:90]}")

    chosen = next((t for t in targets if keyword.lower() in (t.get("url") or "").lower()), targets[0] if targets else None)
    if not chosen:
        print("没找到目标标签页。")
        return 1
    print(f"\n探测目标：{chosen['title'][:60]}")

    conn = TabConnection(chosen["webSocketDebuggerUrl"])
    conn.send("Page.enable")
    conn.send("Runtime.enable")
    global ATTACHED
    ATTACHED = {}
    collect_sessions(conn)

    lines: list[str] = [f"(已附加 {len(ATTACHED)} 个子框架会话)"]
    walk(conn, None, 0, "main", lines)
    for tid, sid in ATTACHED.items():
        walk(conn, sid, 1, f"frame:{tid[-6:]}", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
