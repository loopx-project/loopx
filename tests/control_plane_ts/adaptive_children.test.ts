import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {admitAdaptiveChildren} from "../../loopx/control_plane/quota/adaptive_children.ts";
import {schedulingWriteScopeAllowed} from "../../loopx/control_plane/work_items/task_lease_acquire_decision.ts";
import {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} from "../../loopx/control_plane/effect_runtime_handlers.ts";

const lane = (id: string, extra: JsonObject = {}): JsonObject => ({todo_id: id, dependency_todo_id: id,
  task_class: "advancement_task", done: false, status: "open", resume_when: null,
  claimed_by: null, excluded_agents: [], task_domain: "code", task_repository: null,
  required_write_scopes: [], required_capabilities: [], target_capabilities: [],
  objective: "Inspect synthetic evidence", action_kind: "inspect", continuation_policy: null,
  target_key: null, validation_label: null, ...extra});
const facts = (items: JsonObject[]): JsonObject => ({agent_id: "agent-a", registered_agents: ["agent-a"],
  goal_repository: null, goal_write_scope: ["src/**"], allowed_domains: null, max_children: 2,
  user_items: [], available_capabilities: ["subagent_spawn"], items});

for (const [value, pattern, allowed] of [
  ["src/a.ts", "src/**", true], ["SRC/a.ts", "src/**", false],
  ["src/A.ts", "src/[A-Z].ts", true], ["src/a.ts", "src/[A-Z].ts", false],
  ["src/a.ts", "src/[!A-Z].ts", true], ["src/😀.ts", "src/?.ts", true],
  ["src/SS.ts", "src/[ß].ts", false], ["src/ß.ts", "src/[ß].ts", true],
  ["src/ς.ts", "src/[Σ].ts", false], ["src/a.ts", "src/[z-a].ts", false],
  ["src/].ts", "src/[]].ts", true], ["src/[.ts", "src/[.ts", true],
  ["src/file.ts", "src", false],
] as const) test(`case-sensitive grant ${value} in ${pattern}`, () => {
  assert.equal(schedulingWriteScopeAllowed(value, [pattern]), allowed);
});

test("one admission handler composes admission and retired leaf RPC fails closed", async () => {
  const handlers = createEffectRuntimeHandlers({fingerprint: "synthetic", requestShutdown() {}});
  const result = await dispatchEffectRuntimeMethod(handlers, "quota.task_orchestration.admit",
    facts([lane("todo_primary"), lane("todo_child")])) as JsonObject;
  assert.equal(result.primary_todo_id, "todo_primary");
  assert.equal((result.eligible_child_lanes as JsonObject[])[0].todo_id, "todo_child");
  await assert.rejects(dispatchEffectRuntimeMethod(handlers, "quota.task_orchestration.write_scope_overlap",
    {left: ["src"], right: ["src/a.ts"]}), {code: "unsupported_method"});
});

test("acceptance and User dependencies exclude election candidates", () => {
  const input = facts([lane("todo_primary"), lane("todo_child", {goal_acceptance_guard: {allowed: false}})]);
  assert.equal(admitAdaptiveChildren(input), null);
  input.items = [lane("todo_primary"), lane("todo_child")];
  input.user_items = [{unblocks_todo_id: "todo_child", status: "open", done: false}];
  assert.equal(admitAdaptiveChildren(input), null);
});

test("claimed election can elect another coordinator without granting this caller authority", () => {
  assert.equal(admitAdaptiveChildren(facts([lane("todo_primary", {claimed_by: "agent-b"}),
    lane("todo_child", {claimed_by: "agent-b"})])), null);
});

test("invalid requirements fail instead of falling back to host admission", () => {
  assert.throws(() => admitAdaptiveChildren({...facts([]), items: null}));
  assert.throws(() => admitAdaptiveChildren({...facts([]), max_children: 1.5}));
});
