import { parseTodoTimestampMicros } from "../../../../../../loopx/control_plane/runtime_timestamp.js";
import type { WorkspaceGoal } from "./personal-workspace-model";

/** Cross-Goal order is independent of sidebar grouping and last-edit time.
 * Undated work belongs to completion totals, never a fabricated recent rank. */
export function recentCompletions(goals: WorkspaceGoal[]) {
  return goals.flatMap(goal => goal.agentTodos.flatMap(todo => {
    if (!todo.done || todo.status !== "done" || todo.taskClass !== "advancement_task") return [];
    const completedAt = todo.completedAt?.trim();
    const instant = completedAt ? parseTodoTimestampMicros(completedAt) : null;
    return instant === null ? [] : [{
      goal, key: `${goal.goalId}:${todo.todoId}`, text: todo.text, instant,
    }];
  })).sort((a, b) => a.instant === b.instant ? a.key.localeCompare(b.key)
    : a.instant > b.instant ? -1 : 1);
}
