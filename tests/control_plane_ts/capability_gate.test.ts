import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {evaluateCapabilityGate, missingRequiredCapabilities, projectCapabilityGate} from "../../loopx/control_plane/agents/capability_gate.ts";
import {projectTodoQuotaPlanning, projectQuotaSelection} from "../../loopx/control_plane/todos/quota_selection.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";

const row = (id: string, required: string[] = [], targets: string[] = [], priority = 1): JsonObject => ({
  payload: {todo_id: id, text: `Inspect ${id}`, priority: `P${priority}`}, required, targets, rank: [0, 1, priority, 1, 1, 1],
});
const project = (candidates: JsonObject[], fields: JsonObject = {}) => projectCapabilityGate({
  source: "fixture", candidates, available: [], candidate_order_policy: "claim_then_priority_then_active_next_then_repair", ...fields,
});

test("requirements, repair outputs and runtime availability are different facts", () => {
  assert.deepEqual(missingRequiredCapabilities(["shell", "network", "credentials"], [], []), ["network", "credentials"]);
  assert.deepEqual(missingRequiredCapabilities(["network", "credentials"], ["network"], []), ["credentials"]);
  assert.deepEqual(missingRequiredCapabilities(["network"], [], ["network"]), []);
  const input = [row("repair", ["network"], ["network"]), row("blocked", ["network"])];
  const before = structuredClone(input);
  const gate = project(input)!;
  assert.equal(gate.action, "run");
  assert.deepEqual(gate.available, ["shell", "filesystem_read", "filesystem_write"]);
  assert.equal(gate.repair_candidate_count, 1);
  assert.deepEqual((gate.blocked_candidates as JsonObject[]).map(item => item.todo_id), ["blocked"]);
  assert.deepEqual(input, before);
});

test("owner and bridge resolution remain scoped to affected work, including mixed blockers", () => {
  const gate = project([row("a", ["credentials", "network"]), row("b", ["production_access", "gpu_runner"])])!;
  assert.equal(gate.action, "ask_owner");
  assert.deepEqual(gate.owner_missing, ["credentials", "production_access"]);
  assert.deepEqual(gate.repair_missing, ["network", "gpu_runner"]);
  assert.deepEqual(gate.resolution_steps, [
    {owner: "user", action: "provide_or_authorize", capabilities: ["credentials", "production_access"]},
    {owner: "agent", action: "repair_bridge", capabilities: ["network", "gpu_runner"]},
  ]);
  const runnable = project([row("blocked", ["credentials"]), row("local", ["shell"])])!;
  assert.equal(runnable.action, "run");
  assert.equal(runnable.blocks_delivery, undefined);
  assert.deepEqual(runnable.owner_missing, ["credentials"]);
});

test("binding priority is independent of display order without dropping impacted identities", () => {
  for (const candidates of [[row("low", ["network"], [], 2), row("high", ["network"], [], 0)],
    [row("high", ["network"], [], 0), row("low", ["network"], [], 2)]]) {
    const binding = (project(candidates)!.resolution_bindings as JsonObject[])[0]!;
    assert.equal(binding.priority, "P0"); assert.equal(binding.primary_blocked_todo_id, "high");
    assert.deepEqual(new Set(binding.blocked_todo_ids as string[]), new Set(["low", "high"]));
  }
});

test("repair order stays inside existing claim/profile/priority buckets", () => {
  const normal = row("normal", ["shell"], [], 0), repair = row("repair", ["network"], ["network"], 1);
  const ids = (value: JsonObject) => (value.runnable_candidates as JsonObject[]).map(row => row.todo_id);
  assert.deepEqual(ids(project([repair, normal])!), ["normal", "repair"]);
  assert.deepEqual(ids(project([repair, normal], {candidate_order_policy: null})!), ["repair", "normal"]);
  assert.equal(project([row("plain")]), null);
  assert.equal(project([]), null);
});

test("production-scale requirements use the same rule for every retained record without mutation", () => {
  const fixture = productionScaleCoordinationFixture("goal-capability");
  const before = structuredClone(fixture);
  const records = fixture.projection.todos as JsonObject[];
  const candidates = records.map((record, index) => row(String(record.todo_id),
    index % 3 === 0 ? ["network"] : index % 3 === 1 ? ["credentials"] : ["shell"]));
  const gate = project(candidates)!;
  assert.equal(Number(gate.runnable_count) + (gate.blocked_candidates as JsonObject[]).length, records.length);
  assert.deepEqual(fixture, before);
});

