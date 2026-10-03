import assert from "node:assert/strict";
import test from "node:test";
import { projectTodoContextPage } from "../../loopx/control_plane/todos/context_projection.ts";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";

const page = (records: JsonObject[], options: JsonObject = {}) => projectTodoContextPage({
  records, owner_scope: true, offset: 0, limit: 48, ...options,
});

test("overview retains declared relationships without changing their meaning", () => {
  const records = [
    {todo_id: "research", role: "agent", status: "open", priority: "P1", text: "Public report",
      resume_when: "todo_done:data", resume_ready: false, successor_todo_ids: ["review"],
      required_decision_scopes: [{kind: "direction", granularity: "action", scope_key: "publish"}]},
    {todo_id: "release", role: "user", status: "open", priority: "P0", text: "Choose release date",
      task_class: "user_action", global_gate: false, goal_bound: true},
    {todo_id: "polish", role: "agent", status: "deferred", priority: "P2", text: "Optional polish"},
    {todo_id: "old", status: "done", text: "Old result"},
  ];
  const before = structuredClone(records);
  const result = page(records, {limit: 2});
  assert.deepEqual(result.coverage, {active: 3, included: 2, omitted: 1});
  const rows = result.todos as JsonObject[];
  assert.deepEqual(rows.map(row => row.todo_id), ["release", "research"]);
  assert.equal(rows[0].global_gate, false);
  assert.equal(rows[0].goal_bound, true);
  assert.equal(rows[1].resume_ready, false);
  assert.deepEqual(rows[1].required_decision_scopes, records[0].required_decision_scopes);
  assert.equal(rows[1].deadline, undefined);
  assert.equal(rows[1].execution_authorized, undefined);
  assert.deepEqual(records, before);
});

test("overview loss is visible and an exact read recovers the original constraint", () => {
  const record = {todo_id: "report", status: "open", text: "研究报告😀".repeat(90),
    note: "背景。".repeat(120) + "只写草稿，不要发布。", private_provider_payload: "excluded"};
  const overview = (page([record]).todos as JsonObject[])[0];
  assert.equal(overview.content_truncated, true);
  assert.equal(Array.from(String(overview.title)).length, 420);
  const exact = (page([record], {todo_id: "report"}).todos as JsonObject[])[0];
  assert.equal(exact.continuation, record.note);
  assert.equal(exact.title, record.text);
  assert.equal(exact.content_truncated, false);
  assert.equal(exact.private_provider_payload, undefined);
  const external = (page([record], {owner_scope: false, todo_id: "report"}).todos as JsonObject[])[0];
  assert.equal(external.continuation, undefined);
});

test("exact completed or missing records remain observations, never runnable work", () => {
  const records = [{todo_id: "done", status: "done", text: "Completed"}];
  assert.equal((page(records, {todo_id: "done"}).todos as JsonObject[])[0].status, "done");
  assert.deepEqual(page(records, {todo_id: "missing"}).coverage, {active: 0, included: 0, omitted: 0});
  for (const options of [{owner_scope: "true"}, {offset: -1}, {limit: 49}, {todo_id: ""}]) {
    assert.throws(() => page(records, options));
  }
});
