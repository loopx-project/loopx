import type { WorkspaceAttention } from "./personal-workspace-model";
import { todoRequestContent } from "../../../../../../loopx/control_plane/presentation/todo_request_content.js";

/** A display of existing Todo facts, never a gate or dependency evaluator. */
export type AttentionDetails = {
  /** Mirrors the User Todo task_class; anything else stays "unknown". */
  interaction: "decision" | "user_action" | "unknown";
  lifecycle: "open" | "closed" | "deferred" | "superseded" | "unknown" | "unavailable";
  /** False when the row was projected without a stable todo_id to write against. */
  todoIdentified: boolean;
  requestText?: string | null;
  reason: string | null;
  evidence: string | null;
  blocksAgent: string | null;
  unblocksTodoId: string | null;
  decisionScope: { kind: string; granularity: string; scopeKey: string } | null;
  supersededBy: string | null;
};

const text = (value: unknown): string | null => typeof value === "string" && value.trim() ? value.trim() : null;

export function attentionDetails(todo: Record<string, unknown>): AttentionDetails {
  const scope = todo.decision_scope && typeof todo.decision_scope === "object" && !Array.isArray(todo.decision_scope)
    ? todo.decision_scope as Record<string, unknown> : {};
  const kind = text(scope.kind);
  const granularity = text(scope.granularity);
  const scopeKey = text(scope.scope_key);
  const supersededBy = text(todo.superseded_by);
  // Do not infer a request for authorization from wording, blocking, or scope kind.
  return {
    interaction: todo.task_class === "user_gate" ? "decision"
      : todo.task_class === "user_action" ? "user_action" : "unknown",
    lifecycle: supersededBy ? "superseded"
      : todo.status === "deferred" ? "deferred"
        : todo.done === true || ["done", "completed", "closed", "archived"].includes(String(todo.status)) ? "closed"
          : todo.status === "open" || todo.status === "blocked" ? "open" : "unknown",
    todoIdentified: text(todo.todo_id) !== null,
    requestText: text(todo.text),
    reason: text(todo.note),
    evidence: text(todo.evidence),
    blocksAgent: text(todo.blocks_agent),
    unblocksTodoId: text(todo.unblocks_todo_id),
    decisionScope: kind && granularity && scopeKey ? { kind, granularity, scopeKey } : null,
    supersededBy,
  };
}

export function attentionDetailsFromSnapshot(
  todo: Record<string, unknown>, items: Record<string, unknown>[], goalId: string,
): AttentionDetails {
  const detail = attentionDetails(todo);
  const content = todoRequestContent(todo, {goal_id: goalId, items}, goalId);
  // A failed join remains a summary, never a purported complete request.
  return {...detail, requestText: content?.text ?? null,
    reason: content?.note ?? null, evidence: content?.evidence ?? null};
}

/** Stamp only the source/Goal being observed; unrelated failed reads cannot fence it. */
export function sourceAttention(item: WorkspaceAttention, sourceId: string, sourceReady: boolean, goalTitle?: string): WorkspaceAttention {
  return {
    ...item, sourceId, goalTitle: goalTitle ?? item.goalTitle,
    details: sourceReady ? item.details : { ...(item.details ?? attentionDetails({})), lifecycle: "unavailable" },
  };
}

export function refreshAttention(selected: WorkspaceAttention, current: WorkspaceAttention[]): WorkspaceAttention {
  const match = current.find((item) => item.sourceId === selected.sourceId
    && item.goalId === selected.goalId && item.todoId === selected.todoId);
  if (match) return match;
  // Absence can mean a truncated/failed projection or a source switch, not completion.
  return { ...selected, details: { ...(selected.details ?? attentionDetails({})), lifecycle: "unavailable" } };
}

export function attentionSuccessor(item: WorkspaceAttention, current: WorkspaceAttention[]): WorkspaceAttention | undefined {
  const successorId = item.details?.supersededBy;
  if (!successorId || successorId === item.todoId || item.details?.lifecycle === "unavailable") return undefined;
  return current.find((candidate) => candidate.sourceId === item.sourceId
    && candidate.goalId === item.goalId && candidate.todoId === successorId);
}

export function canReviewAttention(item: WorkspaceAttention): boolean {
  // Preview remains available for existing user actions and legacy rows. It is
  // not an authorization grant; only known inactive or missing rows are fenced.
  return !["closed", "deferred", "superseded", "unavailable"].includes(item.details?.lifecycle ?? "unknown");
}

/** Approve/reject/withdraw exist only for a User gate Todo; the owner still validates each preview. */
export function canDecideAttention(item: WorkspaceAttention): boolean {
  return canReviewAttention(item) && item.details?.interaction === "decision"
    && item.decisionSource !== "run_operator_gate";
}

/** Complete, defer, or cancel the User action Todo itself; never an approval.
 * Requires the stable todo_id the typed Todo owner writes against. */
export function canHandleUserAction(item: WorkspaceAttention): boolean {
  return canReviewAttention(item) && item.details?.interaction === "user_action"
    && item.details.todoIdentified && item.decisionSource !== "run_operator_gate";
}

/** Tomorrow's local 09:00 as a timezone-aware resume_at condition. */
export function nextMorningResumeWhen(now: Date): string {
  const target = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 9, 0, 0, 0);
  const pad = (value: number) => String(Math.abs(value)).padStart(2, "0");
  const offset = -target.getTimezoneOffset();
  const zone = `${offset >= 0 ? "+" : "-"}${pad(Math.trunc(offset / 60))}:${pad(offset % 60)}`;
  return `resume_at:${target.getFullYear()}-${pad(target.getMonth() + 1)}-${pad(target.getDate())}T09:00:00${zone}`;
}
