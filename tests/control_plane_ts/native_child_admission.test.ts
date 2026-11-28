import assert from "node:assert/strict";
import {appendFile, mkdir, mkdtemp, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {createHash} from "node:crypto";

import {nativeChildReportAdmission} from "../../loopx/control_plane/capabilities/native_child_admission.ts";
import {settlementIdentity, settlementIdentityPayload, type JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA, readQuotaSettlement} from "../../loopx/control_plane/quota/settlement_readback.ts";
import {heartbeatWorkRequalification, projectHeartbeatReceiptSelection, validateHeartbeatTakeoverParent} from "../../loopx/control_plane/quota/heartbeat_receipt_identity.ts";

const proof = {
  ok: true, should_run: true, must_attempt_work: true,
  delivery_allowed: true, quiet_noop_allowed: false,
};
const goalId = "native-report-fixture";
const agentId = "generic-coordinator";
const turnId = "turn-native-report";
const todoId = "todo_source_validation";

test("Python receipt selection transports the shared binding without rewriting its effect", () => {
  const event = {run_id: turnId, details: {todo_id: todoId}};
  const originalEffect = `${goalId}:${agentId}:${todoId}:${turnId}`;
  const select = (events: unknown[]) => projectHeartbeatReceiptSelection({goal_id: goalId, agent_id: agentId, events});
  assert.deepEqual(select([]), {index: null, binding: null});
  assert.deepEqual(select([{run_id: turnId}]), {index: 0, binding: null});
  assert.deepEqual(select([event, {run_id: turnId}]), {
    index: 0, binding: {binding_kind: "todo", binding_id: todoId, effect_id: originalEffect},
  });
  assert.deepEqual(select([event, {...event, details: {...event.details, settlement_effect_id: originalEffect}}]), {
    index: 1, binding: {binding_kind: "todo", binding_id: todoId, effect_id: originalEffect},
  });
  assert.deepEqual(select([{...event, details: {...event.details, settlement_effect_id: "declared-wrong-effect"}}]), {
    index: 0, binding: {binding_kind: "todo", binding_id: todoId, effect_id: "declared-wrong-effect"},
  });
  assert.throws(() => select([event, {...event, details: {todo_id: "todo_other_validation"}}]), /conflicting settlement identities/);
  assert.throws(() => select([{...event, details: {todo_id: "todo_BAD_ID"}}]), /not a legal Todo id/);
});

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

test("an admitted semantic replan can report without ordinary delivery authority", () => {
  const replan = {...proof, interaction_mode: "autonomous_replan", delivery_allowed: false};
  const obligation = "replan-0000000000000001";
  for (const status of ["autonomous_replan_required", "future_work_label"]) {
    assert.equal(nativeChildReportAdmission(replan, status, "original", "open", false, obligation)
      .report_permission, "new_operation");
    assert.equal(nativeChildReportAdmission(replan, status, "original", "open", true, obligation)
      .report_permission, "existing_operation_only");
    for (const phase of ["settlement_pending", "settled"] as const) {
      assert.equal(nativeChildReportAdmission(replan, status, "original", phase, false, obligation)
        .report_permission, "existing_operation_only");
    }
  }
  for (const denied of [
    {...replan, ok: false}, {...replan, should_run: false}, {...replan, must_attempt_work: false},
    {...replan, quiet_noop_allowed: true}, {...replan, delivery_allowed: "false"},
    {...replan, interaction_mode: "successor_replan_required"},
  ]) {
    assert.equal(nativeChildReportAdmission(denied, "autonomous_replan_required", "original", "open", false, obligation)
      .report_permission, "not_admitted");
  }
  assert.equal(nativeChildReportAdmission(replan, "autonomous_replan_required", "original", "open", false)
    .report_permission, "not_admitted");
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
      selected_todo_id: identity.todo_id,
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

test("readback validates a semantic replan obligation before report admission", async () => {
  for (const replan of [false, true]) {
    const {root, request, identity, event, log} = await fixture(replan);
    try {
      const details = {...event.details, delivery_allowed: false, interaction_mode: "autonomous_replan",
        semantic_replan_obligation_id: "replan-0000000000000001"};
      await writeFile(log, JSON.stringify({...event, details}) + "\n");
      const result = await readQuotaSettlement(request);
      assert.deepEqual(result.identity.result.value, settlementIdentityPayload(identity));
      assert.equal(result.native_child_admission.report_permission, "new_operation");
      assert.equal(result.heartbeat_receipt.details.delivery_allowed, false);
      await writeFile(log, JSON.stringify({...event, details: {...details, semantic_replan_obligation_id: "malformed"}}) + "\n");
      await assert.rejects(readQuotaSettlement(request), /semantic replan guard is malformed/);
    } finally {await rm(root, {recursive: true, force: true});}
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
    const current = {...event.details, semantic_replan_obligation_id: "", quota_spend_source: "visible-goal",
      delivery_workspace_causality: {requirement: "not_required"}, ignored_caller_fact: true};
    const readback = await readQuotaSettlement({...request, heartbeat_reentry_guard: current});
    assert.equal(readback.native_child_admission.report_permission, "not_admitted");
    const qualification = readback.heartbeat_reentry_qualification;
    assert.equal(qualification.append, true);
    assert.deepEqual(qualification.details, {...original.details, ...proof,
      closeout_required: true, settlement_receipt_revision: "work_admission", quota_spend_source: "visible-goal"});
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

test("a retained binding cannot replace fresh same-Todo executable selection", async () => {
  const {root, identity, event} = await fixture();
  try {
    const original = {...event, details: {...event.details, delivery_allowed: false}};
    for (const selected of [undefined, null, "todo_other_validation", true]) {
      const current = {...event.details, selected_todo_id: selected};
      assert.deepEqual(heartbeatWorkRequalification(original, current, identity, "open", false),
        {append: false, details: null});
    }
    assert.equal(heartbeatWorkRequalification(original, event.details, identity, "open", false).append, true);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("one prepared Turn admits only renewal of its original lease generation", async () => {
  const {root, identity, event} = await fixture();
  try {
    const originalDetails = {...event.details, prepared_lease_key_sha256: "a".repeat(64),
      prepared_lease_version: 3, prepared_lease_epoch: 2};
    const original = {...event, details: originalDetails};
    const renewal = {...originalDetails, prepared_lease_version: 4};
    assert.deepEqual(heartbeatWorkRequalification(original, renewal, identity, "open", false), {
      append: true,
      details: {...renewal, settlement_receipt_revision: "lease_readiness"},
    });
    for (const changed of [
      {prepared_lease_epoch: 3},
      {prepared_lease_key_sha256: "b".repeat(64)},
      {prepared_lease_epoch: undefined},
    ]) {
      const replacement = Object.fromEntries(Object.entries({...renewal, ...changed})
        .filter(([, value]) => value !== undefined));
      assert.throws(() => heartbeatWorkRequalification(original, replacement, identity, "open", false),
        /original task lease generation/);
      assert.equal(heartbeatWorkRequalification(original, replacement, identity, "settlement_pending", false).append, false);
      assert.equal(heartbeatWorkRequalification(original, replacement, identity, "open", true).append, false);
    }
    const {prepared_lease_epoch: _, ...historicalDetails} = originalDetails;
    const historical = {...event, details: historicalDetails};
    assert.equal(heartbeatWorkRequalification(historical, historicalDetails, identity, "open", false).append, false);
    assert.throws(() => heartbeatWorkRequalification(historical, renewal, identity, "open", false),
      /original task lease generation/);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("fenced takeover preserves admission and rejects competing or stale source facts", async () => {
  const {root, identity, event} = await fixture();
  try {
    const original = {...event, recorded_at: "2026-01-01T00:00:00Z", details: {...event.details,
      closeout_required: true, prepared_lease_required: true, prepared_lease_key_sha256: "a".repeat(64),
      prepared_lease_version: 1, prepared_lease_epoch: 1}};
    const source = {goal_id: goalId, todo_id: todoId, handoff_mode: "hard_lease",
      source_authority: "sqlite_v0", provider_revision: "sqlite:fixture:3",
      decision_read_from_provider: true, legacy_fallback_used: false,
      observed_at: "2026-01-01T01:00:01Z", takeover_basis: {
        acceptance_sha256: "b".repeat(64), todo: {todo_id: todoId, role: "agent", task_class: "advancement_task",
          status: "open", archive_state: "active", done: false, claimed_by: agentId}},
      leases: [{todo_id: todoId, owner: agentId, status: "active", idempotency_key: "current-claim",
        version: 2, lease_epoch: 2, acquired_at: "2026-01-01T01:00:00Z",
        updated_at: "2026-01-01T01:00:00Z", expires_at: "2026-01-01T04:00:00Z"}]};
    const current = {...original.details, prepared_lease_key_sha256: createHash("sha256").update("current-claim").digest("hex"),
      prepared_lease_version: 2, prepared_lease_epoch: 2, lease_takeover_source: source};
    const result = heartbeatWorkRequalification(original, current, identity, "open", false);
    assert.equal(result.append, true);
    const details = result.details as JsonObject;
    assert.equal(details.settlement_receipt_revision, "lease_takeover");
    assert.equal(details.settlement_effect_id, identity.effect_id);
    assert.equal(details.takeover_original_event_id, original.event_id);
    assert.equal(details.takeover_original_epoch, 1);
    assert.equal(details.prepared_lease_epoch, 2);
    assert.equal(details.lease_takeover_source, undefined);
    const resumed = {...original, event_id: "takeover-event", recorded_at: source.observed_at, details};
    validateHeartbeatTakeoverParent(resumed, original, identity);
    for (const mutate of [
      (s: typeof source) => {s.leases[0]!.owner = "competing-holder";},
      (s: typeof source) => {s.leases[0]!.status = "released";},
      (s: typeof source) => {s.leases[0]!.expires_at = s.observed_at;},
      (s: typeof source) => {s.leases[0]!.acquired_at = "2025-12-31T23:59:59Z";},
      (s: typeof source) => {s.takeover_basis.todo.status = "done";},
      (s: typeof source) => {s.takeover_basis.todo.claimed_by = "other-agent";},
      (s: typeof source) => {s.decision_read_from_provider = false;},
      (s: typeof source) => {s.legacy_fallback_used = true;},
      (s: typeof source) => {s.provider_revision = "";},
      (s: typeof source) => {s.leases.push({...s.leases[0]!});},
    ]) {
      const invalid = structuredClone(source); mutate(invalid);
      assert.throws(() => heartbeatWorkRequalification(original,
        {...current, lease_takeover_source: invalid}, identity, "open", false), /original task lease generation/);
    }
    for (const field of ["takeover_owner", "takeover_original_event_id", "takeover_todo_sha256", "takeover_acceptance_sha256", "takeover_expires_at"]) {
      const corrupted = structuredClone(resumed);
      corrupted.details[field] = "invalid";
      assert.throws(() => validateHeartbeatTakeoverParent(corrupted, original, identity), /lease takeover/);
    }
    assert.throws(() => validateHeartbeatTakeoverParent({...resumed,
      details: {...details, quota_spend_source: "changed-source"}}, original, identity), /lease takeover/);
    assert.equal(heartbeatWorkRequalification(original, current, identity, "settlement_pending", false).append, false);
    assert.equal(heartbeatWorkRequalification(original, current, identity, "open", true).append, false);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("completed lease takeover binds verified retired generation and rejects tampered or foreign sources", async () => {
  const {root, identity, event, log, request} = await fixture();
  try {
    const originalKey = "original-claim";
    const original = {...event, recorded_at: "2026-01-01T00:00:00Z", details: {...event.details,
      closeout_required: true, prepared_lease_required: true,
      prepared_lease_key_sha256: createHash("sha256").update(originalKey).digest("hex"),
      prepared_lease_version: 1, prepared_lease_epoch: 1}};
    const completionEvent = {schema_version: "loopx_rollout_event_v0", event_id: "event-complete",
      event_kind: "todo_complete", goal_id: goalId, agent_id: agentId, todo_id: todoId, run_id: turnId,
      status: "done", details: {settlement_effect_id: identity.effect_id}};
    const source = {goal_id: goalId, todo_id: todoId, handoff_mode: "hard_lease",
      source_authority: "sqlite_v0", provider_revision: "sqlite:fixture:4",
      decision_read_from_provider: true, legacy_fallback_used: false,
      // Even hours after the retired lease's TTL window elapsed, updated_at <= expires_at holds.
      observed_at: "2026-01-01T08:00:00Z", takeover_basis: {
        acceptance_sha256: "b".repeat(64), todo: {todo_id: todoId, role: "agent", task_class: "advancement_task",
          status: "done", archive_state: "active", done: true, claimed_by: agentId,
          completion_turn_key: identity.effect_id}},
      leases: [{todo_id: todoId, owner: agentId, status: "released", idempotency_key: "replacement-claim",
        version: 2, lease_epoch: 2, acquired_at: "2026-01-01T01:00:00Z",
        updated_at: "2026-01-01T01:30:00Z", expires_at: "2026-01-01T04:00:00Z"} as JsonObject]};
    const guardDetails = {todo_id: todoId, settlement_effect_id: identity.effect_id,
      lease_takeover_source: source};

    // Cold readback through settlement_readback passes completionEvent only while accounting has not started.
    // Terminal completion retires the lease via updated_at without a separate released_at field.
    await writeFile(log, [original, completionEvent].map(row => JSON.stringify(row)).join("\n") + "\n");
    const readback = await readQuotaSettlement({...request, heartbeat_reentry_guard: guardDetails});
    assert.equal(readback.replay_phase, "settlement_pending");
    const qualification = readback.heartbeat_reentry_qualification;
    assert.equal(qualification.append, true);
    const details = qualification.details as JsonObject;
    assert.equal(details.settlement_receipt_revision, "completed_lease_takeover");
    assert.equal(details.takeover_original_event_id, original.event_id);
    assert.equal(details.takeover_original_epoch, 1);
    assert.equal(details.prepared_lease_epoch, 2);
    assert.equal(details.prepared_lease_version, 2);
    assert.equal(details.prepared_lease_key_sha256, createHash("sha256").update("replacement-claim").digest("hex"));
    assert.equal(details.takeover_completion_event_id, completionEvent.event_id);
    assert.equal(details.takeover_released_at, "2026-01-01T01:30:00Z");
    assert.equal(details.lease_takeover_source, undefined);

    // Appending the completed takeover receipt validates cleanly on cold readback and replays idempotently.
    const takeoverEvent = {...original, event_id: "completed-takeover-event",
      recorded_at: source.observed_at, causality: {source_event_id: original.event_id, caused_by: original.event_id},
      details};
    validateHeartbeatTakeoverParent(takeoverEvent, original, identity);
    await appendFile(log, JSON.stringify(takeoverEvent) + "\n");
    const replayed = await readQuotaSettlement({...request, heartbeat_reentry_guard: guardDetails});
    assert.equal(replayed.heartbeat_receipt.event_id, "completed-takeover-event");
    assert.deepEqual(replayed.heartbeat_reentry_qualification, {append: false, details: null});

    // Cold readback rejects a completed_lease_takeover whose completion event id does not match rollout history.
    const wrongCompletionEvent = structuredClone(takeoverEvent);
    wrongCompletionEvent.details.takeover_completion_event_id = "event-other";
    await writeFile(log, [original, completionEvent, wrongCompletionEvent].map(row => JSON.stringify(row)).join("\n") + "\n");
    await assert.rejects(readQuotaSettlement(request), /completed lease takeover completion receipt mismatch/);

    // Once accounting (refresh_state) exists, completed takeover is disabled.
    const writebackEvent = {...event, event_id: "event-writeback", event_kind: "refresh_state",
      details: {settlement_effect_id: identity.effect_id}};
    await writeFile(log, [original, completionEvent, writebackEvent].map(row => JSON.stringify(row)).join("\n") + "\n");
    const afterWriteback = await readQuotaSettlement({...request, heartbeat_reentry_guard: guardDetails});
    assert.equal(afterWriteback.heartbeat_reentry_qualification.append, false);

    // Same-generation retired lease requires no takeover (idempotent no-op).
    const sameGenSource = structuredClone(source);
    sameGenSource.leases[0] = {...sameGenSource.leases[0]!, idempotency_key: originalKey, version: 1, lease_epoch: 1};
    assert.deepEqual(
      heartbeatWorkRequalification(original, {...guardDetails, lease_takeover_source: sameGenSource},
        identity, "settlement_pending", true, completionEvent),
      {append: false, details: null},
    );

    // Unprepared Turn (missing prepared_lease) cannot gain admission through completed takeover.
    const unprepared = {...event, recorded_at: "2026-01-01T00:00:00Z", details: {...event.details,
      closeout_required: true, prepared_lease_required: true}};
    assert.throws(() => heartbeatWorkRequalification(unprepared, guardDetails,
      identity, "settlement_pending", true, completionEvent), /original task lease generation/);

    // Any invalid, foreign, open, or tampered source fails closed.
    for (const mutate of [
      (s: typeof source) => {s.leases[0]!.owner = "competing-holder";},
      (s: typeof source) => {s.leases[0]!.status = "active";},
      (s: typeof source) => {s.leases[0]!.released_at = "2026-01-01T04:00:01Z";},
      (s: typeof source) => {s.leases[0]!.released_at = null;},
      (s: typeof source) => {s.leases[0]!.updated_at = "2026-01-01T04:00:01Z";},
      (s: typeof source) => {s.leases[0]!.acquired_at = "2025-12-31T23:59:59Z";},
      (s: typeof source) => {s.takeover_basis.todo.status = "open"; s.takeover_basis.todo.done = false;},
      (s: typeof source) => {s.takeover_basis.todo.status = "blocked"; s.takeover_basis.todo.done = false;},
      (s: typeof source) => {s.takeover_basis.todo.completion_turn_key = "foreign-turn-effect";},
      (s: typeof source) => {s.takeover_basis.todo.claimed_by = "other-agent";},
      (s: typeof source) => {s.decision_read_from_provider = false;},
      (s: typeof source) => {s.legacy_fallback_used = true;},
      (s: typeof source) => {s.provider_revision = "";},
      (s: typeof source) => {s.leases.push({...s.leases[0]!});},
    ]) {
      const invalid = structuredClone(source); mutate(invalid);
      assert.throws(() => heartbeatWorkRequalification(original,
        {...guardDetails, lease_takeover_source: invalid}, identity, "settlement_pending", true, completionEvent),
        /original task lease generation/);
    }
    for (const [field, value] of [
      ["takeover_owner", "invalid"],
      ["takeover_original_event_id", "invalid"],
      ["takeover_todo_sha256", "invalid"],
      ["takeover_acceptance_sha256", "invalid"],
      ["takeover_released_at", "invalid"],
      ["takeover_expires_at", "invalid"],
      ["takeover_completion_event_id", ""],
    ] as const) {
      const corrupted = structuredClone(takeoverEvent);
      corrupted.details[field] = value;
      assert.throws(() => validateHeartbeatTakeoverParent(corrupted, original, identity), /lease takeover/);
    }
    assert.throws(() => validateHeartbeatTakeoverParent({...takeoverEvent,
      details: {...details, quota_spend_source: "changed-source"}}, original, identity), /lease takeover/);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("work requalification fails closed on binding and semantic guard conflicts", async () => {
  const {root, identity, event} = await fixture();
  try {
    const original = {...event, details: {...event.details, delivery_allowed: false,
      semantic_replan_obligation_id: "replan-0000000000000001", quota_spend_source: "visible-goal"}};
    for (const changed of [
      {todo_id: "todo_other_validation"}, {settlement_effect_id: "different-effect"},
      {replan_obligation_id: "replan-0000000000000002"},
      {semantic_replan_obligation_id: "replan-0000000000000002"},
      {quota_spend_source: "heartbeat"}, {quota_spend_source: "unexpected"},
    ]) {
      assert.throws(() => heartbeatWorkRequalification(original, {...event.details, ...changed},
        identity, "open", false), /binding|guard|conflicting|spend source/);
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
