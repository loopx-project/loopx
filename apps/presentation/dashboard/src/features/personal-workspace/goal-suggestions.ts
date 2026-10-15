import type { GoalWorkspaceDigest } from "../../../../../../loopx/control_plane/presentation/goal_workspace_digest.js";

/**
 * Suggestions derived from a Goal's current projection.
 *
 * Each suggestion cites a fact the digest, the Todo list, the execution
 * observation or the acceptance observation already states; nothing is
 * inferred from prose or counts the owner cannot see. A stopped Goal invites
 * nothing (Resume is the masthead's control), and an unknown execution state
 * is never read as idle. Order is the owner's priority: a decision that waits
 * on them, then work that cannot move, then a failing target, then idleness,
 * then a finished Goal.
 */

export const goalSuggestionLimit = 3;

export type GoalSuggestion =
  | { kind: "open_decisions"; count: number; oldestWaitingSince: string | null }
  | { kind: "explain_blocked"; count: number; titles: string[] }
  | { kind: "explain_failed_criterion"; criterion: string; failedCount: number }
  | { kind: "continue_idle"; open: number }
  | { kind: "review_results"; done: number };

export type GoalSuggestionInput = {
  digest: Pick<GoalWorkspaceDigest, "lifecycle" | "work" | "needsYou">;
  todos: readonly { todoId: string; text: string }[];
  execution?: { kind: "running" | "idle" | "unknown" } | null;
  criteria?: readonly { description: string; state: string }[] | null;
};

export function deriveGoalSuggestions(input: GoalSuggestionInput): GoalSuggestion[] {
  const { digest } = input;
  if (digest.lifecycle.activation === "stopped") return [];
  const suggestions: GoalSuggestion[] = [];
  // Decisions are what the attention owner classifies as waiting on the owner.
  // An optional item is not a decision, and an unknown count is not zero.
  const needsYou = digest.needsYou.availability === "ok" ? digest.needsYou : null;
  const waiting = needsYou?.blocking ?? null;
  if (waiting !== null && waiting > 0) {
    suggestions.push({ kind: "open_decisions", count: waiting, oldestWaitingSince: needsYou?.oldestWaitingSince ?? null });
  }
  const blocked = digest.work.classes.blocked;
  if (blocked > 0) {
    const text = new Map(input.todos.map((todo) => [todo.todoId, todo.text.trim()]));
    const titles = digest.work.rail.blocked.map((id) => text.get(id) ?? "").filter(Boolean).slice(0, 2);
    suggestions.push({ kind: "explain_blocked", count: blocked, titles });
  }
  const failed = (input.criteria ?? []).filter((criterion) => criterion.state === "failed");
  if (failed.length > 0) {
    suggestions.push({ kind: "explain_failed_criterion", criterion: failed[0].description, failedCount: failed.length });
  }
  // Idle needs an observed idle executor, open work and a known absence of
  // decisions; the owner being the blocker is not idleness. The count is the
  // owner's projected open work when it is known, never only the listed part.
  const openWork = digest.work.projectedOpen ?? digest.work.open;
  if (input.execution?.kind === "idle" && openWork > 0 && waiting === 0) {
    suggestions.push({ kind: "continue_idle", open: openWork });
  }
  // "All done" is a completeness claim: it needs the owner's complete work
  // coverage with a known zero open count, not an empty listing of a partial one.
  const finished = digest.work.coverage === "complete" && digest.work.projectedOpen === 0 && digest.work.open === 0;
  if (finished && (digest.work.done ?? 0) > 0 && waiting === 0 && blocked === 0) {
    suggestions.push({ kind: "review_results", done: digest.work.done ?? 0 });
  }
  return suggestions.slice(0, goalSuggestionLimit);
}
