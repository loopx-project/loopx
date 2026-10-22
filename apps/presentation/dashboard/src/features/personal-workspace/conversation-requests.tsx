import {useEffect, useRef, useState} from "react";
import {ChevronRight, ListChecks, RefreshCw, X} from "lucide-react";
import {fetchChatSession, fetchChatSessions, type ChatVisibleMessage} from "../../data/chat";
import {fetchConversationRequests, type ConversationRequest, type ConversationRequestInventory} from "../../data/conversation-requests";
import {ContextHelp} from "./context-help";
import {MarkdownText} from "./markdown";
import {useWorkspaceI18n} from "./i18n";
import "./conversation-requests.css";

function requestStage(row: ConversationRequest, zh: boolean) {
  if (!row.unfinished) return row.control_state === "cancelled" ? (zh ? "已取消" : "Cancelled")
    : (zh ? "已回传" : "Returned");
  switch (row.agenda?.step) {
    case "recover_evidence": return zh ? "待恢复" : "Readback unavailable";
    case "reconcile_reservation": return zh ? "取消待收尾" : "Cancellation settling";
    case "await_return_delivery": return zh ? "待送达" : "Awaiting delivery";
    case "consume_peer_result": case "await_peer_consumption": return zh ? "结果待核对" : "Result awaiting readback";
    default: return row.decision === "adopt" ? (zh ? "待结论" : "Awaiting conclusion")
      : row.decision === "defer" ? (zh ? "已暂缓" : "Deferred")
      : (zh ? "待回复" : "Awaiting reply");
  }
}

function OriginalRequest({row, zh}: {row: ConversationRequest; zh: boolean}) {
  const [messages, setMessages] = useState<ChatVisibleMessage[] | null>(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState(false);
  const content = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open && messages) {content.current?.focus(); content.current?.scrollIntoView({block: "nearest"});}
  }, [open, messages]);
  useEffect(() => {
    setMessages(null); setError(false);
    if (!open) return;
    const abort = new AbortController();
    void fetchChatSession(row.source_session_id, abort.signal).then(value => {
      if (abort.signal.aborted) return;
      const source = value.messages.filter(message => message.turn_id === row.source_turn_id);
      if (!source.length) setError(true); else setMessages(source);
    }).catch(() => {if (!abort.signal.aborted) setError(true);});
    return () => abort.abort();
  }, [open, row.source_session_id, row.source_turn_id]);
  return <details onToggle={event => setOpen(event.currentTarget.open)}><summary>{zh ? "原对话" : "Original conversation"}</summary>
    <div ref={content} tabIndex={-1}>{error ? <p role="alert">{zh ? "原对话暂时无法读取，请重新打开重试。" : "Original conversation unavailable. Reopen to retry."}</p>
      : messages ? messages.map(message => <article key={message.message_id}><strong>{message.role === "user" ? (zh ? "你" : "You") : "LoopX"}</strong><MarkdownText text={message.text}/></article>)
      : <p role="status">{zh ? "正在读取…" : "Loading…"}</p>}</div>
  </details>;
}

