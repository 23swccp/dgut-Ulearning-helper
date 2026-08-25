"""优学院课件学习辅助模块。

通过 Edge CDP(9222) 向 ua.dgut.edu.cn 课件页面注入 JS，控制视频倍速播放、
课程内自动翻页、章节完成提示衔接，以及跨域文档的阅读滚动。

测验始终由用户自行完成：本模块不读取题目答案，也不填写或提交测验。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

import requests
from websocket import create_connection


COURSE_TAB_URL_KEYWORD = "ua.dgut.edu.cn/learnCourse"


# ============================================================================
# INJECT_JS —— 注入课件页面的主控制器
# 改编自 luluzzy/DGUT_Ulearning_Tool (MIT)
# ============================================================================
# 原作者: luluzzy (https://github.com/luluzzy/DGUT_Ulearning_Tool)
# 改造: 去 GM_xmlhttpRequest 改 fetch；加弹窗关闭/补时长/mousemove 随机化/CDP 接口
# ============================================================================
INJECT_JS = r"""
(function(config) {
  const C = config;
  let running = true;
  let videoSpeed = C.playback_rate;

  function log(msg) {
    var t = new Date();
    function p(n) { return n < 10 ? '0' + n : '' + n; }
    var ts = p(t.getHours()) + ':' + p(t.getMinutes()) + ':' + p(t.getSeconds());
    console.log('[yxy] [' + ts + '] ' + msg);
  }

  // ---- 鼠标模拟（防挂机弹窗主防御）----
  // 用户实测：无操作约15分钟弹出"长时间无操作"窗口，必须手动点掉才能继续。
  // 当前间隔 0.8-1.5 秒，远小于 15 分钟阈值，可确保不弹窗。
  // 同时 dispatch 到 document 和 window（部分检测监听 window），双重保险。
  function fakeMouseActivity() {
    if (!running) return;
    var x = Math.random() * window.innerWidth;
    var y = Math.random() * window.innerHeight;
    var evtCfg = {clientX: x, clientY: y, bubbles: true};
    document.dispatchEvent(new MouseEvent('mousemove', evtCfg));
    try { window.dispatchEvent(new MouseEvent('mousemove', evtCfg)); } catch(e) {}
    var next = (C.mouse_interval_min + Math.random() * (C.mouse_interval_max - C.mouse_interval_min)) * 1000;
    setTimeout(fakeMouseActivity, next);
  }

  // ---- 挂机弹窗自动关闭（兜底：万一 mousemove 没拦住）----
  // 优学院弹窗确认按钮 class 未知，用宽松匹配 + 可见性判断。
  function autoDismissDialog() {
    if (!C.auto_dismiss_dialog) return;
    // 宽松匹配：常见弹窗确认按钮 + 任何含"继续/确定/知道了"文本的按钮
    var sels = '.modal .btn-confirm, .modal .btn-ok, .dialog .btn-confirm, ' +
      '.el-message-box__btns .el-button--primary, .popup .btn-confirm, ' +
      '.layui-layer-btn0, .ant-modal-confirm-btns .ant-btn-primary, ' +
      'button[class*="confirm"], button[class*="continue"], button[class*="确定"], ' +
      '.layui-layer-btn a';
    var btns = document.querySelectorAll(sels);
    for (var i = 0; i < btns.length; i++) {
      var btn = btns[i];
      if (btn.dataset.yxyDismissed) continue;
      // 只点可见的按钮（避免点到隐藏的）
      if (btn.offsetParent === null && btn.getClientRects().length === 0) continue;
      btn.dataset.yxyDismissed = '1';
      btn.click();
      log('已自动关闭挂机弹窗');
    }
    // 兜底2：找所有可见按钮里文本含"继续/确定/知道了/关闭"的
    var allBtns = document.querySelectorAll('button, a.btn, .btn');
    for (var j = 0; j < allBtns.length; j++) {
      var b = allBtns[j];
      if (b.dataset.yxyDismissed) continue;
      if (b.offsetParent === null && b.getClientRects().length === 0) continue;
      var txt = (b.textContent || '').trim();
      if (/^(继续|确定|知道了|关闭|确认|好的|OK)$/i.test(txt)) {
        b.dataset.yxyDismissed = '1';
        b.click();
        log('已自动关闭挂机弹窗(文本匹配: ' + txt + ')');
      }
    }
    // 章节完成页不是普通“确定”弹窗：只在包含完成提示的对话框中点击“继续下一章”。
    // 双重文本约束避免误点课程页面里的普通导航按钮。
    for (var k = 0; k < allBtns.length; k++) {
      var nextChapter = allBtns[k];
      if (nextChapter.dataset.yxyDismissed) continue;
      if (nextChapter.offsetParent === null && nextChapter.getClientRects().length === 0) continue;
      if (!/继续\s*下\s*一章/.test((nextChapter.textContent || '').trim())) continue;
      var box = nextChapter;
      var isCompletionDialog = false;
      for (var depth = 0; box && depth < 8; depth++, box = box.parentElement) {
        var boxText = box.textContent || '';
        if (/恭喜你完成本章|完成本章的学习|本章成绩/.test(boxText)) {
          isCompletionDialog = true;
          break;
        }
      }
      if (isCompletionDialog) {
        nextChapter.dataset.yxyDismissed = '1';
        nextChapter.click();
        log('本章学习完成，已继续下一章');
        break;
      }
    }
  }


  // ---- 视频控制（设 playbackRate）----
  // 课件页是 SPA：翻页后经常复用同一个 <video> 节点，只替换 source。
  // 因此不能只在首次 hook 时设倍速；新资源的 metadata/canplay 会重置播放器状态。
  function applyVideoSettings(video, reason) {
    if (!running || !document.contains(video)) return;
    video.muted = C.auto_mute;
    if (Math.abs((video.playbackRate || 1) - videoSpeed) > 0.01) {
      video.playbackRate = videoSpeed;
    }
    // ratechange/play 会被播放器高频触发；只在首挂钩和元数据就绪时记录一次。
    if (reason === 'hook' || reason === 'loadedmetadata') {
      var dur = isFinite(video.duration) ? Math.round(video.duration) : '?';
      log('视频已就绪（' + reason + '，时长 ' + dur + 's，倍速 ' + video.playbackRate + 'x）');
    }
    if (video.paused) video.play().catch(function() {});
  }

  function hookVideo(video) {
    if (video.dataset.yxyHooked) return;
    video.dataset.yxyHooked = '1';
    var dur = video.duration ? Math.round(video.duration) : '?';
    log('开始播放视频（时长 ' + dur + 's，倍速 ' + videoSpeed + 'x）');
    applyVideoSettings(video, 'hook');
    // 新章节在同一 video 节点内换源时，以下事件会再次触发。
    ['loadstart', 'loadedmetadata', 'loadeddata', 'canplay', 'play'].forEach(function(name) {
      video.addEventListener(name, function() { applyVideoSettings(video, name); });
    });
    // 某些播放器会在 metadata 后把 rate 重置为 1；检测到时立即恢复配置。
    video.addEventListener('ratechange', function() {
      if (running && Math.abs((video.playbackRate || 1) - videoSpeed) > 0.01) {
        applyVideoSettings(video, 'ratechange');
      }
    });
    setInterval(function() {
      if (!running || !document.contains(video)) return;
      applyVideoSettings(video, 'poll');
    }, 2000);
    video.addEventListener('ended', function() {
      log('视频播放结束');
      if (C.auto_next) goNext();
    });
  }

  // ---- 文档自动滚动（顶层同源文档）----
  // docs.ulearning.cn 的跨域 iframe 由 Python/CDP 在 iframe context 内处理；
  // 顶层页面不能访问其 contentDocument，也绝不能在这里把左侧目录误当正文滚动。
  // 关键：左侧目录宽度固定，滚到主区域中央的真实坐标 dispatch wheel 事件
  var docScrolledUnchanged = 0;
  // 判断元素是否属于左侧目录
  function isSidebar(el) {
    if (!el) return false;
    var cls = (el.className || '').toLowerCase();
    var id = (el.id || '').toLowerCase();
    return /sidebar|catalog|outline|menu|toc|directory|left-nav|chapter-list|catalogue/.test(cls + ' ' + id)
      || el.offsetWidth > 0 && el.offsetWidth < 320;  // 侧边栏通常 <320px
  }
  // 找到主内容区（排除侧边目录，宽度大，含"content/learn/course/main"关键字）
  function findMainContent() {
    var candidates = document.querySelectorAll(
      'main, [class*="content"], [class*="learnContent"], [class*="courseContent"], ' +
      '[class*="main-content"], [class*="learn-content"], [class*="course-content"], ' +
      '[class*="player-container"], [class*="doc-content"], [class*="page-content"]'
    );
    var best = null;
    var bestArea = 0;
    for (var i = 0; i < candidates.length; i++) {
      var el = candidates[i];
      if (isSidebar(el)) continue;  // 排除侧边栏
      if (el.scrollHeight <= el.clientHeight) continue;  // 必须是可滚动的
      var area = el.clientWidth * el.clientHeight;
      if (area > bestArea) { bestArea = area; best = el; }
    }
    return best;
  }
  // 派发真实滚轮事件到主区域
  function dispatchWheelToMain() {
    var main = findMainContent();
    if (!main) {
      // 兜底：派发到视口中央
      var cx = window.innerWidth / 2, cy = window.innerHeight / 2;
      var target = document.elementFromPoint(cx, cy) || document.body;
      target.dispatchEvent(new WheelEvent('wheel', {deltaY: 400, bubbles: true, cancelable: true, clientX: cx, clientY: cy}));
      return {scrolled: true, target: 'viewport'};
    }
    // 真实坐标：主区域中心
    var rect = main.getBoundingClientRect();
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height / 2;
    var oldTop = main.scrollTop;
    // 同时尝试：滚 wheel / scrollTop / 滚主窗口 / keyboard
    main.dispatchEvent(new WheelEvent('wheel', {deltaY: 400, bubbles: true, cancelable: true, clientX: cx, clientY: cy}));
    if (main.scrollTop === oldTop) {
      main.scrollTop += 300;
    }
    if (main.scrollTop === oldTop) {
      main.scrollBy(0, 300);
    }
    if (main.scrollTop === oldTop) {
      window.scrollBy(0, 300);
    }
    return {scrolled: main.scrollTop !== oldTop, target: main.tagName + '.' + (main.className || '').slice(0, 30)};
  }
  function hookDoc() {
    if (document.querySelector('video')) {
      if (window._yxyDocTimer) { clearInterval(window._yxyDocTimer); window._yxyDocTimer = null; }
      return false;
    }
    var externalDoc = document.querySelector('iframe[src*="docs.ulearning.cn"]');
    if (externalDoc) {
      if (window._yxyDocTimer) { clearInterval(window._yxyDocTimer); window._yxyDocTimer = null; }
      if (!window._yxyExternalDocNotice) {
        window._yxyExternalDocNotice = true;
        log('检测到跨域文档 iframe，交由 CDP frame 滚动器处理');
      }
      return true;
    }
    window._yxyExternalDocNotice = false;
    if (window._yxyDocTimer) return true;
    log('检测到非视频页面，开始自动滚动阅读');
    window._yxyDocTimer = setInterval(function() {
      if (!running) return;
      var res = dispatchWheelToMain();
      if (res.scrolled) {
        docScrolledUnchanged = 0;
      } else {
        docScrolledUnchanged++;
        if (docScrolledUnchanged >= 8) {
          log('文档已阅读完毕（' + res.target + '），跳转下一节');
          clearInterval(window._yxyDocTimer);
          window._yxyDocTimer = null;
          docScrolledUnchanged = 0;
          if (C.auto_next) goNext();
        }
      }
    }, 3000);
    return true;
  }

  // ---- 翻页 ----
  function goNext() {
    var btn = document.querySelector('.next-btn,.btn-next,.nextVideoBtn,.mobile-next-page-btn');
    if (btn && !btn.classList.contains('disabled')) {
      btn.click();
      log('跳转下一节');
    } else {
      log('未找到下一节按钮，可能本章结束');
    }
  }


  // ---- 主循环：独立定时器主动轮询，不依赖 MutationObserver 触发时机 ----
  function tick() {
    if (!running) return;
    // 视频：找未 hook 的视频并 hook
    if (C.auto_play) {
      var video = document.querySelector('video');
      if (video) {
        if (!video.dataset.yxyHooked) {
          hookVideo(video);
        } else {
          // 同一 DOM 节点被 SPA 换成下一节资源时也继续校正倍速。
          applyVideoSettings(video, 'poll');
        }
      } else {
        hookDoc();  // 没有视频时尝试文档滚动
      }
    }
    autoDismissDialog();
  }

  // MutationObserver 保留但只做辅助触发；主逻辑靠 2 秒定时器
  new MutationObserver(function() { setTimeout(tick, 300); }).observe(document.body, {childList: true, subtree: true});
  tick();
  setInterval(tick, 2000);  // 每 2 秒主动检查一次
  fakeMouseActivity();

  // ---- CDP 控制接口 ----
  window.__yxy_stop = function() { running = false; log('已停止'); };
  window.__yxy_set_speed = function(r) { videoSpeed = r; log('倍速->' + r); };
  window.__yxy_go_next = function() { if (C.auto_next) goNext(); };
  log('刷课控制器已注入，倍速 ' + videoSpeed + 'x');
})(window.__YXY_CONFIG__);
"""


@dataclass
class CourseConfig:
    """刷课配置，注入 JS 时序列化传给页面。"""
    # 倍速：实测 16x 不被优学院检测。默认 2.0 保守，可按需调高。
    # 开源给同学用，不假设装了 Global Speed 插件。
    playback_rate: float = 8.0
    auto_play: bool = True
    auto_mute: bool = True
    auto_next: bool = True
    mouse_interval_min: float = 0.8     # 鼠标模拟最小间隔（秒）
    mouse_interval_max: float = 1.5     # 鼠标模拟最大间隔（秒）
    auto_dismiss_dialog: bool = True    # 自动关闭挂机弹窗
    document_scroll_enabled: bool = True  # 通过 CDP 滚动 docs.ulearning.cn 跨域文档 iframe
    document_scroll_interval: float = 3.0 # 文档基础滚动间隔（秒）
    document_scroll_speed: float = 3.0    # 文档滚动倍率：1–3，默认 3 倍（实际约每秒一次）

    def to_js(self) -> str:
        """序列化为 JS 对象字面量字符串，注入用。"""
        return json.dumps(asdict(self), ensure_ascii=False)


class CourseController:
    """通过 CDP 控制课件页面刷课。一个实例对应一个标签页。

    生命周期：start(config) → 注入页面控制器；stop() 只断开 CDP，不关闭浏览器标签页。
    """

    def __init__(self, emit: Callable[[str, str], None]) -> None:
        self.emit = emit
        self.ws_url: str | None = None
        self.ws = None
        self._msg_id = 0
        self._lock = threading.Lock()
        self._responses: dict[int, Any] = {}
        self._running = False
        self._recv_thread: threading.Thread | None = None
        self._watcher_thread: threading.Thread | None = None
        self._document_scroll_thread: threading.Thread | None = None
        self._document_scrolled_frames: set[str] = set()
        self._document_completed_frames: set[str] = set()
        self._document_status: dict[str, str] = {}
        self._last_frame_urls: list[str] = []
        self._last_target_urls: list[str] = []
        self._iframe_sessions: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 标签页定位
    # ------------------------------------------------------------------
    def find_course_tab(self, port: int = 9222) -> str | None:
        """GET /json 找 url 含 ua.dgut.edu.cn/learnCourse 的 page，返回 ws URL。"""
        try:
            targets = requests.get(f"http://127.0.0.1:{port}/json", timeout=3).json()
        except (requests.RequestException, ValueError):
            return None
        for target in targets:
            if target.get("type") == "page" and COURSE_TAB_URL_KEYWORD in target.get("url", ""):
                return target.get("webSocketDebuggerUrl")
        return None

    # ------------------------------------------------------------------
    # CDP 连接
    # ------------------------------------------------------------------
    def attach(self) -> bool:
        """连接 CDP ws，启用 Runtime/Page 域，启动接收线程。"""
        if not self.ws_url:
            return False
        try:
            self.ws = create_connection(self.ws_url, timeout=10)
        except Exception as error:
            self.emit(f"[刷课] 连接 CDP 失败：{error}", "warn")
            return False
        self._running = True
        self._send("Runtime.enable")
        self._send("Page.enable")
        # 跨域 iframe 会成为独立的 OOPIF target，不会出现在 Page.getFrameTree。
        # flatten 模式允许在同一 WebSocket 上带 sessionId 对该 target 执行 Runtime.evaluate。
        self._send("Target.setDiscoverTargets", {"discover": True})
        self._send("Target.setAutoAttach", {
            "autoAttach": True,
            "waitForDebuggerOnStart": False,
            "flatten": True,
        })
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        return True

    def _next_id(self) -> int:
        with self._lock:
            self._msg_id += 1
            return self._msg_id

    def _send(self, method: str, params: dict | None = None, *, session_id: str | None = None) -> int:
        msg_id = self._next_id()
        msg: dict = {"id": msg_id, "method": method}
        if params:
            msg["params"] = params
        if session_id:
            msg["sessionId"] = session_id
        self.ws.send(json.dumps(msg))
        return msg_id

    def _cdp_call(
        self, method: str, params: dict | None = None, timeout: float = 10.0, *, session_id: str | None = None
    ) -> dict | None:
        """发送 CDP 命令并等待同一消息 ID 的响应。"""
        msg_id = self._send(method, params, session_id=session_id)
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if msg_id in self._responses:
                    return self._responses.pop(msg_id)
            time.sleep(0.05)
        return None

    def _cdp_eval(
        self, expression: str, timeout: float = 10.0, *, context_id: int | None = None, session_id: str | None = None
    ) -> dict | None:
        """发 Runtime.evaluate，阻塞等对应 id 的响应。"""
        params: dict[str, Any] = {
            "expression": expression,
            "awaitPromise": False,
            "returnByValue": True,
        }
        if context_id is not None:
            params["contextId"] = context_id
        return self._cdp_call("Runtime.evaluate", params, timeout, session_id=session_id)

    def eval_js(self, expression: str, timeout: float = 10.0):
        """执行任意 JS，返回值的 value 部分。"""
        result = self._cdp_eval(expression, timeout=timeout)
        if result is None:
            return None
        try:
            return result["result"]["value"]
        except (KeyError, TypeError):
            return None

    def _recv_loop(self) -> None:
        """接收线程：分发响应和事件。"""
        while self._running:
            try:
                raw = self.ws.recv()
                if not raw:
                    continue
                msg = json.loads(raw)
            except Exception:
                break
            if "id" in msg:
                with self._lock:
                    self._responses[msg["id"]] = msg.get("result")
            else:
                method = msg.get("method", "")
                if method == "Runtime.consoleAPICalled":
                    self._on_console(msg.get("params", {}))
                elif method == "Target.attachedToTarget":
                    self._on_target_attached(msg.get("params", {}))

    def _on_console(self, params: dict) -> None:
        """把 JS console.log('[yxy] ...') 回流到终端。"""
        args = params.get("args", [])
        parts = []
        for arg in args:
            if arg.get("type") == "string":
                parts.append(arg.get("value", ""))
            else:
                val = arg.get("value")
                if val is not None:
                    parts.append(str(val))
        text = " ".join(parts)
        if text.startswith("[yxy]"):
            self.emit(text, "info")

    def _on_target_attached(self, params: dict) -> None:
        """记录自动附着到的 OOPIF session，供跨域文档滚动器使用。"""
        info = params.get("targetInfo", {})
        target_id = info.get("targetId")
        session_id = params.get("sessionId")
        if target_id and session_id and info.get("type") == "iframe":
            self._iframe_sessions[target_id] = session_id

    # ------------------------------------------------------------------
    # 跨域文档滚动
    # ------------------------------------------------------------------
    @staticmethod
    def _walk_frames(frame_tree: dict) -> list[dict]:
        """扁平化 Page.getFrameTree 返回值，保留所有嵌套 iframe。"""
        frames: list[dict] = []
        if not isinstance(frame_tree, dict):
            return frames
        frame = frame_tree.get("frame")
        if isinstance(frame, dict):
            frames.append(frame)
        for child in frame_tree.get("childFrames", []) or []:
            frames.extend(CourseController._walk_frames(child))
        return frames

    def _document_frames(self) -> list[dict]:
        """返回 docs.ulearning.cn 的 iframe；不用顶层 DOM 猜滚动容器。"""
        result = self._cdp_call("Page.getFrameTree", timeout=5.0)
        if not result:
            self._last_frame_urls = []
            return []
        all_frames = self._walk_frames(result.get("frameTree", {}))
        self._last_frame_urls = [frame.get("url", "") for frame in all_frames]
        # 实测域名通常是 docs.ulearning.cn，但课件文档也可能在同一主域的其他子域。
        return [frame for frame in all_frames if "ulearning.cn" in frame.get("url", "")]

    def _document_log_once(self, key: str, text: str, kind: str = "info") -> None:
        """只在状态变化时记录滚动诊断，避免终端被每 3 秒一次的轮询刷屏。"""
        if self._document_status.get(key) == text:
            return
        self._document_status[key] = text
        self.emit(text, kind)

    def _frame_context_id(self, frame_id: str) -> int | None:
        """为跨域 frame 创建隔离世界，取得可执行 Runtime.evaluate 的 context。"""
        result = self._cdp_call("Page.createIsolatedWorld", {
            "frameId": frame_id,
            "worldName": "yxy-course-document-scroll",
            "grantUniveralAccess": True,
        }, timeout=5.0)
        try:
            return int(result["executionContextId"])
        except (KeyError, TypeError, ValueError):
            return None

    def _document_targets(self) -> list[dict]:
        """查找独立 OOPIF target 并附着。

        Chromium 会将跨域 iframe 放进独立 renderer process：顶层 Page.getFrameTree
        仅显示 about:blank。这条路径直接对 iframe target 建立 flat session。
        """
        result = self._cdp_call("Target.getTargets", timeout=5.0)
        if not result:
            self._last_target_urls = []
            return []
        self._last_target_urls = [
            f"{info.get('type', '?')}:{info.get('url', '')}"
            for info in result.get("targetInfos", [])
        ]
        documents = [
            info for info in result.get("targetInfos", [])
            if info.get("type") == "iframe" and "ulearning.cn" in info.get("url", "")
        ]
        attached: list[dict] = []
        for info in documents:
            target_id = info.get("targetId")
            if not target_id:
                continue
            session_id = self._iframe_sessions.get(target_id)
            if not session_id:
                response = self._cdp_call("Target.attachToTarget", {
                    "targetId": target_id,
                    "flatten": True,
                }, timeout=5.0)
                session_id = (response or {}).get("sessionId")
                if session_id:
                    self._iframe_sessions[target_id] = session_id
            if session_id:
                attached.append({"id": target_id, "url": info.get("url", ""), "sessionId": session_id, "kind": "oopif"})
            else:
                self._document_log_once(
                    f"attach-target:{target_id}",
                    f"[刷课] 已发现文档 OOPIF，但无法附着：{info.get('url', '')}",
                    "warn",
                )
        return attached

    _FRAME_SCROLL_JS = r"""
(() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 80 && r.height > 80 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const candidates = [document.scrollingElement, document.documentElement, document.body]
    .concat(Array.from(document.querySelectorAll('*')))
    .filter(el => el && visible(el) && el.scrollHeight > el.clientHeight + 2)
    .sort((a, b) => (b.clientWidth * b.clientHeight) - (a.clientWidth * a.clientHeight));
  const target = candidates[0];
  if (!target) return {state: 'not-scrollable', url: location.href};
  const remaining = target.scrollHeight - target.clientHeight - target.scrollTop;
  if (remaining <= 2) return {state: 'complete', url: location.href, target: target.tagName};
  const step = Math.max(160, Math.min(560, Math.round(target.clientHeight * 0.65)));
  target.scrollTop = Math.min(target.scrollTop + step, target.scrollHeight - target.clientHeight);
  return {state: 'scrolled', url: location.href, top: target.scrollTop, remaining: remaining, target: target.tagName};
})()
"""

    def _scroll_document_frame(self, frame: dict) -> dict | None:
        frame_id = frame.get("id")
        if not frame_id:
            return None
        context_id = self._frame_context_id(frame_id)
        if context_id is None:
            self._document_log_once(
                f"context:{frame_id}",
                f"[刷课] 文档 frame 无法创建执行环境：{frame.get('url', '')}",
                "warn",
            )
            return None
        result = self._cdp_eval(self._FRAME_SCROLL_JS, timeout=5.0, context_id=context_id)
        try:
            value = result["result"]["value"]
            if isinstance(value, dict):
                return value
        except (KeyError, TypeError):
            pass
        self._document_log_once(
            f"eval:{frame_id}",
            f"[刷课] 文档 frame 执行滚动脚本失败：{frame.get('url', '')}",
            "warn",
        )
        return None

    def _scroll_document_target(self, target: dict) -> dict | None:
        """在独立 OOPIF target 的默认执行环境中执行滚动。"""
        result = self._cdp_eval(
            self._FRAME_SCROLL_JS,
            timeout=5.0,
            session_id=target.get("sessionId"),
        )
        try:
            value = result["result"]["value"]
            if isinstance(value, dict):
                return value
        except (KeyError, TypeError):
            pass
        self._document_log_once(
            f"oopif-eval:{target.get('id', '')}",
            f"[刷课] 文档 OOPIF 执行滚动脚本失败：{target.get('url', '')}",
            "warn",
        )
        return None

    def _handle_document_scroll_state(self, item: dict, state: dict) -> None:
        """处理 frame 与 OOPIF 共用的滚动状态；只在真实滚动完成后调用既有翻页。"""
        item_id = item.get("id", "")
        if state.get("state") == "scrolled":
            self._document_scrolled_frames.add(item_id)
            self._document_completed_frames.discard(item_id)
            self._document_log_once(
                f"scrolling:{item_id}",
                f"[刷课] 正在滚动文档：{state.get('url', item.get('url', ''))}",
            )
        elif state.get("state") == "complete" and item_id in self._document_scrolled_frames:
            if item_id not in self._document_completed_frames:
                self._document_completed_frames.add(item_id)
                self.emit("[刷课] 文档已滚动至末尾，按现有流程翻至下一节。", "info")
                self._cdp_eval("window.__yxy_go_next && window.__yxy_go_next()", timeout=5.0)
        elif state.get("state") == "not-scrollable":
            self._document_log_once(
                f"not-scrollable:{item_id}",
                f"[刷课] 文档未找到可滚动容器，保留当前页等待手动处理：{state.get('url', item.get('url', ''))}",
                "warn",
            )

    def _document_scroll_loop(self, interval: float) -> None:
        """在跨域 frame 内逐段滚动；仅滚动过且到达底部才请求现有翻页函数。"""
        self.emit("[刷课] 跨域文档滚动器已启动。", "info")
        while self._running:
            try:
                targets = self._document_targets()
                frames = self._document_frames() if not targets else []
            except Exception as error:
                self._document_log_once("frame-tree-error", f"[刷课] 读取文档 frame 失败：{error}", "warn")
                time.sleep(max(1.0, interval))
                continue
            if not targets and not frames:
                urls = " | ".join(
                    url for url in (self._last_frame_urls + self._last_target_urls) if url
                ) or "（无 frame / target）"
                self._document_log_once(
                    "no-document-frame",
                    f"[刷课] 未发现 ulearning 文档 frame；当前 frame：{urls}",
                    "warn",
                )
            for target in targets:
                target_id = target.get("id", "")
                self._document_log_once(
                    f"found-oopif:{target_id}",
                    f"[刷课] 已发现独立文档 OOPIF：{target.get('url', '')}",
                )
                state = self._scroll_document_target(target)
                if state:
                    self._handle_document_scroll_state(target, state)
            for frame in frames:
                frame_id = frame.get("id", "")
                self._document_log_once(
                    f"found:{frame_id}",
                    f"[刷课] 已发现文档 frame：{frame.get('url', '')}",
                )
                state = self._scroll_document_frame(frame)
                if not state:
                    continue
                self._handle_document_scroll_state(frame, state)
            time.sleep(max(1.0, interval))

    # ------------------------------------------------------------------
    # 注入
    # ------------------------------------------------------------------
    def inject_main_script(self, config: CourseConfig) -> bool:
        """Runtime.evaluate 注入主刷课 JS，传配置参数。"""
        js = f"window.__YXY_CONFIG__={config.to_js()};\n{INJECT_JS}"
        result = self._cdp_eval(js, timeout=15.0)
        if result is None:
            self.emit("[刷课] 注入 JS 超时", "warn")
            return False
        return True

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------
    def start(self, config: CourseConfig) -> bool:
        """定位课件标签页 → attach → 注入 → 启动达标监控。

        需用户先在浏览器打开课件学习页（ua.dgut.edu.cn/learnCourse）。
        """
        self.ws_url = self.find_course_tab()
        if not self.ws_url:
            self.emit("[刷课] 未找到课件学习页。请先在浏览器打开 ua.dgut.edu.cn 课件学习页。", "warn")
            return False
        self.emit("[刷课] 已定位课件标签页，正在连接 CDP…", "info")
        if not self.attach():
            return False
        if not self.inject_main_script(config):
            return False
        self.emit(f"[刷课] 已注入。倍速 {config.playback_rate}x。", "success")
        if config.document_scroll_enabled:
            # 保持单次滚动距离不变，只缩短间隔；避免 PDF/文档渲染尚未完成就跨大步跳过。
            scroll_speed = max(1.0, min(3.0, float(config.document_scroll_speed)))
            self._document_scroll_thread = threading.Thread(
                target=self._document_scroll_loop,
                args=(max(1.0, float(config.document_scroll_interval) / scroll_speed),),
                daemon=True,
            )
            self._document_scroll_thread.start()
        return True

    def stop(self) -> None:
        """停止刷课：调 window.__yxy_stop()，关闭 CDP ws。不关浏览器。"""
        self._running = False
        if self.ws:
            try:
                self._cdp_eval("window.__yxy_stop()", timeout=3.0)
            except Exception:
                pass
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
        self.emit("[刷课] 已停止。浏览器页面保持打开，请自行关闭。", "muted")

    def set_speed(self, rate: float) -> None:
        """运行时改倍速。"""
        self.eval_js(f"window.__yxy_set_speed({rate})")


__all__ = [
    "INJECT_JS",
    "COURSE_TAB_URL_KEYWORD",
    "CourseConfig",
    "CourseController",
]
