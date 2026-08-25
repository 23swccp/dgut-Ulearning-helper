import { useEffect, useRef, useState } from "react";

type Page = "terminal" | "courses" | "learning" | "browser" | "logs" | "about";
type Phase = "ready" | "login" | "courses" | "selected" | "monitoring";
type BackendEvent = { message: string; kind: string };
type AccountLogin = { enabled: boolean; username: string; has_password: boolean };
type Course = { id: number; name: string; teacherName: string };
type AppConfig = {
  browser_name?: string; browser_path?: string; save_log?: boolean; log_path?: string;
  course_playback_rate?: number; course_auto_dismiss_dialog?: boolean; course_document_scroll_enabled?: boolean;
};
type BackendResult = { ok: boolean; error?: string; courses?: Course[]; course?: Course | null; config?: AppConfig; account?: AccountLogin; events?: BackendEvent[] };

const loginPayload = { url: "https://lms.dgut.edu.cn" };
const icon: Record<Page, string> = { terminal: "⌘", courses: "▦", learning: "▶", browser: "◎", logs: "▤", about: "i" };

function App() {
  const [page, setPage] = useState<Page>("terminal");
  const [phase, setPhase] = useState<Phase>("ready");
  const [command, setCommand] = useState("");
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState<string[]>(["优学院助手", "────────────────────────────────────────────────────", "正在连接本地后端…"]);
  const [courses, setCourses] = useState<Course[]>([]);
  const [selectedCourse, setSelectedCourse] = useState<Course | null>(null);
  const [logging, setLogging] = useState(true);
  const [browser, setBrowser] = useState("自动检测");
  const [path, setPath] = useState("");
  const [logPath, setLogPath] = useState("./签到记录.md");
  const [accountEnabled, setAccountEnabled] = useState(false);
  const [accountName, setAccountName] = useState("");
  const [accountPassword, setAccountPassword] = useState("");
  const [hasSavedPassword, setHasSavedPassword] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(8);
  const [autoDismiss, setAutoDismiss] = useState(true);
  const [documentScroll, setDocumentScroll] = useState(true);
  const [helperRunning, setHelperRunning] = useState(false);
  const [saved, setSaved] = useState("");
  const endRef = useRef<HTMLPreElement>(null);
  const commandRef = useRef<HTMLInputElement>(null);
  const append = (line: string) => setLogs(items => [...items, line]);

  useEffect(() => { endRef.current?.scrollTo({ top: endRef.current.scrollHeight, behavior: "smooth" }); }, [logs]);
  useEffect(() => {
    if (page !== "terminal" || busy) return;
    const frame = requestAnimationFrame(() => commandRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [page, busy]);
  useEffect(() => {
    void call("get_settings").then(result => {
      if (result.ok && result.config) { loadConfig(result.config); append("本地后端已连接。按 Enter 读取本地登录缓存。"); }
      else append(`后端连接失败：${result.error || "未知错误"}`);
    });
    void call("get_account_login_status").then(result => { if (result.ok && result.account) loadAccount(result.account); });
  }, []);
  useEffect(() => {
    let disposed = false;
    const pullEvents = async () => {
      try { const result = await call("get_events"); if (!disposed) result.events?.forEach(event => append(event.message)); }
      catch { /* 本地服务启动和关闭阶段安静忽略。 */ }
    };
    void pullEvents();
    const timer = window.setInterval(() => void pullEvents(), 1000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    const heartbeat = () => void fetch("/api/heartbeat", { method: "POST", keepalive: true }).catch(() => undefined);
    const closing = () => { navigator.sendBeacon("/api/client-closed", ""); };
    heartbeat();
    const timer = window.setInterval(heartbeat, 2000);
    window.addEventListener("pagehide", closing);
    return () => { window.clearInterval(timer); window.removeEventListener("pagehide", closing); };
  }, []);

  async function call(commandName: string, payload: Record<string, unknown> = {}): Promise<BackendResult> {
    try {
      const response = await fetch("/api/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command: commandName, payload }) });
      return await response.json() as BackendResult;
    } catch (error) { return { ok: false, error: `后端连接失败：${String(error)}` }; }
  }
  function loadConfig(config: AppConfig) {
    setBrowser(config.browser_name || "自动检测"); setPath(config.browser_path || ""); setLogging(config.save_log !== false); setLogPath(config.log_path || "./签到记录.md");
    setPlaybackRate(config.course_playback_rate || 8); setAutoDismiss(config.course_auto_dismiss_dialog !== false); setDocumentScroll(config.course_document_scroll_enabled !== false);
  }
  function loadAccount(account: AccountLogin) {
    setAccountEnabled(account.enabled); setAccountName(account.username); setHasSavedPassword(account.has_password); setAccountPassword("");
  }
  function compatibleBrowserPath(name: string, candidate: string) {
    const expected = name === "Microsoft Edge" ? "msedge.exe" : name === "Google Chrome" ? "chrome.exe" : "";
    return expected && candidate && !candidate.toLowerCase().endsWith(expected) ? "" : candidate;
  }
  function chooseBrowser(name: string) { setBrowser(name); setPath(current => compatibleBrowserPath(name, current)); }
  function save(message: string) { setSaved(message); window.setTimeout(() => setSaved(""), 2200); }
  async function saveSettings(values: Record<string, unknown>, message: string) {
    setBusy(true); const result = await call("update_settings", values); setBusy(false);
    if (result.ok) { if (result.config) loadConfig(result.config); save(message); }
  }
  async function saveBrowserSettings(message: string) {
    const browserPath = compatibleBrowserPath(browser, path); setPath(browserPath);
    await saveSettings({ browser_name: browser === "自动检测" ? "" : browser, browser_path: browserPath }, message);
  }
  async function saveAccountLogin() {
    setBusy(true); const result = await call("update_account_login", { username: accountName, password: accountPassword, enabled: accountEnabled }); setBusy(false);
    if (result.ok && result.account) { loadAccount(result.account); save(accountEnabled ? "账号自动重新登录已保存" : "已关闭账号自动重新登录"); }
  }
  async function refreshCourses(fromBrowser = false) {
    if (busy) return; setBusy(true); const result = await call(fromBrowser ? "load_session_and_courses" : "load_saved_courses"); setBusy(false);
    if (result.ok && result.courses?.length) { setCourses(result.courses); setPhase("courses"); save(`已读取 ${result.courses.length} 门课程`); }
    else { setCourses([]); save(fromBrowser ? "未能从浏览器读取课程" : "登录缓存不可用，请从浏览器重新读取"); }
  }
  async function chooseCourse(course: Course) {
    if (busy) return; setBusy(true); const result = await call("select_course", { query: String(course.id) }); setBusy(false);
    if (result.ok && result.course) { setSelectedCourse(result.course); setPhase("selected"); save(`已选定《${result.course.name}》`); }
  }
  async function startMonitor() {
    if (busy || !selectedCourse) return; setBusy(true); const result = await call("start_monitor"); setBusy(false);
    if (result.ok) { setPhase("monitoring"); save("已开始课程监测"); }
  }
  async function stopMonitor() {
    if (busy) return; setBusy(true); const result = await call("stop_monitor"); setBusy(false);
    if (result.ok) { setPhase("selected"); save("课程监测已停止"); }
  }
  async function saveLearningSettings() {
    await saveSettings({ course_playback_rate: playbackRate, course_auto_dismiss_dialog: autoDismiss, course_document_scroll_enabled: documentScroll }, "学习辅助设置已保存");
  }
  async function toggleCourseHelper() {
    setBusy(true);
    if (helperRunning) {
      const result = await call("stop_course_helper"); if (result.ok) { setHelperRunning(false); save("学习辅助已停止"); }
    } else {
      await call("update_settings", { course_playback_rate: playbackRate, course_auto_dismiss_dialog: autoDismiss, course_document_scroll_enabled: documentScroll });
      const result = await call("start_course_helper");
      if (result.ok) { setHelperRunning(true); save("学习辅助已启动"); } else save(result.error || "未找到已打开的课件学习页");
    }
    setBusy(false);
  }

  async function run() {
    if (busy) return;
    const input = command.trim(); setCommand(""); setBusy(true);
    if (input.toLowerCase() === "clear") { setLogs([]); setBusy(false); return; }
    if (input) append(`> ${input}`);
    if (input.toLowerCase() === "kill") {
      const result = await call("shutdown_app"); if (result.ok) append("正在关闭本地服务与前端进程…"); else append(`退出失败：${result.error || "未知错误"}`);
      setBusy(false); return;
    }
    if (input.toLowerCase() === "login" || input === "登录") {
      append("正在打开优学院登录页，请稍候…"); setPhase("login");
      void call("start_browser", loginPayload).then(result => result.ok ? append("请在新标签页完成登录，完成后回到这里按 Enter 或输入 读取。 ") : append(`启动浏览器失败：${result.error || "未知错误"}`));
      window.setTimeout(() => setBusy(false), 1000); return;
    }
    if (phase === "login") {
      if (input && !["读取", "继续", "enter"].includes(input.toLowerCase())) append("完成登录后按 Enter 或输入 读取。 ");
      else { const result = await call("load_session_and_courses"); if (result.ok && result.courses?.length) { setCourses(result.courses); setPhase("courses"); append("请输入课程名称关键词或课程 ID。 "); } else append("尚未读取到有效登录状态，请完成登录后重试。 "); }
    } else if (phase === "ready") {
      if (input && !["读取", "继续", "enter"].includes(input.toLowerCase())) append("按 Enter 读取本地登录缓存；如需登录请输入 login。 ");
      else { const result = await call("load_saved_courses"); if (result.ok && result.courses?.length) { setCourses(result.courses); setPhase("courses"); append("请输入课程名称关键词或课程 ID。 "); } else append("缓存不可用。请输入 login 完成浏览器登录。 "); }
    } else if (phase === "courses") {
      const result = await call("select_course", { query: input });
      if (result.ok && result.course) { setSelectedCourse(result.course); setPhase("selected"); append(`已选定：${result.course.name}。按 Enter 开始轮询；输入 / 取消选定。`); } else append("未找到唯一匹配课程，请输入更精确的名称或课程 ID。 ");
    } else if (phase === "selected") {
      if (input === "/") { await call("clear_selected_course"); setSelectedCourse(null); setPhase("courses"); append("已取消选定。 "); }
      else if (!input) { const result = await call("start_monitor"); if (result.ok) setPhase("monitoring"); }
      else append("按 Enter 开始轮询，或输入 / 取消选定。 ");
    } else if (phase === "monitoring") {
      if (input === "/" || input.toLowerCase() === "stop") { await call("stop_monitor"); setPhase("selected"); append("已停止轮询。 "); } else append("正在轮询。输入 / 或 stop 可以停止。 ");
    }
    setBusy(false);
  }
  async function startLogin() {
    if (busy) return; const browserPath = compatibleBrowserPath(browser, path); setBusy(true);
    const settings = await call("update_settings", { browser_name: browser === "自动检测" ? "" : browser, browser_path: browserPath }); if (settings.config) loadConfig(settings.config);
    setPage("terminal"); const result = await call("start_browser", loginPayload); if (!result.ok) append(`启动浏览器失败：${result.error || "未知错误"}`); setBusy(false);
  }

  const navItems: Page[] = ["courses", "learning", "browser", "logs", "about"];
  const labels: Record<Page, string> = { terminal: "", courses: "课程监测", learning: "学习辅助", browser: "浏览器与登录", logs: "日志与数据", about: "关于" };

  return <div className="app-shell"><main className="content">
    <header className={page === "terminal" ? "terminal-header" : "settings-header"}>{page === "terminal" ? <button className="settings" onClick={() => setPage("courses")}>⚙</button> : <><button className="back" aria-label="返回终端" onClick={() => setPage("terminal")}><span className="back-arrow" /></button><h1>优学院助手</h1></>}</header>
    {saved && <div className="toast">✓ {saved}</div>}
    {page === "terminal" && <section className="terminal"><pre ref={endRef}>{logs.join("\n")}</pre><div className="command"><b>›</b><input ref={commandRef} autoFocus disabled={busy} value={command} onChange={event => setCommand(event.target.value)} onKeyDown={event => event.key === "Enter" && run()} placeholder={phase === "courses" ? "输入课程名称或课程 ID…" : phase === "selected" ? "按 Enter 开始，/ 取消…" : "按 Enter 继续，或输入命令…"}/><button disabled={busy} onClick={run}>{busy ? "处理中…" : "确认 ↵"}</button></div></section>}
    {page !== "terminal" && <section className="settings-layout"><aside className="settings-nav"><p>功能</p>{navItems.map(item => <button key={item} onClick={() => setPage(item)} className={page === item ? "active" : ""}><b>{icon[item]}</b>{labels[item]}</button>)}</aside><div className="settings-body">
      {page === "courses" && <SettingsSection title="课程监测" subtitle="刷新课程后选择一门课程，自动检查课堂签到。"><Card title="我的课程" desc="优先使用本地登录缓存；缓存失效时可从已登录的浏览器重新读取。"><div className="course-actions"><button className="primary" disabled={busy} onClick={() => refreshCourses()}>刷新课程</button><button className="secondary" disabled={busy} onClick={() => refreshCourses(true)}>从浏览器重新读取</button></div>{courses.length ? <div className="course-list">{courses.map(course => <button key={course.id} className={`course-item ${selectedCourse?.id === course.id ? "selected" : ""}`} disabled={busy} onClick={() => chooseCourse(course)}><span><b>{course.name}</b><small>{course.teacherName} · ID {course.id}</small></span><em>{selectedCourse?.id === course.id ? "已选" : "选择"}</em></button>)}</div> : <p className="empty-courses">尚未读取课程。若缓存失效，请先在“浏览器与登录”中完成登录。</p>}</Card>{selectedCourse && <Card title="当前课程" desc={`《${selectedCourse.name}》 · ${selectedCourse.teacherName}`}><div className="setting-line"><span>{phase === "monitoring" ? "正在自动监测签到" : "尚未开始监测"}</span><button className={`switch ${phase === "monitoring" ? "on" : ""}`} disabled={busy} onClick={() => phase === "monitoring" ? stopMonitor() : startMonitor()}><i /></button></div><div className="actions"><button className="secondary" disabled={busy} onClick={async () => { await call("clear_selected_course"); setSelectedCourse(null); setPhase("courses"); }}>取消选择</button><button className="primary" disabled={busy} onClick={() => phase === "monitoring" ? stopMonitor() : startMonitor()}>{phase === "monitoring" ? "停止监测" : "开始监测"}</button></div></Card>}</SettingsSection>}
      {page === "learning" && <SettingsSection title="课件学习辅助" subtitle="控制视频播放、文档滚动和章节衔接；测验必须由用户自行完成。"><Card title="使用步骤" desc="先在同一个调试浏览器中登录优学院，并打开 URL 含 ua.dgut.edu.cn/learnCourse 的课件学习页。"><div className="actions"><button className="secondary" disabled={busy} onClick={() => call("start_browser", { url: "https://ua.dgut.edu.cn" })}>打开课件网站</button></div></Card><Card title="播放与阅读" desc="高倍速可能受课程页面限制；文档滚动仅模拟阅读，不处理测验。"><label className="field-label">视频倍速<input className="field" type="number" min="1" max="16" step="0.5" value={playbackRate} onChange={event => setPlaybackRate(Number(event.target.value) || 1)}/></label><div className="setting-line spaced"><span>自动处理章节提示框</span><button onClick={() => setAutoDismiss(!autoDismiss)} className={`switch ${autoDismiss ? "on" : ""}`}><i /></button></div><div className="setting-line spaced"><span>自动滚动文档</span><button onClick={() => setDocumentScroll(!documentScroll)} className={`switch ${documentScroll ? "on" : ""}`}><i /></button></div><div className="actions"><button className="secondary" disabled={busy} onClick={saveLearningSettings}>保存设置</button><button className="primary" disabled={busy} onClick={toggleCourseHelper}>{helperRunning ? "停止学习辅助" : "开始学习辅助"}</button></div></Card></SettingsSection>}
      {page === "browser" && <SettingsSection title="浏览器与登录" subtitle="选择调试浏览器，并管理可选的登录恢复方式。"><Card title="默认浏览器" desc="未设置时按 Edge、Chrome、其他 Chromium 浏览器顺序检测。"><div className="choice-row">{["自动检测", "Microsoft Edge", "Google Chrome"].map(name => <button key={name} onClick={() => chooseBrowser(name)} className={`choice ${browser === name ? "selected" : ""}`}>{name}</button>)}</div></Card><Card title="浏览器程序位置" desc="自动检测失败时填写浏览器 .exe 的完整路径。"><input className="field" value={path} onChange={event => setPath(event.target.value)} placeholder="自动检测"/></Card><Card title="账号密码自动重新登录（可选）" desc="只在 Token 不存在或返回 401 时尝试一次；关闭后清除 account.json。"><div className="setting-line"><span>启用自动重新登录</span><button onClick={() => setAccountEnabled(!accountEnabled)} className={`switch ${accountEnabled ? "on" : ""}`}><i /></button></div>{accountEnabled && <div className="account-fields"><input className="field" value={accountName} onChange={event => setAccountName(event.target.value)} placeholder="学号" autoComplete="username"/><input className="field" type="password" value={accountPassword} onChange={event => setAccountPassword(event.target.value)} placeholder={hasSavedPassword ? "密码已保存；留空则不修改" : "密码"} autoComplete="current-password"/><p className="hint">凭据只保存在本机 account.json，不进入日志或 Git。</p></div>}<div className="actions"><button className="secondary" disabled={busy} onClick={saveAccountLogin}>{accountEnabled ? "保存账号设置" : "关闭并清除账号"}</button></div></Card><div className="actions"><button className="secondary" disabled={busy} onClick={startLogin}>打开登录页</button><button className="primary" disabled={busy} onClick={() => saveBrowserSettings("浏览器设置已保存")}>保存浏览器设置</button></div></SettingsSection>}
      {page === "logs" && <SettingsSection title="日志与数据" subtitle="运行记录追加到 Markdown 文件，便于长期追溯。"><Card title="保存运行日志" desc="关闭后不再写入签到记录文件。"><div className="setting-line"><span>保存签到与错误详情</span><button onClick={() => setLogging(!logging)} className={`switch ${logging ? "on" : ""}`}><i /></button></div></Card><Card title="日志文件位置" desc="默认写入仓库目录下的 签到记录.md。"><input className="field" value={logPath} onChange={event => setLogPath(event.target.value)}/></Card><div className="actions"><button className="primary" disabled={busy} onClick={() => saveSettings({ save_log: logging, log_path: logPath }, "日志设置已保存")}>保存日志设置</button></div></SettingsSection>}
      {page === "about" && <SettingsSection title="关于" subtitle="优学院助手 · 本机浏览器版。"><Card title="优学院助手" desc="课程读取、签到监测与课件学习辅助的统一本地网页界面。"><dl><dt>版本</dt><dd>v0.2.0</dd><dt>运行方式</dt><dd>React + Python · 本机浏览器</dd><dt>GitHub</dt><dd>23swccp/dgut.yxy-checkin_assistant</dd><dt>数据范围</dt><dd>仅保存在本机</dd></dl></Card></SettingsSection>}
    </div></section>}
  </main></div>;
}

function SettingsSection({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) { return <section className="settings-page"><div className="page-intro"><span className="page-icon">✦</span><div><h2>{title}</h2><p>{subtitle}</p></div></div>{children}</section>; }
function Card({ title, desc, children }: { title: string; desc: string; children: React.ReactNode }) { return <article className="card"><h3>{title}</h3><p>{desc}</p><div className="card-content">{children}</div></article>; }

export default App;
