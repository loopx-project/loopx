import {useRef, useState} from "react";
import {cancelGoalTransfer, confirmGoalTransfer, controlGoalTransfer, inspectGoalTransfer, type GoalTransferView} from "../../data/chat";
import {useWorkspaceI18n} from "./i18n";
import "./goal-transfer-settings.css";

type ExecutionDisposition = NonNullable<Awaited<ReturnType<typeof confirmGoalTransfer>>["execution"]>["loop_disposition"];

type TransferAction = "read" | "cancel" | "confirm" | "stop" | "resume";

/** The Agent prepares the existing local owners; this entry confirms only the
 * retained exact review and reads actual destination work. Full journey held. */
export function GoalTransferSettings({goalId}: {goalId: string}) {
  const {locale} = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [view, setView] = useState<GoalTransferView | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState<TransferAction | null>(null);
  const [error, setError] = useState("");
  const [cancelled, setCancelled] = useState<boolean | null>(null);
  const [cutoverRecorded, setCutoverRecorded] = useState(false);
  const [executionAccepted, setExecutionAccepted] = useState<boolean | null>(null);
  const [executionDisposition, setExecutionDisposition] = useState<ExecutionDisposition>();
  const dispositionLabels: Record<NonNullable<ExecutionDisposition>, string> = zh ? {
    run_now: "可继续", capability_action_required: "先处理能力要求", wait: "等待当前准入",
    stop: "已停止", user_action_required: "需要本人处理当前事项", repair: "需要恢复", replan: "需要重新规划", terminal: "已结束",
  } : {
    run_now: "Ready", capability_action_required: "Capability action required", wait: "Waiting for admission",
    stop: "Stopped", user_action_required: "Owner action required", repair: "Recovery required", replan: "Replan required", terminal: "Ended",
  };
  const inFlight = useRef(false);
  const holds: Record<GoalTransferView["holds"][number]["code"], string> = {
    retained_leases: zh ? "仍有未释放的任务租约，需由原任务结算。" : "Original tasks still hold unreleased leases.",
    turn_or_host_unsettled: zh ? "原执行或 Host 进程尚未确认结束。" : "Original work or Host processes are not confirmed settled.",
    quota_unsettled: zh ? "原执行扣额尚未结算。" : "Original accounting still needs settlement.",
    destination_admission_unavailable: zh ? "目标环境的执行准入及原任务续办尚未接通，当前不能完成迁移。" : "Destination admission and original-task continuation are not connected yet. Moving this Agent is unavailable.",
    transfer_journey_incomplete: zh ? "此处可读取本地切换、原任务状态和证据引用；完整迁移流程与安装环境验证仍在开发中。" : "Local cutover, original-task status and evidence references can be read here. The complete migration flow and installed-environment validation are still in development.",
  };

  async function perform(action: TransferAction = "read") {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setPendingAction(action); setError("");
    if (action === "read") {setView(null); setExecutionDisposition(undefined);}
    try {
      if ((action === "stop" || action === "resume") && view?.operation_id && view.destination_control?.operation === action) {
        const result = await controlGoalTransfer(goalId, view.operation_id, view.destination_control);
        setView(result.readback);
        if (!result.readback) setError(zh ? "目标状态已确认，请重新读取当前状态。" : "The destination state was confirmed. Read its current status again.");
        return;
      }
      if (action === "confirm" && view?.prepared_cutover) {
        const result = await confirmGoalTransfer(goalId, view.prepared_cutover);
        setCutoverRecorded(true); setView(result.readback);
        if (result.execution) {
          setExecutionAccepted(result.execution.ok);
          setExecutionDisposition(result.execution.loop_disposition);
        }
        if (!result.readback) setError(zh ? "切换已接受，请读取当前状态以恢复结果。" : "The switch was accepted. Read current status to recover the result.");
        return;
      }
      if (action === "cancel" && view?.operation_id) {
        const result = await cancelGoalTransfer(goalId, view.operation_id);
        setCancelled(result.readback.original_writer_fence_restored);
        // A later read failure must not describe a recorded cancellation as
        // failed, nor keep an old confirmation button active after it committed.
        setView(null);
      }
      setView(await inspectGoalTransfer(goalId));
    } catch {
      setError(zh ? "无法读取原迁移状态，请重试。" : "The original transfer state could not be read. Retry.");
    } finally {inFlight.current = false; setBusy(false); setPendingAction(null);}
  }

  return <details className="personal-detail-card personal-goal-transfer" onToggle={event => {
    if (event.currentTarget.open && !view && !busy && !error) void perform();
  }}>
    <summary>{zh ? "迁移 Agent" : "Move Agent"}</summary>
    {busy ? <p role="status">{pendingAction === "confirm"
      ? (view?.prepared_cutover?.continue_work
        ? (zh ? "正在确认切换并继续原任务…" : "Confirming the move and continuing original work…")
        : (zh ? "正在确认本地切换…" : "Confirming the local move…"))
      : pendingAction === "cancel" ? (zh ? "正在取消迁移准备…" : "Cancelling move preparation…")
      : pendingAction === "stop" ? (zh ? "正在停止目标 Goal…" : "Stopping the destination Goal…")
      : pendingAction === "resume" ? (zh ? "正在恢复目标 Goal…" : "Resuming the destination Goal…")
      : (zh ? "读取原工作与迁移状态…" : "Reading original work and transfer state…")}</p> : null}
    {cancelled !== null ? <p role="status">{cancelled
      ? (zh ? "准备已取消，源环境的原写入边界已恢复。" : "Preparation cancelled. The original source write boundary is restored.")
      : (zh ? "取消决定已记录；源环境已有后续状态，请读取当前状态。" : "Cancellation is recorded. The source has later state; read its current status.")}</p> : null}
    {cutoverRecorded ? <p role="status">{zh ? "本次切换已接受，源环境保持停写。" : "This switch was accepted. Source writes remain closed."}</p> : null}
    {executionAccepted !== null ? <p role={executionAccepted ? "status" : "alert"}>{executionAccepted
      ? (zh ? "续办请求已处理；下方为原任务的当前状态和独立验收结果。" : "The continuation request was processed. Its original task's current state and independent checks appear below.")
      : (zh ? "切换已接受，本次续办请求需要恢复。请以下方原任务的当前状态为准；恢复本次切换不会重复已记录的工作或扣额。" : "The switch was accepted. This continuation request needs recovery; read the original task's current status below. Resume this move without repeating recorded work or debits.")}</p> : null}
    {executionDisposition && executionDisposition !== "run_now" && executionDisposition !== "terminal" ? <p role="status">
      {zh ? "本次推进：" : "This continuation: "}{dispositionLabels[executionDisposition]}{zh ? "。" : ". "}
      {zh ? "原任务当前状态见下方。重复确认只恢复原轮次，不重置已审阅的轮次上限，也不创建后台定时器。"
        : "Read the original Task's current state below. Repeating confirmation only recovers its original Turns; it does not reset the reviewed Turn limit or create a background timer."}
    </p> : null}
    {view ? <>
      <p>{view.local_cutover ? (view.local_cutover.target_activation_state === "stopped"
        ? (zh ? "目标 Goal 已停止，仍可查看原任务结果。恢复该 Goal 后才可继续执行。" : "The destination Goal is stopped. Original work remains readable; resume that Goal before continuing execution.")
        : view.local_cutover.target_runtime_admitted
        ? (zh ? "目标环境已取得新的本地执行准入，可用审阅过的上下文继续原任务。下方为当前目标结果；完整迁移流程仍待验证。" : "The destination has new local execution admission and can continue the original work with reviewed context. Current destination results appear below; the complete migration flow still needs qualification.")
        : view.local_cutover.target_task_admitted
        ? (zh ? "源环境已停止写入，目标可重新领取原任务的本地租约。下方为目标任务状态；Host 续办仍待验证。" : "Source writes are closed. The destination can acquire a fresh local lease for the original task. Status below comes from the destination; Host continuation still needs qualification.")
        : (zh ? "切换决定已记录，源环境保持停写；目标尚待同一次操作恢复任务准入。" : "Cutover is recorded and source writes remain closed. Recover the same operation to finish destination Task admission."))
        : view.source_state === "prepared"
        ? (zh ? "源环境已准备迁移，当前停止写入。" : "The source is prepared for transfer and rejects writes.")
        : (zh ? "源环境可供检查；尚未切换到目标环境。" : "The source is available for inspection. No destination cutover has occurred.")}</p>
      {view.destination_control ? <>
        <button className="personal-secondary-action" type="button" disabled={busy} onClick={() => void perform(view.destination_control!.operation)}>
          {view.destination_control.operation === "stop"
            ? (zh ? "停止目标 Goal" : "Stop destination Goal")
            : (zh ? "恢复目标 Goal" : "Resume destination Goal")}
        </button>
        <p>{view.destination_control.operation === "stop"
          ? (zh ? "停止后不再启动新工作；当前工作是否结束，以实际状态为准。源环境仍保持停写。" : "Stopping prevents new work. Read actual status for work already in progress. Source writes remain closed.")
          : (zh ? "恢复允许后续执行，不会重新运行已完成的工作。源环境仍保持停写。" : "Resuming permits later execution without rerunning completed work. Source writes remain closed.")}</p>
      </> : null}
      {view.destination_control_unavailable ? <p role="alert">{zh
        ? "目标控制暂不可用。请重新读取当前状态；原任务结果和源环境停写仍保留。"
        : "Destination controls are unavailable. Read current status again; original results and the source write fence are retained."}</p> : null}
      <p>{view.work.objective}</p>
      <ul>{view.holds.map(hold => <li key={hold.code}>{holds[hold.code]}</li>)}</ul>
      {view.prepared_cutover_unavailable ? <p role="alert">{zh
        ? "已准备目标的当前状态无法确认。可重新读取；尚未切换时也可取消准备，恢复源环境。"
        : "The prepared destination could not be verified. Read current status again, or cancel preparation if the switch has not happened."}</p> : null}
      <details open={cutoverRecorded ? true : undefined}>
        <summary>{zh ? "原工作" : "Original work"} · {view.work.todos.length}</summary>
        {view.work.agents.length ? <p>{view.work.agents.join(" · ")}</p> : null}
        <ul>{view.work.todos.map(todo => <li key={todo.todo_id}>{todo.text} · {zh
          ? ({open: "待完成", done: "已完成", blocked: "受阻", deferred: "已推迟"} as Record<string, string>)[todo.status] ?? todo.status
          : todo.status}
          {todo.note ? <p>{todo.note}</p> : null}
          {todo.evidence ? <p>{zh ? "证据引用：" : "Evidence reference: "}{todo.evidence}</p> : null}
        </li>)}</ul>
      </details>
      {view.prepared_cutover ? <>
        <p>{view.local_cutover ? (zh ? "本地目标：" : "Local destination: ")
          : (zh ? "已准备的本地目标：" : "Prepared local destination: ")}{view.prepared_cutover.destination_name}</p>
        {!view.local_cutover ? <p>{view.prepared_cutover.continue_work
          ? (zh ? `确认后，${view.prepared_cutover.continue_work.agent_id} 使用目标环境当前的 Codex 登录，在目标工作区继续原任务并运行原验收。不会安装源环境凭证。`
            : `Confirm to let ${view.prepared_cutover.continue_work.agent_id} continue the original task in its destination workspace with the destination's current Codex login and original independent checks. Source credentials are not installed.`)
          : (zh ? "确认后，目标环境取得新的执行准入并保留原任务。尚未配置自动续办，仍需在目标环境启动执行。" : "Confirm to admit the destination and retain its original tasks. Automatic continuation is not configured; start execution in the destination environment.")}</p> : null}
        {!view.local_cutover && view.prepared_cutover.continue_work?.continuation_budget ? <p>{zh
          ? `本次最多自动推进 ${view.prepared_cutover.continue_work.continuation_budget.max_turns} 轮，新轮启动窗口 ${view.prepared_cutover.continue_work.continuation_budget.timeout_seconds} 秒；每轮重新检查准入。当前轮次沿原超时完成验收和结算。等待、停止或需要恢复时结束本次推进，不创建后台定时器。`
          : `This request continues for at most ${view.prepared_cutover.continue_work.continuation_budget.max_turns} Turns with a ${view.prepared_cutover.continue_work.continuation_budget.timeout_seconds}-second window for starting further Turns, checking admission each time. Current validation and settlement retain their own timeouts. Waiting, stopping or recovery ends this request; no background timer is created.`}</p> : null}
        {view.prepared_cutover.continue_work?.result ? <p>{zh
          ? `结果文件：${view.prepared_cutover.continue_work.result.relative_path}。原任务和 Goal 验收通过后，可在此 Goal 的产出中读取实际内容。`
          : `Result file: ${view.prepared_cutover.continue_work.result.relative_path}. After the original Task and Goal acceptance pass, read its actual contents in this Goal's outputs.`}</p> : null}
        <button className="personal-primary-action" disabled={busy} type="button" onClick={() => void perform("confirm")}>
          {view.local_cutover ? (zh ? "恢复本次切换" : "Resume this move") : (zh ? "确认切换" : "Confirm local move")}
        </button>
      </> : null}
      {view.cancel_available && view.operation_id ? <>
        <p>{zh ? "取消本次准备，恢复源环境的原写入边界，以便重新领取并继续原任务。" : "Cancel this preparation to restore source writes so the original tasks can be claimed and continued."}</p>
        <button className="personal-secondary-action" disabled={busy} type="button" onClick={() => void perform("cancel")}>
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
