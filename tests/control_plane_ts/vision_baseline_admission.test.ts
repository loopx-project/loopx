import test from "node:test";
import assert from "node:assert/strict";
import {mkdtemp, readFile, readdir, rm, writeFile} from "node:fs/promises";
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
      replan_obligation: {obligation_id: "replan-baseline", triggers: [{kind: "vision_checkpoint_missing"}]},
      acceptance_gaps: [{kind: "vision_checkpoint_missing", missing_baseline: true, agent_id: "agent-a"}]}};
}

test("concurrent fresh Turns admit only one baseline initialization", async () => {
  const root = await mkdtemp(join(tmpdir(), "vision-admission-"));
  try {
    const results = await Promise.all(["one", "two", "three"].map(turn => admitQuotaRunDecision(facts(root, turn))));
    assert.equal(results.filter(result => result.should_run === true).length, 1);
    assert.equal(results.filter(result => result.effective_action === "agent_scope_wait").length, 2);
  } finally { await rm(root, {recursive: true, force: true}); }
});

test("real obligations and unknown or runnable frontiers preserve their owner decisions", async () => {
  const root = await mkdtemp(join(tmpdir(), "vision-admission-"));
  try {
    await admitQuotaRunDecision(facts(root));
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

test("malformed durable state cannot silently rearm the allowance", async () => {
  const root = await mkdtemp(join(tmpdir(), "vision-admission-"));
  try {
    await admitQuotaRunDecision(facts(root));
    const directory = join(root, "vision-baseline-admissions");
    const [file] = await readdir(directory);
    await writeFile(join(directory, file), JSON.stringify({schema_version: "vision_baseline_admission_v0",
      goal_id: "another-goal", agent_id: "agent-a", turn_instance_id: "first"}));
    await assert.rejects(admitQuotaRunDecision(facts(root, "fresh")), /scope\/schema mismatch/);
  } finally { await rm(root, {recursive: true, force: true}); }
});
