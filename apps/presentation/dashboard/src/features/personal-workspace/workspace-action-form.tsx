import { useEffect, useId, useRef, useState } from "react";
import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceActionPreviewRequest } from "./personal-workspace-model";

// Action identity comes from an explicit control, never from the text being edited.
export type WorkspaceActionDraft = {
  kind: "goal" | "todo" | "heartbeat" | "monitor";
  goalId: string | null;
  goalTitle: string;
  agentId: string;
  text?: string;
};

// Mirrors the backend preview normalizer: whitespace is collapsed, then code points are counted.
const todoTextLimit = 400;
const goalObjectiveLimit = 1000;
function boundedLength(value: string) {
  return Array.from(value.split(/\s+/u).filter(Boolean).join(" ")).length;
}

export function WorkspaceActionForm({ draft, onClose, onPreview }: {
  draft: WorkspaceActionDraft;
  onClose: () => void;
  onPreview: (request: WorkspaceActionPreviewRequest) => Promise<unknown>;
}) {
  const { locale, t } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const dialog = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  const submitting = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [objective, setObjective] = useState(draft.text ?? "");
  const [completion, setCompletion] = useState("");
  const [boundary, setBoundary] = useState("");
  const [permission, setPermission] = useState("workspace_write_on_confirmation");
  const [interval, setInterval] = useState(draft.kind === "heartbeat" ? "1" : "2");
  const [unit, setUnit] = useState(draft.kind === "heartbeat" ? "d" : "h");
  const [stop, setStop] = useState("goal_complete");
  const [target, setTarget] = useState(t("schedule.defaultTarget"));
  const scheduled = draft.kind === "heartbeat" || draft.kind === "monitor";
  const title = draft.kind === "goal" ? t("composer.createGoal") : draft.kind === "todo" ? (zh ? "创建任务" : "Create task") : draft.kind === "heartbeat" ? t("schedule.heartbeat") : t("composer.monitor");
  const goalObjective = [objective.trim(), t("goal.objectiveCompletion", { criteria: completion.trim() }), boundary.trim() ? t("goal.objectiveBoundary", { boundary: boundary.trim() }) : ""].filter(Boolean).join("\n");
  const goalInitialTodo = t("goal.initialTodo", { criteria: completion.trim() });
  const lengthIssue = draft.kind === "todo" && boundedLength(objective) > todoTextLimit
    ? (zh ? `任务内容 ${boundedLength(objective)}/${todoTextLimit} 字，请精简后再检查。` : `Task is ${boundedLength(objective)}/${todoTextLimit} characters. Shorten it before review.`)
    : draft.kind === "goal" && boundedLength(goalObjective) > goalObjectiveLimit
      ? (zh ? `目标、完成标准和执行边界合计 ${boundedLength(goalObjective)}/${goalObjectiveLimit} 字，请精简。` : `Objective, completion criteria and boundary total ${boundedLength(goalObjective)}/${goalObjectiveLimit} characters. Shorten them.`)
      : draft.kind === "goal" && boundedLength(goalInitialTodo) > todoTextLimit
        ? (zh ? `完成标准过长（首个任务 ${boundedLength(goalInitialTodo)}/${todoTextLimit} 字），请精简。` : `Completion criteria are too long for the first task (${boundedLength(goalInitialTodo)}/${todoTextLimit}). Shorten them.`)
        : "";
  useEffect(() => {
    const node = dialog.current;
    node?.showModal();
    return () => node?.close();
  }, []);

  async function preview() {
    if (submitting.current || lengthIssue) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      const goalId = draft.kind === "goal" ? `goal-${crypto.randomUUID()}` : draft.goalId;
      if (!goalId) throw new Error(zh ? "请先选择 Goal。" : "Select a Goal first.");
      const actionKind = draft.kind === "goal" ? "goal.create" : draft.kind === "todo" ? "todo.create" : draft.kind === "heartbeat" ? "heartbeat.bind" : "monitor.create";
      const parameters = draft.kind === "goal" ? {
        goal_id: goalId, agent_id: draft.agentId, title: objective.trim().slice(0, 80),
        objective: goalObjective,
        completion_criteria: completion.trim(), execution_boundary: boundary.trim(),
        initial_todos: [goalInitialTodo], permission,
        workspace_ref: "current", heartbeat: { enabled: false, cadence: "1d", timezone: "Asia/Shanghai" }, stop_condition: "goal_complete",
      } : draft.kind === "todo" ? { goal_id: goalId, text: objective.trim() } : {
        goal_id: goalId, agent_id: draft.agentId, cadence: `${interval}${unit}`, stop_condition: stop,
        timezone: "Asia/Shanghai",
        ...(draft.kind === "monitor" ? { target: target.trim(), target_key: `goal-${goalId}` } : {}),
      };
      await onPreview({ actionKind, context: { kind: draft.kind === "goal" ? "manager" : "goal", goal_id: draft.goalId },
        idempotencyKey: `workspace-${actionKind}-${crypto.randomUUID()}`, normalizedParameters: parameters,
        summary: draft.kind === "goal" ? t("proposal.summary.goalCreate", { title: objective.trim().slice(0, 80) }) : title });
      onClose();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  return <dialog ref={dialog} className="personal-action-form" aria-labelledby={headingId} onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
    <form onSubmit={(event) => { event.preventDefault(); void preview(); }}>
      <header><h2 id={headingId}>{title}</h2>{draft.goalId ? <p>{draft.goalTitle}</p> : null}</header>
      <fieldset disabled={busy}>
        {scheduled ? <>
          {draft.kind === "monitor" ? <label>{zh ? "检查内容" : "Check target"}<textarea aria-label={zh ? "检查内容" : "Check target"} required value={target} onChange={(event) => setTarget(event.target.value)} /></label> : null}
          <label>{zh ? "检查间隔" : "Interval"}<input aria-label={zh ? "检查间隔" : "Interval"} type="number" min="1" max="999" step="1" required value={interval} onChange={(event) => setInterval(event.target.value)} /></label>
          <label>{zh ? "时间单位" : "Time unit"}<select aria-label={zh ? "时间单位" : "Time unit"} value={unit} onChange={(event) => setUnit(event.target.value)}><option value="m">{zh ? "分钟" : "Minutes"}</option><option value="h">{zh ? "小时" : "Hours"}</option><option value="d">{zh ? "天" : "Days"}</option></select></label>
          <label>{zh ? "停止条件" : "Stop when"}<select aria-label={zh ? "停止条件" : "Stop when"} value={stop} onChange={(event) => setStop(event.target.value)}><option value="goal_complete">{zh ? "Goal 完成" : "Goal completes"}</option><option value="pr_merged">{zh ? "PR 合并" : "PR merges"}</option><option value="release_complete">{zh ? "发布完成" : "Release completes"}</option></select></label>
        </> : <>
          <label>{draft.kind === "goal" ? (zh ? "目标" : "Objective") : (zh ? "任务内容" : "Task")}<textarea aria-label={draft.kind === "goal" ? (zh ? "目标" : "Objective") : (zh ? "任务内容" : "Task")} required value={objective} onChange={(event) => setObjective(event.target.value)} /></label>
          {draft.kind === "goal" ? <>
            <label>{zh ? "完成标准" : "Completion criteria"}<textarea aria-label={zh ? "完成标准" : "Completion criteria"} required value={completion} onChange={(event) => setCompletion(event.target.value)} /></label>
            <label>{zh ? "执行边界（可选）" : "Execution boundary (optional)"}<textarea aria-label={zh ? "执行边界（可选）" : "Execution boundary (optional)"} value={boundary} onChange={(event) => setBoundary(event.target.value)} /></label>
            <label>{zh ? "执行权限" : "Execution permission"}<select aria-label={zh ? "执行权限" : "Execution permission"} value={permission} onChange={(event) => setPermission(event.target.value)}><option value="read_only">{zh ? "只读" : "Read only"}</option><option value="workspace_write_on_confirmation">{zh ? "确认后允许修改工作区" : "Allow workspace changes after confirmation"}</option></select></label>
          </> : null}
        </>}
      </fieldset>
      {lengthIssue ? <p className="personal-action-form-limit" role="status">{lengthIssue}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      <footer><button type="button" disabled={busy} onClick={onClose}>{t("common.cancel")}</button><button type="submit" disabled={busy || Boolean(lengthIssue) || (!scheduled && !objective.trim()) || (draft.kind === "goal" && !completion.trim()) || (draft.kind === "monitor" && !target.trim())}>{busy ? (zh ? "正在准备…" : "Preparing…") : (zh ? "检查配置" : "Review configuration")}</button></footer>
    </form>
  </dialog>;
}
