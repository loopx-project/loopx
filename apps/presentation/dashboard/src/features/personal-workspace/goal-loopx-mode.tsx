import {ContextHelp} from "./context-help";
import {useEffect, useRef, useState} from "react";
import {Pause, Play, Settings2, Users, X} from "lucide-react";
import {fetchLoopXMode, updateLoopXMode, fetchManagedTaskEntry, startManagedTaskTurn,
  type ManagedConversationTarget, type ManagedTaskEntry, type ManagedTaskTurn, type LoopXModeSnapshot, type LoopXModeSettings} from "../../data/chat";
import {useWorkspaceI18n} from "./i18n";
import {GoalTeamWork} from "./goal-team-work";
import "./goal-loopx-mode.css";

type GoalLoopXModeProps = {
  sessionId?: string;
  onPrepare: () => Promise<string>;
  onExecute: (operation: "start" | "resume", settings?: LoopXModeSettings) => void;
  onChange: (snapshot: LoopXModeSnapshot | null) => void;
  managed?: {target: ManagedConversationTarget; onObserve: () => Promise<void>};
};

/** The existing primary control has distinct native and coordinator owners. */
export function GoalLoopXMode(props: GoalLoopXModeProps) {
  return props.managed ? <ManagedTaskMode key={JSON.stringify(props.managed.target)} {...props.managed}/>
    : <CoordinatorMode {...props}/>;
}

type TaskAttempt = {operationId: string; todoId: string; turnId?: string; status?: string};
const terminalTaskTurn = (status?: string) => ["completed", "failed", "interrupted", "timed_out"].includes(status ?? "");
function readTaskAttempt(key: string): TaskAttempt | null {
  const stored = sessionStorage.getItem(key);
  if (stored === null) return null;
  const value: unknown = JSON.parse(stored);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Unreadable original Task request");
  const attempt = value as Record<string, unknown>;
  if (Object.keys(attempt).some(field => !["operationId", "todoId", "turnId", "status"].includes(field))
    || typeof attempt.operationId !== "string" || !/^[A-Za-z0-9._:-]{1,100}$/.test(attempt.operationId)
    || typeof attempt.todoId !== "string" || !attempt.todoId
    || attempt.turnId !== undefined && (typeof attempt.turnId !== "string" || !attempt.turnId)
    || attempt.status !== undefined && (typeof attempt.status !== "string" || !attempt.status)) throw new Error("Unreadable original Task request");
  return attempt as TaskAttempt;
}

