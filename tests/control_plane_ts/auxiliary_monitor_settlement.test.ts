import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { appendFile, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { settlementIdentity, type JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { EffectRuntimeRequestError } from "../../loopx/control_plane/effect_runtime_errors.ts";
import {
  evaluateQuotaMonitorPollCommit, QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA,
} from "../../loopx/control_plane/quota/monitor_poll_commit.ts";

const goal = "auxiliary-monitor-goal";
const agent = "agent-monitor-fixture";
const turn = "turn-completed-advancement";
const primary = "todo_primary";
const monitor = "todo_monitor";
const identity = settlementIdentity({ goal_id: goal, agent_id: agent, turn_instance_id: turn, todo_id: primary });

function event(kind: string): JsonObject {
  return { schema_version: "loopx_rollout_event_v0", event_id: `event-${kind}`,
    event_kind: kind, goal_id: goal, agent_id: agent, run_id: turn,
    details: { todo_id: primary, settlement_effect_id: identity.effect_id } };
}

async function fixture(t: test.TestContext): Promise<{ runtime: string; params: JsonObject }> {
  const runtime = await mkdtemp(join(tmpdir(), "loopx-auxiliary-settlement-"));
  t.after(() => rm(runtime, { recursive: true, force: true }));
  await mkdir(join(runtime, "goals", goal, "runs"), { recursive: true });
  await writeFile(join(runtime, "goals", goal, "runs", "index.jsonl"), "");
  await writeFile(join(runtime, "goals", goal, "rollout-event-log.jsonl"), `${JSON.stringify(event("quota_should_run"))}\n`);
  const candidate = { todo_id: monitor, task_class: "continuous_monitor", claimed_by: agent,
    target_key: "public-watch", due: true };
  return { runtime, params: {
    schema_version: QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA,
    phase: "preflight", execute: true, effect_id: "auxiliary-observation", runtime_root: runtime,
    goal_id: goal, source: "heartbeat", generated_at: "2026-09-01T00:00:00Z",
    expected_index_digest: `sha256:${createHash("sha256").update("").digest("hex")}`, turn_instance_id: turn,
    decision: { goal_id: goal, agent_id: agent, should_run: true,
      normal_delivery_allowed: true, recovery_delivery_allowed: false, effective_action: "normal_run",
      self_repair_allowed: false, capability_repair_allowed: false, workspace_repair_allowed: false,
      state: "active", safe_bypass_allowed: false, safe_bypass_kind: null, blocked_action_scope: null,
      compute: 1, window_hours: 24, slot_minutes: 1, spent_slots: 0, allowed_slots: 10,
      recommended_action: "Continue the original settlement", reason: "Due observation",
      requires_user_action: false, heartbeat_recommendation: {}, external_evidence_observation: null,
      vision_wait_state: null, work_lane_contract: { must_attempt_work: true, obligation: "attempt_due_monitor" },
      due_monitor_candidates: [candidate], registry_due_monitor: candidate,
      auxiliary_settlement_todo: { todo_id: primary, task_class: "advancement_task", status: "done",
        claimed_by: agent, excluded_agents: [] } },
    observation: { actor_agent_id: agent, settlement_todo_id: primary, todo_id: monitor,
      target_key: "public-watch", result_hash: "unchanged", material_change: false, reason_summary: null,
      cadence: "1h", next_due_at: "2026-09-01T01:00:00Z", next_agent_todo: null,
      next_action_kind: null, next_task_repository: null, next_required_capabilities: [],
      next_continuation_policy: null, next_target_key: null, next_user_todo: null,
      next_user_task_class: null, next_claimed_by: null }, provider_receipt: null, status_reload_warning: null,
  } };
}

async function settlePrimary(runtime: string): Promise<void> {
  await appendFile(join(runtime, "goals", goal, "rollout-event-log.jsonl"),
    [event("refresh_state"), event("quota_spend")].map(row => JSON.stringify(row)).join("\n") + "\n");
  await appendFile(join(runtime, "goals", goal, "runs", "index.jsonl"), [
    { classification: "state_refreshed", delivery_outcome: "outcome_progress" },
    { classification: "quota_slot_spent" },
  ].map(row => JSON.stringify({ ...row, goal_id: goal, agent_id: agent, todo_id: primary,
    turn_instance_id: turn, settlement_identity: identity })).join("\n") + "\n");
}

test("fresh auxiliary admission requires exact advancement identity and ordinary due-work gates before and after settlement", async t => {
  const cases: Array<[string, (params: JsonObject) => JsonObject, string]> = [
    ["other Turn", p => ({ ...p, turn_instance_id: "other-turn" }), "heartbeat_receipt_identity_conflict"],
    ["other binding", p => ({ ...p, observation: { ...p.observation as JsonObject, settlement_todo_id: "todo_other" } }), "heartbeat_receipt_identity_conflict"],
    ["missing lifecycle", p => ({ ...p, decision: { ...p.decision as JsonObject, auxiliary_settlement_todo: null } }), "heartbeat_receipt_identity_conflict"],
    ["blocked lifecycle", p => ({ ...p, decision: { ...p.decision as JsonObject,
      auxiliary_settlement_todo: { todo_id: primary, task_class: "advancement_task", status: "blocked" } } }), "heartbeat_receipt_identity_conflict"],
    ["foreign primary", p => ({ ...p, decision: { ...p.decision as JsonObject,
      auxiliary_settlement_todo: { todo_id: primary, task_class: "advancement_task", status: "done", claimed_by: "peer" } } }), "heartbeat_receipt_identity_conflict"],
    ["excluded actor", p => ({ ...p, decision: { ...p.decision as JsonObject,
      auxiliary_settlement_todo: { todo_id: primary, task_class: "advancement_task", status: "done", excluded_agents: [agent] } } }), "heartbeat_receipt_identity_conflict"],
    ["foreign observer", p => ({ ...p, observation: { ...p.observation as JsonObject, actor_agent_id: "peer" } }), "heartbeat_receipt_identity_conflict"],
    ["no due target", p => ({ ...p, decision: { ...p.decision as JsonObject, registry_due_monitor: {} } }), "monitor_poll_admission_rejected"],
    ["foreign monitor", p => ({ ...p, decision: { ...p.decision as JsonObject,
      registry_due_monitor: { ...(p.decision as JsonObject).registry_due_monitor as JsonObject, claimed_by: "peer" } } }), "monitor_poll_admission_rejected"],
    ["ordinary gate closed", p => ({ ...p, decision: { ...p.decision as JsonObject,
      work_lane_contract: { must_attempt_work: false }, should_run: false } }), "monitor_poll_admission_rejected"],
    ["user action", p => ({ ...p, decision: { ...p.decision as JsonObject, requires_user_action: true } }), "monitor_poll_admission_rejected"],
  ];
  for (const settled of [false, true]) for (const [name, change, code] of cases) {
    await t.test(`${settled ? "settled" : "pending"}: ${name}`, async st => {
      const { runtime, params } = await fixture(st);
      if (settled) await settlePrimary(runtime);
      const initialIndex = await readFile(join(runtime, "goals", goal, "runs", "index.jsonl"), "utf8");
      const changed = change(params);
      await assert.rejects(evaluateQuotaMonitorPollCommit(changed), error => {
        assert.ok(error instanceof EffectRuntimeRequestError);
        assert.equal(error.code, code);
        if (code === "heartbeat_receipt_identity_conflict") {
          const observation = changed.observation as JsonObject;
          assert.ok(error.message.includes(String(observation.settlement_todo_id)));
          assert.ok(error.message.includes(String(observation.todo_id)));
        }
        return true;
      });
      await assert.rejects(readFile(join(runtime, "goals", goal, "runs", ".transactions", "quota-monitor-poll")),
        { code: "ENOENT" });
      assert.equal(await readFile(join(runtime, "goals", goal, "runs", "index.jsonl"), "utf8"), initialIndex);
    });
  }
});

function providerReceipt(params: JsonObject): JsonObject {
  return { schema_version: "monitor_poll_todo_writeback_v0", monitor_effect_id: params.effect_id,
    goal_id: goal, todo_id: monitor, target_key: "public-watch", result_hash: "unchanged",
    dry_run: false, material_change: false, material_change_generation: 0, consecutive_no_change: 1,
    last_checked_at: params.generated_at, next_due_at: "2026-09-01T01:00:00Z", cadence: "1h",
    todo_update: { ok: true }, next_todos: [], successor_receipts: [] };
}

test("settled primary admits the first due auxiliary observation without another debit or delivery", async t => {
  const { runtime, params } = await fixture(t);
  await settlePrimary(runtime);
  const index = await readFile(join(runtime, "goals", goal, "runs", "index.jsonl"));
  const request = { ...params, expected_index_digest: `sha256:${createHash("sha256").update(index).digest("hex")}` };
  const preflight = await evaluateQuotaMonitorPollCommit(request);
  assert.equal(preflight.status, "provider_required", JSON.stringify(preflight));
  const commit = { ...request, phase: "commit", provider_receipt: providerReceipt(request) };
  const written = await evaluateQuotaMonitorPollCommit(commit);
  assert.equal(written.status, "written", JSON.stringify(written));
  assert.equal((written.payload.turn_continuation as JsonObject).current_turn_settled, true);
  assert.equal((written.payload.turn_continuation as JsonObject).next_turn_required, true);
  assert.equal((written.payload.turn_continuation as JsonObject).same_turn_independent_settlement_allowed, false);
  assert.equal((await evaluateQuotaMonitorPollCommit(commit)).status, "replayed");
  const rows = (await readFile(join(runtime, "goals", goal, "runs", "index.jsonl"), "utf8"))
    .trim().split("\n").map(line => JSON.parse(line));
  assert.equal(rows.filter(row => row.classification === "quota_monitor_poll").length, 1);
  assert.equal(rows.filter(row => row.classification === "state_refreshed").length, 1);
  assert.equal(rows.filter(row => row.classification === "quota_slot_spent").length, 1);
  assert.equal(rows.length, 3);
});

test("completed primary preserves an admitted pending effect across settlement and replay reads current closeout", async t => {
  const { runtime, params } = await fixture(t);
  const preflight = await evaluateQuotaMonitorPollCommit(params);
  assert.equal(preflight.status, "provider_required", JSON.stringify(preflight));
  await settlePrimary(runtime);
  const receipt = providerReceipt(params);
  const postBusiness = { ...params, phase: "commit", provider_receipt: receipt,
    decision: { ...params.decision as JsonObject, registry_due_monitor: {}, auxiliary_settlement_todo: null,
      due_monitor_candidates: [], work_lane_contract: { must_attempt_work: false }, should_run: false } };
  assert.equal((await evaluateQuotaMonitorPollCommit(postBusiness)).status, "written");
  const replay = await evaluateQuotaMonitorPollCommit(postBusiness);
  assert.equal(replay.status, "replayed");
  assert.equal((replay.payload.turn_continuation as JsonObject).current_turn_settled, true);
  const rows = (await readFile(join(runtime, "goals", goal, "runs", "index.jsonl"), "utf8"))
    .trim().split("\n").map(line => JSON.parse(line));
  assert.equal(rows.filter(row => row.classification === "quota_monitor_poll").length, 1);
  assert.equal(rows.filter(row => row.classification === "quota_slot_spent").length, 1);
});
