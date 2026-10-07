import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {todoExecutionDependencyRejection} from "../../loopx/control_plane/coordination/todo_execution_dependency.ts";

const todo = (todo_id: string, status: string, extra: JsonObject = {}): JsonObject =>
  ({todo_id, status, role: "agent", task_class: "advancement_task", archive_state: "active", ...extra});

test("execution depends on the exact retained prerequisite status", () => {
  const waiting = todo("todo_waiting", "open", {resume_when: "todo_done:todo_first", resume_ready: true});
  const source = [waiting, todo("todo_first", "open")];
  const guard = () => todoExecutionDependencyRejection(new Map(source.map(row => [String(row.todo_id), row])), "todo_waiting");
  assert.equal(guard()?.code, "todo_dependency_pending");
  source[1] = todo("todo_first", "done", {archive_state: "archive",
    resume_when: "todo_done:todo_waiting"});
  assert.equal(guard(), null);
  source[1] = todo("todo_first", "superseded");
  assert.equal(guard()?.code, "todo_dependency_pending");
  source.pop();
  assert.equal(guard()?.code, "todo_dependency_pending");
});

test("self and cycle cannot authorize execution", () => {
  const self = todo("todo_waiting", "open", {resume_when: "todo_done:todo_waiting"});
  assert.equal(todoExecutionDependencyRejection(new Map([["todo_waiting", self]]), "todo_waiting")?.code,
    "todo_dependency_invalid");
  assert.equal(todoExecutionDependencyRejection(new Map([["todo_waiting", todo("todo_waiting", "open",
    {resume_when: "todo_done:bad"})]]), "todo_waiting")?.code, "todo_dependency_invalid");
  const first = todo("todo_first", "open", {resume_when: "todo_done:todo_waiting"});
  const waiting = todo("todo_waiting", "open", {resume_when: "todo_done:todo_first"});
  assert.equal(todoExecutionDependencyRejection(new Map([["todo_waiting", waiting], ["todo_first", first]]),
    "todo_waiting")?.code, "todo_dependency_invalid");
  assert.equal(todoExecutionDependencyRejection(new Map([["todo_waiting", todo("todo_waiting", "open",
    {resume_when: "pr_merged:#1"})]]), "todo_waiting"), null);
});

test("execution recognizes the resume owner's whitespace and case normalization", () => {
  const waiting = todo("todo_waiting", "open", {resume_when: "  TODO_DONE:todo_first  "});
  const first = todo("todo_first", "open");
  const rows = new Map([["todo_waiting", waiting], ["todo_first", first]]);
  assert.equal(todoExecutionDependencyRejection(rows, "todo_waiting")?.code, "todo_dependency_pending");
  first.status = "done";
  assert.equal(todoExecutionDependencyRejection(rows, "todo_waiting"), null);
});