function ManagedTaskMode({target, onObserve}: NonNullable<GoalLoopXModeProps["managed"]>) {
  const zh = useWorkspaceI18n().locale === "zh-CN";
  const storageKey = `loopx-managed-task:${JSON.stringify([target.goalId, target.agentId, target.sessionId])}`;
  const [retained, setRetained] = useState<{attempt: TaskAttempt | null; unreadable: boolean}>(() => {
    try {return {attempt: readTaskAttempt(storageKey), unreadable: false};} catch {return {attempt: null, unreadable: true};}
  });
  const attempt = retained.attempt;
  const attemptRef = useRef(attempt); attemptRef.current = attempt;
  const [read, setRead] = useState<ManagedTaskEntry | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const flight = useRef(false), live = useRef(true), revision = useRef(0);
  const observe = useRef(onObserve); observe.current = onObserve;
  const seenTurn = useRef("");
  const shown = useRef(retained.unreadable || Boolean(attempt));
  const [refresh, setRefresh] = useState(0);
  function retain(next: TaskAttempt) {
    sessionStorage.setItem(storageKey, JSON.stringify(next));
    if (sessionStorage.getItem(storageKey) !== JSON.stringify(next)) throw new Error("Original request could not be retained");
    attemptRef.current = next; setRetained({attempt: next, unreadable: false});
  }
  async function accept(turn: ManagedTaskTurn) {
    if (!live.current) return;
    const current = attemptRef.current!;
    retain({...current, turnId: turn.turn_id, status: turn.status});
    const signature = JSON.stringify([turn.turn_id, turn.status]);
    if (seenTurn.current !== signature) {
      // The existing history/recovery owner adopts this exact Session and stream.
      await observe.current();
      seenTurn.current = signature;
    }
  }
  useEffect(() => {
    live.current = true;
    const controller = new AbortController();
    let reading = false, needsReadback = false, timer: number | undefined;
    function scheduleReadback() {
      window.clearTimeout(timer);
      if (needsReadback && !document.hidden && !controller.signal.aborted) {
        timer = window.setTimeout(() => void load(), 5000);
      }
    }
    async function load() {
      if (reading || flight.current) return;
      reading = true;
      const generation = ++revision.current;
      let storageRead = false;
      try {
        const current = readTaskAttempt(storageKey);
        storageRead = true;
        needsReadback = Boolean(current);
        attemptRef.current = current;
        setRetained({attempt: current, unreadable: false});
        const result = await fetchManagedTaskEntry(target, {operationId: current?.operationId, todoId: current?.todoId,
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)])});
        if (controller.signal.aborted || generation !== revision.current) return;
        if (current?.turnId && result.turn?.turn_id !== current.turnId) throw new Error("The original Task Turn is unavailable");
        needsReadback = Boolean(current && (!result.turn || !terminalTaskTurn(result.turn.status)));
        setRead(result); setError("");
        if (result.turn) await accept(result.turn);
      } catch {
        if (!controller.signal.aborted && generation === revision.current) {
          if (!storageRead) {needsReadback = false; setRetained(previous => ({...previous, unreadable: true}));}
          setRead(null); setError(zh ? "当前任务或原请求暂时无法读取。重新读取不会启动任务。" : "The Task or original request could not be read. Refreshing does not launch it.");
        }
      } finally {reading = false; scheduleReadback();}
    }
    void load();
    const visibility = () => {if (document.hidden) window.clearTimeout(timer); else if (needsReadback) void load();};
    document.addEventListener("visibilitychange", visibility);
    return () => {live.current = false; revision.current++; controller.abort(); window.clearTimeout(timer); document.removeEventListener("visibilitychange", visibility);};
  }, [storageKey, refresh]);

  const ready = read?.available === true && read.task.state === "ready" && read.task.canonical_done === false
    && read.task.observation.check_count > 0 && !retained.unreadable;
  const waiting = Boolean(read?.turn && !terminalTaskTurn(read.turn.status));
  const running = read?.turn?.status === "running";
  async function start() {
    if (flight.current || !ready || waiting || retained.unreadable) return;
    flight.current = true; revision.current++; setBusy(true); setError("");
    try {
      let prior: TaskAttempt | null;
      try {prior = readTaskAttempt(storageKey);} catch {
        setRetained(previous => ({...previous, unreadable: true}));
        throw new Error("The original Task request is unreadable");
      }
      attemptRef.current = prior;
      const current = await fetchManagedTaskEntry(target, {operationId: prior?.operationId,
        todoId: prior?.todoId, signal: AbortSignal.timeout(8000)});
      if (!live.current) return;
      setRead(current);
      if (prior?.turnId && current.turn?.turn_id !== prior.turnId) throw new Error("The original Task Turn is unavailable");
      if (current.turn && !terminalTaskTurn(current.turn.status)) {await accept(current.turn); return;}
      if (!current.available || current.task.state !== "ready" || current.task.canonical_done || current.task.observation.check_count < 1
        || prior && current.task.todo_id !== prior.todoId) throw new Error("Original Task source changed");
      const next = prior && !current.turn ? prior : {operationId: `task.${crypto.randomUUID()}`, todoId: current.task.todo_id};
      retain(next);
      const accepted = await startManagedTaskTurn(target, next.todoId, next.operationId, AbortSignal.timeout(15000));
      if (live.current) await accept(accepted.turn);
    } catch {
      if (live.current) {setRead(null); setError(zh ? "执行结果尚未确认。先读取原请求，不会自动重发。" : "Launch is unconfirmed. Read the original request first; it will not be resent automatically.");}
    } finally {
      flight.current = false;
      if (live.current) {setBusy(false); setRefresh(value => value + 1);}
    }
  }
  const confirmed = read?.task.state === "ready" && read.task.observation.check_count > 0;
  if (confirmed || retained.unreadable || attempt) shown.current = true;
  if (!shown.current) return null;
  return <section className="goal-loopx-mode" aria-label={zh ? "当前任务" : "Current Task"}>
    <div className="goal-loopx-mode-bar">
      <span className="goal-loopx-mode-status" data-active={running || undefined} role="status"><i aria-hidden/>
        {running ? (zh ? "正在推进当前任务" : "Working on the current Task") : waiting ? (zh ? "已提交，等待执行" : "Submitted; waiting to run") : read?.task.state === "ready" && read.task.canonical_done
          ? (zh ? "当前任务已完成" : "Current Task completed") : (zh ? "当前任务" : "Current Task")}
      </span>
      <div className="goal-loopx-mode-actions">
        <ContextHelp label={zh ? "当前任务与检查" : "Current Task and checks"}>
          <p>{zh ? "在原会话推进当前任务，使用已确认的检查和现有额度。" : "Advance the current Task in its original conversation using its confirmed checks and existing allowance."}</p>
          {read?.task.state === "ready" ? <p>{zh ? `${read.task.observation.check_count} 项检查 · ${read.task.observation.pinned_file_count} 个固定来源文件` : `${read.task.observation.check_count} checks · ${read.task.observation.pinned_file_count} pinned source files`}</p> : null}
        </ContextHelp>
        <button type="button" className="goal-loopx-primary" disabled={busy || waiting || !ready && !error}
          onClick={() => error ? setRefresh(value => value + 1) : void start()}>
          <Play size={14}/>{error ? (zh ? "重新读取" : "Refresh") : busy ? (zh ? "正在提交…" : "Submitting…")
            : waiting ? (zh ? "正在推进" : "Working") : attempt ? (zh ? "继续当前任务" : "Continue Task") : (zh ? "开始当前任务" : "Start Task")}
        </button>
      </div>
    </div>
    {error ? <p className="personal-composer-error" role="alert">{error}</p> : null}
    {read && !ready && !waiting && !read.task.canonical_done ? <p role="status">{zh ? "当前任务暂不能执行，请读取检查或原会话状态。" : "The Task cannot run yet. Read its checks or original conversation state."}</p> : null}
  </section>;
}

