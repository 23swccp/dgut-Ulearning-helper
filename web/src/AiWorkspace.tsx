import { useEffect, useRef, useState } from "react";
import { SafeMarkdown } from "./safeMarkdown";

type Role = "user" | "assistant";
type Message = { id: string; role: Role; content: string; reasoning?: string };
type ChatResult = { ok: boolean; error?: string; answer?: string; reasoning?: string; upstreamToolCallCount?: number };

export function AiWorkspace() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const heartbeat = () => void fetch("/api/heartbeat", { method: "POST", keepalive: true }).catch(() => undefined);
    heartbeat();
    const timer = window.setInterval(heartbeat, 2000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);

  async function send() {
    const content = input.trim();
    if (!content || busy) return;
    const userMessage: Message = { id: crypto.randomUUID(), role: "user", content };
    const history = [...messages, userMessage];
    setMessages(history);
    setInput("");
    setError("");
    setBusy(true);
    try {
      const response = await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          command: "ai_chat",
          payload: { messages: history.map(({ role, content: text }) => ({ role, content: text })) },
        }),
      });
      const result = await response.json() as ChatResult;
      if (!result.ok) throw new Error(result.error || "AI 请求失败");
      setMessages(items => [...items, {
        id: crypto.randomUUID(),
        role: "assistant",
        content: result.answer || "",
        reasoning: result.reasoning || "",
      }]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AI 请求失败");
    } finally {
      setBusy(false);
      window.setTimeout(() => inputRef.current?.focus(), 0);
    }
  }

  return <main className="ai-shell">
    <header className="ai-header">
      <div className="ai-brand"><span>✦</span><div><strong>AI 工作台</strong><small>优学院课程 AI · 实验功能</small></div></div>
      <div className="ai-header-actions"><span className="ai-status">本地安全中继</span><button type="button" onClick={() => window.close()}>关闭</button></div>
    </header>
    <section className="ai-conversation" aria-live="polite">
      {messages.length === 0 && <div className="ai-empty">
        <div className="ai-orb">✦</div>
        <h1>从课程上下文开始</h1>
        <p>请先在优学院课程中打开 AI 助手页面。本工作台会复用浏览器中的短期登录状态，不保存 Cookie 或授权信息。</p>
        <div className="ai-suggestions">
          {["梳理这门课的重点", "解释一个课程概念", "制定本周复习计划"].map(text => <button key={text} type="button" onClick={() => { setInput(text); inputRef.current?.focus(); }}>{text}</button>)}
        </div>
      </div>}
      {messages.map(message => <article key={message.id} className={`ai-message ${message.role}`}>
        <div className="ai-avatar">{message.role === "user" ? "你" : "✦"}</div>
        <div className="ai-bubble">
          {message.reasoning && <details><summary>思考过程</summary><pre>{message.reasoning}</pre></details>}
          {message.role === "assistant" ? <SafeMarkdown source={message.content}/> : <p>{message.content}</p>}
        </div>
      </article>)}
      {busy && <article className="ai-message assistant"><div className="ai-avatar">✦</div><div className="ai-bubble ai-thinking"><i/><i/><i/></div></article>}
      <div ref={endRef}/>
    </section>
    <footer className="ai-composer-wrap">
      {error && <div className="ai-error">{error}</div>}
      <div className="ai-composer">
        <textarea ref={inputRef} autoFocus value={input} disabled={busy} maxLength={12000} rows={1} placeholder="向课程 AI 提问…" onChange={event => setInput(event.target.value)} onKeyDown={event => {
          if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); }
        }}/>
        <button type="button" aria-label="发送" disabled={busy || !input.trim()} onClick={() => void send()}>➤</button>
      </div>
      <small>Enter 发送 · Shift + Enter 换行 · 模型回答可能不准确</small>
    </footer>
  </main>;
}
