import assert from "node:assert/strict";
import test from "node:test";
import {compactTodoQueueSummary, compactThinTodoRead} from "../../loopx/control_plane/todos/read_presentation.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

function source(): JsonObject {
  const group = (role: string) => ({total_count: 10, items: Array.from({length: 10}, (_, index) => ({
    todo_id: `todo_${role}_${index}`, role, text: index === 0 ? "🙂".repeat(200) : `Requirement ${index}`,
    status: index === 1 ? "done" : index === 2 ? "blocked" : "open", priority: "P1",
    claimed_by: "agent", note: "Full evidence belongs to exact read", global_gate: true,
  }))});
  return {ok: true, read_only: true, dry_run: true, command: "list", goal_id: "synthetic",
    role: "all", status_filter: null, source: "file_authority", todo_count: 20,
    user_todos: group("user"), agent_todos: group("agent"),
    authority_read: {source_authority: "sqlite_v0", provider_revision: "revision", cursor: "20",
      decision_read_from_provider: true, legacy_fallback_used: false}};
}

test("queue overview reports full matches and bounds Unicode titles without changing its input", () => {
  const input = source(), before = structuredClone(input);
  const result = compactTodoQueueSummary(input, false);
  assert.deepEqual(input, before);
  assert.equal(result.matched_todo_count, 20);
  assert.equal(result.returned_todo_count, 16);
  assert.equal(result.omitted_todo_count, 4);
  const rows = result.todos as JsonObject[];
  assert.equal(Array.from(String(rows[0]!.title)).length, 180);
  assert.equal(rows[0]!.title_truncated, true);
  assert.equal(rows[1]!.title_truncated, false);
  assert.equal(rows[1]!.status, "done");
  assert.equal(rows[2]!.status, "blocked");
  assert.equal(rows[0]!.note, undefined);
  assert.equal(rows[0]!.global_gate, undefined);
  assert.equal(result.user_todos, undefined);
  assert.equal(result.agent_todos, undefined);
  assert.equal((result.authority_read as JsonObject).provider_revision, "revision");
});

test("overview reuses hot display's done omission while thin retains its v0 cap", () => {
  const input = source();
  const hot = compactTodoQueueSummary(input, true);
  const rows = hot.todos as JsonObject[];
  assert.equal(rows.length, 16);
  assert.ok(rows.every(row => row.status !== "done"));
  assert.equal(rows[1]!.todo_id, "todo_user_2");
  const limited = compactTodoQueueSummary({...input, explicit_limit: 1,
    todo_list_projection: {matched_todo_count: 20}}, false);
  assert.equal(limited.returned_todo_count, 2);
  assert.equal(limited.omitted_todo_count, 18);
  const thin = compactThinTodoRead({...input, explicit_limit: 8});
  assert.equal(thin.returned_todo_count, 4);
  assert.equal((thin.todo_list_field_projection as JsonObject).schema_version, "todo_list_thin_projection_v0");
});
