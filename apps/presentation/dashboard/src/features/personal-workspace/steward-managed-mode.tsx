import {fetchManagedNativeTurn, startManagedNativeTurn, type ManagedNativeTurn, type ManagedNativeTurnSnapshot, type PrivateConversation} from "../../data/chat";
import {useEffect, useRef, useState} from "react";
import {StewardReport} from "./steward-report";

/** Identity preparation is deliberately distinct from enabling scheduled work. */
export function StewardManagedMode({state, zh, busy, available, onPrepare, onMonitor, onReport}: {
  state: PrivateConversation["managed_steward"]; zh: boolean; busy: boolean;
  available: boolean; onPrepare: () => void;
  onMonitor: (operation: "enable" | "pause", cadenceMinutes: number) => void;
  onReport: (operation: "enable" | "pause" | "cancel", schedule?: Record<string, unknown>) => void;
}) {
  const initial = state?.available ? state.monitor?.cadence_minutes : null;
  const monitorId = state?.available ? state.monitor?.todo_id : null;
  const [cadence, setCadence] = useState(initial ?? 30);
  useEffect(() => setCadence(initial ?? 30), [initial, monitorId]);
  if (state === undefined) return null;
  return <details className="personal-cadence-details">
    <summary>{zh ? "原生周期监控" : "Native periodic monitoring"}</summary>
    {!state.available ? <p role="status">{zh ? "管家工作身份暂不可用；请检查原会话和连接后重试。" : "The steward identity is unavailable. Check the original conversation and connection, then retry."}</p>
      : state.prepared ? <>
        <p role="status">{state.scheduling_enabled
          ? (zh ? "授权 Goal 状态监控已启用。" : "Authorized Goal monitoring is enabled.")
          : state.monitor ? (zh ? "状态监控已暂停。" : "Goal monitoring is paused.")
          : (zh ? "管家身份已准备，状态监控尚未启用。" : "The steward identity is prepared; monitoring is not enabled.")}</p>
        <p>{zh ? "状态检查复用原生监控，不调用模型。" : "State checks use the native monitor without model calls."}</p>
        {state.goal_active === false ? <p role="status">{zh ? "管家的工作 Goal 已停止；恢复原 Goal 后才能继续监控。" : "The steward Goal is stopped. Resume that original Goal before monitoring."}</p> : null}
        {state.monitor ? <p>{state.scheduling_enabled ? (zh ? "下次检查：" : "Next check: ") : (zh ? "保留的到期时间：" : "Retained deadline: ")}{state.monitor.next_due_at ? new Date(state.monitor.next_due_at).toLocaleString(zh ? "zh-CN" : "en-US") : (zh ? "未排期" : "Unscheduled")}
          <br/>{zh ? "上次检查：" : "Last check: "}{state.monitor.last_checked_at ? new Date(state.monitor.last_checked_at).toLocaleString() : (zh ? "尚未完成首次检查" : "No completed check yet")}</p> : null}
        <div className="personal-cadence-fields personal-cadence-target"><label>{zh ? "检查周期（分钟）" : "Check interval (minutes)"}
          <input type="number" min={30} max={1440} step={1} value={cadence} disabled={busy || !available || state.goal_active === false}
            aria-invalid={!Number.isInteger(cadence) || cadence < 30 || cadence > 1440}
            onChange={event => setCadence(Number(event.target.value))}/>
        </label></div>
        <div className="personal-cadence-actions"><button className="is-primary" style={{minHeight: 44}} type="button" disabled={busy || !available || state.goal_active === false || !Number.isInteger(cadence) || cadence < 30 || cadence > 1440}
          onClick={() => onMonitor("enable", cadence)}>{state.scheduling_enabled ? (zh ? "更新周期" : "Update interval") : (zh ? "启用状态监控" : "Enable monitoring")}</button>
        {state.monitor?.status === "open" ? <button style={{minHeight: 44}} type="button" disabled={busy || !available} onClick={() => onMonitor("pause", initial ?? 30)}>{zh ? "暂停状态监控" : "Pause monitoring"}</button> : null}</div>
        <StewardReport state={state.report} zh={zh} busy={busy} available={available} goalActive={state.goal_active !== false} onChange={onReport}/>
      </>
      : <>
        <p>{zh ? "为当前管家准备一个可见的 LoopX 工作身份，保留原对话和权限。此步骤不会启动定时任务或模型。" : "Prepare one visible LoopX identity for this steward, retaining its original conversation and permissions. This step starts neither scheduled tasks nor a model."}</p>
        <button type="button" disabled={busy || !available} onClick={onPrepare}>
          {zh ? "准备管家身份" : "Prepare steward identity"}
        </button>
      </>}
  </details>;
}

