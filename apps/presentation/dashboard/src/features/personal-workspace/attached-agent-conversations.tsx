import {useEffect, useRef, useState} from "react";
import {MarkdownText} from "./markdown";
import "./attached-agent-conversations.css";

type Binding = {agent_id: string; thread_id: string; binding_ref: string};
type Message = {id: string; role: "user" | "assistant"; text: string; truncated: boolean; timestamp: string | null};
type Snapshot = {messages?: Message[]; before?: number | null; source_ref?: string; history_size?: number; revision: string; unchanged?: boolean; connected?: boolean; can_reply?: boolean; activity: {state: string}};
type Submission = {operation_id: string; text: string; status?: string};

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {...options, signal: AbortSignal.timeout(6_000)});
  const value = await response.json();
  if (!response.ok || value.ok !== true) throw new Error(value.error || "Conversation unavailable");
  return value as T;
}

/** Owner-only original conversation. It never changes the selected executor. */
export function AttachedAgentConversations({goalId, zh}: {goalId: string; zh: boolean}) {
  const [open, setOpen] = useState(false);
  const [agents, setAgents] = useState<Binding[]>([]);
  const [selected, setSelected] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [before, setBefore] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [recoveryError, setRecoveryError] = useState("");
  const [draft, setDraft] = useState("");
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [sending, setSending] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [opening, setOpening] = useState(false);
  const [openRequested, setOpenRequested] = useState(false);
  const [openError, setOpenError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const generation = useRef(0);
  const revision = useRef("");
  const history = useRef<{source: string; size: number} | null>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    else if (!open && dialog.current?.open) dialog.current.close();
  }, [open]);
  const binding = agents.find(agent => agent.agent_id === selected);
  const awaitingReceipt = Boolean(submission?.status && submission.status !== "delivered");
  const cacheKey = binding ? `loopx-attached-input:${goalId}:${binding.binding_ref}` : "";
  const query = binding ? new URLSearchParams({goal_id: goalId, agent_id: binding.agent_id, binding_ref: binding.binding_ref}) : null;

  useEffect(() => {
    let active = true;
    setAgents([]); setSelected(""); setMessages([]); setSnapshot(null); setError("");
    if (open) void request<{agents: Binding[]}>(`/api/chat/codex-attached?${new URLSearchParams({goal_id: goalId})}`)
      .then(value => {if (active) {setAgents(value.agents); setSelected(value.agents[0]?.agent_id ?? "");}})
      .catch(failure => {if (active) setError(String(failure));});
    return () => {active = false;};
  }, [goalId, open, refresh]);

  useEffect(() => {
    const current = ++generation.current;
    revision.current = "";
    history.current = null;
    setOpening(false); setOpenRequested(false); setOpenError("");
    setMessages([]); setSnapshot(null); setBefore(null); setDraft(""); setSubmission(null); setSending(false); setLoadingOlder(false); setError(""); setRecoveryError("");
    if (!binding || !open) return;
    try {
      const stored = sessionStorage.getItem(cacheKey);
      if (stored) {
        const pending = JSON.parse(stored) as Submission;
        if (!pending || typeof pending.operation_id !== "string" || typeof pending.text !== "string" || !["pending", "delivered"].includes(pending.status ?? "")) throw new Error("Invalid recovery record");
        setDraft(pending.status === "delivered" ? "" : pending.text); setSubmission(pending);
      }
    } catch {setRecoveryError(zh ? "浏览器回复恢复记录不可读，暂不能安全发送。请先在原 App 核对。" : "Reply recovery is unreadable; check the original App before clearing it.");}
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      const params = new URLSearchParams(query!);
      if (revision.current) params.set("revision", revision.current);
      try {
        const next = await request<Snapshot>(`/api/chat/codex-attached?${params}`);
        if (current !== generation.current) return;
        const follow = !transcript.current || transcript.current.scrollHeight - transcript.current.scrollTop - transcript.current.clientHeight < 64;
        revision.current = next.revision;
        if (!next.unchanged) {
          const reset = !history.current || history.current.source !== next.source_ref || (next.history_size ?? 0) < history.current.size;
          history.current = {source: next.source_ref ?? "", size: next.history_size ?? 0};
          setMessages(previous => [...new Map([...(reset ? [] : previous), ...next.messages ?? []].map(message => [message.id, message])).values()].sort((a,b) => Number(a.id) - Number(b.id)).slice(-1000));
          if (reset) setBefore(next.before ?? null);
        }
        setSnapshot(previous => ({...previous, ...next})); setError("");
        if (follow) requestAnimationFrame(() => transcript.current?.scrollTo({top: transcript.current.scrollHeight}));
      } catch (failure) {if (current === generation.current) {setError(String(failure)); setSnapshot(null);}}
      finally {if (current === generation.current) timer = setTimeout(read, 1000);}
    }
    void read();
    return () => {generation.current++; clearTimeout(timer);};
  }, [goalId, binding?.binding_ref, open]);

  async function openOriginal() {
    if (!binding || opening) return;
    const current = generation.current;
    setOpening(true); setOpenRequested(false); setOpenError("");
    try {
      await request<{requested: true}>("/api/chat/codex-attached/open", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({goal_id: goalId, agent_id: binding.agent_id, binding_ref: binding.binding_ref})});
      if (current === generation.current) setOpenRequested(true);
    } catch (failure) {if (current === generation.current) setOpenError(String(failure));}
    finally {if (current === generation.current) setOpening(false);}
  }

  async function older() {
    if (before === null || !query || loadingOlder) return;
    const current = generation.current;
    setLoadingOlder(true);
    const params = new URLSearchParams(query); params.set("before", String(before));
    try {
      const next = await request<Snapshot>(`/api/chat/codex-attached?${params}`);
      if (current === generation.current) {
        if (!history.current || history.current.source !== next.source_ref || (next.history_size ?? 0) < history.current.size) throw new Error(zh ? "原会话历史已更新，请重新连接。" : "Original history changed; reconnect to read it.");
        setMessages(previous => [...new Map([...(next.messages ?? []), ...previous].map(row => [row.id, row])).values()].sort((a,b) => Number(a.id) - Number(b.id)).slice(0,1000));
        setBefore(next.before ?? null);
      }
    } catch (failure) {if (current === generation.current) setError(String(failure));}
    finally {if (current === generation.current) setLoadingOlder(false);}
  }

  async function send() {
    if (!binding || sending || error || recoveryError || !snapshot?.can_reply || !draft.trim() || awaitingReceipt) return;
    const current = generation.current;
    const pending = {operation_id: crypto.randomUUID(), text: draft, status: "pending"};
    try {sessionStorage.setItem(cacheKey, JSON.stringify(pending));}
    catch {setError(zh ? "无法保存回复回执，未发送。" : "Cannot save reply recovery; nothing was sent."); return;}
    setSubmission(pending); setSending(true);
    try {
      const result = await request<{status: string}>("/api/chat/codex-attached", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({operation_id: pending.operation_id, text: pending.text, goal_id: goalId, agent_id: binding.agent_id, binding_ref: binding.binding_ref})});
      if (current === generation.current) {
        const delivered = {...pending, status: result.status};
        sessionStorage.setItem(cacheKey, JSON.stringify(delivered)); setSubmission(delivered);
        if (result.status === "delivered") setDraft("");
        revision.current = "";
      }
    } catch (failure) {if (current === generation.current) setError(String(failure));}
    finally {if (current === generation.current) setSending(false);}
  }
  async function readReceipt() {
    if (!query || !submission) return;
    const current = generation.current;
    const params = new URLSearchParams(query); params.set("operation_id", submission.operation_id);
    try {
      const receipt = await request<{status: string}>(`/api/chat/codex-attached?${params}`);
      if (current === generation.current) {
        const next = {...submission, status: receipt.status};
        sessionStorage.setItem(cacheKey, JSON.stringify(next)); setSubmission(next);
        if (receipt.status === "delivered") {setDraft(""); setRecoveryError("");}
      }
    } catch (failure) {if (current === generation.current) setRecoveryError(String(failure));}
  }
  function nextReply() {
    try {sessionStorage.removeItem(cacheKey); setSubmission(null); setDraft(""); setError(""); setRecoveryError("");}
    catch {setError(zh ? "无法清理回复回执。" : "Reply recovery could not be cleared.");}
  }
  return <section className="attached-agent-conversations">
    <button type="button" className="attached-conversation-entry" onClick={() => setOpen(true)}>{zh ? "与 Codex App 原会话对话" : "Talk to original Codex App conversations"}</button>
    <dialog ref={dialog} className="attached-conversation-dialog" aria-label={zh ? "Codex App 原会话" : "Original Codex App conversations"} onClose={() => setOpen(false)}>
    <header className="attached-conversation-heading"><h2>{zh ? "Codex App 原会话" : "Original Codex App conversations"}</h2><button type="button" onClick={() => setOpen(false)}>{zh ? "返回工作台" : "Return to workspace"}</button></header>
    <div className="attached-conversation-content">
      <p>{zh ? "每秒同步原会话已保存的消息；逐字流和审批仍在 Codex App。" : "Saved messages sync every second; token streaming and approvals remain in Codex App."}</p>
      {agents.length ? <label>{zh ? "Agent" : "Agent"}<select aria-label={zh ? "选择原会话 Agent" : "Original conversation Agent"} value={selected} disabled={sending} onChange={event => setSelected(event.target.value)}>{agents.map(agent => <option key={agent.binding_ref} value={agent.agent_id}>{agent.agent_id}</option>)}</select></label> : !error ? <p>{zh ? "此 Goal 暂无已绑定的本机 Codex App 会话。" : "This Goal has no bound local Codex App conversation."}</p> : null}
      {recoveryError ? <p role="alert">{recoveryError} <button type="button" disabled={sending} onClick={nextReply}>{zh ? "已在原 App 核对，清理本页回执" : "Checked in original App; clear this receipt"}</button></p> : null}
      {error ? <p role="alert">{error} <button type="button" onClick={() => setRefresh(value => value + 1)}>{zh ? "重新连接" : "Reconnect"}</button></p> : null}
      {binding ? <>
        <p role="status">{!snapshot ? (zh ? "正在连接或连接不可用" : "Connecting or unavailable") : snapshot.can_reply ? (zh ? "原会话可回复" : "Original conversation accepts replies") : snapshot.activity.state === "turn_open" ? (zh ? "原会话有未结束执行；可在原 App 中追加输入" : "The original conversation has an unfinished turn; add input in the original App") : (zh ? "历史可读；回复连接或执行状态尚未确认" : "History available; reply connection or execution state unconfirmed")}</p>
        <button type="button" className="attached-conversation-open" aria-disabled={opening} aria-busy={opening} onClick={() => void openOriginal()}>{zh ? "在 Codex App 中打开" : "Open in Codex App"}</button>
        {openRequested ? <p role="status">{zh ? "已请求打开原会话，请在 Codex App 中查看。" : "Requested opening the original conversation; check Codex App."}</p> : null}
        {openError ? <p role="alert">{openError}</p> : null}
        {before !== null ? <button type="button" disabled={loadingOlder || messages.length >= 1000} onClick={() => void older()}>{zh ? "读取更早的消息" : "Read earlier messages"}</button> : null}
        <div className="attached-conversation-messages" ref={transcript} aria-label={zh ? "原会话消息" : "Original conversation messages"}>
          {messages.map(message => <article key={message.id} data-role={message.role}><header><strong>{message.role === "user" ? (zh ? "你" : "You") : binding.agent_id}</strong> {message.timestamp ? <time>{new Date(message.timestamp).toLocaleString()}</time> : null}</header><MarkdownText text={message.text}/>{message.truncated ? <p>{zh ? "此消息已截短；完整内容见原 App。" : "Message shortened; open the original App for full content."}</p> : null}</article>)}
          {!messages.length && snapshot ? <p>{zh ? "当前有界历史窗口内没有可显示的消息，可读取更早记录。" : "No displayable messages in this bounded window; read earlier records."}</p> : null}
        </div>
        {submission?.status ? <p role="status">{submission.status === "delivered" ? (zh ? "已由原 App 接纳；等待 Agent 结果。" : "Accepted by the original App; awaiting the Agent result.") : (zh ? "投递尚未确认，请先在原 App 核对，不会自动重发。" : "Delivery unconfirmed; check the original App. No automatic resend.")} {submission.status === "pending" ? <button type="button" disabled={sending} onClick={() => void readReceipt()}>{zh ? "重读投递回执" : "Recheck delivery receipt"}</button> : null}{awaitingReceipt ? <button type="button" disabled={sending} onClick={nextReply}>{zh ? "已核对，写下一条" : "Checked; write another"}</button> : null}</p> : null}
        {!awaitingReceipt ? <form onSubmit={event => {event.preventDefault(); void send();}}><label>{zh ? `回复 ${binding.agent_id}` : `Reply to ${binding.agent_id}`}<textarea value={draft} maxLength={24000} disabled={sending} onChange={event => {setDraft(event.target.value); setSubmission(null);}}/></label><button type="submit" disabled={sending || Boolean(error || recoveryError) || !snapshot?.can_reply || !draft.trim()}>{sending ? (zh ? "正在交给原 App…" : "Sending to original App…") : (zh ? "发送到原会话" : "Send to original conversation")}</button></form> : null}
      </> : null}
    </div>
    </dialog>
  </section>;
}
