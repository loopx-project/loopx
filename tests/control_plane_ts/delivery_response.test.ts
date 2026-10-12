import assert from "node:assert/strict";
import test from "node:test";
import { projectDeliveryResponse } from "../../loopx/control_plane/work_items/delivery_history.ts";
import { evaluateTodoResumeConditions, TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION } from "../../loopx/control_plane/todos/resume_condition.ts";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";

const run = { delivery_outcome: "outcome_gap", delivery_batch_scale: "implementation",
  delivery_turn_kind: "", todo_id: "todo_delivery", replan_obligation_id: "",
  outcome_followthrough_required: true,
  progress_observation: { schema_version: "typed_progress_observation_v0", result_class: "blocked",
    work_item_id: "todo_delivery", blocker_id: "blocker_dependency", evidence_ids: ["evidence_dependency"] } };
const waiting = { todo_id: "todo_delivery", role: "agent", status: "deferred",
  task_class: "advancement_task", claimed_by: "agent-a", resume_when: "todo_done:todo_dependency",
  resume_ready: false, resume_condition: { schema_version: "todo_resume_condition_v0",
    resume_when: "todo_done:todo_dependency", satisfied: false, kind: "todo_done",
    target_todo_id: "todo_dependency", target_status: "open", target_task_class: "advancement_task", target_archive_state: "active" } };
const input = { run, todo: waiting, run_agent_id: "agent-a", agent_id: "agent-a" };

test("wait proof consumes the real resume evaluator for all five condition kinds", () => {
  const dependency = { todo_id: "todo_dependency", role: "agent", status: "open", task_class: "advancement_task" };
  for (const [resume, source, capabilities, valid] of [
    ["todo_done:todo_dependency", [dependency], [], true],
    ["todo_done:todo_dependency", [], [], false],
    ["monitor_changed:todo_dependency", [{ ...dependency, task_class: "continuous_monitor", material_change_generation: 0 }], [], true],
    ["monitor_changed:todo_dependency", [], [], false],
    ["capacity_available:network", [], [], true],
    ["capacity_available:network", [], null, false],
    ["pr_merged:example/project#1", [], [], false],
    ["pr_merged:#1", [], [], false],
    ["resume_at:2026-09-15T00:00:00Z", [], [], true],
  ] as const) {
    const todo = { ...waiting, resume_when: resume, resume_monitor_generation: 0 };
    const evaluated = evaluateTodoResumeConditions({ schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
      items: [todo], source_items: source, rollout_events: [], available_capabilities: capabilities,
      evaluated_at: "2026-09-14T00:00:00Z" });
    const condition = (evaluated.conditions as JsonObject[])[0].condition;
    assert.equal(projectDeliveryResponse({ ...input, todo: { ...todo, resume_condition: condition } }).outcome_floor_applicable,
      !valid, resume + JSON.stringify(source));
    if (valid) {
      const proof = condition as JsonObject;
      const mutations: JsonObject[] = [{ kind: "unknown" }, { target: "other_target" }];
      if (proof.kind === "todo_done" || proof.kind === "monitor_changed") {
        mutations.push({ target_todo_id: "todo_other" }, { target_todo_id: null }, { target_task_class: "unknown" });
      }
      if (proof.kind === "monitor_changed") mutations.push(
        { baseline_generation: 1 }, { material_change_generation: -1 }, { material_change_generation: 0.5 });
      if (proof.kind === "capacity_available") mutations.push({ capability: "other" }, { provider_required: true });
      if (proof.kind === "pr_merged") mutations.push({ pr_number: 2 }, { pr_repo: "example/other" });
      if (proof.kind === "resume_at") mutations.push(
        { scheduled_for: "2026-09-16T00:00:00Z" }, { clock_provider: "other" },
        { material_change_generation: 1 }, { resume_receipt: {} });
      for (const patch of mutations) assert.equal(projectDeliveryResponse({ ...input,
        todo: { ...todo, resume_condition: { ...proof, ...patch } } }).reason,
      "history_supervision", resume + JSON.stringify(patch));
    }
  }
});