/** One explicit segment; calendar scheduling remains with its existing owner. */
export function ManagedSessionTurnControl({sessionId, zh, onReadback}: {sessionId: string; zh: boolean; onReadback?: () => void}) {
  const cacheKey = `loopx.managed-turn.${sessionId}`;
  const [intent, setIntent] = useState<{agent: string; id: string} | null>(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(cacheKey) ?? "null");
      return saved && typeof saved.agent === "string" && typeof saved.id === "string" ? saved : null;
    } catch {return null;}
  });
  const [state, setState] = useState<ManagedNativeTurnSnapshot | null>(null);
  const [agent, setAgent] = useState(intent?.agent ?? "");
  const [turn, setTurn] = useState<ManagedNativeTurn | null>(null);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [readRevision, setReadRevision] = useState(0);
  const alive = useRef(true);
  const requestInFlight = useRef(false);
  const readbackRef = useRef(onReadback);
  readbackRef.current = onReadback;
  const observedTurn = useRef("");
  const terminal = Boolean(turn && ["completed", "failed", "interrupted"].includes(turn.status));
  const unresolved = Boolean(intent && !terminal);
  useEffect(() => {
    alive.current = true;
    let reading = false;
    let cancelled = false;
    async function read() {
      if (reading) return;
      reading = true;
      try {
        const next = await fetchManagedNativeTurn(sessionId, agent || undefined, intent?.id);
        if (cancelled) return;
        setState(next); setTurn(next.turn); setError("");
        const identity = next.turn ? `${next.turn.turn_id}:${next.turn.status}` : "";
        if (identity && next.turn && ["completed", "failed", "interrupted"].includes(next.turn.status)
            && observedTurn.current !== identity) {observedTurn.current = identity; readbackRef.current?.();}
        if (!agent && next.registered_agents.length === 1) setAgent(next.registered_agents[0]);
      } catch (failure) {if (!cancelled) setError(failure instanceof Error ? failure.message : String(failure));}
      finally {reading = false;}
    }
    void read();
    const interval = unresolved ? window.setInterval(() => {if (!document.hidden) void read();}, 2500) : undefined;
    return () => {cancelled = true; alive.current = false; if (interval) window.clearInterval(interval);};
  }, [sessionId, agent, intent?.id, unresolved, readRevision]);
  async function run() {
    if (requestInFlight.current || !agent) return;
    const next = unresolved && intent ? intent : {agent, id: crypto.randomUUID()};
    requestInFlight.current = true; setSending(true); setError("");
    try {
      // Persist correlation before POST. An unknown response, reload or retry
      // keeps this identity; only a known terminal result permits new work.
      sessionStorage.setItem(cacheKey, JSON.stringify(next)); setIntent(next); setTurn(null);
      const accepted = await startManagedNativeTurn(sessionId, next.agent, next.id);
      if (alive.current) setTurn(accepted.turn);
    } catch (failure) {if (alive.current) setError(failure instanceof Error ? failure.message : String(failure));}
    finally {requestInFlight.current = false; if (alive.current) setSending(false);}
  }
  const native = turn?.response?.native_loopx;
  const replayHeld = terminal && native?.status !== "committed" && native?.status !== "stopped";
  const summaryState = error || replayHeld ? (zh ? " · 需要核验" : " · Review required")
    : native?.result_kind === "user_action_required" ? (zh ? " · 需要处理" : " · Action required")
    : turn && !terminal ? (zh ? " · 执行中" : " · Running")
    : native?.status === "committed" ? (zh ? " · 已验收" : " · Validated")
    : state && !state.available ? (zh ? " · 暂不可用" : " · Unavailable") : "";
  const status = !turn ? intent ? (zh ? "正在核对原请求；重试会保留同一请求。" : "Checking the original request; retry keeps its identity.") : ""
    : !terminal ? (zh ? "本轮任务正在执行。" : "This work segment is running.")
    : native?.status === "committed" ? native.result_kind === "validated_completion"
      ? (zh ? "本任务已验收并完成，结果已回写。" : "This task was validated, completed and recorded.")
      : (zh ? "本轮进展已验收并回写。" : "This segment's progress was validated and recorded.")
    : native?.result_kind === "wait" ? (zh ? "本轮等待执行条件。" : "This segment is waiting for execution conditions.")
    : native?.result_kind === "user_action_required" ? turn?.response?.message ?? (zh ? "本任务需要补充执行条件。" : "This task requires an execution condition.")
    : (zh ? "本轮未完成，原执行记录已保留。" : "This segment did not complete; its original record is retained.");
  return <details className="goal-loopx-mode">
    <summary aria-label={zh ? "按 LoopX 任务推进" : "Advance LoopX tasks"} style={{minHeight: 44, paddingBlock: 10, cursor: "pointer"}}>{zh ? "按 LoopX 任务推进" : "Advance LoopX tasks"}{summaryState}</summary>
    <div className="goal-loopx-mode-actions" style={{flexWrap: "wrap", gap: 12, marginBlock: 8}}><label style={{display: "grid", gap: 4, flex: "1 1 220px", maxWidth: 420}}>{zh ? "执行身份" : "Registered Agent"}
      <select style={{minHeight: 44, width: "100%", padding: 8, font: "inherit", borderRadius: 6, border: "1px solid var(--pw-line)"}} value={agent} disabled={sending || unresolved} onChange={event => setAgent(event.target.value)}>
        <option value="">{zh ? "选择本 Goal 的 Agent" : "Select this Goal's Agent"}</option>
        {state?.registered_agents.map(id => <option key={id} value={id}>{id}</option>)}
      </select>
    </label><button type="button" onClick={() => void run()}
      disabled={sending || replayHeld || !agent || (!state?.available && !unresolved) || Boolean(turn && !terminal)}>
      {unresolved && !turn ? (zh ? "重试原请求" : "Retry original request") : (zh ? "推进一次" : "Advance once")}
    </button></div>
    {status ? <p style={{fontSize: "var(--text-body)", lineHeight: "var(--leading-body)"}} role="status">{status}</p> : null}
    {replayHeld ? <p role="status">{zh ? "请先核验原执行记录；此入口保留失败结果，不重放执行。" : "Review the original execution record first; this control retains failures without replaying them."}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {native?.reason || native?.error || turn?.error || state && !state.available && !unresolved
      ? <details><summary>{zh ? "未执行原因" : "Execution details"}</summary><p>{native?.reason || native?.error || turn?.error || state?.reason}</p></details> : null}
    {error || state && !state.available ? <button type="button" disabled={sending} onClick={() => setReadRevision(value => value + 1)}>{zh ? "刷新状态" : "Refresh status"}</button> : null}
    <details><summary>{zh ? "执行范围" : "Execution scope"}</summary><p>{zh
      ? "沿用当前会话的沙箱和原任务的独立验收声明，不升级写入权限。普通只读会话可核验已授权来源；缺少验收时不会启动模型或记为完成。本入口不启用周期调度。"
      : "Keep this conversation's sandbox and the task's declared validation without adding write permission. Ordinary read-only conversations can verify authorized sources. Missing validation starts no model and cannot count as completion. This control does not enable scheduling."}</p></details>
  </details>;
}
