import assert from "node:assert/strict";
import test from "node:test";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { projectFallbackDisposition } from "../../loopx/control_plane/goals/goal_frontier/fallback_disposition.ts";

function todo(id: string, overrides: JsonObject = {}): JsonObject {
  return { todo_id: id, role: "agent", task_class: "advancement_task", status: "open",
    archive_state: "active", done: false, removed_continuation: false,
    excluded_agents: [], ...overrides };
}
function request(items: JsonObject[], overrides: JsonObject = {}): JsonObject {
  return { schema_version: "goal_fallback_disposition_request_v0", terminal: false,
    source_state: "complete", agent_id: "worker", blocker_present: true,
    declarations: [{ candidate_ids: ["todo_fallback"], unresolved_id: "todo_fallback" }],
    items, legacy_selectable_ids: [], created_or_reopened_ids: [],
    resume_evaluation: { schema_version: "todo_resume_evaluation_request_v0", rollout_events: [] },
    resume_planning: { schema_version: "todo_resume_planning_request_v0",
      agent_id: "worker", item_limit: 5, has_deferred_count: false,
      has_visible_deferred_count: false, deferred_count: null, available_capabilities: null,
      sources: Object.fromEntries(["items", "backlog_items", "first_open_items", "deferred_items",
        "deferred_resume_candidates", "resume_blocked_items", "monitor_open_items",
        "current_agent_claimed_monitor_items", "claimed_monitor_open_items"].map((key) => [key, []])),
    }, ...overrides };
}

for (const [name, patch] of Object.entries({
  "peer claim": { claimed_by: "peer" }, "exclusion": { excluded_agents: ["worker"] },
  "archived": { archive_state: "archive" }, "terminal": { status: "done" },
  "contradictory done": { done: true }, "removed continuation": { removed_continuation: true },
  "monitor": { task_class: "continuous_monitor" }, "user role": { role: "user" },
})) {
  test(`fallback cannot resolve from ${name}`, () => {
    assert.equal(projectFallbackDisposition(request([todo("todo_fallback", patch)])).kind,
      "vision_fallback_unresolved");
  });
}

test("dependency facts, not cached readiness or supplied secondary rows, decide waits", () => {
  const waiter = todo("todo_fallback", { status: "deferred",
    resume_when: "todo_done:todo_dependency", resume_ready: true });
  const input = request([waiter]);
  input.resume_evaluation = { ...(input.resume_evaluation as JsonObject),
    source_items: [todo("todo_dependency", { status: "done" })] };
  assert.equal(projectFallbackDisposition(input).kind, "vision_fallback_unresolved");
  input.items = [waiter, todo("todo_dependency")];
  assert.equal(projectFallbackDisposition(input).kind, "resolved");
  input.items = [waiter, todo("todo_dependency", { status: "done", archive_state: "archive" })];
  assert.equal(projectFallbackDisposition(input).kind, "resolved");
});

test("only repository-bound PR waits count as known pending evidence", () => {
  const waiting = todo("todo_fallback", { resume_when: "pr_merged:#23" });
  assert.equal(projectFallbackDisposition(request([waiting])).kind, "vision_fallback_unresolved");
  waiting.task_repository = "git:github.com/example/project";
  assert.equal(projectFallbackDisposition(request([waiting])).kind, "resolved");
});

test("duplicate alternatives or prerequisites are uncertain, independent of row order", () => {
  const waiting = todo("todo_fallback", { resume_when: "todo_done:todo_dependency" });
  for (const items of [
    [waiting, waiting], [waiting, todo("todo_dependency"), todo("todo_dependency", { status: "done" })],
  ]) {
    for (const rows of [items, [...items].reverse()]) {
      assert.equal(projectFallbackDisposition(request(rows)).kind, "vision_fallback_lookup_uncertain");
    }
  }
});

test("resolving one declaration never clears another, and projection is read-only", () => {
  const input = request([todo("todo_fallback")], {
    declarations: [
      { candidate_ids: ["todo_fallback", "todo_missing"], unresolved_id: "todo_missing" },
      { candidate_ids: ["todo_other"], unresolved_id: "todo_other" },
    ],
  });
  const before = structuredClone(input);
  const result = projectFallbackDisposition(input);
  assert.deepEqual(result.unresolved_todo_ids, ["todo_other"]);
  assert.deepEqual(input, before);
  assert.equal("execution_obligation" in result, false);
});

test("unavailable source cannot be overridden by display positives; omitted source can", () => {
  for (const [state, expected] of [
    ["unavailable", "vision_fallback_lookup_uncertain"], ["omitted", "resolved"],
  ]) assert.equal(projectFallbackDisposition(request([], {
    source_state: state, legacy_selectable_ids: ["todo_fallback"],
  })).kind, expected);
});

test("no-declaration and terminal projections are elided, malformed wire data rejected", () => {
  assert.equal(projectFallbackDisposition(request([], { terminal: true })).kind, "resolved");
  assert.equal(projectFallbackDisposition(request([], { declarations: [] })).kind, "resolved");
  for (const patch of [{ schema_version: "future" }, { source_state: "partial" },
    { terminal: "false" }, { declarations: Array(5).fill({ candidate_ids: [], unresolved_id: "direction" }) },
    { items: [todo("todo_fallback", { excluded_agents: "worker" })] }])
    assert.throws(() => projectFallbackDisposition(request([], patch)));
});
