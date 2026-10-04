/** One read-only subject for a blocker and its owner decision. This is input
 * to semantic synthesis, never a notification receipt or authorization. */
import type {JsonObject} from "../effect_program.ts";
import {requireInteger, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {projectDecisionNotice} from "./decision_notice.ts";

export function projectGoalAttention(input: JsonObject): {
  items: JsonObject[]; coverage: {known: number; included: number; omitted: number};
} {
  const goalId = requireNonEmptyString(input.goal_id, "goal_id");
  const limit = input.limit === undefined ? 8 : requireInteger(input.limit, "limit");
  if (limit < 1 || limit > 48) throw new Error("attention limit must be 1..48");
  const bySubject = new Map<string, JsonObject>();
  for (const raw of Array.isArray(input.blockers) ? input.blockers : []) {
    const blocker = requireJsonObject(raw, "blocker");
    const task = requireJsonObject(blocker.task, "blocker.task");
    if ((blocker.goal_id != null && blocker.goal_id !== goalId)
        || (task.goal_id != null && task.goal_id !== goalId)
        || blocker.superseded_by || blocker.owner_must_know !== true) continue;
    const id = typeof task.todo_id === "string" ? task.todo_id : null;
    const subject = requireNonEmptyString(blocker.blocker_identity, "blocker_identity");
    bySubject.set(id ? `todo:${id}` : subject, {
      todo_id: id, blocker, request: null, owner_must_act: blocker.owner_must_act === true,
    });
  }
  for (const raw of Array.isArray(input.todos) ? input.todos : []) {
    const todo = requireJsonObject(raw, "todo");
    if ((todo.goal_id != null && todo.goal_id !== goalId)
        || todo.role !== "user" || todo.done === true || todo.superseded_by
        || (todo.status !== "open" && todo.status !== "blocked")
        || (todo.task_class !== "user_gate" && todo.task_class !== "user_action")) continue;
    const id = requireNonEmptyString(todo.todo_id, "todo_id");
    const notice = projectDecisionNotice({requests: [{...todo, request_id: id,
      reason: todo.note ?? todo.reason ?? ""}]});
    const requests = notice.items as JsonObject[];
    const previous = bySubject.get(`todo:${id}`);
    bySubject.set(`todo:${id}`, {
      ...(previous ?? {todo_id: id, blocker: null}),
      owner_must_act: true, request: requests[0] ?? null,
      ...(notice.incomplete ? {incomplete: notice.incomplete} : {}),
    });
  }
  const items = [...bySubject.values()].sort((a, b) =>
    Number(b.owner_must_act === true) - Number(a.owner_must_act === true)).slice(0, limit);
  return {items, coverage: {known: bySubject.size, included: items.length,
    omitted: bySubject.size - items.length}};
}

/** Bound the whole Turn, retaining per-Goal coverage for on-demand Todo reads. */
export function boundGoalAttention(input: JsonObject): {goals: JsonObject[]} {
  const limit = input.limit === undefined ? 12 : requireInteger(input.limit, "limit");
  if (limit < 1 || limit > 48) throw new Error("attention limit must be 1..48");
  const goals = (Array.isArray(input.goals) ? input.goals : []).map(raw =>
    requireJsonObject(raw, "goal"));
  const candidates = goals.flatMap((goal, goalIndex) => {
    const attention = goal.attention as JsonObject | undefined;
    return (Array.isArray(attention?.items) ? attention.items : []).map((raw, itemIndex) =>
      ({goalIndex, itemIndex, item: requireJsonObject(raw, "attention.item")}));
  }).sort((a, b) => Number(b.item.owner_must_act === true)
    - Number(a.item.owner_must_act === true)).slice(0, limit);
  return {goals: goals.map((goal, goalIndex) => {
    if (!goal.attention) return goal;
    const attention = requireJsonObject(goal.attention, "goal.attention");
    const items = candidates.filter(candidate => candidate.goalIndex === goalIndex)
      .sort((a, b) => a.itemIndex - b.itemIndex).map(candidate => candidate.item);
    if (attention.status !== "read") return goal;
    const coverage = attention.coverage as JsonObject | undefined;
    const known = typeof coverage?.known === "number" ? coverage.known
      : (Array.isArray(attention.items) ? attention.items.length : 0);
    return {...goal, attention: {...attention, items,
      coverage: {known, included: items.length, omitted: known - items.length}}};
  })};
}
