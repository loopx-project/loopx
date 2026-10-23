import type {PrivateConversation} from "../../data/chat";
import {useEffect, useState} from "react";
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
