import test from "node:test";
import assert from "node:assert/strict";
import {appendFile, mkdir, mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {admitQuotaRunDecision} from "../../loopx/control_plane/quota/run_decision.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const fixture = JSON.parse(await readFile(new URL("./fixtures/quota_run_decision.json", import.meta.url), "utf8"));
function facts(root: string, turn = "initialization"): JsonObject {
  return {...fixture.base_facts, replan_required: true, replan_applies: true,
    vision_initialization_context: {runtime_root: root, goal_id: "baseline-fixture", agent_id: "agent-a",
      turn_instance_id: turn, receipt_bound: false,
      todo_summary: {work_counts: {complete: true, agent_id: "agent-a", advancement: 0}, monitor_due_count: 0},
      todo_items: [{todo_id: "todo_blocked", status: "blocked", task_class: "advancement_task", claimed_by: "agent-a"}],
      replan_obligation: {obligation_id: "replan-0123456789abcdef", triggers: [{kind: "vision_checkpoint_missing"}]},
      acceptance_gaps: [{kind: "vision_checkpoint_missing", missing_baseline: true, agent_id: "agent-a"}]}};
}

const receipt = {
  schema_version: "loopx_rollout_event_v0",
  event_kind: "quota_should_run",
  goal_id: "baseline-fixture",
  agent_id: "agent-a",
  run_id: "initialization",
  status: "autonomous_replan_required",
  details: {
    ok: true,
    should_run: true,
    must_attempt_work: true,
    quiet_noop_allowed: false,
    delivery_allowed: false,
    interaction_mode: "autonomous_replan",
    replan_obligation_id: "replan-0123456789abcdef",
    semantic_replan_obligation_id: "replan-0123456789abcdef",
    settlement_effect_id: "baseline-fixture:agent-a:autonomous_replan:replan-0123456789abcdef:initialization",
    vision_baseline_initialization: true,
  },
};

async function openReceiptLog(root: string): Promise<string> {
  const directory = join(root, "goals", "baseline-fixture");
  await mkdir(directory, {recursive: true});
  return join(directory, "rollout-event-log.jsonl");
}

test("the allowance is consumed by the committed receipt, not guard projection", async () => {
  const root = await mkdtemp(join(tmpdir(), "vision-admission-"));
  try {
    const log = await openReceiptLog(root);
    const candidates = await Promise.all(["one", "two"].map(turn => admitQuotaRunDecision(facts(root, turn))));
    assert.ok(candidates.every(result => result.vision_baseline_initialization === true));

    await writeFile(log, JSON.stringify(receipt));
    assert.equal((await admitQuotaRunDecision(facts(root, "fresh"))).should_run, true);
    await appendFile(log, "\n");

    const wait = await admitQuotaRunDecision(facts(root, "fresh"));
    assert.equal(wait.should_run, false);
    assert.equal(wait.effective_action, "agent_scope_wait");
    assert.equal((await admitQuotaRunDecision(facts(root))).should_run, true);
  } finally {
    await rm(root, {recursive: true, force: true});
  }
});

test("real obligations and unknown or runnable frontiers preserve their owner decisions", async () => {
  const root = await mkdtemp(join(tmpdir(), "vision-admission-"));
  try {
    await writeFile(await openReceiptLog(root), JSON.stringify(receipt) + "\n");
    const patches: JsonObject[] = [
      {receipt_bound: true},
      {replan_obligation: {triggers: [{kind: "vision_checkpoint_missing"}, {kind: "periodic_review_due"}]}},
      {acceptance_gaps: [{kind: "vision_checkpoint_missing", missing_baseline: false, agent_id: "agent-a"}]},
      {todo_summary: {work_counts: {complete: false, agent_id: "agent-a", advancement: 0}, monitor_due_count: 0}},
      {todo_summary: {work_counts: {complete: true, agent_id: "agent-a", advancement: 1}, monitor_due_count: 0}},
      {todo_summary: {work_counts: {complete: true, agent_id: "agent-a", advancement: 0}, monitor_due_count: 1}},
      {todo_items: [{status: "blocked", task_class: "advancement_task", claimed_by: "agent-b"}]},
      {todo_items: [{status: "blocked", task_class: "advancement_task", excluded_agents: ["agent-a"]}]},
      {todo_items: [{status: "blocked", task_class: "advancement_task", resume_ready: true}]},
    ];
    for (const patch of patches) {
      const request = facts(root, "fresh");
      Object.assign(request.vision_initialization_context as JsonObject, patch);
      assert.equal((await admitQuotaRunDecision(request)).effective_action, "autonomous_replan_required");
    }
    for (const patch of [{inbox_reply_due: true}, {workspace_guard: {reason: "Repair workspace"}},
      {automation_prompt_upgrade_required: true}, {replan_required: false}]) {
      const result = await admitQuotaRunDecision({...facts(root, "fresh"), ...patch});
      assert.notEqual(result.effective_action, "agent_scope_wait");
    }
  } finally { await rm(root, {recursive: true, force: true}); }
});

test("diagnostic rows and receipts from other agents and Goal lifetimes do not consume the allowance", async () => {
  const root = await mkdtemp(join(tmpdir(), "vision-admission-"));
  try {
    const log = await openReceiptLog(root);
    await writeFile(log, [
      {...receipt, agent_id: "agent-b"},
      {...receipt, goal_ref: {goal_id: "baseline-fixture", goal_instance_id: "another-instance"}},
      {...receipt, status: "agent_scope_wait", details: {...receipt.details, should_run: false}},
      {...receipt, details: {...receipt.details, vision_baseline_initialization: false}},
    ].map(row => JSON.stringify(row) + "\n").join(""));
    assert.equal((await admitQuotaRunDecision(facts(root, "fresh"))).vision_baseline_initialization, true);
  } finally {
    await rm(root, {recursive: true, force: true});
  }
});
