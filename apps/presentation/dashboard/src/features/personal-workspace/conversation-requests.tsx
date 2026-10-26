import {useEffect, useRef, useState} from "react";
import {ChevronRight, ListChecks, RefreshCw, X} from "lucide-react";
import {fetchChatSession, fetchChatSessions, requestJson, type ChatVisibleMessage} from "../../data/chat";
import {z} from "zod";
import {fetchConversationRequests, type ConversationRequest, type ConversationRequestInventory} from "../../data/conversation-requests";
import {ContextHelp} from "./context-help";
import {MarkdownText} from "./markdown";
import {useWorkspaceI18n} from "./i18n";
import "./conversation-requests.css";

const originalRecipientSchema = z.object({schema_version: z.literal("collaboration_original_conversation_recipient_v0"),
  binding_id: z.string(), session_id: z.string(), identity_sha256: z.string()});
const requesterSchema = z.object({goal_ref: z.object({goal_id: z.string(), goal_instance_id: z.string()}), agent_id: z.string()});
const sourceGrantSchema = z.object({requester: requesterSchema, parent_request_id: z.string(),
  source_id: z.string(), recipient: originalRecipientSchema});
const projectInboxSchema = z.object({ok: z.literal(true), available: z.boolean(), policy_sha256: z.string(),
  grants: z.array(sourceGrantSchema.extend({source_purpose: z.string().nullable().optional()})), recipient: originalRecipientSchema.optional(),
  candidates: z.array(z.object({requester: requesterSchema, parent_request_id: z.string(), purpose: z.string()})),
  requests_has_more: z.boolean().optional(), requests_next_cursor: z.string().nullable().optional(), error: z.string().optional(),
  coverage: z.object({unreadable: z.number(), overflow: z.boolean()}).optional(),
  requests: z.array(z.object({request_id: z.string(), requester: requesterSchema,
    brief: z.object({purpose: z.string(), context: z.string(), constraints: z.array(z.string()), acceptance: z.array(z.string()), return_requirement: z.string(),
      inputs: z.array(z.object({ref: z.string(), description: z.string(), sha256: z.string().optional()}))}),
    queue_status: z.string().nullable(), request_control_view: z.object({revision: z.number(), control_state: z.string(),
      assessment: z.object({decision: z.string(), reason: z.string()}).nullable()}),
    returns: z.array(z.object({result_key: z.string(), text: z.string(), decision: z.string(), request_revision: z.number(), delivery_status: z.string()}))}))})
  .refine(value => !value.available || value.recipient !== undefined, "Verified recipient identity is required");

