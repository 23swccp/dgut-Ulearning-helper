import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

type Page = "terminal" | "browser" | "logs" | "about";
type Phase = "ready" | "login" | "courses" | "selected" | "monitoring";
type BackendEvent = { message: string; kind: string };
type BackendResult = { ok: boolean; error?: string; courses?: Course[]; course?: Course | null; config?: AppConfig; events?: BackendEvent[] };
type Course = { id: number; name: string; teacherName: string };
type AppConfig = { browser_name?: string; browser_path?: string; save_log?: boolean; log_path?: string };
const icon = { terminal: "⌘", browser: "◎", logs: "▤", about: "i" };
const isTauri = "__TAURI_INTERNALS__" in window;
const browserLoginPayload = isTauri ? {} : { url: "https://lms.dgut.edu.cn" };

function App() {
  const [page, setPage] = useState<Page>("terminal");
  const [phase, setPhase] = useState<Phase>("ready");
  const [command, setCommand] = useState("");
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState<string[]>(["优学院签到助手", "────────────────────────────────────────────────────", "正在连接本地后端…"]);
  const [logging, setLogging] = useState(true);
  const [browser, setBrowser] = useState("自动检测");
  const [path, setPath] = useState("");
  const [logPath, setLogPath] = useState("./签到记录.md");
  const [saved, setSaved] = useState("");
  const endRef = useRef<HTMLPreElement>(null);
  const commandRef = useRef<HTMLInputElement>(null);
  const append = (line: string) => setLogs(items => [...items, line]);

  useEffect(() => { endRef.current?.scrollTo({ top: endRef.current.scrollHeight, behavior: "smooth" }); }, [logs]);
  // 命令执行期间输入框会暂时禁用；结束后主动恢复焦点，避免用户反复点击。
  useEffect(() => {
    if (page !== "terminal" || busy) return;
    const frame = requestAnimationFrame(() => commandRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [page, busy]);
  useEffect(() => {
    let off: (() => void) | undefined;
    if (isTauri) {
      listen<{ message: string }>("backend-log", event => append(event.payload.message)).then(unlisten => { off = unlisten; });
    }
    call("get_settings").then(result => {
      if (result.ok) {
        if (result.config) loadConfig(result.config);
        append("本地后端已连接。按 Enter 读取本地登录缓存。");
      } else {
        append(`后端连接失败：${result.error || "未知错误"}`);
      }
    });
    return () => off?.();
  }, []);
  useEffect(() => {
    // 浏览器版与发布版都主动拉取后台日志，避免依赖子进程 stdout 管道。
    let disposed = false;
    const pullEvents = async () => {
      try {
        const result = await call("get_events");
        if (!disposed) result.events?.forEach(event => append(event.message));
      } catch {
        // 启动阶段 sidecar 尚未就绪时安静重试，不污染终端。
      }
    };
    void pullEvents();
    const timer = window.setInterval(() => void pullEvents(), 1000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (isTauri) return;
    const heartbeat = () => void fetch("/api/heartbeat", { method: "POST", keepalive: true }).catch(() => undefined);
    const closing = () => { navigator.sendBeacon("/api/client-closed", ""); };
    heartbeat();
    const timer = window.setInterval(heartbeat, 2000);
    window.addEventListener("pagehide", closing);
    return () => { window.clearInterval(timer); window.removeEventListener("pagehide", closing); };
  }, []);
  useEffect(() => { const key = (e: KeyboardEvent) => e.key === "F8" && updateDemo(); window.addEventListener("keydown", key); return () => window.removeEventListener("keydown", key); });

  async function call(command: string, payload: Record<string, unknown> = {}): Promise<BackendResult> {
    try {
      if (isTauri) return await invoke<BackendResult>("backend_command", { command, payload });
      const response = await fetch("/api/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command, payload }) });
      return await response.json() as BackendResult;
    }
    catch (error) { const message = `后端连接失败：${String(error)}`; append(message); return { ok: false, error: message }; }
  }
  function loadConfig(config: AppConfig) {
    setBrowser(config.browser_name || "自动检测"); setPath(config.browser_path || ""); setLogging(config.save_log !== false); setLogPath(config.log_path || "./签到记录.md");
  }
  function compatibleBrowserPath(name: string, candidate: string) {
    const expected = name === "Microsoft Edge" ? "msedge.exe" : name === "Google Chrome" ? "chrome.exe" : "";
    return expected && candidate && !candidate.toLowerCase().endsWith(expected) ? "" : candidate;
  }
  function chooseBrowser(name: string) {
    setBrowser(name);
    setPath(current => compatibleBrowserPath(name, current));
  }
  function save(message: string) { setSaved(message); setTimeout(() => setSaved(""), 2200); }
  async function saveSettings(values: Record<string, unknown>, message: string) { setBusy(true); const result = await call("update_settings", values); setBusy(false); if (result.ok) { if (result.config) loadConfig(result.config); save(message); } }
  async function saveBrowserSettings(message: string) {
    const browserPath = compatibleBrowserPath(browser, path);
    setPath(browserPath);
    await saveSettings({ browser_name: browser === "自动检测" ? "" : browser, browser_path: browserPath }, message);
  }
  async function run() {
    if (busy) return;
    const input = command.trim(); setCommand(""); setBusy(true);
    if (input.toLowerCase() === "clear") { setLogs([]); setBusy(false); return; }
    if (input) append(`> ${input}`);
    if (input.toLowerCase() === "kill") {
      if (isTauri) {
        append("kill 仅用于浏览器源码版；桌面版请直接关闭窗口。 ");
        setBusy(false);
        return;
      }
      const result = await call("shutdown_app");
      if (result.ok) append("正在关闭本地服务与前端进程…");
      else append(`退出失败：${result.error || "未知错误"}`);
      setBusy(false);
      return;
    }
    if (input.toLowerCase() === "login" || input === "登录") {
      append("正在启动登录浏览器，请稍候…");
      setPhase("login");
      void call("start_browser", browserLoginPayload).then(result => {
        if (result.ok) append("请在浏览器中完成登录，完成后回到这里按 Enter 或输入 读取。 ");
        else append(`启动浏览器失败：${result.error || "未知错误"}`);
      });
      window.setTimeout(() => setBusy(false), 7000);
      return;
    }
    if (phase === "login") {
      if (input && !["读取", "继续", "enter"].includes(input.toLowerCase())) {
        append("请先在浏览器完成登录；完成后按 Enter 或输入 读取。 ");
      } else {
        const result = await call("load_session_and_courses");
        if (result.ok && result.courses?.length) {
          setPhase("courses");
          append("请输入一门课程的名称关键词或课程 ID。 ");
        } else {
          append("尚未读取到有效登录状态。请确认浏览器已完成优学院登录后，再按 Enter 重试。 ");
        }
      }
    } else if (phase === "ready") {
      if (["读取", "继续"].includes(input)) {
        const result = await call("load_session_and_courses");
        if (result.ok && result.courses?.length) { setPhase("courses"); append("请输入一门课程的名称关键词或课程 ID。 "); }
        else append("尚未读取到有效登录状态。请先输入 login 并在浏览器完成登录。 ");
        setBusy(false);
        return;
      }
      if (input && !["读取", "继续", "enter"].includes(input.toLowerCase())) { append("按 Enter 读取本地登录缓存；如需要登录请输入 login。 "); setBusy(false); return; }
      const result = await call("load_saved_courses");
      if (result.ok && result.courses?.length) { setPhase("courses"); append("请输入一门课程的名称关键词或课程 ID。 "); } else append("缓存不可用。请输入 login 启动浏览器，登录完成后输入 读取。 ");
    } else if (phase === "courses") {
      const result = await call("select_course", { query: input });
      if (result.ok && result.course) { setPhase("selected"); append(`已选定：${result.course.name}。按 Enter 开始轮询；输入 / 取消选定。`); }
      else append("未找到唯一匹配课程，请输入更精确的名称或课程 ID。 ");
    } else if (phase === "selected") {
      if (input === "/") { await call("clear_selected_course"); setPhase("courses"); append("已取消选定，请重新输入课程名称。 "); }
      else if (!input) { const result = await call("start_monitor"); if (result.ok) setPhase("monitoring"); }
      else append("按 Enter 开始轮询，或输入 / 取消选定。 ");
    } else if (phase === "monitoring") {
      if (input === "/" || input.toLowerCase() === "stop") { await call("stop_monitor"); setPhase("courses"); append("已停止轮询。 "); }
      else append("正在轮询。输入 / 或 stop 可以停止。 ");
    }
    setBusy(false);
  }
  async function startLogin() {
    if (busy) return;
    const browserPath = compatibleBrowserPath(browser, path);
    setBusy(true);
    const savedBrowser = await call("update_settings", { browser_name: browser === "自动检测" ? "" : browser, browser_path: browserPath });
    if (!savedBrowser.ok) { setBusy(false); return; }
    if (savedBrowser.config) loadConfig(savedBrowser.config);
    setPage("terminal");
    const result = await call("start_browser", browserLoginPayload);
    if (!result.ok) append(`启动浏览器失败：${result.error || "未知错误"}`);
    setBusy(false);
  }
  function updateDemo() { append(""); append("[更新] 正在检查版本… / - \\ |"); setTimeout(() => { append("[更新] 发现新版本 v1.1.0（当前 v1.0.0）"); append("[公告] 修复登录状态读取；优化课程选择与日志记录。"); append("[更新] 下载更新包 [████████████████░░░░] 80%  19.3 MB / 24.1 MB"); }, 500); }

  return <div className="app-shell">
    <main className="content"><header className={page === "terminal" ? "terminal-header" : "settings-header"}>{page === "terminal" ? <button className="settings" onClick={() => setPage("browser")}>⚙</button> : <><button className="back" aria-label="返回终端" onClick={() => setPage("terminal")}><span className="back-arrow" /></button><h1>设置</h1></>}</header>
      {saved && <div className="toast">✓ {saved}</div>}
      {page === "terminal" && <section className="terminal"><pre ref={endRef}>{logs.join("\n")}</pre><div className="command"><b>›</b><input ref={commandRef} autoFocus disabled={busy} value={command} onChange={e => setCommand(e.target.value)} onKeyDown={e => e.key === "Enter" && run()} placeholder={phase === "courses" ? "输入课程名称或课程 ID…" : phase === "selected" ? "按 Enter 开始，/ 取消…" : "按 Enter 继续，或输入命令…"}/><button disabled={busy} onClick={run}>{busy ? "处理中…" : "确认 ↵"}</button></div></section>}
      {page !== "terminal" && <section className="settings-layout"><aside className="settings-nav"><p>设置</p>{(["browser", "logs", "about"] as Page[]).map(item => <button key={item} onClick={() => setPage(item)} className={page === item ? "active" : ""}><b>{icon[item]}</b>{{ terminal: "", browser: "浏览器", logs: "日志与数据", about: "关于" }[item]}</button>)}</aside><div className="settings-body">{page === "browser" && <SettingsSection title="浏览器" subtitle="选择用于登录与读取课程的浏览器。"><Card title="默认浏览器" desc="优先使用你的选择；未设置时按 Edge、Chrome、其他浏览器的顺序自动检测。"><div className="choice-row">{["自动检测", "Microsoft Edge", "Google Chrome"].map(name => <button key={name} onClick={() => chooseBrowser(name)} className={`choice ${browser === name ? "selected" : ""}`}>{name}</button>)}</div></Card><Card title="浏览器程序位置" desc="自动检测失败时，可手动填写浏览器 .exe 文件的完整路径。"><input className="field" value={path} onChange={e => setPath(e.target.value)} placeholder="自动检测"/></Card><div className="actions"><button className="secondary" disabled={busy} onClick={startLogin}>启动浏览器登录</button><button className="primary" disabled={busy} onClick={() => saveBrowserSettings("浏览器设置已保存")}>保存浏览器设置</button></div></SettingsSection>}
      {page === "logs" && <SettingsSection title="日志与数据" subtitle="运行记录会追加到同一个 Markdown 文件，便于长期追溯。"><Card title="保存运行日志" desc="关闭后不再写入签到记录文件。"><div className="setting-line"><span>保存签到与错误详情</span><button onClick={() => setLogging(!logging)} className={`switch ${logging ? "on" : ""}`}><i /></button></div></Card><Card title="日志文件位置" desc="默认写入程序目录下的 签到记录.md。"><input className="field" value={logPath} onChange={e => setLogPath(e.target.value)}/></Card><Card title="脚本后端" desc="当前开发版使用本机 Python 后端；发布版会随程序携带独立 sidecar。"><code>sidecar/bridge.py → yxy_backend.py</code></Card><div className="actions"><button className="primary" disabled={busy} onClick={() => saveSettings({ save_log: logging, log_path: logPath }, "日志设置已保存")}>保存日志设置</button></div></SettingsSection>}
      {page === "about" && <SettingsSection title="关于" subtitle="优学院签到助手 · 本地 Windows 桌面程序。"><Card title="优学院签到助手" desc="面向优学院课程监测的本地工具。"><dl><dt>版本</dt><dd>v0.1.0 · Tauri + React</dd><dt>技术栈</dt><dd>Tauri 2 · React · TypeScript · Python sidecar</dd><dt>GitHub</dt><dd>待填写</dd><dt>用户 QQ 群</dt><dd>待填写</dd></dl></Card></SettingsSection>}</div></section>}
    </main>
  </div>;
}
function SettingsSection({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) { return <section className="settings-page"><div className="page-intro"><span className="page-icon">✦</span><div><h2>{title}</h2><p>{subtitle}</p></div></div>{children}</section>; }
function Card({ title, desc, children }: { title: string; desc: string; children: React.ReactNode }) { return <article className="card"><h3>{title}</h3><p>{desc}</p><div className="card-content">{children}</div></article>; }
export default App;
