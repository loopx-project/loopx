import {useEffect, useLayoutEffect, useRef, useState} from "react";
import {AttachedConversationTimeline, type ConversationRecord, type ContentPage} from "./attached-conversation-record";
import "./attached-agent-conversations.css";

type Binding = {agent_id: string; thread_id: string; binding_ref: string};
type Message = ConversationRecord;
type Snapshot = {messages?: Message[]; before?: number | null; after?: number; has_more?: boolean; reset?: boolean; source_ref?: string; history_size?: number; revision: string; unchanged?: boolean; connected?: boolean; can_reply?: boolean; activity: {state: string}};
type Submission = {operation_id: string; text: string; status?: string};
type LiveRecords = {identity: string; records: Message[]};
type LiveObservation = {kind: "records"; revision: number; records: Message[]} |
  {kind: "unavailable"; retry: boolean};

class ConversationRequestError extends Error {
  constructor(message: string, readonly unsubmittedOperation?: string, readonly httpStatus?: number) {super(message);}
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {...options, signal: AbortSignal.timeout(6_000)});
  // Read the body as text first: a service failure can answer with an HTML or
  // empty body, and the caller still needs the HTTP status to decide whether
  // the read is retryable. Parsing first would surface a SyntaxError instead.
  let body: string;
  try {
    body = await response.text();
  } catch {
    // The headers arrived, so the status is already known even though the body
    // did not. Drop the status here and a stalled 503 body looks like a plain
    // transport failure while a truncated 403 body looks retryable; the caller
    // must classify on this status, not on how the body read failed.
    throw new ConversationRequestError("Conversation unavailable", undefined, response.status);
  }
  let value: Record<string, unknown> | null = null;
  try {value = body ? JSON.parse(body) as Record<string, unknown> : null;} catch {value = null;}
  if (!response.ok || value?.ok !== true) throw new ConversationRequestError(
    typeof value?.error === "string" && value.error ? value.error : "Conversation unavailable",
    response.status === 409 && value?.input_not_submitted === true && typeof value?.operation_id === "string" ? value.operation_id : undefined,
    response.status,
  );
  return value as T;
}

function interruptedRead(failure: unknown): boolean {
  return failure instanceof TypeError || (failure instanceof DOMException && failure.name === "TimeoutError");
}

function retryableRead(failure: unknown): boolean {
  return interruptedRead(failure) || (failure instanceof ConversationRequestError && (failure.httpStatus ?? 0) >= 500);
}

function readFailureMessage(failure: unknown, zh: boolean): string {
  return retryableRead(failure)
    ? (zh ? "暂时无法读取原会话。已读记录会保留，连接恢复后将自动更新。" : "The conversation is temporarily unavailable. Existing records are retained; updates resume when the connection recovers.")
    : String(failure);
}

// A bounded file window may contain only metadata. Advance without making the
// reader click through empty pages; keep each request and the whole burst bounded.
async function readVisiblePage(params: URLSearchParams, current: () => boolean): Promise<Snapshot> {
  const forward = params.has("after");
  let page = await request<Snapshot>(`/api/chat/codex-attached?${params}`);
  const newestBoundary = page.after;
  const newerAvailable = page.has_more;
  for (let windows = 1; windows < 8 && current() && !page.unchanged && !page.reset && !page.messages?.length; windows++) {
    const cursor = forward ? (page.has_more ? page.after : null) : page.before;
    const previous = params.get(forward ? "after" : "before");
    if (cursor == null) break;
    if (previous !== null && (forward ? cursor <= Number(previous) : cursor >= Number(previous))) {
      throw new Error("History position did not advance; reconnect to read it.");
    }
    params.set(forward ? "after" : "before", String(cursor));
    params.set("source_ref", page.source_ref ?? "");
    params.delete("revision");
    const next = await request<Snapshot>(`/api/chat/codex-attached?${params}`);
    if (next.reset || next.source_ref !== page.source_ref) return next;
    page = next;
  }
  // Backward skipping must not move the tail cursor backwards: those bytes
  // contained no visible records, and polling should resume at the original tail.
  return forward ? page : {...page, after: newestBoundary, has_more: newerAvailable};
}