/** The original project conversation's existing collaboration entry. */
export function OriginalProjectRequests({sessionId, refreshKey}: {sessionId: string; refreshKey: string}) {
  const {locale} = useWorkspaceI18n(), zh = locale === "zh-CN";
  const [open, setOpen] = useState(false), [search, setSearch] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [view, setView] = useState<z.infer<typeof projectInboxSchema> | null>(null);
  const [selected, setSelected] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0), dialog = useRef<HTMLDialogElement>(null), trigger = useRef<HTMLButtonElement>(null), body = useRef<HTMLDivElement>(null);
  const path = `/api/chat/sessions/${encodeURIComponent(sessionId)}/collaboration`;
  useEffect(() => {setOpen(false); setView(null); setSelected(""); setSearch(""); setCursor(null);}, [sessionId]);
  useEffect(() => {if (body.current) body.current.scrollTop = 0;}, [cursor]);
  useEffect(() => {if (open) dialog.current?.showModal(); else dialog.current?.close();}, [open]);
  useEffect(() => {
    if (!open) return;
    const current = ++generation.current, abort = new AbortController();
    setBusy(true); setError(false); setSelected("");
    void requestJson<unknown>(`${path}?search=${encodeURIComponent(search)}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, {signal: abort.signal}).then(raw => {
      if (current !== generation.current) return;
      const next = projectInboxSchema.parse(raw);
      if (next.recipient && next.recipient.session_id !== sessionId) throw new Error("Conversation identity changed");
      setView(next);
    }).catch(() => {if (!abort.signal.aborted && current === generation.current) {setError(true); setView(null);}})
      .finally(() => {if (current === generation.current) setBusy(false);});
    return () => {generation.current++; abort.abort();};
  }, [open, sessionId, search, cursor, revision, refreshKey]);
  async function change(grant: boolean, source: Record<string, unknown>) {
    if (!view) return;
    const current = generation.current;
    setBusy(true); setError(false);
    try {
      await requestJson(path, {method: "POST", body: JSON.stringify({...source, grant, execute: true, expected_policy_sha256: view.policy_sha256})});
      if (current === generation.current) setRevision(value => value + 1);
    } catch {if (current === generation.current) {setError(true); setView(null);}}
    finally {if (current === generation.current) setBusy(false);}
  }
  const close = () => {setOpen(false); trigger.current?.focus();};
  const candidate = view?.candidates.find(row => row.parent_request_id === selected);
  const name = (source: z.infer<typeof requesterSchema>) => `${source.goal_ref.goal_id} · ${source.agent_id}`;
  const title = zh ? "协作请求" : "Collaboration requests";
  return <div className="conversation-requests-entry">
    <button ref={trigger} type="button" aria-haspopup="dialog" onClick={() => setOpen(true)}><ListChecks size={16}/>{title}</button>
    <dialog ref={dialog} className="conversation-requests-dialog project-peer-dialog" aria-labelledby="project-peer-title"
      onCancel={event => {event.preventDefault(); close();}} onClick={event => {if (event.target === event.currentTarget) close();}}>
      <header><h2 id="project-peer-title" tabIndex={-1} autoFocus>{title}</h2><div><button type="button" disabled={busy} onClick={() => setRevision(value => value + 1)} aria-label={zh ? "刷新协作请求" : "Refresh collaboration requests"}><RefreshCw size={16}/></button><button type="button" onClick={close} aria-label={zh ? "关闭协作请求" : "Close collaboration requests"}><X size={18}/></button></div></header>
      <div ref={body} className="conversation-requests-body" aria-busy={busy}>
        {error ? <p role="alert">{zh ? "协作来源或记录未能核验。" : "Collaboration sources or records could not be verified."} <button type="button" onClick={() => setRevision(value => value + 1)}>{zh ? "重新读取" : "Reload"}</button></p> : null}
        {busy && !view ? <p role="status">{zh ? "正在核验…" : "Verifying…"}</p> : null}
        {view ? <>
          {!view.available ? <p role="alert">{zh ? "原会话当前不可接收请求，请恢复原 App、项目与执行器连接后重试。已保留的授权仍可撤销。" : "The original conversation cannot receive requests. Restore its App, project and executor connection, then retry. Retained grants can still be revoked."}</p> : null}
          <section aria-label={zh ? "收到的请求" : "Received requests"}><h3>{zh ? "收到的请求" : "Received requests"}</h3>
            {!view.available ? <p>{zh ? "请求记录暂不可核验。" : "Request records are temporarily unverifiable."}</p> : !view.requests.length ? <p>{zh ? "暂无协作请求" : "No collaboration requests"}</p> : view.requests.map(row => {
              const control = row.request_control_view, latest = row.returns.filter(result => result.request_revision === control.revision).at(-1);
              const state = control.control_state === "cancelled" ? (zh ? "已取消" : "Cancelled")
                : latest ? (latest.delivery_status === "delivered" ? (zh ? "请求方已读" : "Read by requester") : (zh ? "结论待请求方读取" : "Conclusion awaiting requester"))
                : row.queue_status === "failed" ? (zh ? "执行失败，记录已保留" : "Execution failed; records retained")
                : row.queue_status === "running" || row.queue_status === "starting" ? (zh ? "处理中" : "In progress")
                : row.queue_status === "queued" ? (zh ? "已排队" : "Queued")
                : row.queue_status ? (zh ? "本轮已结束，结论待核验" : "Turn ended; conclusion unverified")
                : (zh ? "等待入队确认" : "Awaiting queue confirmation");
              return <details key={row.request_id} className="project-peer-request"><summary><strong>{row.brief.purpose}</strong><span>{state}</span></summary>
                <p>{name(row.requester)}</p><MarkdownText text={row.brief.context}/>
                {row.brief.constraints.length ? <ul>{row.brief.constraints.map((line, i) => <li key={i}>{line}</li>)}</ul> : null}
                {row.brief.inputs.length ? <ul>{row.brief.inputs.map(input => <li key={input.ref}><code>{input.ref}</code> · {input.description}{input.sha256 ? <details><summary>{zh ? "内容摘要" : "Content digest"}</summary><code>{input.sha256}</code></details> : null}</li>)}</ul> : null}
                <h4>{zh ? "验收与返回" : "Acceptance and return"}</h4><ul>{row.brief.acceptance.map((line, i) => <li key={i}>{line}</li>)}</ul><MarkdownText text={row.brief.return_requirement}/>
                {control.assessment ? <p>{({adopt: zh ? "已采用" : "Adopted", defer: zh ? "已暂缓" : "Deferred", reject: zh ? "已拒绝" : "Rejected", no_change: zh ? "无需变更" : "No change"} as Record<string, string>)[control.assessment.decision]} · {control.assessment.reason}</p> : null}
                {row.returns.map(result => <article key={result.result_key}><h4>{zh ? `修订 ${result.request_revision} 的结论` : `Revision ${result.request_revision} conclusion`}</h4><MarkdownText text={result.text}/></article>)}
              </details>;
            })}
            {cursor ? <button type="button" disabled={busy} onClick={() => setCursor(null)}>{zh ? "返回首页" : "First page"}</button> : null}
            {view.requests_has_more && view.requests_next_cursor ? <button type="button" disabled={busy} onClick={() => setCursor(view.requests_next_cursor ?? null)}>{zh ? "下一页" : "Next page"}</button> : null}
          </section>
          <section className="project-peer-grants" aria-label={zh ? "接收来源" : "Allowed sources"}><h3>{zh ? "接收来源" : "Allowed sources"}</h3>
            {view.grants.length ? <ul>{view.grants.map(grant => <li key={JSON.stringify(grant)}><div><strong>{name(grant.requester)}</strong><details className="project-peer-grant"><summary>{grant.source_purpose ?? (zh ? "来源记录暂不可读" : "Source record unavailable")}</summary><code>{grant.parent_request_id}</code></details></div><button type="button" disabled={busy} onClick={() => void change(false, {requester: grant.requester, recipient: grant.recipient, source_id: grant.source_id, parent_request_id: grant.parent_request_id})}>{zh ? "撤销" : "Revoke"}</button></li>)}</ul> : null}
            <label>{zh ? "查找来源请求" : "Find source request"}<input type="search" value={search} maxLength={200} onChange={event => setSearch(event.target.value)}/></label>
            <label>{zh ? "来源请求" : "Source request"}<select value={selected} disabled={busy || !view.available} onChange={event => setSelected(event.target.value)}>
              <option value="">{zh ? "选择一条原请求" : "Choose an original request"}</option>
              {view.candidates.map(row => <option key={row.parent_request_id} value={row.parent_request_id}>{name(row.requester)} · {row.purpose}</option>)}
            </select></label>
            {candidate ? <details><summary>{zh ? "查看来源请求" : "Read source request"}</summary><MarkdownText text={candidate.purpose}/></details> : null}
            <p>{zh ? "允许所选来源的协作请求加入本会话队列。" : "Allow the selected source's collaboration requests into this conversation's queue."}</p>
            {view.coverage?.overflow || view.coverage?.unreadable ? <p role="status">{zh ? "部分来源未显示，请细化搜索并核对原请求。" : "Some sources are not shown. Refine the search and verify the original request."}</p> : null}
            <button type="button" disabled={busy || !view.available || !candidate} onClick={() => candidate && void change(true, {requester: candidate.requester, parent_request_id: candidate.parent_request_id})}>{zh ? "允许此来源" : "Allow this source"}</button>
          </section>
        </> : null}
      </div>
    </dialog>
  </div>;
}

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
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
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
    setOpen(false); setHistory(false); setPages([]); setError(false); setSearch(""); setSearchDraft("");
  }, [sessionId]);
  useEffect(() => {
    if (open) dialog.current?.showModal(); else dialog.current?.close();
  }, [open]);
  useEffect(() => {
    void load();
    return () => {generation.current++; requestAbort.current?.abort();};
  }, [sessionId, ownerFallback, history, search, refreshKey, revision]);
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
      const value = await fetchConversationRequests(source, history, offset, abort.signal, search);
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
  const applySearch = (value: string) => {
    // Fence immediately, including the interval before the effect starts.
    generation.current++; requestAbort.current?.abort();
    setPages([]); setError(false); setBusy(true); setSearch(value.trim());
    setRevision(value => value + 1);
  };
  const close = () => {generation.current++; requestAbort.current?.abort(); setBusy(false); setOpen(false); trigger.current?.focus();};
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
      <form className="conversation-requests-search" role="search" onSubmit={event => {event.preventDefault(); applySearch(searchDraft);}}>
        <input type="search" value={searchDraft} maxLength={200}
          aria-label={zh ? "搜索委派" : "Search delegations"}
          placeholder={zh ? "请求内容、Goal 或 Agent" : "Request text, Goal or Agent"}
          onChange={event => setSearchDraft(event.target.value)}/>
        <button type="submit" disabled={busy}>{zh ? "搜索" : "Search"}</button>
        {search || searchDraft ? <button type="button" onClick={() => {setSearchDraft(""); applySearch("");}}>{zh ? "清空" : "Clear"}</button> : null}
      </form>
      <div className="conversation-requests-body" aria-busy={busy}>
        {error ? <p role="alert">{zh ? "委派记录暂时无法核验。" : "Delegation records cannot be verified."} <button type="button" onClick={() => setRevision(value => value + 1)}>{zh ? "重试" : "Retry"}</button></p> : null}
        {unknown ? <p role="status">{zh ? "部分记录未能读取，数量与列表尚不完整。" : "Some records could not be read. Counts and list are incomplete."}</p> : null}
        {busy && !rows.length ? <p role="status">{zh ? "正在读取委派…" : "Loading delegations…"}</p> : null}
        {!busy && noSession ? <p>{zh ? "尚无可读取的管家会话。" : "No readable steward conversation yet."}</p> : null}
        {!busy && !noSession && !error && page && !unknown && !rows.length ? <p>{search
          ? (zh ? "未找到匹配的委派。" : "No matching delegations.")
          : history ? (zh ? "暂无委派记录。" : "No delegation records.")
            : (zh ? "当前没有待跟进的委派。" : "No delegations awaiting follow-up.")}</p> : null}
        {!busy && !error && page && search && rows.length ? <p role="status">{zh ? `匹配 ${page.matched} 条${unknown ? "（部分记录）" : ""}` : `${page.matched} matches${unknown ? " (partial records)" : ""}`}</p> : null}
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
