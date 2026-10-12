import assert from "node:assert/strict";
import {appendFile, mkdir, mkdtemp, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import {nativeChildReportAdmission} from "../../loopx/control_plane/capabilities/native_child_admission.ts";
import {settlementIdentity, settlementIdentityPayload} from "../../loopx/control_plane/effect_program.ts";
import {QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA, readQuotaSettlement} from "../../loopx/control_plane/quota/settlement_readback.ts";
import {heartbeatWorkRequalification} from "../../loopx/control_plane/quota/heartbeat_receipt_identity.ts";

const proof = {
  ok: true, should_run: true, must_attempt_work: true,
  delivery_allowed: true, quiet_noop_allowed: false,
};
const goalId = "native-report-fixture";
const agentId = "generic-coordinator";
const turnId = "turn-native-report";
const todoId = "todo_source_validation";

test("committed work admission, not status spelling, admits a report", () => {
  for (const status of ["normal_run", "turn_run_once", "autonomous_replan_required", "future_work_label"]) {
    assert.deepEqual(nativeChildReportAdmission(proof, status, "effect-original", "open", false), {
      schema_version: "native_child_report_admission_v0",
      report_permission: "new_operation", reason_code: "turn_work_admitted",
      settlement_effect_id: "effect-original", turn_phase: "open",
    });
  }
});

test("late facts are allowed but no new operation once closeout has begun", () => {
  for (const phase of ["settlement_pending", "settled"] as const) {
    assert.equal(nativeChildReportAdmission(proof, "autonomous_replan_required", "original", phase, false)
      .report_permission, "existing_operation_only");
  }
  // A committed effect with a missing receipt is not permission for new work.
  assert.equal(nativeChildReportAdmission(proof, "normal_run", "original", "open", true)
    .report_permission, "existing_operation_only");
});

test("legacy ordinary compatibility never overrides partial or negative facts", async (t) => {
  for (const status of ["normal_run", "turn_run_once"]) {
    assert.equal(nativeChildReportAdmission({}, status, "original", "open", false)
      .report_permission, "new_operation");
    assert.equal(nativeChildReportAdmission({ok: true, should_run: true}, status, "original", "open", false)
      .report_permission, "new_operation");
  }
  for (const [label, facts] of [
    ["no work proof for replan", {}],
    ["partial", {must_attempt_work: true}],
    ["failure", {...proof, ok: false}],
    ["wait", {...proof, should_run: false}],
    ["no obligation", {...proof, must_attempt_work: false}],
    ["no delivery", {...proof, delivery_allowed: false}],
    ["quiet", {...proof, quiet_noop_allowed: true}],
    ["truthy", {...proof, delivery_allowed: "true"}],
    ["legacy failure", {ok: false}],
    ["legacy wait", {should_run: false}],
  ] as const) {
    await t.test(label, () => {
      const status = label === "no work proof for replan" ? "autonomous_replan_required" : "normal_run";
      assert.equal(nativeChildReportAdmission(facts, status, "original", "open", false)
        .report_permission, "not_admitted");
    });
  }
});

async function fixture(replan = false) {
  const root = await mkdtemp(join(tmpdir(), "loopx-native-report-admission-"));
  const goalRoot = join(root, "goals", goalId);
  await mkdir(join(goalRoot, "runs"), {recursive: true});
  const identity = settlementIdentity({goal_id: goalId, agent_id: agentId,
    todo_id: replan ? null : todoId, turn_instance_id: turnId,
    replan_obligation_id: replan ? "replan-0000000000000001" : null});
  const event = {schema_version: "loopx_rollout_event_v0", event_id: "event-original",
    event_kind: "quota_should_run", goal_id: goalId, agent_id: agentId, run_id: turnId,
    status: "autonomous_replan_required", details: {...proof, todo_id: identity.todo_id,
      replan_obligation_id: identity.replan_obligation_id, settlement_effect_id: identity.effect_id}};
  const log = join(goalRoot, "rollout-event-log.jsonl");
  await writeFile(log, JSON.stringify(event) + "\n");
  await writeFile(join(goalRoot, "runs", "index.jsonl"), "");
  const request = {schema_version: QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
    runtime_root: root, goal_id: goalId, agent_id: agentId, turn_instance_id: turnId,
    todo_id: null, replan_obligation_id: null, infer_turn_instance_id: false,
    allow_unbound_binding: false, resolve_original_binding: true};
  return {root, event, log, request, identity};
}

test("exact-Turn original binding is read without selecting or inferring new work", async () => {
  for (const replan of [false, true]) {
    const {root, request, identity} = await fixture(replan);
    try {
      const result = await readQuotaSettlement(request);
      assert.equal(result.identity.result.failure, null);
      assert.deepEqual(result.identity.result.value, settlementIdentityPayload(identity));
      assert.equal(result.native_child_admission.settlement_effect_id, identity.effect_id);
      assert.equal(result.native_child_admission.report_permission, "new_operation");
      assert.equal(result.replay_phase, "open");
    } finally {
      await rm(root, {recursive: true, force: true});
    }
  }
});

test("exact binding resolution preserves explicit denials and conflicts", async (t) => {
  for (const [label, mutate] of [
    ["other actor", {agent_id: "other-coordinator"}],
    ["other Turn", {turn_instance_id: "other-turn"}],
    ["other Todo", {todo_id: "todo_other_validation"}],
    ["invalid Todo", {todo_id: "not-a-todo"}],
    ["dual binding", {todo_id: todoId, replan_obligation_id: "replan-0000000000000002"}],
  ] as const) {
    await t.test(label, async () => {
      const {root, request} = await fixture();
      try {
        const result = await readQuotaSettlement({...request, ...mutate});
        assert.notEqual(result.identity.result.failure, null);
        assert.equal(result.native_child_admission, null);
      } finally {await rm(root, {recursive: true, force: true});}
    });
  }
  for (const [label, details] of [
    ["unbound", {...proof}],
    ["effect mismatch", {...proof, todo_id: todoId, settlement_effect_id: "other-effect"}],
    ["dual receipt", {...proof, todo_id: todoId, replan_obligation_id: "replan-0000000000000002"}],
  ] as const) {
    await t.test(label, async () => {
      const {root, request, event, log} = await fixture();
      try {
        await writeFile(log, JSON.stringify({...event, details}) + "\n");
        const result = await readQuotaSettlement(request);
        assert.notEqual(result.identity.result.failure, null);
        assert.equal(result.native_child_admission, null);
      } finally {await rm(root, {recursive: true, force: true});}
    });
  }
  const {root, request, event, log} = await fixture();
  try {
    await appendFile(log, JSON.stringify({...event, event_id: "event-conflict",
      details: {...event.details, todo_id: "todo_other_validation", settlement_effect_id: "other-effect"}}) + "\n");
    assert.notEqual((await readQuotaSettlement(request)).identity.result.failure, null);
    await assert.rejects(readQuotaSettlement({...request, resolve_original_binding: "yes"}), /must be a boolean/);
    await assert.rejects(readQuotaSettlement({...request, infer_turn_instance_id: true}), /explicit Turn identity/);
    // Existing callers still require an explicit binding by default.
    assert.notEqual((await readQuotaSettlement({...request, resolve_original_binding: false})).identity.result.failure, null);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("same-binding reentry appends only explicit work qualification and preserves original authority", async () => {
  const {root, request, identity, event, log} = await fixture();
  try {
    const original = {...event, status: "successor_replan_required", details: {
      ...event.details, delivery_allowed: false, quiet_noop_allowed: false,
      semantic_replan_obligation_id: "replan-0000000000000001",
      delivery_workspace_causality: {requirement: "required"},
      pending_action_selection: {todo_id: todoId}, closeout_required: false,
    }};
    await writeFile(log, JSON.stringify(original) + "\n");
    const current = {...event.details, semantic_replan_obligation_id: "",
      delivery_workspace_causality: {requirement: "not_required"}, ignored_caller_fact: true};
    const readback = await readQuotaSettlement({...request, heartbeat_reentry_guard: current});
    assert.equal(readback.native_child_admission.report_permission, "not_admitted");
    const qualification = readback.heartbeat_reentry_qualification;
    assert.equal(qualification.append, true);
    assert.deepEqual(qualification.details, {...original.details, ...proof,
      closeout_required: true, settlement_receipt_revision: "work_admission"});
    // A readback cannot commit or grant work by itself.
    assert.equal((await readQuotaSettlement(request)).native_child_admission.report_permission, "not_admitted");
    const corrected = {...original, event_id: "event-qualified", status: "normal_run",
      details: qualification.details};
    await appendFile(log, JSON.stringify(corrected) + "\n");
    const cold = await readQuotaSettlement({...request, heartbeat_reentry_guard: current});
    assert.equal(cold.heartbeat_receipt.event_id, "event-qualified");
    assert.equal(cold.native_child_admission.report_permission, "new_operation");
    assert.equal(cold.native_child_admission.settlement_effect_id, identity.effect_id);
    assert.deepEqual(cold.heartbeat_reentry_qualification, {append: false, details: null});
  } finally {await rm(root, {recursive: true, force: true});}
});

test("partial, denied or truthy current facts cannot requalify a negative guard", async (t) => {
  const {root, identity, event} = await fixture();
  try {
    const original = {...event, details: {...event.details, delivery_allowed: false}};
    for (const [label, facts] of [
      ["legacy", {ok: true, should_run: true}], ["partial", {must_attempt_work: true}],
      ["failed", {...proof, ok: false}], ["wait", {...proof, should_run: false}],
      ["no obligation", {...proof, must_attempt_work: false}],
      ["no delivery", {...proof, delivery_allowed: false}],
      ["quiet", {...proof, quiet_noop_allowed: true}],
      ["truthy", {...proof, delivery_allowed: "true"}],
    ] as const) {
      await t.test(label, () => {
        const current = {...facts, todo_id: todoId, settlement_effect_id: identity.effect_id};
        assert.deepEqual(heartbeatWorkRequalification(original, current, identity, "open", false),
          {append: false, details: null});
      });
    }
    for (const phase of ["settlement_pending", "settled"] as const) {
      assert.equal(heartbeatWorkRequalification(original, event.details, identity, phase, false).append, false);
    }
    assert.equal(heartbeatWorkRequalification(original, event.details, identity, "open", true).append, false);
    assert.equal(heartbeatWorkRequalification(event, event.details, identity, "open", false).append, false);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("work requalification fails closed on binding and semantic guard conflicts", async () => {
  const {root, identity, event} = await fixture();
  try {
    const original = {...event, details: {...event.details, delivery_allowed: false,
      semantic_replan_obligation_id: "replan-0000000000000001"}};
    for (const changed of [
      {todo_id: "todo_other_validation"}, {settlement_effect_id: "different-effect"},
      {replan_obligation_id: "replan-0000000000000002"},
      {semantic_replan_obligation_id: "replan-0000000000000002"},
    ]) {
      assert.throws(() => heartbeatWorkRequalification(original, {...event.details, ...changed},
        identity, "open", false), /binding|guard|conflicting/);
    }
  } finally {await rm(root, {recursive: true, force: true});}
});

test("begun closeout readback cannot promote a previously denied guard", async () => {
  const {root, request, event, log, identity} = await fixture();
  try {
    const original = {...event, details: {...event.details, delivery_allowed: false}};
    const writeback = {...event, event_id: "event-writeback", event_kind: "refresh_state",
      details: {settlement_effect_id: identity.effect_id}};
    await writeFile(log, [original, writeback].map(row => JSON.stringify(row)).join("\n") + "\n");
    const result = await readQuotaSettlement({...request, heartbeat_reentry_guard: event.details});
    assert.equal(result.heartbeat_reentry_qualification.append, false);
    assert.equal(result.heartbeat_receipt.event_id, event.event_id);
    assert.equal(result.native_child_admission.report_permission, "not_admitted");
  } finally {await rm(root, {recursive: true, force: true});}
});