test("known completion targets qualify waits; an unobserved PR binding cannot hide stalled delivery", () => {
  for (const task_class of ["advancement_task", "user_gate", "user_action", "blocker"]) {
    assert.equal(projectDeliveryResponse({ ...input, todo: { ...waiting,
      resume_condition: { ...waiting.resume_condition, target_task_class: task_class } } }).reason, "canonical_todo_wait");
  }
  const todo = { ...waiting, resume_when: "pr_merged:#1", task_repository: "git:github.com/example/project" };
  const evaluated = evaluateTodoResumeConditions({ schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [todo], source_items: [], rollout_events: [], available_capabilities: [] });
  const condition = (evaluated.conditions as JsonObject[])[0].condition;
  assert.equal(projectDeliveryResponse({ ...input, todo: { ...todo, resume_condition: condition } }).reason, "history_supervision");
  assert.equal(projectDeliveryResponse({ ...input, todo: { ...todo, task_repository: "git:github.com/example/other",
    resume_condition: condition } }).reason, "history_supervision");
});

test("a bound blocked observation delegates a current legal wait to canonical planning", () => {
  const before = structuredClone(input);
  const result = projectDeliveryResponse(input);
  assert.equal(result.outcome_floor_applicable, false);
  assert.equal(result.outcome_followthrough, null);
  assert.equal(result.reason, "canonical_todo_wait");
  assert.deepEqual(input, before);
});

test("exact dependency identity and a supported completion class are required", () => {
  for (const patch of [
    { target_todo_id: "todo_other" }, { target_todo_id: null },
    { target: "todo_other" }, { kind: "capacity_available" },
    { target_task_class: "unknown_class" }, { target_task_class: "" },
  ]) {
    const result = projectDeliveryResponse({ ...input, todo: { ...waiting,
      resume_condition: { ...waiting.resume_condition, ...patch } } });
    assert.equal(result.reason, "history_supervision", JSON.stringify(patch));
    assert.equal(result.outcome_floor_applicable, true);
    assert.equal((result.outcome_followthrough as JsonObject).required, true);
  }
});

test("history alone, missing source, invalid wait, and other actors cannot exempt the floor", () => {
  for (const patch of [
    { todo: null }, { agent_id: "agent-b" }, { run_agent_id: "agent-b" },
    { agent_id: null }, { run_agent_id: null }, { agent_id: "", run_agent_id: "" },
    { todo: { ...waiting, todo_id: "todo_other" } },
    { todo: { ...waiting, claimed_by: "agent-b" } },
    { todo: { ...waiting, excluded_agents: ["agent-a"] } },
    { todo: { ...waiting, status: "done" } },
    { todo: { ...waiting, resume_ready: true } },
    { todo: { ...waiting, resume_condition: null } },
    { todo: { ...waiting, resume_when: "todo_done:todo_delivery", resume_condition: {
      ...waiting.resume_condition, resume_when: "todo_done:todo_delivery", target_todo_id: "todo_delivery" } } },
    { todo: { ...waiting, resume_condition: { ...waiting.resume_condition, target_status: null } } },
    { todo: { ...waiting, resume_condition: { ...waiting.resume_condition, target_task_class: "continuous_monitor" } } },
    { todo: { ...waiting, resume_condition: { ...waiting.resume_condition, invalid_state: "target_missing" } } },
    { todo: { ...waiting, resume_condition: { ...waiting.resume_condition, satisfied: true } } },
    { run: { ...run, progress_observation: null, delivery_turn_kind: "blocker_writeback" } },
    { run: { ...run, replan_obligation_id: "replan_other" } },
    { run: { ...run, progress_observation: { ...run.progress_observation, evidence_ids: [] } } },
  ]) assert.equal(projectDeliveryResponse({ ...input, ...patch }).outcome_floor_applicable, true, JSON.stringify(patch));
});

test("surface-only supervision and unknown remain distinct from durable work state", () => {
  const surface = projectDeliveryResponse({ ...input, run: { ...run, delivery_outcome: "surface_only" } });
  assert.equal(surface.outcome_floor_applicable, true);
  assert.ok(surface.outcome_followthrough);
  const unknown = projectDeliveryResponse({ ...input, run: { ...run, delivery_outcome: "unknown",
    outcome_followthrough_required: false, progress_observation: null } });
  assert.equal(unknown.outcome_floor_applicable, true);
  assert.equal(unknown.outcome_followthrough, null);
  assert.equal("resolved_todo_id" in unknown, false);
});