export function ConversationRequests({sessionId, ownerFallback, refreshKey}: {sessionId?: string; ownerFallback: boolean; refreshKey: string}) {
  const {locale} = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState(false);
  const [pages, setPages] = useState<ConversationRequestInventory[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [noSession, setNoSession] = useState(false);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const requestAbort = useRef<AbortController | null>(null);
  useEffect(() => {
    setOpen(false); setHistory(false); setPages([]); setError(false);
  }, [sessionId]);
  useEffect(() => {
    if (open) dialog.current?.showModal(); else dialog.current?.close();
  }, [open]);
  useEffect(() => {
    void load();
    return () => {generation.current++; requestAbort.current?.abort();};
  }, [sessionId, ownerFallback, history, refreshKey, revision]);
  async function load(offset = 0) {
    const current = ++generation.current;
    requestAbort.current?.abort();
    const abort = new AbortController(); requestAbort.current = abort;
    setBusy(true); setError(false); setNoSession(false);
    // Never retain a previous source/page behind an error or changed scope.
    if (!offset) setPages([]);
    try {
      let source = sessionId && sessionId !== "new-session-pending" ? sessionId : undefined;
      if (!source && ownerFallback) {
        const listed = await fetchChatSessions({channelId: "manager", signal: abort.signal});
        source = listed.sessions.find(row => row.channel_id === "manager")?.session_id;
      }
      if (current !== generation.current) return;
      if (!source) {setNoSession(true); return;}
      const value = await fetchConversationRequests(source, history, offset, abort.signal);
      if (current !== generation.current) return;
      // Returns and revised briefs can reorder a live list between pages.
      // Restart the read rather than silently skipping work at an old offset.
      if (offset && value.snapshot_digest !== pages[0]?.snapshot_digest) {void load(); return;}
      if (offset && value.next_offset !== null && value.next_offset <= offset) throw new Error("Request paging did not advance.");
      setPages(previous => offset ? [...previous, value] : [value]);
    } catch {
      if (current === generation.current && !abort.signal.aborted) {setError(true); setPages([]);}
    } finally {if (current === generation.current) setBusy(false);}
  }
  const page = pages[0], last = pages.at(-1);
  const unknown = page && (!page.coverage.scan_complete || page.coverage.unreadable > 0);
  const rows = [...new Map(pages.flatMap(value => value.rows).map(row => [row.request_id, row])).values()];
  const recipients = [...new Set(rows.map(row => `${row.goal_id}\n${row.agent_id}`))];
  const count = error || !page ? "—" : unknown ? (page.pending_count ? `${page.pending_count}+` : "—") : page.pending_count;
  const title = zh ? "委派" : "Delegations";
  const close = () => {setOpen(false); trigger.current?.focus();};
  return <div className="conversation-requests-entry">
    <button ref={trigger} type="button" onClick={() => {setHistory(false); setOpen(true); setRevision(value => value + 1);}}
      aria-haspopup="dialog" aria-label={zh ? `委派，${count} 条待跟进` : `Delegations, ${count} awaiting follow-up`}>
      <ListChecks size={16}/><span>{title}</span><span className="conversation-requests-count">{count}</span>
    </button>
    {error ? <span role="status">{zh ? "读取失败" : "Read unavailable"}</span> : null}
    <dialog ref={dialog} className="conversation-requests-dialog" aria-labelledby="conversation-requests-title"
      onCancel={event => {event.preventDefault(); close();}} onClick={event => {if (event.target === event.currentTarget) close();}}>
      <header><div><h2 id="conversation-requests-title" tabIndex={-1} autoFocus>{title}</h2><ContextHelp label={zh ? "委派范围与状态" : "Delegation scope and status"}>
        <p>{zh ? "当前管家发出的请求，含同一来源的较早会话。数量是仍需回传、送达或核对的请求，不是任务数或成果验收。时间为最近可核验的记录。结论与取消记录仍可在历史中查看。" : "Requests from this steward, including earlier sessions from the same source. Counts describe returns, delivery or readback still owed, not task counts or accepted outcomes. Times identify the latest verifiable record. Concluded and cancelled requests remain in history."}</p>
      </ContextHelp></div><button type="button" onClick={close} aria-label={zh ? "关闭委派" : "Close delegations"}><X size={18}/></button></header>
      <nav aria-label={zh ? "委派记录" : "Delegation records"}>
        <button type="button" aria-pressed={!history} onClick={() => setHistory(false)}>{zh ? "待跟进" : "Awaiting follow-up"}</button>
        <button type="button" aria-pressed={history} onClick={() => setHistory(true)}>{zh ? "全部记录" : "All records"}</button>
        <button type="button" disabled={busy} onClick={() => setRevision(value => value + 1)} aria-label={zh ? "刷新委派" : "Refresh delegations"}><RefreshCw size={16}/></button>
      </nav>
      <div className="conversation-requests-body" aria-busy={busy}>
        {error ? <p role="alert">{zh ? "委派记录暂时无法核验。" : "Delegation records cannot be verified."} <button type="button" onClick={() => setRevision(value => value + 1)}>{zh ? "重试" : "Retry"}</button></p> : null}
        {unknown ? <p role="status">{zh ? "部分记录未能读取，数量与列表尚不完整。" : "Some records could not be read. Counts and list are incomplete."}</p> : null}
        {busy && !rows.length ? <p role="status">{zh ? "正在读取委派…" : "Loading delegations…"}</p> : null}
        {!busy && noSession ? <p>{zh ? "尚无可读取的管家会话。" : "No readable steward conversation yet."}</p> : null}
        {!busy && !noSession && !error && page && !unknown && !rows.length ? <p>{zh ? "当前没有待跟进的委派。" : "No delegations awaiting follow-up."}</p> : null}
        {recipients.map(recipient => {const [goal, agent] = recipient.split("\n"); return <section key={recipient} className="conversation-requests-group">
          <h3>{agent}<span>{goal}</span></h3>
          {rows.filter(row => row.goal_id === goal && row.agent_id === agent).map(row => <details key={row.request_id} className="conversation-request-row">
            <summary><ChevronRight className="conversation-request-toggle" aria-hidden size={15}/><strong>{row.brief?.purpose ?? (row.read_status === "unavailable" ? (zh ? "交办内容待恢复" : "Brief needs recovery") : row.source_question)}</strong><span>{requestStage(row, zh)}</span>
              {row.last_event_at ? <time dateTime={row.last_event_at} aria-label={zh ? "最近记录" : "Latest record"}>{new Date(row.last_event_at).toLocaleString(locale, {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"})}</time> : null}</summary>
            <div className="conversation-request-detail">
              {row.decision_reason ? <p>{row.decision_reason}</p> : null}
              {row.brief ? <><p>{row.brief.context}</p>
                {row.brief.constraints.length ? <><h4>{zh ? "约束" : "Constraints"}</h4><ul>{row.brief.constraints.map((value, index) => <li key={index}>{value}</li>)}</ul></> : null}
                <h4>{zh ? "验收要求" : "Acceptance"}</h4><ul>{row.brief.acceptance.map((value, index) => <li key={index}>{value}</li>)}</ul>
                <h4>{zh ? "需要回传" : "Expected return"}</h4><p>{row.brief.return_requirement}</p>
                {row.brief.inputs.length ? <details><summary>{zh ? "输入材料" : "Inputs"}</summary><ul>{row.brief.inputs.map((input, index) => <li key={index}>{input.description} <code>{input.ref}</code></li>)}</ul></details> : null}</> : null}
              <details><summary>{zh ? "关联工作与记录" : "Linked work and records"}</summary>
                <p>{zh ? "请求版本" : "Request revision"} {row.request_revision ?? "—"} · {zh ? ({supplied: "已读取", pending: "待读取", unavailable: "读取未知"}[row.read_status] ?? "读取未知") : row.read_status}
                  {" · "}{zh ? ({adopt: "已采纳", defer: "已暂缓", reject: "未采纳", no_change: "无需调整"}[row.decision] ?? "尚无判断") : row.decision}</p>
                <ul>{row.linked_todos.map(todo => <li key={todo.todo_id}>{todo.title ?? todo.todo_id} · {todo.status ?? (zh ? "状态未知" : "Unknown")}</li>)}</ul>
                {row.evidence_refs.map(value => <p key={value}><code>{value}</code></p>)}
                {row.warnings.length ? <p role="status">{zh ? "部分关联记录需要恢复。" : "Some linked records need recovery."}</p> : null}
              </details>
              <OriginalRequest row={row} zh={zh}/>
            </div>
          </details>)}
        </section>;})}
        {last?.next_offset !== null && last?.next_offset !== undefined ? <button type="button" disabled={busy} onClick={() => void load(last.next_offset!)}>{zh ? "更多记录" : "More records"}</button> : null}
      </div>
    </dialog>
  </div>;
}