function CoordinatorMode({sessionId, onPrepare, onExecute, onChange}: GoalLoopXModeProps) {
  const {locale} = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [snapshot, setSnapshot] = useState<LoopXModeSnapshot | null>(null);
  const [panel, setPanel] = useState<"settings" | "team" | null>(null);
  const editing = panel === "settings";
  const setEditing = (value: boolean) => setPanel(value ? "settings" : null);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (panel && !dialog.current?.open) dialog.current?.showModal();
    else if (!panel && dialog.current?.open) dialog.current.close();
  }, [panel]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");
  const [settings, setSettings] = useState<LoopXModeSettings>({agent_id: "", token_budget: 0});
  useEffect(() => {
    let alive = true;
    let refreshing = false;
    setSnapshot(null); onChange(null); setError(""); setReadError(""); setEditing(false);
    if (!sessionId || sessionId === "new-session-pending") return;
    async function refresh() {
      if (refreshing) return;
      refreshing = true;
      try {
        const result = await fetchLoopXMode(sessionId!);
        if (alive) {setSnapshot(result); onChange(result); setReadError("");}
      } catch (failure) {if (alive) setReadError(failure instanceof Error ? failure.message : String(failure));}
      finally {refreshing = false;}
    }
    void refresh();
    const interval = window.setInterval(() => {if (!document.hidden) void refresh();}, 2500);
    return () => {alive = false; window.clearInterval(interval);};
  }, [sessionId]); // onChange is the owning component's stable state setter.
  const active = Boolean(snapshot?.enabled && snapshot.active_turn_id);
  const native = snapshot?.native.status ?? "absent";
  const resume = !["absent", "complete"].includes(native);
  const configured = Boolean(snapshot?.settings.agent_id && snapshot.settings.token_budget
    && snapshot.settings.execution_config);
  const editSettings = (current: LoopXModeSnapshot) => {
    setSettings({agent_id: current.settings.agent_id ?? "", token_budget: current.settings.token_budget ?? 0});
    setEditing(true);
  };
  const status = !snapshot?.enabled ? (zh ? "普通对话" : "Conversation")
    : snapshot.recovery_required ? (zh ? "LoopX · 需要恢复连接" : "LoopX · Reconnect required")
    : native === "blocked" ? (zh ? "LoopX · 需要处理阻塞" : "LoopX · Blocked")
    : active ? (zh ? "LoopX · 正在推进" : "LoopX · Working")
    : native === "complete" ? (zh ? "LoopX · 本轮已结束" : "LoopX · Run finished")
    : ["budgetLimited", "usageLimited"].includes(native) ? (zh ? "LoopX · 已到额度限制" : "LoopX · Usage limit")
    : (zh ? "LoopX · 已暂停" : "LoopX · Paused");
  const openSettings = () => {
    if (snapshot && !editing) editSettings(snapshot);
    else setEditing(false);
  };
  async function prepareSettings() {
    setBusy(true); setError("");
    try {
      const preparedSessionId = await onPrepare();
      const result = await fetchLoopXMode(preparedSessionId);
      setSnapshot(result); onChange(result); editSettings(result);
    } catch (failure) {setError(failure instanceof Error ? failure.message : String(failure));}
    finally {setBusy(false);}
  }
  async function mutate(operation: string) {
    if (!sessionId) return;
    setBusy(true); setError("");
    try {const result = await updateLoopXMode(sessionId, operation, operation === "configure" ? settings : undefined);
      setSnapshot(result); onChange(result); if (operation === "configure" || operation === "exit") setEditing(false);
    } catch (failure) {setError(failure instanceof Error ? failure.message : String(failure));}
    finally {setBusy(false);}
  }
  const needsReadback = snapshot?.deliveries.some(row => ["rejected", "unavailable"].includes(row.status));
  const pendingMessages = snapshot?.ingress.filter(row => row.status !== "delivered") ?? [];
  return <section className="goal-loopx-mode" aria-label={zh ? "LoopX 运行模式" : "LoopX execution mode"}>
    <div className="goal-loopx-mode-bar">
      <span className="goal-loopx-mode-status" data-active={active} role="status"><i aria-hidden="true"/>{status}</span>
      <div className="goal-loopx-mode-actions">
        {configured && sessionId ? <button type="button" className="goal-loopx-team-trigger" onClick={() => setPanel("team")} aria-haspopup="dialog"><Users size={16}/>{zh ? "团队执行情况" : "Team execution"}{needsReadback ? <span className="goal-loopx-alert-dot" aria-label={zh ? "最近回读需要核验" : "Last observations need review"}/> : null}</button> : null}
        <button type="button" disabled={busy || snapshot?.conversation_busy || !snapshot} onClick={openSettings} aria-label={zh ? "运行设置" : "Settings"} title={zh ? "运行设置与用量" : "Settings and usage"} aria-haspopup="dialog"><Settings2 size={16}/></button>
        <button type="button" className="goal-loopx-primary" disabled={busy || Boolean(snapshot?.conversation_busy && !active)} title={active ? (zh ? "暂停协调员；已派发的成员继续执行" : "Pause coordinator; dispatched members keep working") : undefined} onClick={async () => {
          if (!snapshot) {await prepareSettings(); return;}
          if (active) void mutate("pause");
          else if (!configured || Number(snapshot.settings.token_budget ?? 0) <= Number(snapshot.native.tokensUsed ?? 0)) openSettings();
          else onExecute(resume ? "resume" : "start");
        }}>{active ? <Pause size={14}/> : <Play size={14}/>}{active ? (zh ? "暂停协调员" : "Pause coordinator") : !snapshot?.enabled ? (zh ? "开启 LoopX 模式" : "Enable LoopX") : native === "complete" ? (zh ? "开启新一轮" : "Start new run") : (zh ? "恢复推进" : "Continue")}</button>
      </div>
    </div>
    {pendingMessages.length ? <p role="status">{zh ? "待处理消息：" : "Pending messages: "}{pendingMessages.map(row => `${row.mode === "loopx_queue" ? "queue" : "inbox"} · ${row.status}`).join(" / ")}</p> : null}
    {needsReadback ? <button className="goal-loopx-review-notice" type="button" onClick={() => setPanel("team")}>{zh ? "最近成员回读有未通过或无法核验的结果 · 查看团队" : "Last member observations include rejected or unverified results · View team"}</button> : null}
    {(error || readError) && !panel ? <p className="personal-composer-error" role="alert">{error || readError}</p> : null}
    <dialog className="goal-loopx-dialog" ref={dialog} aria-labelledby="goal-loopx-dialog-title" onClose={() => setPanel(null)} onClick={event => {if (event.target === event.currentTarget) setPanel(null);}}>
      {panel ? <div className="goal-loopx-dialog-content">
        <header><h2 id="goal-loopx-dialog-title">{panel === "team" ? (zh ? "团队执行情况" : "Team execution") : (zh ? "Agent Loop 运行设置" : "Agent Loop settings")}</h2><button type="button" autoFocus aria-label={zh ? "关闭" : "Close"} onClick={() => setPanel(null)}><X size={18}/></button></header>
        {panel === "settings" ? <ContextHelp label={zh ? "持续推进设置" : "Continuation settings"}><p>{zh ? "对话执行器、当前模型和推理强度见页面上方。这里管理持续推进 Goal 的协调身份、额度和成员绑定；保存设置不会自动开启 Agent Loop。" : "The conversation executor, current model and reasoning effort appear above. These settings manage the Goal coordinator, allowance and member bindings; saving does not start the Agent Loop."}</p></ContextHelp> : null}
        {error || readError ? <p className="personal-composer-error" role="alert">{error || readError}</p> : null}
        {panel === "team" && sessionId && !readError ? <GoalTeamWork key={`${sessionId}:${snapshot?.settings.agent_id}:${snapshot?.settings.execution_config}`} sessionId={sessionId} members={snapshot?.members ?? []} zh={zh} canMessage={active && !snapshot?.paused && !readError} ingress={snapshot?.ingress ?? []}/> : null}
        {panel === "team" ? <div className="goal-team-control">
          <button type="button" disabled={busy || !active || Boolean(readError)} onClick={() => void mutate("pause")}>{zh ? "暂停协调员" : "Pause coordinator"}</button>
          <p role="status">{snapshot?.paused ? (active ? (zh ? "已暂停后续调度，等待当前协调轮次停止回读。" : "Further dispatch paused; awaiting coordinator turn stop readback.") : (zh ? "协调员已暂停。" : "Coordinator paused.")) : null}
            {zh ? "此操作不会停止已派发成员；成员状态以上次执行回读为准。当前入口不支持停止整个团队。" : "This does not stop dispatched members; their states are last-read observations. Whole-team stop is unavailable here."}</p>
        </div> : null}
    {editing ? <div className="goal-loopx-mode-settings"><label>{zh ? "已注册的协调身份" : "Registered coordinator"}<select value={settings.agent_id} onChange={event => setSettings({...settings, agent_id: event.target.value})}><option value="">{zh ? "选择已授权身份" : "Select authorized identity"}</option>{snapshot?.registered_agents.map(id => <option key={id} value={id}>{id}</option>)}</select></label>
      <label>{zh ? "协调员总 token 额度" : "Coordinator total token allowance"}<input type="number" min={1} max={2147483647} value={settings.token_budget || ""} onChange={event => setSettings({...settings, token_budget: Number(event.target.value)})}/></label>
      <label>{zh ? "成员执行绑定文件（Goal 配置）" : "Member execution bindings (Goal configuration)"}<input readOnly value={snapshot?.settings.execution_config ?? (zh ? "未配置" : "Not configured")}/></label>
      <ContextHelp label={zh ? "协调身份与额度" : "Coordinator and allowance"}><p>{zh ? "绑定文件由 Goal 子代理设置统一管理；额度包含协调员历史用量，成员授权不会因开启模式而扩大。" : "Manage the binding file in Goal sub-agent settings. The allowance includes coordinator history; enabling this mode does not expand member grants."}</p></ContextHelp>
      <button type="button" disabled={busy || !settings.agent_id || settings.token_budget < 1 || !snapshot?.settings.execution_config} onClick={() => void mutate("configure")}>{zh ? "保存设置" : "Save settings"}</button></div> : null}
    {panel === "settings" && snapshot?.enabled && snapshot.native.tokensUsed !== undefined ? <p className="goal-loopx-mode-usage">{zh ? "协调员累计用量" : "Coordinator usage"} {snapshot.native.tokensUsed.toLocaleString()} / {snapshot.native.tokenBudget?.toLocaleString() ?? "—"} tokens</p> : null}
        {panel === "settings" ? <>
          <p>{zh ? "开启后，当前协调员持续推进本 Goal。暂停仅影响协调员，已派发成员继续执行；本轮结束不等于 Goal 完成。" : "The coordinator continues this Goal. Pausing affects only the coordinator; dispatched members keep working. A finished run does not complete the Goal."}</p>
          {snapshot?.enabled && !active ? <button type="button" disabled={busy} onClick={() => void mutate("exit")}>{zh ? "退出模式" : "Exit mode"}</button> : null}
        </> : null}
      </div> : null}
    </dialog>
  </section>;
}
