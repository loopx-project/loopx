import assert from "node:assert/strict";
import {fetchManagedGoalResults, readManagedGoalResult} from "../src/data/chat";

const goalId = "product-release", todoId = "todo_report", digest = "a".repeat(64);
const row = {todo_id: todoId, title: "Report", producer_agent_id: "lead", sha256: digest,
  content_type: "text/markdown", size_bytes: 12, completed_at: null};
const page = {ok: true, items: [row], total: 1, next_cursor: null, unavailable_count: 0, unavailable_todo_ids: []};
const detail = {ok: true, goal_id: goalId, todo_id: todoId, text: "Report body",
  result: {sha256: digest, producer_agent_id: "lead", content_type: "text/markdown"}};

const originalFetch = globalThis.fetch;
let payload: unknown;
globalThis.fetch = async () => Response.json(payload);
try {
  payload = page;
  assert.deepEqual(await fetchManagedGoalResults(goalId), page);
  const emptyPage = {...page, items: [], total: 2, next_cursor: "opaque:40", unavailable_count: 1, unavailable_todo_ids: [todoId]};
  payload = emptyPage;
  assert.deepEqual(await fetchManagedGoalResults(goalId, "opaque:0"), emptyPage);
  const expanded = {...detail, result: {...detail.result, acceptance_contract_revision: 3}};
  payload = expanded;
  assert.deepEqual(await readManagedGoalResult(goalId, todoId), expanded);

  for (const invalid of [
    {...detail, goal_id: "other-goal"}, {...detail, todo_id: "todo_other"},
    {...detail, text: 42}, {...detail, result: {...detail.result, sha256: "not-a-digest"}},
    {...detail, ok: false},
  ]) {
    payload = invalid;
    await assert.rejects(readManagedGoalResult(goalId, todoId), "invalid expanded result must fail closed");
  }
  for (const invalid of [
    {...page, items: [{...row, todo_id: null}]},
    {...page, items: [{...row, sha256: "not-a-digest"}]},
    {...page, items: [{...row, size_bytes: -1}]},
    {...page, total: -1},
    {...page, total: 0},
    {...page, next_cursor: ""},
    {...page, unavailable_count: 1},
    {...page, unavailable_todo_ids: [42], unavailable_count: 1},
    {...page, ok: false},
  ]) {
    payload = invalid;
    await assert.rejects(fetchManagedGoalResults(goalId), "invalid inventory must fail closed");
  }
} finally {
  globalThis.fetch = originalFetch;
}
console.log("managed goal results: page and exact readback validation passed");
