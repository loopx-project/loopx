import assert from "node:assert/strict";
import { recentCompletions } from "../src/features/personal-workspace/recent-completions";
import { todoGroupSchema } from "../src/data/status";
import type { WorkspaceAgentTodo, WorkspaceGoal } from "../src/features/personal-workspace/personal-workspace-model";

const done = (id: string, completedAt?: string | null, extra = {}): WorkspaceAgentTodo => ({
  todoId: id, text: id, done: true, status: "done", taskClass: "advancement_task", completedAt, ...extra,
});
const goal = (id: string, agentTodos: WorkspaceAgentTodo[]): WorkspaceGoal => ({
  goalId: id, title: id, agentId: "example-agent", agentTodos, activationState: "active", agentSentence: "", nextSentence: "", state: "已安排",
});
const old = goal("first-in-sidebar", [done("old", "2026-01-01T00:00:00Z", {updatedAt: "2027-01-01T00:00:00Z"})]);
const newer = goal("last-in-sidebar", [
  done("no-clock"), done("invalid", "2026-02-30T00:00:00Z"),
  done("offset", "2026-09-27T10:00:00.000001+08:00"),
  done("latest", "2026-09-27T02:00:00.000002Z"),
  done("monitor", "2027-01-01T00:00:00Z", {taskClass: "continuous_monitor"}),
  done("deferred", "2027-01-01T00:00:00Z", {status: "deferred"}),
  done("open", "2027-01-01T00:00:00Z", {done: false, status: "open"}),
]);
const names = (goals: WorkspaceGoal[]) => recentCompletions(goals).map(row => row.text);
assert.deepEqual(names([old, newer]), ["latest", "offset", "old"]);
assert.deepEqual(names([newer, old]), names([old, newer]), "sidebar order cannot change newest work");
assert.equal(newer.agentTodos[0].todoId, "no-clock", "projection never mutates task order");
const tieA = goal("a", [done("same", "2026-09-27T10:00:00+08:00")]);
const tieB = goal("b", [done("same", "2026-09-27T02:00:00Z")]);
assert.deepEqual(recentCompletions([tieB, tieA]).map(row => row.key), ["a:same", "b:same"]);
const parsed = todoGroupSchema.parse({recent_completed_advancement_items: [{
  todo_id: "retained", done: true, text: "Kept body", completed_at: "2026-09-27T02:00:00Z",
}]});
assert.equal(parsed.recent_completed_advancement_items?.[0].completed_at, "2026-09-27T02:00:00Z", "status parser retains the recent lane and clock");
assert.deepEqual(names([goal("unknown", [done("undated")])]), []);
console.log("recent-completions-smoke: ok");