/** Owner-only original conversation. It never changes the selected executor. */
export function AttachedAgentConversations({goalId, zh, targetAgentId}: {
  goalId: string; zh: boolean; targetAgentId: string;
}) {
  const [discovering, setDiscovering] = useState(true);
  const [agents, setAgents] = useState<Binding[]>([]);
  const [selected, setSelected] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [before, setBefore] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [recoveryError, setRecoveryError] = useState("");
  const [sendNotice, setSendNotice] = useState("");
  const [draft, setDraft] = useState("");
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [sending, setSending] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [opening, setOpening] = useState(false);
  const [openRequested, setOpenRequested] = useState(false);
  const [openError, setOpenError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [readingHistory, setReadingHistory] = useState(false);
  const [unread, setUnread] = useState(false);
  const [liveView, setLiveView] = useState<LiveRecords>({identity: "", records: []});
  const [liveUnavailable, setLiveUnavailable] = useState(false);
  const latestLive = useRef<LiveRecords>({identity: "", records: []});
  const following = useRef(true);
  const after = useRef<number | null>(null);
  const historyEpoch = useRef(0);
  const generation = useRef(0);
  const receiptGeneration = useRef(0);
  const revision = useRef("");
  const history = useRef<{source: string; size: number} | null>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const paging = useRef<number | null>(null);
  const pagingFailed = useRef(false);
  const pagingRetry = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const scrollDirection = useRef(0);
  const scrollTop = useRef(0);
  const touchY = useRef<number | null>(null);
  const draggingScroll = useRef(false);
  const restoringScroll = useRef(false);
  const position = useRef<{ids: string[]; group: boolean; offset: number; top: number} | null>(null);
  const readLocale = useRef(zh);
  useEffect(() => {readLocale.current = zh;}, [zh]);
  const binding = agents.find(agent => agent.agent_id === selected);
  const awaitingReceipt = Boolean(submission?.status && submission.status !== "delivered");
  const cacheKey = binding ? `loopx-attached-input:${goalId}:${binding.binding_ref}` : "";
  const liveEnabled = Boolean(snapshot?.connected && snapshot.activity.state !== "idle");
  const liveIdentity = `${goalId}:${targetAgentId ?? ""}:${binding?.agent_id ?? ""}:${binding?.binding_ref ?? ""}`;
  const liveRecords = liveView.identity === liveIdentity ? liveView.records : [];
  // Frozen history is still readable, but must not look like current work.
  // Connection/registration alone is never an activity observation.
  const liveActive = liveEnabled && !liveUnavailable && !readingHistory;
  const query = binding ? new URLSearchParams({goal_id: goalId, agent_id: binding.agent_id, binding_ref: binding.binding_ref}) : null;

  useLayoutEffect(() => {
    const element = transcript.current;
    const saved = position.current;
    position.current = null;
    if (!element || !saved) return;
    const ids = new Set(saved.ids);
    const anchor = [...element.querySelectorAll<HTMLElement>("[data-record-anchor]")]
      .find(node => (node.tagName === "SUMMARY") === saved.group && node.dataset.recordAnchor?.split(",").some(id => ids.has(id)));
    restoringScroll.current = true;
    element.scrollTop = anchor ? element.scrollTop + anchor.getBoundingClientRect().top - saved.offset : saved.top;
    scrollTop.current = element.scrollTop;
    requestAnimationFrame(() => {restoringScroll.current = false;});
  }, [messages]);

  // Collapsed tools and metadata can make a page shorter than the viewport.
  // Fill it from history without requiring an otherwise impossible scroll.
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const element = transcript.current;
      if (!element || error || loadingOlder) return;
      if (element.scrollHeight <= element.clientHeight + 1 && messages.length < 300 && scrollDirection.current <= 0 && before !== null) void readHistory(false, true);
      else if (scrollDirection.current) pageAtEdge(scrollDirection.current);
    });
    return () => cancelAnimationFrame(frame);
  }, [messages, liveView, before, loadingOlder, error]);

  useEffect(() => {
    let active = true;
    latestLive.current = {identity: "", records: []}; setLiveView(latestLive.current); setLiveUnavailable(false);
    if (!binding || !liveEnabled) return;
    const source = new EventSource(`/api/chat/codex-attached/live?${query}`);
    const unavailable = () => {latestLive.current = {identity: "", records: []}; setLiveView(latestLive.current); setLiveUnavailable(true);};
    source.addEventListener("observation", event => {
      if (!active) return;
      try {
        const value = JSON.parse((event as MessageEvent<string>).data) as LiveObservation;
        if (value.kind === "records" && Array.isArray(value.records)) {
          latestLive.current = {identity: liveIdentity, records: value.records}; setLiveUnavailable(false);
          if (following.current) {
            const follow = !transcript.current || transcript.current.scrollHeight - transcript.current.scrollTop - transcript.current.clientHeight < 64;
            setLiveView(latestLive.current);
            if (follow) requestAnimationFrame(() => {if (active && following.current) transcript.current?.scrollTo({top: transcript.current.scrollHeight});});
          } else setUnread(true);
        } else if (value.kind === "unavailable") {
          unavailable();
          if (!value.retry) source.close();
        } else {source.close(); unavailable();}
      } catch {source.close(); unavailable();}
    });
    source.onerror = () => {if (active) unavailable();};
    return () => {active = false; source.close();};
  }, [liveIdentity, liveEnabled]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setAgents([]); setSelected(""); setMessages([]); setSnapshot(null); setError("");
    setDiscovering(true);
    async function discover() {
      try {
        const value = await request<{agents: Binding[]}>(`/api/chat/codex-attached?${new URLSearchParams({goal_id: goalId})}`);
        if (active) {setAgents(value.agents); setSelected(targetAgentId); setError("");}
      } catch (failure) {
        if (active) {
          setError(readFailureMessage(failure, readLocale.current));
          if (retryableRead(failure)) timer = setTimeout(discover, 1000);
        }
      } finally {if (active) setDiscovering(false);}
    }
    void discover();
    return () => {active = false; clearTimeout(timer);};
  }, [goalId, refresh, targetAgentId]);

  useEffect(() => {
    const current = ++generation.current;
    revision.current = "";
    history.current = null;
    after.current = null; following.current = true; historyEpoch.current++;
    paging.current = null; pagingFailed.current = false; scrollDirection.current = 0; scrollTop.current = 0; position.current = null;
    setReadingHistory(false); setUnread(false);
    setOpening(false); setOpenRequested(false); setOpenError("");
    setSendNotice("");
    setMessages([]); setSnapshot(null); setBefore(null); setDraft(""); setSubmission(null); setSending(false); setLoadingOlder(false); setError(""); setRecoveryError("");
    if (!binding) return;
    try {
      setDraft(sessionStorage.getItem(`${cacheKey}:draft`) ?? "");
      const stored = sessionStorage.getItem(cacheKey);
      if (stored) {
        const pending = JSON.parse(stored) as Submission;
        if (!pending || typeof pending.operation_id !== "string" || typeof pending.text !== "string" || !["pending", "delivered"].includes(pending.status ?? "")) throw new Error("Invalid recovery record");
        if (pending.status === "pending") setDraft(pending.text);
        setSubmission(pending);
        if (pending.status === "pending") void readReceipt(pending, current);
      }
    } catch {setRecoveryError(zh ? "浏览器回复恢复记录不可读，暂不能安全发送。请先在原 App 核对。" : "Reply recovery is unreadable; check the original App before clearing it.");}
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      if (paging.current !== null) {timer = setTimeout(read, 1000); return;}
      const epoch = historyEpoch.current;
      const params = new URLSearchParams(query!);
      if (following.current && after.current !== null) params.set("after", String(after.current));
      if (history.current) params.set("source_ref", history.current.source);
      if (revision.current) params.set("revision", revision.current);
      try {
        const next = await readVisiblePage(params, () => current === generation.current && epoch === historyEpoch.current);
        if (current !== generation.current || epoch !== historyEpoch.current) return;
        const follow = !transcript.current || transcript.current.scrollHeight - transcript.current.scrollTop - transcript.current.clientHeight < 64;
        revision.current = next.revision;
        if (!next.unchanged) {
          const reset = next.reset || !history.current || history.current.source !== next.source_ref || (next.history_size ?? 0) < history.current.size;
          history.current = {source: next.source_ref ?? "", size: next.history_size ?? 0};
          if (following.current || reset) {
            after.current = next.after ?? null;
            setMessages(previous => {
              const combined = [...new Map([...(reset ? [] : previous), ...next.messages ?? []].map(message => [message.id, message])).values()].sort((a,b) => Number(a.id) - Number(b.id));
              if (combined.length > 300) setBefore(Number(combined[combined.length - 300].id));
              return combined.slice(-300);
            });
            if (reset) {setBefore(next.before ?? null); resumeFollowing();}
          } else setUnread(true);
        }
        setSnapshot(previous => ({...previous, ...next}));
        if (!pagingFailed.current) setError("");
        if (follow && following.current) requestAnimationFrame(() => transcript.current?.scrollTo({top: transcript.current.scrollHeight}));
      } catch (failure) {
        if (current === generation.current) {
          setError(readFailureMessage(failure, readLocale.current));
          setSnapshot(null);
        }
      }
      finally {if (current === generation.current) timer = setTimeout(read, 1000);}
    }
    void read();
    return () => {generation.current++; clearTimeout(timer); clearTimeout(pagingRetry.current);};
  }, [goalId, binding?.binding_ref]);

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

  async function readHistory(newer = false, fill = false) {
    if ((!newer && before === null) || !query || paging.current !== null || pagingFailed.current) return;
    const current = generation.current;
    const epoch = ++historyEpoch.current;
    paging.current = epoch;
    const keepFollowing = fill && following.current;
    const tail = after.current;
    if (!keepFollowing) {following.current = false; setReadingHistory(true);}
    setLoadingOlder(true);
    const params = new URLSearchParams(query);
    params.set(newer ? "after" : "before", String(newer ? after.current ?? 0 : before));
    if (history.current) params.set("source_ref", history.current.source);
    try {
      const next = await readVisiblePage(params, () => current === generation.current && epoch === historyEpoch.current);
      if (current === generation.current && epoch === historyEpoch.current) {
        if (next.reset || !history.current || history.current.source !== next.source_ref || (next.history_size ?? 0) < history.current.size) throw new Error(zh ? "原会话历史已更新，请重新连接。" : "Original history changed; reconnect to read it.");
        // Anchor a visible source record, not total height: the opposite end of
        // the 300-record window may be trimmed, or a tool group may grow.
        const element = transcript.current;
        if (element) {
          const bounds = element.getBoundingClientRect();
          const anchor = [...element.querySelectorAll<HTMLElement>("[data-record-anchor]")].find(node => {
            const rect = node.getBoundingClientRect();
            return rect.height > 0 && rect.bottom > bounds.top + 1 && rect.top < bounds.bottom;
          });
          position.current = {ids: anchor?.dataset.recordAnchor?.split(",") ?? [], group: anchor?.tagName === "SUMMARY", offset: anchor?.getBoundingClientRect().top ?? bounds.top, top: element.scrollTop};
        }
        setMessages(previous => {
          const combined = [...new Map([...(next.messages ?? []), ...previous].map(row => [row.id, row])).values()].sort((a,b) => Number(a.id) - Number(b.id));
          const retained = newer || keepFollowing ? combined.slice(-300) : combined.slice(0,300);
          after.current = keepFollowing ? tail : newer ? next.after ?? after.current : retained.at(-1)?.end ?? next.after ?? null;
          setBefore(newer || (keepFollowing && combined.length > 300) ? Number(retained[0]?.id) || null : next.before ?? null);
          return retained;
        });
        if (newer && !next.has_more) resumeFollowing();
        setError("");
      }
    } catch (failure) {
      if (current === generation.current) {
        pagingFailed.current = true; setError(readFailureMessage(failure, zh));
        if (retryableRead(failure)) pagingRetry.current = setTimeout(() => {
          if (current !== generation.current) return;
          pagingFailed.current = false;
          void readHistory(newer, fill);
        }, 1000);
      }
    }
    finally {if (paging.current === epoch) {paging.current = null; setLoadingOlder(false);}}
  }

  // Every path that returns to the live tail publishes the buffered projection.
  // Resuming while keeping the frozen draft would show stale work as current.
  function resumeFollowing() {
    following.current = true; setReadingHistory(false); setUnread(false);
    setLiveView(latestLive.current);
  }
  function pauseHistory() {following.current = false; scrollDirection.current = 0; setReadingHistory(true);}
  function pageAtEdge(direction: number) {
    const element = transcript.current;
    if (!element || restoringScroll.current) return;
    if (direction < 0 && element.scrollTop < 100) void readHistory();
    if (direction > 0 && !following.current && element.scrollHeight - element.scrollTop - element.clientHeight < 100) void readHistory(true);
  }
  function scrollIntent(direction: number) {
    if (direction < 0) pauseHistory();
    scrollDirection.current = direction;
    requestAnimationFrame(() => pageAtEdge(direction));
  }
  function scrolled() {
    const element = transcript.current;
    if (!element) return;
    const delta = element.scrollTop - scrollTop.current;
    scrollTop.current = element.scrollTop;
    if (restoringScroll.current) return;
    if (draggingScroll.current && Math.abs(delta) > 1) scrollIntent(Math.sign(delta));
    else if (scrollDirection.current) pageAtEdge(scrollDirection.current);
  }
  async function loadContent(record: Message, offset: number): Promise<ContentPage> {
    const params = new URLSearchParams(query!);
    params.set("entry_id", record.id); params.set("content_offset", String(offset));
    params.set("source_ref", history.current?.source ?? ""); params.set("content_ref", record.content_ref ?? "");
    return request<ContentPage>(`/api/chat/codex-attached?${params}`);
  }

  async function send() {
    if (!binding || sending || error || recoveryError || !snapshot?.can_reply || !draft.trim() || awaitingReceipt) return;
    const current = generation.current;
    const pending = {operation_id: crypto.randomUUID(), text: draft, status: "pending"};
    try {sessionStorage.setItem(cacheKey, JSON.stringify(pending));}
    catch {setError(zh ? "无法保存回复回执，未发送。" : "Cannot save reply recovery; nothing was sent."); return;}
    receiptGeneration.current++;
    setSubmission(pending); setSending(true); setSendNotice("");
    try {
      const result = await request<{status: string}>("/api/chat/codex-attached", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({operation_id: pending.operation_id, text: pending.text, goal_id: goalId, agent_id: binding.agent_id, binding_ref: binding.binding_ref})});
      if (current === generation.current) {
        const delivered = {...pending, status: result.status};
        sessionStorage.setItem(cacheKey, JSON.stringify(delivered)); setSubmission(delivered);
        if (result.status === "delivered") {sessionStorage.removeItem(`${cacheKey}:draft`); setDraft("");}
        else void readReceipt(pending, current);
        revision.current = "";
      }
    } catch (failure) {
      if (current === generation.current) {
        if (failure instanceof ConversationRequestError && failure.unsubmittedOperation === pending.operation_id) {
          // Only the owning server can prove no native effect. Keep the draft,
          // discard this attempt, and require a fresh explicit send when idle.
          try {
            sessionStorage.removeItem(cacheKey);
            receiptGeneration.current++;
            setSubmission(null); setRecoveryError("");
            setSnapshot(previous => previous ? {...previous, can_reply: false} : null);
            setSendNotice(zh ? "Agent 正在忙碌或状态尚未确认；消息未发送，草稿已保留。" : "The Agent is busy or its state is unconfirmed; nothing was sent. Your draft is retained.");
          } catch {setRecoveryError(zh ? "消息未发送，但浏览器回复恢复记录无法清理。" : "Nothing was sent, but the browser recovery record could not be cleared.");}
        } else {setError(String(failure)); void readReceipt(pending, current);}
      }
    }
    finally {if (current === generation.current) setSending(false);}
  }
  async function readReceipt(pending = submission, current = generation.current) {
    // Recover from existing evidence only. Never retry the native effect.
    if (!query || !pending || current !== generation.current) return;
    const receiptVersion = receiptGeneration.current;
    const params = new URLSearchParams(query); params.set("operation_id", pending.operation_id);
    try {
      const receipt = await request<{status: string}>(`/api/chat/codex-attached?${params}`);
      if (current === generation.current && receiptVersion === receiptGeneration.current) {
        const next = {...pending, status: receipt.status};
        sessionStorage.setItem(cacheKey, JSON.stringify(next)); setSubmission(next);
        setRecoveryError("");
        if (receipt.status === "delivered") {sessionStorage.removeItem(`${cacheKey}:draft`); receiptGeneration.current++; setDraft("");}
      }
    } catch (failure) {if (current === generation.current && receiptVersion === receiptGeneration.current) setRecoveryError(String(failure));}
  }
  function editDraft(text: string) {
    setDraft(text); setSubmission(null); setSendNotice("");
    try {sessionStorage.setItem(`${cacheKey}:draft`, text);}
    catch {setRecoveryError(zh ? "草稿无法保存在此浏览器，请复制保留后重试。" : "This browser cannot retain the draft; copy it before retrying.");}
  }
  function nextReply() {
    try {sessionStorage.removeItem(cacheKey); sessionStorage.removeItem(`${cacheKey}:draft`); receiptGeneration.current++; setSubmission(null); setDraft(""); setError(""); setRecoveryError("");}
    catch {setError(zh ? "无法清理回复回执。" : "Reply recovery could not be cleared.");}
  }
  const connectionError = error ? <p role="alert">{error} <button type="button" onClick={() => setRefresh(value => value + 1)}>{zh ? "重新连接" : "Reconnect"}</button></p> : null;
  return <div className="attached-conversation-content">

      {discovering ? <p role="status">{zh ? "正在连接原会话…" : "Connecting to the original conversation…"}</p> : !binding && !error ? <p role="status">{zh ? "此 Agent 暂无已绑定的本机 Codex App 原会话。" : "This Agent has no bound local Codex App conversation."}</p> : null}
      {recoveryError ? <p role="alert">{recoveryError} <button type="button" disabled={sending} onClick={nextReply}>{zh ? "已在原 App 核对，清理本页回执" : "Checked in original App; clear this receipt"}</button></p> : null}
      {!binding ? connectionError : null}
      {sendNotice ? <p role="status">{sendNotice}</p> : null}
      {binding ? <>
        <div className="attached-connection-status"><div><p className="attached-conversation-source" title={zh ? "已保存消息与工具记录自动更新；实时内容来自原宿主。" : "Saved messages and tools update automatically; live content comes from the original host."}>Codex App</p><p role="status">{!snapshot ? (zh ? "正在连接或连接不可用" : "Connecting or unavailable") : snapshot.can_reply ? (zh ? "原会话可回复" : "Original conversation accepts replies") : snapshot.activity.state === "turn_open" ? (zh ? "原会话有未结束执行；可在原 App 中追加输入" : "The original conversation has an unfinished turn; add input in the original App") : (zh ? "历史可读；回复连接或执行状态尚未确认" : "History available; reply connection or execution state unconfirmed")}</p></div>
        <button type="button" className="attached-conversation-open" aria-disabled={opening} aria-busy={opening} onClick={() => void openOriginal()}>{zh ? "在 Codex App 中打开" : "Open in Codex App"}</button></div>
        {openRequested ? <p role="status">{zh ? "已请求打开原会话，请在 Codex App 中查看。" : "Requested opening the original conversation; check Codex App."}</p> : null}
        {openError ? <p role="alert">{openError}</p> : null}
        <div className="attached-history-status" role="status">{loadingOlder ? (zh ? "正在加载消息…" : "Loading messages…") : readingHistory ? <span>{zh ? "正在回看历史" : "Reading history"}{unread ? (zh ? " · 有新活动" : " · new activity") : ""}</span> : null}</div>
        <div className="attached-conversation-messages" ref={transcript} tabIndex={0} onScroll={scrolled} onPointerDown={event => {draggingScroll.current = event.target === event.currentTarget;}} onPointerUp={() => {draggingScroll.current = false;}} onPointerCancel={() => {draggingScroll.current = false;}} onWheel={event => {if (event.deltaY) scrollIntent(Math.sign(event.deltaY));}} onTouchStart={event => {touchY.current = event.touches.length === 1 ? event.touches[0].clientY : null;}} onTouchMove={event => {
          if (touchY.current === null || event.touches.length !== 1) return;
          const delta = touchY.current - event.touches[0].clientY;
          touchY.current = event.touches[0].clientY;
          if (delta) scrollIntent(Math.sign(delta));
        }} onClick={pauseHistory} onKeyDown={event => {if (["ArrowUp", "PageUp", "Home"].includes(event.key)) scrollIntent(-1); else if (["ArrowDown", "PageDown", "End"].includes(event.key)) scrollIntent(1);}} aria-label={zh ? "原会话消息" : "Original conversation messages"} aria-busy={loadingOlder}>
          <AttachedConversationTimeline key={history.current?.source ?? ""} records={messages} agent={binding.agent_id} zh={zh} load={loadContent}/>
          {liveRecords.length ? <div aria-label={zh ? "原宿主实时记录" : "Original host live records"}>
            <AttachedConversationTimeline records={liveRecords} agent={binding.agent_id} zh={zh} load={loadContent} live active={liveActive}/>
          </div> : null}
          {!messages.length && !liveRecords.length && snapshot ? <p>{zh ? "暂无可显示的消息。" : "No displayable messages yet."}</p> : null}
        </div>
        {connectionError}
        {liveUnavailable ? <p role="status">{zh ? "实时观察暂不可用，已保存的消息与工具记录仍会自动更新。" : "Live observation is unavailable; saved messages and tools still update automatically."}</p> : null}
        {submission?.status ? <p role="status">{submission.status === "delivered" ? (zh ? "已由原 App 接纳；等待 Agent 结果。" : "Accepted by the original App; awaiting the Agent result.") : (zh ? "投递尚未确认，请先在原 App 核对，不会自动重发。" : "Delivery unconfirmed; check the original App. No automatic resend.")} {submission.status === "pending" ? <button type="button" disabled={sending} onClick={() => void readReceipt()}>{zh ? "重读投递回执" : "Recheck delivery receipt"}</button> : null}{awaitingReceipt ? <button type="button" disabled={sending} onClick={nextReply}>{zh ? "已核对，写下一条" : "Checked; write another"}</button> : null}</p> : null}
        {!awaitingReceipt ? <form onSubmit={event => {event.preventDefault(); void send();}}><label>{zh ? `回复 ${binding.agent_id}` : `Reply to ${binding.agent_id}`}<textarea value={draft} maxLength={24000} disabled={sending} onChange={event => editDraft(event.target.value)}/></label><button type="submit" disabled={sending || Boolean(error || recoveryError) || !snapshot?.can_reply || !draft.trim()}>{sending ? (zh ? "正在交给原 App…" : "Sending to original App…") : (zh ? "发送到原会话" : "Send to original conversation")}</button></form> : null}
      </> : null}
    </div>
  ;
}