test("quota v1 recomputes missing capabilities, never trusts stale Python results", () => {
  const candidate = {payload: {todo_id: "monitor"}, claim: null, bound: null, blocks: null, excluded: [],
    global: false, gate: false, removed: false, actionable: true, due: true, task_class: "continuous_monitor",
    priority: 1, index: 1, profile_rank: 1, missing: [], raw_claimed: false,
    watch_only: false, required: ["network"], targets: []};
  const request = {items: [candidate], active_items: [], active_executable_items: [], agent_id: "agent-a",
    user_gate_scope: false, monitor_supported: true, diagnostic_limit: 3, backlog_limit: 8,
    visibility_limit: 16, profile: null, source_open_count: 1, available: []};
  assert.equal(((projectQuotaSelection(request).lanes as JsonObject).monitor_due_items as unknown[]).length, 0);
  assert.equal(((projectQuotaSelection({...request, available: ["network"]}).lanes as JsonObject).monitor_due_items as unknown[]).length, 1);
  assert.throws(() => projectTodoQuotaPlanning({schema_version: "todo_quota_planning_request_v1", selection: {}}), /available/);
});

test("runtime decoder rejects malformed facts rather than treating a requirement as absent", () => {
  const request = {schema_version: "capability_gate_request_v0", operation: "missing", available: [],
    items: [{required: ["network"], targets: []}]};
  assert.deepEqual(evaluateCapabilityGate(request).result, [["network"]]);
  assert.throws(() => evaluateCapabilityGate({...request, items: [{required: "network", targets: []}]}), /required/);
  assert.throws(() => evaluateCapabilityGate({...request, operation: "enable"}), /unknown/);
  assert.throws(() => project([ {...row("a"), rank: [1]}]), /six/);
});

const reentry = (fields: JsonObject = {}) => evaluateCapabilityGate({
  schema_version: "capability_gate_request_v0", operation: "reentry",
  gate: project([row("p0", ["network"], [], 0), row("fallback", ["shell"], [], 1)]),
  available: ["shell"], selection_required: true, selected_todo_id: "fallback", receipt_todo_id: null,
  command_prefix: ["loopx", "--format", "json", "quota", "should-run", "--goal-id", "fixture", "--turn-instance-id", "same-turn"],
  scheduler_args: ["--codex-app"], ...fields,
}).result as JsonObject | null;

test("an advisory fallback cannot hide the blocked runtime check; bound work cannot switch", () => {
  const plan = reentry()!;
  const candidate = (plan.candidates as JsonObject[])[0]!;
  assert.equal((candidate.verification_target as JsonObject).todo_id, "p0");
  assert.deepEqual((candidate.command_argv as string[]).slice(-5), ["--available-capability", "shell", "--available-capability", "network", "--codex-app"]);
  assert.ok((candidate.command_argv as string[]).includes("same-turn"));
  assert.equal(reentry({selection_required: false}), null);
  assert.equal(reentry({receipt_todo_id: "fallback"}), null);
  assert.equal(reentry({available: ["network"]}), null);
  assert.equal(reentry({scheduler_args: []}), null);
});

test("verification target honors primary priority and an exact binding even after another target", () => {
  const gate = project([row("low", ["network"], [], 2), row("high", ["network"], [], 0)])!;
  const target = (plan: JsonObject) => ((plan.candidates as JsonObject[])[0]!.verification_target as JsonObject).todo_id;
  assert.equal(target(reentry({gate})!), "high");
  assert.equal(target(reentry({gate, receipt_todo_id: "low"})!), "low");
});

test("verification never converts owner authority into runtime availability", () => {
  assert.equal(reentry({gate: project([row("private", ["credentials", "production_access"])])}), null);
  const plan = reentry({available: ["shell", "credentials", "production_access"]})!;
  const args = (plan.candidates as JsonObject[])[0]!.command_argv as string[];
  assert.ok(!args.includes("credentials") && !args.includes("production_access"));
  assert.equal((plan.inheritance_contract as JsonObject).durable_grant_written, false);
  assert.equal((plan.verification_contract as JsonObject).ordinary_delivery_allowed, false);
});

test("reentry decoder rejects malformed selection and requirement facts", () => {
  assert.throws(() => reentry({selection_required: "false"}), /selection_required/);
  assert.throws(() => reentry({receipt_todo_id: 3}), /receipt_todo_id/);
  assert.throws(() => reentry({gate: {repair_missing: "network"}}), /repair_missing/);
  assert.throws(() => reentry({gate: {blocked_candidates: [{required_capabilities: "network"}]}}), /required_capabilities/);
});


test("repair binding preserves declared P3 and P4 instead of promoting them to P1", () => {
  const binding = (project([row("low", ["network"], [], 4), row("higher", ["network"], [], 3)])!
    .resolution_bindings as JsonObject[])[0]!;
  assert.equal(binding.priority, "P3"); assert.equal(binding.primary_blocked_todo_id, "higher");
});
