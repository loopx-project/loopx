import {useRef, useState} from "react";
import {cancelGoalTransfer, inspectGoalTransfer, type GoalTransferView} from "../../data/chat";
import {useWorkspaceI18n} from "./i18n";
import "./goal-transfer-settings.css";

/** Source inspection/recovery is a partial step of the same transfer journey.
 * There is intentionally no cutover control before destination admission exists. */
export function GoalTransferSettings({goalId}: {goalId: string}) {
  const {locale} = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [view, setView] = useState<GoalTransferView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [cancelled, setCancelled] = useState<boolean | null>(null);
  const inFlight = useRef(false);
  const holds: Record<GoalTransferView["holds"][number]["code"], string> = {
    retained_leases: zh ? "仍有未释放的任务租约，需由原任务结算。" : "Original tasks still hold unreleased leases.",
    turn_or_host_unsettled: zh ? "原执行或 Host 进程尚未确认结束。" : "Original work or Host processes are not confirmed settled.",
    quota_unsettled: zh ? "原执行扣额尚未结算。" : "Original accounting still needs settlement.",
    destination_admission_unavailable: zh ? "目标环境的执行准入及原任务续办尚未接通，当前不能完成迁移。" : "Destination admission and original-task continuation are not connected yet. Moving this Agent is unavailable.",
    transfer_journey_incomplete: zh ? "此处可读取本地切换、原任务状态和证据引用；完整迁移流程与安装环境验证仍在开发中。" : "Local cutover, original-task status and evidence references can be read here. The complete migration flow and installed-environment validation are still in development.",
  };

  async function perform(cancel = false) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(""); setView(null);
    try {
      if (cancel && view?.operation_id) {
        const result = await cancelGoalTransfer(goalId, view.operation_id);
        setCancelled(result.readback.original_writer_fence_restored);
        // A later read failure must not describe a recorded cancellation as
        // failed, nor keep an old confirmation button active after it committed.
        setView(null);
      }
      setView(await inspectGoalTransfer(goalId));
    } catch {
      setError(zh ? "无法读取原迁移状态，请重试。" : "The original transfer state could not be read. Retry.");
    } finally {inFlight.current = false; setBusy(false);}
  }

  return <details className="personal-detail-card personal-goal-transfer" onToggle={event => {
    if (event.currentTarget.open && !view && !busy && !error) void perform();
  }}>
    <summary>{zh ? "迁移 Agent" : "Move Agent"}</summary>
    {busy ? <p role="status">{zh ? "读取原工作与迁移状态…" : "Reading original work and transfer state…"}</p> : null}
    {cancelled !== null ? <p role="status">{cancelled
      ? (zh ? "准备已取消，源环境的原写入边界已恢复。" : "Preparation cancelled. The original source write boundary is restored.")
      : (zh ? "取消决定已记录；源环境已有后续状态，请读取当前状态。" : "Cancellation is recorded. The source has later state; read its current status.")}</p> : null}
    {view ? <>
      <p>{view.local_cutover ? (view.local_cutover.target_runtime_admitted
        ? (zh ? "目标环境已取得新的本地执行准入，可用审阅过的上下文继续原任务。下方为当前目标结果；完整迁移流程仍待验证。" : "The destination has new local execution admission and can continue the original work with reviewed context. Current destination results appear below; the complete migration flow still needs qualification.")
        : view.local_cutover.target_task_admitted
        ? (zh ? "源环境已停止写入，目标可重新领取原任务的本地租约。下方为目标任务状态；Host 续办仍待验证。" : "Source writes are closed. The destination can acquire a fresh local lease for the original task. Status below comes from the destination; Host continuation still needs qualification.")
        : (zh ? "切换决定已记录，源环境保持停写；目标尚待同一次操作恢复任务准入。" : "Cutover is recorded and source writes remain closed. Recover the same operation to finish destination Task admission."))
        : view.source_state === "prepared"
        ? (zh ? "源环境已准备迁移，当前停止写入。" : "The source is prepared for transfer and rejects writes.")
        : (zh ? "源环境可供检查；尚未切换到目标环境。" : "The source is available for inspection. No destination cutover has occurred.")}</p>
      <p>{view.work.objective}</p>
      <ul>{view.holds.map(hold => <li key={hold.code}>{holds[hold.code]}</li>)}</ul>
      <details>
        <summary>{zh ? "原工作" : "Original work"} · {view.work.todos.length}</summary>
        {view.work.agents.length ? <p>{view.work.agents.join(" · ")}</p> : null}
        <ul>{view.work.todos.map(todo => <li key={todo.todo_id}>{todo.text} · {zh
          ? ({open: "待完成", done: "已完成", blocked: "受阻", deferred: "已推迟"} as Record<string, string>)[todo.status] ?? todo.status
          : todo.status}
          {todo.note ? <p>{todo.note}</p> : null}
          {todo.evidence ? <p>{zh ? "证据引用：" : "Evidence reference: "}{todo.evidence}</p> : null}
        </li>)}</ul>
      </details>
      {view.cancel_available && view.operation_id ? <>
        <p>{zh ? "取消本次准备，恢复源环境的原写入边界，以便重新领取并继续原任务。" : "Cancel this preparation to restore source writes so the original tasks can be claimed and continued."}</p>
        <button className="personal-secondary-action" disabled={busy} type="button" onClick={() => void perform(true)}>
          {zh ? "取消迁移准备" : "Cancel move preparation"}
        </button>
      </> : null}
    </> : null}
    {error ? <p role="alert">{error}</p> : null}
    <button className="personal-secondary-action" disabled={busy} type="button" onClick={() => void perform()}>
      {zh ? "重新读取状态" : "Read current status"}
    </button>
  </details>;
}
