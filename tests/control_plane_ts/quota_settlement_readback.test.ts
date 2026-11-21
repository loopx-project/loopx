import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  appendFile,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

import { settlementIdentity, settlementIdentityPayload, type JsonObject } from "../../loopx/control_plane/effect_program.ts";
import {
  createEffectRuntimeHandlers,
  dispatchEffectRuntimeMethod,
} from "../../loopx/control_plane/effect_runtime_handlers.ts";
import {
  acquireFileMutationLock,
  releaseFileMutationLock,
} from "../../loopx/control_plane/effect_runtime_io.ts";
import { BLOCKED_WAIT_REQUEST_SCHEMA, prepareBlockedWait } from "../../loopx/control_plane/quota/blocked_wait.ts";
import { evaluateTodoResumeConditions, TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION } from "../../loopx/control_plane/todos/resume_condition.ts";
import {
  projectSemanticReplanGuard,
  projectNativeTaskLeaseCleanup,
  NATIVE_TASK_LEASE_CLEANUP_REQUEST_SCHEMA,
  NATIVE_TASK_LEASE_CLEANUP_SCHEMA,
  QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
  readQuotaSettlement,
  readQuotaSettlementFromSnapshot,
  readQuotaSettlementSnapshot,
} from "../../loopx/control_plane/quota/settlement_readback.ts";
import {
  PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA,
  preflightPriorHostTurnCloseout,
} from "../../loopx/control_plane/quota/unsettled_host_turn_recovery.ts";
import {heartbeatSemanticAdmissionEvent} from "../../loopx/control_plane/quota/heartbeat_receipt_identity.ts";
import {QUOTA_NATIVE_ADMISSION_SCHEMA} from "../../loopx/control_plane/quota/source_admission.ts";
import {shadowMaintenanceLockPath} from "../../loopx/control_plane/coordination/shadow_management.ts";
import {buildVisionCheckpoint, VISION_REFRESH_REQUEST_SCHEMA, visionAuthoringContract}
  from "../../loopx/control_plane/goals/vision_checkpoint.ts";
import {decodeRefreshRetry, refreshRecovery} from "../../loopx/control_plane/quota/refresh_recovery.ts";
import { requireJsonObject } from "../../loopx/control_plane/runtime_decode.ts";
import {
  AUTONOMOUS_REPLAN_DEFAULT_STOP_CONDITION,
  AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID,
  AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION,
} from "../../loopx/control_plane/work_items/replan_semantics.ts";

const goalId = "settlement-goal";
const agentId = "codex-settlement";
const todoId = "todo_settlement";
const turnId = "turn-settlement-1";
const instanceA = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const instanceB = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const identity = settlementIdentity({
  goal_id: goalId,
  agent_id: agentId,
  todo_id: todoId,
  turn_instance_id: turnId,
});
const cleanupRequest = {schema_version: NATIVE_TASK_LEASE_CLEANUP_REQUEST_SCHEMA,
  recovery: false, admission_retry: false, resume_turn_key_present: false, original_journal_present: false};

test("native cleanup distinguishes proven release, owed closeout and unconfirmed receipt authority", () => {
  const bound = (details: JsonObject, phase = "open"): JsonObject => ({found: true,
    identity: {result: {value: settlementIdentityPayload(identity), failure: null}},
    heartbeat_receipt: {details}, replay_phase: phase});
  const failed = (kind: string): JsonObject => ({found: true,
    identity: {result: {value: null, failure: {kind}}}, replay_phase: null});
  const result = (disposition: string, reason: string) => ({schema_version: NATIVE_TASK_LEASE_CLEANUP_SCHEMA,
    disposition, reason});
  assert.deepEqual(projectNativeTaskLeaseCleanup(failed("receipt_missing"), cleanupRequest),
    result("release", "fresh_pre_admission_missing"));
  for (const flag of ["recovery", "admission_retry", "resume_turn_key_present", "original_journal_present"]) {
    assert.deepEqual(projectNativeTaskLeaseCleanup(failed("receipt_missing"), {...cleanupRequest, [flag]: true}),
      result("unconfirmed", "original_identity_unconfirmed"));
  }
  assert.deepEqual(projectNativeTaskLeaseCleanup(failed("receipt_unbound"), {...cleanupRequest, recovery: true}),
    result("release", "receipt_unbound"));
  for (const details of [{settlement_receipt_revision: "turn_reservation"},
    {settlement_receipt_revision: "turn_reservation", closeout_required: false}]) {
    assert.deepEqual(projectNativeTaskLeaseCleanup(bound(details), cleanupRequest),
      result("release", "original_reservation_without_closeout"));
  }
  for (const revision of ["work_admission", "lease_readiness", "identity_upgrade"]) {
    assert.deepEqual(projectNativeTaskLeaseCleanup(bound({settlement_receipt_revision: revision,
      closeout_required: true}), cleanupRequest), result("retain", "original_turn_owes_closeout"));
    for (const required of [undefined, false, null, "true", 1]) {
      assert.equal(projectNativeTaskLeaseCleanup(bound({settlement_receipt_revision: revision,
        closeout_required: required}), cleanupRequest).disposition, "unconfirmed");
    }
  }
  for (const details of [{closeout_required: true}, {closeout_required: false},
    {settlement_receipt_revision: "unknown", closeout_required: true},
    ...[true, null, "false", 0].map(required => ({settlement_receipt_revision: "turn_reservation", closeout_required: required}))]) {
    assert.equal(projectNativeTaskLeaseCleanup(bound(details), cleanupRequest).disposition, "unconfirmed");
  }
  assert.deepEqual(projectNativeTaskLeaseCleanup(bound({}, "settled"), cleanupRequest),
    result("release", "original_turn_settled"));
  assert.equal(projectNativeTaskLeaseCleanup({...failed("identity_mismatch"), replay_phase: "settled"}, cleanupRequest)
    .disposition, "unconfirmed");
  assert.equal(projectNativeTaskLeaseCleanup({found: false}, cleanupRequest).disposition, "unconfirmed");
  assert.throws(() => projectNativeTaskLeaseCleanup(bound({}), {...cleanupRequest, original_journal_present: null}));
  assert.throws(() => projectNativeTaskLeaseCleanup(bound({}), {...cleanupRequest, closeout_required: false}));
});

test("native cleanup attachment preserves B3 original proof and borrowed Source ownership", async () => {
  for (const exact of [false, true]) {
    const root = await fixture(exact ? {goalRef: {goal_id: goalId, goal_instance_id: instanceA}} : {});
    try {
      const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
      const reservation = JSON.parse((await readFile(path, "utf8")).trim());
      reservation.details = {...reservation.details, closeout_required: false};
      const original = {...reservation, event_id: "cleanup-admitted", status: "normal_run",
        details: {...reservation.details, settlement_receipt_revision: "work_admission", closeout_required: true,
          prepared_lease_required: true, prepared_lease_key_sha256: "a".repeat(64),
          prepared_lease_version: 119, prepared_lease_epoch: 50,
          ok: true, should_run: true, must_attempt_work: true, delivery_allowed: true, quiet_noop_allowed: false},
        causality: {source_event_id: reservation.event_id, caused_by: reservation.event_id}};
      await writeFile(path, [reservation, original].map(row => JSON.stringify(row)).join("\n") + "\n");
      const before = await readFile(path);
      const inspect = async (binding: Record<string, unknown> = {}) => {
        const ordinaryRequest = request(root, {...binding, ...(exact ? {borrow_source_admission: true} : {})});
        const ordinary = await readQuotaSettlement(ordinaryRequest);
        assert.equal(Object.hasOwn(ordinary, "native_task_lease_cleanup"), false);
        const requested = await readQuotaSettlement({...ordinaryRequest, native_task_lease_cleanup: cleanupRequest});
        assert.deepEqual(requested.native_task_lease_cleanup, {schema_version: NATIVE_TASK_LEASE_CLEANUP_SCHEMA,
          disposition: "retain", reason: "original_turn_owes_closeout"});
        const rest = {...requested}; delete rest.native_task_lease_cleanup;
        assert.deepEqual(rest, ordinary);
        assert.deepEqual(requested.original_prepared_receipt, original);
        assert.equal(requested.original_prepared_event_id, original.event_id);
        assert.deepEqual(await readQuotaSettlement(ordinaryRequest), ordinary);
      };
      if (exact) await withSourceAdmission(root, instanceA, instanceA, inspect);
      else {
        await inspect();
        const snapshot = await readQuotaSettlementSnapshot(root, goalId);
        const projected = readQuotaSettlementFromSnapshot({...request(root), native_task_lease_cleanup: cleanupRequest}, snapshot);
        assert.equal((projected.native_task_lease_cleanup as JsonObject).disposition, "retain");
      }
      assert.deepEqual(await readFile(path), before);
    } finally {await rm(root, {recursive: true, force: true});}
  }
});

test("native cleanup is an explicit read and cannot mix with resume or settlement admission", async () => {
  const root = await fixture({guard: false});
  try {
    const snapshot = await readQuotaSettlementSnapshot(root, goalId);
    const requested = readQuotaSettlementFromSnapshot({...request(root), native_task_lease_cleanup: cleanupRequest}, snapshot);
    assert.deepEqual(requested.native_task_lease_cleanup, {schema_version: NATIVE_TASK_LEASE_CLEANUP_SCHEMA,
      disposition: "release", reason: "fresh_pre_admission_missing"});
    for (const incompatible of [{infer_turn_instance_id: true}, {allow_unbound_binding: true},
      {resolve_original_binding: true}, {pending_closeout: true}, {refresh_retry: {}},
      {agent_id: null}, {todo_id: null}, {turn_instance_id: null},
      {replan_obligation_id: "replan-0000000000000001"}, {heartbeat_reentry_guard: {}},
      {spend_precheck: true}, {tolerate_malformed_history: true},
      {resume_lease_candidate: {requested_proof: null}}]) {
      assert.throws(() => readQuotaSettlementFromSnapshot({...request(root), ...incompatible,
        native_task_lease_cleanup: cleanupRequest}, snapshot));
    }
  } finally {await rm(root, {recursive: true, force: true});}
  const settledRoot = await fixture({writeback: true, spend: true, completion: true});
  try {
    const compact = await readQuotaSettlement({...request(settledRoot), include_record_bodies: false,
      native_task_lease_cleanup: cleanupRequest});
    assert.equal(compact.heartbeat_receipt, null);
    assert.deepEqual(compact.native_task_lease_cleanup, {schema_version: NATIVE_TASK_LEASE_CLEANUP_SCHEMA,
      disposition: "release", reason: "original_turn_settled"});
  } finally {await rm(settledRoot, {recursive: true, force: true});}
});

test("semantic replan guard distinguishes legacy, none, and exact selection", () => {
  assert.deepEqual(projectSemanticReplanGuard({}), {
    schema_version: "semantic_replan_guard_v0",
    scope: "legacy_unscoped",
    selected_obligation_id: null,
  });
  assert.deepEqual(projectSemanticReplanGuard({
    semantic_replan_obligation_id: "",
  }), {
    schema_version: "semantic_replan_guard_v0",
    scope: "turn_guard",
    selected_obligation_id: null,
  });
  assert.deepEqual(projectSemanticReplanGuard({
    semantic_replan_obligation_id: "replan-0000000000000001",
  }), {
    schema_version: "semantic_replan_guard_v0",
    scope: "turn_guard",
    selected_obligation_id: "replan-0000000000000001",
  });
  assert.throws(
    () => projectSemanticReplanGuard({ semantic_replan_obligation_id: "bad" }),
    /semantic replan guard is malformed/,
  );
});

test("capability guard survives scalar receipt transport and rejects missing scope facts", () => {
  const details = {semantic_replan_obligation_id: "replan-0000000000000001",
    semantic_replan_capability_id: "explore", semantic_replan_gap_id: "research-composition-0123456789abcdef",
    semantic_replan_frontier_revision: "research-composition-v0:0123456789abcdef"};
  assert.deepEqual(projectSemanticReplanGuard(details).selected_capability_guard, {
    schema_version: "semantic_replan_capability_guard_v0", capability_id: "explore",
    gap_id: details.semantic_replan_gap_id, frontier_revision: details.semantic_replan_frontier_revision,
  });
  assert.throws(() => projectSemanticReplanGuard({...details, semantic_replan_obligation_id: ""}), /selected obligation/);
  assert.throws(() => projectSemanticReplanGuard({...details, semantic_replan_gap_id: ""}), /capability guard is malformed/);
  assert.throws(() => projectSemanticReplanGuard({...details, semantic_replan_frontier_revision: undefined}), /capability guard is malformed/);
});

test("recorded review source retains its stop clause under the exact selected guard", () => {
  const details = {semantic_replan_obligation_id: "replan-0000000000000001",
    semantic_replan_review_kind: "goal_outcome_uncovered",
    semantic_replan_review_stop_condition: "Stop at owner-only decisions."};
  assert.deepEqual(projectSemanticReplanGuard(details).selected_review_source, {
    kind: details.semantic_replan_review_kind, stop_condition: details.semantic_replan_review_stop_condition});
  const digest = createHash("sha256").update(details.semantic_replan_review_stop_condition, "utf8").digest("hex");
  assert.deepEqual(projectSemanticReplanGuard({semantic_replan_obligation_id: details.semantic_replan_obligation_id,
    semantic_replan_review_kind: details.semantic_replan_review_kind,
    semantic_replan_review_stop_condition_sha256: digest}).selected_review_source, {
    kind: details.semantic_replan_review_kind, stop_condition_sha256: digest});
  assert.deepEqual(projectSemanticReplanGuard({...details,
    semantic_replan_review_stop_condition_sha256: digest}).selected_review_source, {
    kind: details.semantic_replan_review_kind,
    stop_condition: details.semantic_replan_review_stop_condition,
    stop_condition_sha256: digest});
  const defaultDigest = createHash("sha256").update(
    AUTONOMOUS_REPLAN_DEFAULT_STOP_CONDITION, "utf8").digest("hex");
  assert.deepEqual(projectSemanticReplanGuard({
    semantic_replan_obligation_id: details.semantic_replan_obligation_id,
    semantic_replan_review_kind: "periodic_review_due",
    semantic_replan_review_stop_condition: AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION,
    semantic_replan_review_stop_condition_sha256: defaultDigest,
    semantic_replan_review_stop_contract_id: AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID,
  }).selected_review_source, {
    kind: "periodic_review_due",
    stop_condition: AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION,
    stop_condition_sha256: defaultDigest,
    stop_contract_id: AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID,
  });
  for (const patch of [{semantic_replan_obligation_id: ""},
    {semantic_replan_obligation_id: undefined}, {semantic_replan_review_kind: undefined},
    {semantic_replan_review_stop_condition: undefined}, {semantic_replan_review_stop_condition: "s".repeat(241)},
    {semantic_replan_review_stop_condition_sha256: "bad"},
    {semantic_replan_review_stop_condition_sha256: "0".repeat(64)},
    {semantic_replan_review_stop_contract_id: AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID},
    {semantic_replan_capability_id: "explore"}]) {
    assert.throws(() => projectSemanticReplanGuard({...details, ...patch}));
  }
});

test("capability retirement verifies its exact receipt and never erases a committed debit", async () => {
  const obligation = "replan-0000000000000001";
  const guard = {schema_version: "semantic_replan_capability_guard_v0", capability_id: "explore",
    gap_id: "research-composition-0123456789abcdef", frontier_revision: "research-composition-v0:0123456789abcdef"};
  const bound = settlementIdentity({goal_id: goalId, agent_id: agentId, todo_id: null,
    replan_obligation_id: obligation, turn_instance_id: turnId});
  const progress = {schema_version: "typed_progress_observation_v0", work_item_id: obligation,
    result_class: "blocked", blocker_id: "capability-invalidated-fixture",
    evidence_ids: ["capability-evidence-v0:fedcba9876543210"], fingerprint: "retirement-progress"};
  const retirement = {schema_version: "capability_obligation_retirement_v0", disposition: "invalidated",
    reason_code: "source_ineligible", capability_id: "explore", obligation_id: obligation,
    original_guard: guard, current_revision: progress.evidence_ids[0], blocking_todo_count: 0,
    blocking_todo_ids: [], progress_observation: progress, progress_fingerprint: progress.fingerprint};
  const ack = {recorded: true, source: "refresh_state_semantic_delta", semantic_delta: {
    schema_version: "replan_semantic_delta_v0", accepted: true, obligation_id: obligation,
    outcomes: ["capability_duty_retired"], satisfying_outcomes: ["capability_duty_retired"], capability_guard: guard, retirement}};
  const root = await fixture({writeback: true});
  try {
    const index = join(root, "goals", goalId, "runs", "index.jsonl");
    const log = join(root, "goals", goalId, "rollout-event-log.jsonl");
    const events = (await readFile(log, "utf8")).trim().split("\n").map(line => JSON.parse(line));
    for (const event of events) {
      event.details = {...event.details, todo_id: "", replan_obligation_id: obligation, settlement_effect_id: bound.effect_id};
      if (event.event_kind === "quota_should_run") Object.assign(event.details, {
        semantic_replan_obligation_id: obligation, semantic_replan_capability_id: "explore",
        semantic_replan_gap_id: guard.gap_id, semantic_replan_frontier_revision: guard.frontier_revision});
    }
    await writeFile(log, events.map(event => JSON.stringify(event)).join("\n") + "\n");
    const base = {classification: "state_refreshed", goal_id: goalId, agent_id: agentId,
      turn_instance_id: turnId, replan_obligation_id: obligation, settlement_identity: bound,
      delivery_outcome: "outcome_gap", progress_observation: progress, autonomous_replan_ack: ack};
    const read = () => readQuotaSettlement(request(root, {todo_id: null, replan_obligation_id: obligation}));
    await writeFile(index, JSON.stringify(base) + "\n");
    const accepted = await read();
    assert.equal((accepted.progress as any).closeout_kind, "capability_duty_retired_no_spend");
    assert.equal((accepted.progress as any).no_spend, true);
    assert.equal(accepted.replay_phase, "settled");
    assert.equal((accepted.spend as any).payload.ok, false);
    assert.equal((accepted.terminal_closeout as any).payload.ok, false);
    for (const patch of [{obligation_id: "replan-0000000000000002"}, {blocking_todo_count: 1},
      {original_guard: {...guard, frontier_revision: "research-composition-v0:fedcba9876543210"}},
      {current_revision: "unrelated"}, {progress_fingerprint: "forged"}]) {
      const changed = {...base, autonomous_replan_ack: {...ack, semantic_delta: {...ack.semantic_delta,
        retirement: {...retirement, ...patch}}}};
      await writeFile(index, JSON.stringify(changed) + "\n");
      assert.equal((await read()).replay_phase, "settlement_pending");
    }
    await writeFile(index, JSON.stringify(base) + "\n" + JSON.stringify({classification: "quota_slot_spent",
      goal_id: goalId, agent_id: agentId, turn_instance_id: turnId, replan_obligation_id: obligation,
      settlement_identity: bound}) + "\n");
    await appendFile(log, JSON.stringify({schema_version: "loopx_rollout_event_v0", event_id: "spent-before-retirement",
      event_kind: "quota_spend", goal_id: goalId, agent_id: agentId, run_id: turnId,
      details: {settlement_effect_id: bound.effect_id}}) + "\n");
    const spent = await read();
    assert.equal((spent.progress as any).state, "settled");
    assert.equal((spent.progress as any).closeout_kind, undefined);
    assert.equal((spent.progress as any).no_spend, undefined);
    assert.equal((spent.spend as any).payload.ok, true);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("a service-qualified blocked replan closes only its exact Turn without debit or completion", async () => {
  const obligation = "replan-0000000000000001";
  const bound = settlementIdentity({goal_id: goalId, agent_id: agentId, todo_id: null,
    replan_obligation_id: obligation, turn_instance_id: turnId});
  const progress = {schema_version: "typed_progress_observation_v0", work_item_id: obligation,
    result_class: "blocked", blocker_id: "source-not-ready", evidence_ids: ["evidence:source-wait"],
    fingerprint: "blocked-source-wait"};
  const semantic = {schema_version: "replan_semantic_delta_v0", accepted: true,
    obligation_id: obligation, observation_fingerprint: progress.fingerprint,
    outcomes: ["new_concrete_blocker", "fresh_vision_path_outcome"],
    satisfying_outcomes: ["new_concrete_blocker"]};
  const ack = {schema_version: "autonomous_replan_ack_v0", recorded: true, semantic_delta: semantic};
  const checkpoint = {schema_version: "vision_checkpoint_v0", agent_id: agentId, satisfied: true,
    delivery_boundary: "semantic_closeout", decision: "patched",
    triggers: [{kind: "material_delivery_outcome", delivery_outcome: "outcome_gap"}]};
  const root = await fixture({writeback: true});
  try {
    const index = join(root, "goals", goalId, "runs", "index.jsonl");
    const log = join(root, "goals", goalId, "rollout-event-log.jsonl");
    const events = (await readFile(log, "utf8")).trim().split("\n").map(line => JSON.parse(line));
    for (const event of events) {
      event.details = {...event.details, todo_id: "", replan_obligation_id: obligation,
        settlement_effect_id: bound.effect_id};
      if (event.event_kind === "quota_should_run") event.details.semantic_replan_obligation_id = obligation;
    }
    await writeFile(log, events.map(event => JSON.stringify(event)).join("\n") + "\n");
    const base = {classification: "state_refreshed", goal_id: goalId, agent_id: agentId,
      turn_instance_id: turnId, replan_obligation_id: obligation, settlement_identity: bound,
      delivery_outcome: "outcome_gap", progress_observation: progress,
      autonomous_replan_ack: ack, vision_checkpoint: checkpoint};
    const read = () => readQuotaSettlement(request(root, {todo_id: null, replan_obligation_id: obligation}));
    await writeFile(index, JSON.stringify(base) + "\n");
    const accepted = await read();
    assert.equal(accepted.replay_phase, "settled");
    assert.equal((accepted.progress as any).closeout_kind, "typed_blocked_writeback_no_spend");
    assert.equal((accepted.progress as any).no_spend, true);
    assert.equal((accepted.spend as any).payload.ok, false);
    assert.equal((accepted.terminal_closeout as any).payload.ok, false);
    const history = async (runs: unknown[]) => (await readQuotaSettlement({
      schema_version: "loopx_quota_history_qualification_request_v0", runs,
    })).accepted_indexes;
    assert.deepEqual(await history([base, {...base, administrative_blocked_replan: true,
      autonomous_replan_ack: null}]), [0]);
    const compactCheckpoint = {...checkpoint} as Record<string, unknown>;
    delete compactCheckpoint.delivery_boundary;
    assert.deepEqual(await history([{...base, vision_checkpoint: compactCheckpoint}]), [0]);
    await writeFile(index, JSON.stringify({...base, autonomous_replan_ack: {...ack,
      semantic_delta: {...semantic, satisfying_outcomes: ["fresh_vision_path_outcome"]}}}) + "\n");
    assert.equal((await read()).replay_phase, "settled");
    const invalid = [
      {...base, delivery_outcome: "outcome_progress"},
      {...base, agent_id: "other-agent"},
      {...base, turn_instance_id: "other-turn"},
      {...base, autonomous_replan_ack: {...ack, recorded: false}},
      ...[{accepted: false}, {obligation_id: "replan-0000000000000002"},
        {observation_fingerprint: "different-observation"}, {satisfying_outcomes: []},
        {outcomes: []},
        {satisfying_outcomes: ["new_runnable_successor"]}].map(patch => ({...base,
          autonomous_replan_ack: {...ack, semantic_delta: {...semantic, ...patch}}})),
      ...[{work_item_id: "todo_other"}, {result_class: "advanced"}, {evidence_ids: []},
        {blocker_id: null}].map(patch => ({...base, progress_observation: {...progress, ...patch}})),
      ...[{satisfied: false}, {agent_id: "other-agent"}, {delivery_boundary: "in_flight_continuation"},
        {triggers: []}].map(patch => ({...base, vision_checkpoint: {...checkpoint, ...patch}})),
    ];
    for (const changed of invalid) {
      assert.deepEqual(await history([changed]), []);
      await writeFile(index, JSON.stringify(changed) + "\n");
      assert.notEqual((await read()).replay_phase, "settled");
    }
    await writeFile(index, JSON.stringify(base) + "\n");
    await writeFile(log, JSON.stringify(events[0]) + "\n");
    assert.notEqual((await read()).replay_phase, "settled");
    await writeFile(log, events.map(event => JSON.stringify(event)).join("\n") + "\n");
    await appendFile(index, JSON.stringify({classification: "quota_slot_spent", goal_id: goalId,
      agent_id: agentId, turn_instance_id: turnId, replan_obligation_id: obligation,
      settlement_identity: bound}) + "\n");
    await appendFile(log, JSON.stringify({schema_version: "loopx_rollout_event_v0", event_id: "already-paid",
      event_kind: "quota_spend", goal_id: goalId, agent_id: agentId, run_id: turnId,
      details: {settlement_effect_id: bound.effect_id}}) + "\n");
    const paid = await read();
    assert.equal(paid.replay_phase, "settled");
    assert.equal((paid.progress as any).closeout_kind, undefined);
    assert.equal((paid.spend as any).payload.ok, true);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("execution-loss blocked retry without administrative proof cannot certify no-spend", async () => {
  const progress = {schema_version: "typed_progress_observation_v0", work_item_id: todoId,
    result_class: "blocked", blocker_id: "execution-lost", evidence_ids: ["evidence:loss"],
    fingerprint: "a".repeat(16)};
  const closeout = {schema_version: "quota_execution_loss_closeout_v0", effect_id: identity.effect_id,
    todo_id: todoId, source: "turn_settlement", observed_at: "2026-01-01T01:02:00Z",
    original_receipt_event_id: "event-guard", original_prepared: {
      prepared_lease_key_sha256: "a".repeat(64), prepared_lease_version: 1, prepared_lease_epoch: 1},
    current_control_writer: {lease_key_sha256: "b".repeat(64), version: 2, lease_epoch: 2,
      owner: agentId, store_identity: "file:original", provider_revision: "revision-2"},
    observation_fingerprint: progress.fingerprint};
  const root = await fixture({writeback: true, writebackOutcome: "outcome_gap",
    progressObservation: progress, blockedRetry: closeout});
  try {
    await assert.rejects(readQuotaSettlement(request(root)), {
      code: "malformed_settlement_state",
      message: "administrative execution-loss receipt differs from original admission",
    });
  } finally {await rm(root, {recursive: true, force: true});}
});

async function fixture(options: {
  guard?: boolean;
  /**
   * Commit the same-turn guard receipt the way the documented wake order does:
   * the guard runs before a work item is chosen, so the receipt exists for this
   * turn but carries no settlement binding.
   */
  guardUnbound?: boolean;
  /**
   * Commit the guard's own deferred explicit selection for this Turn: the
   * receipt retains the chosen Todo but still carries no settlement binding,
   * because the guard bound the preemption only on argument-less reentry.
   */
  guardDeferred?: boolean;
  writeback?: boolean;
  spend?: boolean;
  completion?: boolean;
  noFollowup?: boolean;
  workspace?: boolean;
  monitor?: boolean;
  writebackOutcome?: string;
  progressObservation?: Record<string, unknown>;
  blockedRetry?: boolean | Record<string, unknown>;
  visionCheckpoint?: Record<string, unknown>;
  autonomousReplanAck?: Record<string, unknown>;
  goalRef?: Record<string, string>;
  turnId?: string;
} = {}) {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-settlement-readback-"));
  const goalRoot = join(runtimeRoot, "goals", goalId);
  const runsRoot = join(goalRoot, "runs");
  const ownerProjection = options.goalRef ? { goal_ref: options.goalRef } : {};
  const fixtureTurnId = options.turnId ?? turnId;
  const fixtureIdentity = settlementIdentity({
    goal_id: goalId,
    agent_id: agentId,
    todo_id: todoId,
    turn_instance_id: fixtureTurnId,
  });
  await mkdir(runsRoot, { recursive: true });
  const events: Record<string, unknown>[] = options.guard === false
    ? []
    : [{
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-guard",
      event_kind: "quota_should_run",
      goal_id: goalId,
      agent_id: agentId,
      run_id: fixtureTurnId,
      ...ownerProjection,
      details: {
        ...(options.guardUnbound
          ? {}
          : {
            todo_id: todoId,
            settlement_effect_id: fixtureIdentity.effect_id,
          }),
        ...(options.workspace
          ? {
            delivery_workspace_causality_schema_version:
              "delivery_workspace_causality_v0",
            delivery_workspace_causality_todo_id: todoId,
            delivery_workspace_requirement: "required",
            delivery_workspace_causality_source: "selected_todo_contract",
            delivery_workspace_causality_reason:
              "declared_repository_or_write_contract",
          }
          : {}),
      },
    }];
  const runs: Record<string, unknown>[] = [];
  if (options.guardDeferred) {
    events.push({
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-guard-deferred",
      event_kind: "quota_should_run",
      goal_id: goalId,
      agent_id: agentId,
      run_id: fixtureTurnId,
      status: "action_selection_deferred",
      ...ownerProjection,
      details: {
        pending_action_selection_todo_id: todoId,
        pending_action_selection_state: "deferred",
        pending_action_selection_reason: "autonomous_replan",
        settlement_effect_id: "",
        todo_id: "",
        replan_obligation_id: "",
      },
    });
  }
  if (options.writeback) {
    events.push({
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-writeback",
      event_kind: "refresh_state",
      goal_id: goalId,
      agent_id: agentId,
      run_id: fixtureTurnId,
      ...ownerProjection,
      details: { settlement_effect_id: fixtureIdentity.effect_id },
    });
    runs.push({
      classification: "state_refreshed",
      delivery_outcome: options.writebackOutcome ?? "outcome_progress",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: fixtureTurnId,
      settlement_identity: fixtureIdentity,
      ...ownerProjection,
      ...(options.visionCheckpoint ? {vision_checkpoint: options.visionCheckpoint} : {}),
      ...(options.autonomousReplanAck ? {autonomous_replan_ack: options.autonomousReplanAck} : {}),
      ...(options.blockedRetry ? {blocked_retry: typeof options.blockedRetry === "object" ? options.blockedRetry : {
        schema_version: "quota_blocked_retry_v0",
        source: "todo",
        todo_id: todoId,
        resume_when: "resume_at:2026-09-24T10:05:00Z",
        observed_at: "2026-09-24T10:00:00Z",
        due_at: "2026-09-24T10:05:00Z",
      }} : {}),
      ...(options.progressObservation
        ? { progress_observation: options.progressObservation }
        : {}),
    });
  }
  if (options.spend) {
    events.push({
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-spend",
      event_kind: "quota_spend",
      goal_id: goalId,
      agent_id: agentId,
      run_id: fixtureTurnId,
      ...ownerProjection,
      details: { settlement_effect_id: fixtureIdentity.effect_id },
    });
    runs.push({
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: fixtureTurnId,
      settlement_identity: fixtureIdentity,
      ...ownerProjection,
    });
  }
  if (options.completion) {
    events.push({
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-completion",
      event_kind: "todo_complete",
      goal_id: goalId,
      agent_id: agentId,
      run_id: fixtureTurnId,
      ...ownerProjection,
      details: {
        settlement_effect_id: fixtureIdentity.effect_id,
        no_followup: options.noFollowup === true,
      },
    });
  }
  if (options.monitor) {
    runs.push({
      classification: "quota_monitor_poll",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: fixtureTurnId,
      material_change: true,
      ...ownerProjection,
      quota_monitor_poll_commit: {
        schema_version: "quota_monitor_poll_commit_receipt_v0",
        effect_id: `quota-monitor-poll:${goalId}:${agentId}:${fixtureTurnId}:todo:${todoId}`,
        request_digest: "fixture",
      },
    });
  }
  await writeFile(
    join(goalRoot, "rollout-event-log.jsonl"),
    `${events.map((event) => JSON.stringify(event)).join("\n")}\n`,
  );
  await writeFile(
    join(runsRoot, "index.jsonl"),
    `${runs.map((run) => JSON.stringify(run)).join("\n")}\n`,
  );
  return runtimeRoot;
}

async function appendHistory(targetRoot: string, sourceRoot: string): Promise<void> {
  const relativePaths = [
    join("goals", goalId, "rollout-event-log.jsonl"),
    join("goals", goalId, "runs", "index.jsonl"),
  ];
  for (const relativePath of relativePaths) {
    await appendFile(
      join(targetRoot, relativePath),
      await readFile(join(sourceRoot, relativePath), "utf8"),
    );
  }
}

function request(runtimeRoot: string, overrides: Record<string, unknown> = {}) {
  return {
    schema_version: QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
    runtime_root: runtimeRoot,
    goal_id: goalId,
    agent_id: agentId,
    todo_id: todoId,
    turn_instance_id: turnId,
    replan_obligation_id: null,
    infer_turn_instance_id: false,
    allow_unbound_binding: false,
    ...overrides,
  };
}

test("pending closeout survives maintenance and reads one original receipt snapshot", async t => {
  for (const entry of [
    {state: "writeback_required"},
    {writeback: true, state: "spend_required"},
    {writeback: true, spend: true, remove: "quota_spend", state: "spend_receipt_required"},
    {writeback: true, spend: true, state: null},
    {monitor: true, state: null},
    {guard: false, state: null},
    {legacy: true, state: null},
  ]) await t.test(entry.state ?? "no debt", async () => {
    const root = await fixture(entry);
    try {
      const log = join(root, "goals", goalId, "rollout-event-log.jsonl");
      const index = join(root, "goals", goalId, "runs", "index.jsonl");
      const events = (await readFile(log, "utf8")).trim().split("\n").filter(Boolean).map(line => JSON.parse(line));
      for (const event of events) if (event.event_kind === "quota_should_run") Object.assign(event.details, {
        closeout_required: entry.legacy !== true, ok: true, should_run: true,
        must_attempt_work: true, delivery_allowed: true, quiet_noop_allowed: false,
      });
      await writeFile(log, events.filter(event => event.event_kind !== entry.remove).map(event => JSON.stringify(event)).join("\n") + "\n");
      await appendFile(index, JSON.stringify({goal_id: goalId, agent_id: agentId,
        classification: "local_upgrade_status_updated", delivery_outcome: "surface_only"}) + "\n");
      const input = request(root, {pending_closeout: true, turn_instance_id: null, todo_id: null});
      const snapshot = await readQuotaSettlementSnapshot(root, goalId);
      const result = readQuotaSettlementFromSnapshot(input, snapshot);
      assert.equal(result.found, entry.state !== null);
      if (entry.state !== null) {
        assert.deepEqual((result.identity as any).result.value, settlementIdentityPayload(identity));
        assert.equal((result.progress as any).state, entry.state);
        const mismatch = readQuotaSettlementFromSnapshot({...input, todo_id: "todo_foreign"}, snapshot);
        assert.equal((mismatch.identity as any).payload.ok, false);
        assert.match((mismatch.identity as any).result.failure.reason, /binding differs/);
      }
      assert.equal(readQuotaSettlementFromSnapshot({...input, agent_id: "another-agent"}, snapshot).found, false);
      assert.deepEqual(await readQuotaSettlement(input), result);
      // Candidate selection and exact readback use the already acquired snapshot.
      await writeFile(log, "corrupt live history\n");
      assert.deepEqual(readQuotaSettlementFromSnapshot(input, snapshot), result);
      await assert.rejects(readQuotaSettlement(input), /malformed/);
    } finally {await rm(root, {recursive: true, force: true});}
  });
});

test("explicit pending recovery requires admitted work or verified partial settlement", async t => {
  const denied = {ok: true, should_run: true, must_attempt_work: false,
    delivery_allowed: false, quiet_noop_allowed: true};
  for (const entry of [
    {name: "claim required", guard: denied, state: null},
    {name: "incomplete modern admission", guard: {ok: true, should_run: true, delivery_allowed: true}, state: null},
    {name: "unknown legacy admission", guard: {}, state: null},
    {name: "verified writeback", guard: denied, writeback: true, state: "spend_required"},
    {name: "verified debit", guard: denied, spend: true, state: "writeback_required"},
  ]) await t.test(entry.name, async () => {
    const root = await fixture({writeback: entry.writeback, spend: entry.spend});
    try {
      const log = join(root, "goals", goalId, "rollout-event-log.jsonl");
      const events = (await readFile(log, "utf8")).trim().split("\n").map(line => JSON.parse(line));
      Object.assign(events[0].details, entry.guard);
      const contents = events.map(event => JSON.stringify(event)).join("\n") + "\n";
      await writeFile(log, contents);
      const input = request(root, {pending_closeout: true});
      const snapshot = await readQuotaSettlementSnapshot(root, goalId);
      const result = readQuotaSettlementFromSnapshot(input, snapshot);
      assert.equal(result.found, entry.state !== null);
      if (entry.state !== null) {
        assert.deepEqual((result.identity as any).result.value, settlementIdentityPayload(identity));
        assert.equal((result.progress as any).state, entry.state);
      }
      // A denied guard must not hide a conflicting caller binding.
      const mismatch = readQuotaSettlementFromSnapshot({...input, todo_id: "todo_foreign"}, snapshot);
      assert.equal((mismatch.identity as any).result.failure.kind, "identity_mismatch");
      assert.equal(await readFile(log, "utf8"), contents);
    } finally {await rm(root, {recursive: true, force: true});}
  });
});

test("pending closeout recovers positively admitted replan work without creating a preflight obligation", async () => {
  const root = await fixture();
  try {
    const log = join(root, "goals", goalId, "rollout-event-log.jsonl");
    const events = (await readFile(log, "utf8")).trim().split("\n").map(line => JSON.parse(line));
    const obligation = "replan-0000000000000001";
    const bound = settlementIdentity({goal_id: goalId, agent_id: agentId, todo_id: null,
      replan_obligation_id: obligation, turn_instance_id: turnId});
    const admitted = {closeout_required: false, replan_obligation_id: obligation,
      semantic_replan_obligation_id: obligation, settlement_effect_id: bound.effect_id,
      ok: true, should_run: true, must_attempt_work: true, delivery_allowed: false,
      quiet_noop_allowed: false, interaction_mode: "autonomous_replan"};
    const writeGuard = async (details: Record<string, unknown>) => {
      events[0].details = details;
      await writeFile(log, events.map(event => JSON.stringify(event)).join("\n") + "\n");
    };
    await writeGuard(admitted);
    const input = request(root, {pending_closeout: true, turn_instance_id: null, todo_id: null});
    for (const query of [input, {...input, replan_obligation_id: obligation},
      {...input, turn_instance_id: turnId},
      {...input, turn_instance_id: turnId, replan_obligation_id: obligation}]) {
      const readback = await readQuotaSettlement(query);
      assert.equal(readback.found, true);
      assert.deepEqual((readback.identity as any).result.value, settlementIdentityPayload(bound));
    }
    assert.equal((await readQuotaSettlement({...input, agent_id: "another-agent"})).found, false);
    const mismatched = await readQuotaSettlement({...input, replan_obligation_id: "replan-0000000000000002"});
    assert.equal((mismatched.identity as any).result.failure.kind, "identity_mismatch");
    const preflight = await preflightPriorHostTurnCloseout({
      ...input, schema_version: PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA,
    });
    assert.equal(preflight.reason, "no_prior_turn_requires_closeout");

    for (const patch of [{should_run: false}, {must_attempt_work: undefined}, {quiet_noop_allowed: true},
      {interaction_mode: "control_plane_self_repair"}, {semantic_replan_obligation_id: "replan-0000000000000002"},
      {settlement_effect_id: undefined}, {replan_obligation_id: undefined, settlement_effect_id: undefined}]) {
      await writeGuard({...admitted, ...patch});
      assert.equal((await readQuotaSettlement(input)).found, false, JSON.stringify(patch));
      const explicit = await readQuotaSettlement({...input, turn_instance_id: turnId});
      assert.ok(explicit.found === false || (explicit.identity as any).payload.ok === false,
        `explicit recovery must not admit ${JSON.stringify(patch)}`);
    }
    await writeGuard({...admitted, settlement_effect_id: "another-effect"});
    const wrongEffect = await readQuotaSettlement(input);
    assert.equal((wrongEffect.identity as any).result.failure.kind, "identity_mismatch");

    for (const patch of [{infer_turn_instance_id: true}, {allow_unbound_binding: true},
      {refresh_retry: {}}, {heartbeat_reentry_guard: {}}, {spend_precheck: true}]) {
      await assert.rejects(readQuotaSettlement({...input, ...patch}), /read-only receipt recovery/);
    }
    await assert.rejects(readQuotaSettlement({...input, pending_closeout: "yes"}), /must be a boolean/);
    await assert.rejects(readQuotaSettlement({...input, agent_id: null}), /explicit Agent/);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("receipt upgrade admission stays on the admitted Goal instance", async () => {
  const currentRef = {goal_id: goalId, goal_instance_id: instanceA};
  const root = await fixture({goalRef: currentRef});
  try {
    const log = join(root, "goals", goalId, "rollout-event-log.jsonl");
    const current = JSON.parse((await readFile(log, "utf8")).trim()) as Record<string, unknown>;
    const details = current.details as Record<string, unknown>;
    const retiredTodo = "todo_retired_instance";
    const retiredIdentity = settlementIdentity({
      goal_id: goalId, agent_id: agentId, todo_id: retiredTodo, turn_instance_id: turnId,
    });
    const retired = {
      ...current, event_id: "event-retired", recorded_at: "2026-01-01T00:00:00Z",
      goal_ref: {goal_id: goalId, goal_instance_id: instanceB},
      details: {todo_id: retiredTodo, settlement_effect_id: retiredIdentity.effect_id},
    };
    const admitted = {
      ...current, event_id: "event-admitted", recorded_at: "2026-02-01T00:00:00Z",
    };
    const upgraded = {
      ...current, event_id: "event-upgraded", recorded_at: "2026-02-01T02:00:00Z",
      causality: {source_event_id: "event-admitted", caused_by: "event-admitted"},
      details: {...details, settlement_receipt_revision: "review_guard_upgrade"},
    };
    await writeFile(log, [retired, admitted, upgraded].map(event => JSON.stringify(event)).join("\n") + "\n");
    await withSourceAdmission(root, instanceA, instanceA, async binding => {
      const result = await readQuotaSettlement(request(root, binding));
      assert.equal(result.found, true);
      assert.equal(result.admission_recorded_at, "2026-02-01T00:00:00Z");
      assert.equal(result.admission_event_id, "event-admitted");
      const receipt = result.heartbeat_receipt as {event_id: string, recorded_at: string};
      assert.equal(receipt.event_id, "event-upgraded");
      assert.equal(receipt.recorded_at, "2026-02-01T02:00:00Z");
    });
    const sameBinding = {
      ...retired,
      details: {todo_id: todoId, settlement_effect_id: identity.effect_id},
    };
    await writeFile(log, [sameBinding, admitted, upgraded].map(event => JSON.stringify(event)).join("\n") + "\n");
    await withSourceAdmission(root, instanceA, instanceA, async binding => {
      const result = await readQuotaSettlement(request(root, binding));
      assert.equal(result.admission_recorded_at, admitted.recorded_at);
      assert.equal(result.admission_event_id, "event-admitted");
    });
  } finally {
    await rm(root, {recursive: true, force: true});
  }
});

test("pending closeout cannot adopt another Goal instance's receipts", async () => {
  const root = await fixture({goalRef: {goal_id: goalId, goal_instance_id: instanceA}});
  try {
    const log = join(root, "goals", goalId, "rollout-event-log.jsonl");
    const event = JSON.parse((await readFile(log, "utf8")).trim());
    event.details.closeout_required = true;
    await writeFile(log, JSON.stringify(event) + "\n");
    for (const turn of [null, turnId]) {
      await withSourceAdmission(root, instanceB, instanceB, async binding => {
        const result = await readQuotaSettlement(request(root, {...binding, pending_closeout: true,
          turn_instance_id: turn, todo_id: null}));
        if (turn === null) assert.equal(result.found, false);
        else assert.equal((result.identity as any).payload.ok, false);
      });
    }
  } finally {await rm(root, {recursive: true, force: true});}
});

function sourceGuardPath(registryPath: string): string {
  const digest = createHash("sha256").update(goalId, "utf8").digest("hex");
  return join(
    dirname(registryPath),
    ".loopx",
    "lifecycle",
    "goal-instance",
    "guards",
    `${digest}.guard`,
  );
}

async function withSourceAdmission<T>(
  runtimeRoot: string,
  plannedInstanceId: string,
  currentInstanceId: string,
  run: (binding: Record<string, unknown>) => Promise<T>,
): Promise<T> {
  const indexPath = join(runtimeRoot, "goals", goalId, "runs", "index.jsonl");
  const registryPath = join(runtimeRoot, "project", ".loopx", "registry.json");
  const guardPath = sourceGuardPath(registryPath);
  const indexLock = await acquireFileMutationLock(indexPath);
  const guardLock = await acquireFileMutationLock(guardPath);
  try {
    return await run({
      goal_ref: {
        goal_id: goalId,
        goal_instance_id: plannedInstanceId,
      },
      source_admission: {
        schema_version: "loopx_quota_source_admission_v0",
        profile_id: "source_session_v1",
        registry_path: registryPath,
        planned_goal_ref: {
          goal_id: goalId,
          goal_instance_id: plannedInstanceId,
        },
        authority: {
          kind: "present",
          goal_ref: {
            goal_id: goalId,
            goal_instance_id: currentInstanceId,
          },
        },
        locks: [
          {
            role: "run_index",
            target: indexPath,
            pid: process.pid,
            token: indexLock.token,
          },
          {
            role: "source_guard",
            target: guardPath,
            pid: process.pid,
            token: guardLock.token,
          },
        ],
      },
    });
  } finally {
    await releaseFileMutationLock(guardPath, guardLock.token, null, true);
    await releaseFileMutationLock(indexPath, indexLock.token, null, true);
  }
}

test("exact Source resume cannot become not_required from missing prepared admission", async () => {
  const root = await fixture({goalRef: {goal_id: goalId, goal_instance_id: instanceA}});
  try {
    const {FileAuthorityStore} = await import("../../loopx/control_plane/coordination/file_authority_store.ts");
    const {projection, todo} = await import("./shadow_file_fixture.ts");
    const head = {...projection([{...todo(todoId), claimed_by: agentId}], [], "soft_claim"), goal_id: goalId};
    const store = new FileAuthorityStore(join(root, "authority", "file-v0"), goalId);
    const seeded = await store.commitAuthority({operation_id: "seed-source-no-prepared",
      expected_provider_revision: null, next_projection: head, events: [], receipts: []});
    assert.equal(seeded.status, "applied", JSON.stringify(seeded));
    await withSourceAdmission(root, instanceA, instanceA, async binding => {
      await assert.rejects(readQuotaSettlement(request(root, {...binding, borrow_source_admission: true,
        resume_lease_candidate: {requested_proof: null}})), {code: "refresh_task_lease_unavailable"});
    });
  } finally {await rm(root, {recursive: true, force: true});}
});

test("scoped supersede settles only its exact Turn and never terminal acceptance", async t => {
  const cases = [
    {name: "original tuple", expected: "settled", patch: {}},
    {name: "missing turn", expected: "open", patch: {run_id: null}},
    {name: "foreign turn", expected: "open", patch: {run_id: "other-turn"}},
    {name: "foreign goal", expected: "open", patch: {goal_id: "other-goal"}},
    {name: "foreign actor", expected: "open", patch: {agent_id: "other-agent"}},
    {name: "foreign Todo", expected: "open", patch: {todo_id: "todo_other"}},
    {name: "failed retirement", expected: "open", patch: {status: "failed"}},
    {name: "missing effect", expected: "open", patch: {details: {}}},
    {name: "foreign effect", expected: "open", patch: {details: {settlement_effect_id: "other-effect"}}},
  ];
  for (const entry of cases) await t.test(entry.name, async () => {
    const root = await fixture({writeback: true, spend: true});
    try {
      const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
      await appendFile(path, JSON.stringify({
        schema_version: "loopx_rollout_event_v0", event_id: "supersede-receipt",
        event_kind: "todo_supersede", status: "done", goal_id: goalId,
        agent_id: agentId, todo_id: todoId, run_id: turnId,
        details: {settlement_effect_id: identity.effect_id}, ...entry.patch,
      }) + "\n");
      const result = await readQuotaSettlement(request(root));
      assert.equal(result.replay_phase, entry.expected);
      assert.equal(result.completion_event, null);
      assert.equal((result.terminal_closeout as {payload: {ok: boolean}}).payload.ok, false);
    } finally { await rm(root, {recursive: true, force: true}); }
  });
});

test("only the tolerant readback route survives a damaged audit row", async () => {
  const root = await fixture({writeback: true, spend: true});
  try {
    const clean = await readQuotaSettlement(request(root));
    const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
    await appendFile(path, Buffer.concat([
      Buffer.from('{"schema_version":"loopx_rollout_event_v0","event_kind":"quota_should_run","goal_id":"'),
      Buffer.from(goalId),
      Buffer.from('","agent_id":"'),
      Buffer.from(agentId),
      Buffer.from('","run_id":"later-turn","summary":"'),
      Buffer.from([0xff]),
      Buffer.from('"}\n'),
    ]));
    await assert.rejects(readQuotaSettlement(request(root)), {
      code: "malformed_settlement_state",
    });
    const tolerant = await readQuotaSettlement(request(root, {
      tolerate_malformed_history: true,
    }));
    assert.deepEqual(tolerant, clean,
      "a damaged row must not change the settlement facts a valid return can read");
  } finally { await rm(root, {recursive: true, force: true}); }
});

test("tolerant settlement readback ignores an unterminated receipt", async () => {
  const root = await fixture();
  try {
    const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
    const committed = await readQuotaSettlement(request(root));
    assert.equal(committed.found, true);
    const committedBytes = await readFile(path);
    assert.ok(committedBytes.at(-1) === 0x0a);
    await writeFile(path, committedBytes.subarray(0, -1));
    const strictUncommitted = await readQuotaSettlement(request(root));
    const tolerantUncommitted = await readQuotaSettlement(request(root, {
      tolerate_malformed_history: true,
    }));
    assert.deepEqual(tolerantUncommitted, strictUncommitted,
      "both reads ignore a receipt whose writer did not reach the newline");
    await appendFile(path, "\n");
    const strictCommitted = await readQuotaSettlement(request(root));
    const tolerantCommitted = await readQuotaSettlement(request(root, {
      tolerate_malformed_history: true,
    }));
    assert.deepEqual(tolerantCommitted, strictCommitted);
    assert.deepEqual(strictCommitted, committed,
      "the ordinary append commit exposes the receipt again");
  } finally { await rm(root, {recursive: true, force: true}); }
});

test("settlement progress requires both effects and exact receipts", async t => {
  const cases = [
    { guard: false, state: "identity_required", next: "validation" },
    { state: "writeback_required", next: "durable_writeback" },
    { writeback: true, remove: "refresh_state", state: "writeback_receipt_required", next: "durable_writeback" },
    { writeback: true, state: "spend_required", next: "quota_spend" },
    { writeback: true, spend: true, remove: "quota_spend", state: "spend_receipt_required", next: "quota_spend" },
    { writeback: true, spend: true, state: "settled", next: null },
  ];
  for (const entry of cases) await t.test(entry.state, async () => {
    const root = await fixture(entry);
    try {
      if (entry.remove) {
        const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
        const events = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
        await writeFile(path, events.filter(event => event.event_kind !== entry.remove).map(event => JSON.stringify(event)).join("\n") + "\n");
      }
      if (entry.state === "settled") {
        const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
        const events = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
        const guard = events.find(event => event.event_kind === "quota_should_run")!;
        guard.status = "normal_run";
        Object.assign(guard.details, {closeout_required: true, prepared_lease_required: true,
          prepared_lease_key_sha256: "a".repeat(64), prepared_lease_version: 119, prepared_lease_epoch: 50,
          ok: true, should_run: true, must_attempt_work: true, delivery_allowed: true, quiet_noop_allowed: false});
        await writeFile(path, events.map(event => JSON.stringify(event)).join("\n") + "\n");
      }
      const result = await readQuotaSettlement(request(root));
      assert.deepEqual(result.progress, {
        schema_version: "quota_settlement_progress_v0", state: entry.state,
        next_step: entry.next, quota_spend_source: "heartbeat",
      });
      assert.deepEqual(await readQuotaSettlement(request(root, {include_record_bodies: true})), result);
      const controls = await readQuotaSettlement(request(root, {include_record_bodies: false}));
      const expected = {...result};
      if (entry.state === "settled") {
        assert.ok(result.writeback_run && result.spend_run && result.heartbeat_receipt);
        assert.ok(result.original_prepared_receipt);
        assert.equal(controls.original_prepared_event_id, "event-guard");
        assert.equal(controls.original_prepared_receipt_sha256, result.original_prepared_receipt_sha256);
        for (const field of ["writeback_run", "spend_run", "heartbeat_receipt", "original_prepared_receipt",
          "writeback_event", "spend_event", "completion_event"]) expected[field] = null;
        for (const field of ["writeback", "spend", "delivery", "settlement"]) {
          const original = result[field] as {result: {value: unknown}};
          assert.ok(original.result.value, "Complete reads retain their original Run body");
          expected[field] = {...original, result: {...original.result, value: null}};
        }
      }
      assert.deepEqual(controls, expected, "Pending recovery and all controls/receipts retain complete parity");
      const precheck = await readQuotaSettlement(request(root, {spend_precheck: true}));
      const settlement = requireJsonObject(result.settlement, "full settlement bundle");
      const outcome = requireJsonObject(settlement.result, "full settlement result");
      assert.deepEqual(precheck, {
        schema_version: result.schema_version,
        found: true,
        identity: result.identity,
        settlement: {...settlement, result: {...outcome, value: null}},
        progress: result.progress,
        spend_run_present: entry.spend === true,
      });
      await assert.rejects(readQuotaSettlement(request(root, {include_record_bodies: "false"})), /must be a boolean/);
    } finally { await rm(root, { recursive: true, force: true }); }
  });
});

test("settlement readback enforces exact source ownership before identity inference", async () => {
  const goalRefA = {
    goal_id: goalId,
    goal_instance_id: instanceA,
  };
  const root = await fixture({
    writeback: true,
    spend: true,
    goalRef: goalRefA,
  });
  try {
    const unscoped = await readQuotaSettlement(request(root));
    assert.equal(unscoped.found, true);
    const unscopedIdentity = requireJsonObject(
      requireJsonObject(unscoped.identity, "unscoped identity").result,
      "unscoped identity result",
    );
    assert.equal(
      requireJsonObject(
        unscopedIdentity.failure,
        "unscoped identity failure",
      ).kind,
      "receipt_missing",
    );
    assert.equal(unscoped.spend_run, null);

    const inferred = await withSourceAdmission(
      root,
      instanceB,
      instanceB,
      async (binding) => await readQuotaSettlement(request(root, {
        ...binding,
        turn_instance_id: null,
        infer_turn_instance_id: true,
      })),
    );
    assert.deepEqual(inferred, {
      schema_version: "loopx_quota_settlement_readback_result_v0",
      found: false,
    });

    await assert.rejects(
      withSourceAdmission(
        root,
        instanceA,
        instanceB,
        async (binding) => await readQuotaSettlement(request(root, binding)),
      ),
      (error: unknown) => {
        assert.equal(
          requireJsonObject(error, "stale GoalRef error").code,
          "stale_goal_instance",
        );
        return true;
      },
    );

    const matching = await withSourceAdmission(
      root,
      instanceA,
      instanceA,
      async (binding) => await readQuotaSettlement(request(root, binding)),
    );
    const matchingSpend = requireJsonObject(matching.spend, "matching spend");
    const matchingPayload = requireJsonObject(
      matchingSpend.payload,
      "matching spend payload",
    );
    assert.equal(matchingPayload.ok, true);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("alias and exact histories remain independently readable", async () => {
  const legacy = await fixture({ writeback: true, spend: true });
  const exact = await fixture({
    writeback: true,
    spend: true,
    goalRef: {
      goal_id: goalId,
      goal_instance_id: instanceA,
    },
  });
  try {
    await appendHistory(legacy, exact);
    const aliasReadback = await readQuotaSettlement(request(legacy));
    assert.equal(
      requireJsonObject(
        requireJsonObject(aliasReadback.settlement, "alias settlement").payload,
        "alias settlement payload",
      ).ok,
      true,
    );
    const exactReadback = await withSourceAdmission(
      legacy,
      instanceA,
      instanceA,
      async (binding) => await readQuotaSettlement(request(legacy, binding)),
    );
    assert.equal(
      requireJsonObject(
        requireJsonObject(exactReadback.settlement, "exact settlement").payload,
        "exact settlement payload",
      ).ok,
      true,
    );
  } finally {
    await rm(legacy, { recursive: true, force: true });
    await rm(exact, { recursive: true, force: true });
  }
});

test("a delayed stale owner cannot replace the current owner's inferred Turn", async () => {
  const current = await fixture({
    writeback: true,
    spend: true,
    goalRef: {
      goal_id: goalId,
      goal_instance_id: instanceB,
    },
  });
  const delayed = await fixture({
    writeback: true,
    spend: true,
    turnId: "turn-stale-a",
    goalRef: {
      goal_id: goalId,
      goal_instance_id: instanceA,
    },
  });
  try {
    await appendHistory(current, delayed);
    const inferred = await withSourceAdmission(
      current,
      instanceB,
      instanceB,
      async (binding) => await readQuotaSettlement(request(current, {
        ...binding,
        turn_instance_id: null,
        infer_turn_instance_id: true,
      })),
    );
    assert.equal(inferred.found, true);
    assert.equal(
      requireJsonObject(
        requireJsonObject(inferred.settlement, "current settlement").payload,
        "current settlement payload",
      ).ok,
      true,
    );
  } finally {
    await rm(current, { recursive: true, force: true });
    await rm(delayed, { recursive: true, force: true });
  }
});

test("borrowed exact admission remains valid for an enclosing transaction", async () => {
  const root = await fixture({
    writeback: true,
    goalRef: {
      goal_id: goalId,
      goal_instance_id: instanceA,
    },
  });
  try {
    await withSourceAdmission(
      root,
      instanceA,
      instanceA,
      async (binding) => {
        const borrowed = {
          ...binding,
          borrow_source_admission: true,
        };
        assert.equal(
          (await readQuotaSettlement(request(root, borrowed))).found,
          true,
        );
        assert.equal(
          (await readQuotaSettlement(request(root, borrowed))).found,
          true,
        );
      },
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("legacy settlement still consumes legacy rows", async () => {
  const root = await fixture({ writeback: true, spend: true });
  try {
    const result = await readQuotaSettlement(request(root));
    assert.equal(result.found, true);
    assert.equal(
      requireJsonObject(
        requireJsonObject(result.settlement, "legacy settlement").payload,
        "legacy settlement payload",
      ).ok,
      true,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("settlement progress preserves typed source and rejects malformed sources", async () => {
  const root = await fixture({writeback: true});
  try {
    const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
    const events = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
    for (const source of ["visible-goal", "unknown", 42]) {
      events[0].details.quota_spend_source = source;
      await writeFile(path, events.map(event => JSON.stringify(event)).join("\n") + "\n");
      if (source === "visible-goal") {
        const result = await readQuotaSettlement(request(root));
        assert.equal((result.progress as Record<string, unknown>).quota_spend_source, source);
      } else {
        await assert.rejects(readQuotaSettlement(request(root)), /settlement spend source is invalid/);
      }
    }
  } finally { await rm(root, {recursive: true, force: true}); }
});

test("accepted in-flight writeback closes only the exact Turn, not its Todo", async t => {
  const checkpoint = {
    schema_version: "vision_checkpoint_v0", agent_id: agentId, satisfied: true,
    delivery_boundary: "in_flight_continuation",
    triggers: [{kind: "in_flight_continuation", todo_id: todoId}],
  };
  const cases = [
    {name: "both effects", spend: true, expected: "settled"},
    {name: "spend still required", spend: false, expected: "settlement_pending"},
    {name: "writeback receipt missing", spend: true, remove: "refresh_state", expected: "open"},
    {name: "spend receipt missing", spend: true, remove: "quota_spend", expected: "settlement_pending"},
  ];
  for (const entry of cases) await t.test(entry.name, async () => {
    const root = await fixture({writeback: true, spend: entry.spend, visionCheckpoint: checkpoint});
    try {
      if (entry.remove) {
        const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
        const events = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
        await writeFile(path, events.filter(event => event.event_kind !== entry.remove).map(event => JSON.stringify(event)).join("\n") + "\n");
      }
      const result = await readQuotaSettlement(request(root));
      assert.equal(result.replay_phase, entry.expected);
      assert.equal(result.completion_event, null);
      assert.equal((result.terminal_closeout as any).payload.ok, false);
    } finally { await rm(root, {recursive: true, force: true}); }
  });
});

test("accepted semantic progress settles its Turn while the Todo stays open", async t => {
  const checkpoint = {
    schema_version: "vision_checkpoint_v0", agent_id: agentId, satisfied: true,
    delivery_boundary: "semantic_closeout",
    triggers: [{kind: "material_delivery_outcome", delivery_outcome: "outcome_progress"}],
  };
  const cases = [
    {name: "paid progress", spend: true, expected: "settled"},
    {name: "spend still required", spend: false, expected: "settlement_pending"},
    {name: "missing writeback receipt", spend: true, remove: "refresh_state", expected: "open"},
    {name: "missing spend receipt", spend: true, remove: "quota_spend", expected: "settlement_pending"},
    ...[
      ["not accepted", {satisfied: false}],
      ["truthy acceptance", {satisfied: "true"}],
      ["wrong schema", {schema_version: "other"}],
      ["other agent", {agent_id: "peer"}],
      ["no outcome trigger", {triggers: []}],
      ["in-flight trigger", {triggers: [{kind: "in_flight_continuation", todo_id: todoId}]}],
      ["mismatched outcome", {triggers: [{kind: "material_delivery_outcome", delivery_outcome: "outcome_gap"}]}],
    ].map(([name, patch]) => ({name: String(name), spend: true, checkpoint: {...checkpoint, ...(patch as Record<string, unknown>)}, expected: "open"})),
    ...["goal_id", "agent_id", "todo_id", "turn_instance_id"].map(field => ({
      name: `other ${field}`, spend: true, patch: {[field]: "other"}, expected: "open",
    })),
    {name: "other effect", spend: true, patch: {settlement_identity: {...identity, effect_id: "other"}}, expected: "open"},
  ];
  for (const entry of cases) await t.test(entry.name, async () => {
    const root = await fixture({writeback: true, spend: entry.spend,
      visionCheckpoint: "checkpoint" in entry ? entry.checkpoint : checkpoint});
    try {
      if ("remove" in entry) {
        const log = join(root, "goals", goalId, "rollout-event-log.jsonl");
        const rows = (await readFile(log, "utf8")).trim().split("\n").map(line => JSON.parse(line));
        await writeFile(log, rows.filter(row => row.event_kind !== entry.remove).map(row => JSON.stringify(row)).join("\n") + "\n");
      }
      if ("patch" in entry) {
        const index = join(root, "goals", goalId, "runs", "index.jsonl");
        const rows = (await readFile(index, "utf8")).trim().split("\n").map(line => JSON.parse(line));
        rows[0] = {...rows[0], ...entry.patch};
        await writeFile(index, rows.map(row => JSON.stringify(row)).join("\n") + "\n");
      }
      const result = await readQuotaSettlement(request(root));
      assert.equal(result.replay_phase, entry.expected);
      assert.equal(result.completion_event, null);
      assert.equal((result.terminal_closeout as any).payload.ok, false);
      if (entry.name === "paid progress") assert.equal((result.progress as any).state, "settled");
    } finally { await rm(root, {recursive: true, force: true}); }
  });
});

test("qualified path replan closes its Turn without a preselected obligation or Todo completion", async t => {
  const accepted = {
    schema_version: "autonomous_replan_ack_v0", recorded: true,
    delta_contract: {schema_version: "repair_delta_contract_v0", delta_present: true},
  };
  const cases = [
    {name: "qualified repair delta", ack: accepted, spend: true, expected: "settled"},
    {name: "qualified semantic delta", ack: {...accepted, delta_contract: null,
      semantic_delta: {schema_version: "replan_semantic_delta_v0", accepted: true}}, spend: true, expected: "settled"},
    {name: "spend still required", ack: accepted, spend: false, expected: "settlement_pending"},
    {name: "missing writeback receipt", ack: accepted, spend: true, remove: "refresh_state", expected: "open"},
    {name: "missing spend receipt", ack: accepted, spend: true, remove: "quota_spend", expected: "settlement_pending"},
    {name: "unrecorded", ack: {...accepted, recorded: false}, spend: true, expected: "open"},
    {name: "truthy is not recorded", ack: {...accepted, recorded: "true"}, spend: true, expected: "open"},
    {name: "wrong ACK schema", ack: {...accepted, schema_version: "other"}, spend: true, expected: "open"},
    {name: "unqualified delta", ack: {...accepted, delta_contract: {schema_version: "repair_delta_contract_v0", delta_present: false}}, spend: true, expected: "open"},
    {name: "unqualified semantic delta", ack: {...accepted, delta_contract: null,
      semantic_delta: {schema_version: "replan_semantic_delta_v0", accepted: false}}, spend: true, expected: "open"},
    {name: "wrong delta schema", ack: {...accepted, delta_contract: {schema_version: "other", delta_present: true}}, spend: true, expected: "open"},
    ...["goal_id", "agent_id", "todo_id", "turn_instance_id"].map(field => ({
      name: `wrong ${field}`, ack: accepted, spend: true, patch: {[field]: "other"}, expected: "open",
    })),
    {name: "wrong effect", ack: accepted, spend: true,
      patch: {settlement_identity: {...identity, effect_id: "other"}}, expected: "open"},
  ];
  for (const entry of cases) await t.test(entry.name, async () => {
    const root = await fixture({writeback: true, spend: entry.spend, autonomousReplanAck: entry.ack});
    try {
      if ("remove" in entry) {
        const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
        const rows = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
        await writeFile(path, rows.filter(row => row.event_kind !== entry.remove).map(row => JSON.stringify(row)).join("\n") + "\n");
      }
      if ("patch" in entry) {
        const path = join(root, "goals", goalId, "runs", "index.jsonl");
        const rows = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
        rows[0] = {...rows[0], ...entry.patch};
        await writeFile(path, rows.map(row => JSON.stringify(row)).join("\n") + "\n");
      }
      const result = await readQuotaSettlement(request(root));
      assert.equal(result.replay_phase, entry.expected);
      assert.equal(result.completion_event, null);
      assert.equal((result.terminal_closeout as any).payload.ok, false);
    } finally { await rm(root, {recursive: true, force: true}); }
  });
});

test("in-flight replay rejects unaccepted checkpoints and unrelated identities", async t => {
  const checkpoint = {
    schema_version: "vision_checkpoint_v0", agent_id: agentId, satisfied: true,
    delivery_boundary: "in_flight_continuation",
    triggers: [{kind: "in_flight_continuation", todo_id: todoId}],
  };
  const patches: [string, Record<string, unknown>][] = [
    ["checkpoint absent", {vision_checkpoint: null}],
    ["checkpoint malformed", {vision_checkpoint: []}],
    ["not accepted", {vision_checkpoint: {...checkpoint, satisfied: false}}],
    ["truthy is not acceptance", {vision_checkpoint: {...checkpoint, satisfied: "true"}}],
    ["wrong schema", {vision_checkpoint: {...checkpoint, schema_version: "other"}}],
    ["semantic closeout", {vision_checkpoint: {...checkpoint, delivery_boundary: "semantic_closeout"}}],
    ["checkpoint other agent", {vision_checkpoint: {...checkpoint, agent_id: "peer"}}],
    ["no trigger", {vision_checkpoint: {...checkpoint, triggers: []}}],
    ["trigger other Todo", {vision_checkpoint: {...checkpoint, triggers: [{kind: "in_flight_continuation", todo_id: "todo_other"}]}}],
    ["trigger wrong kind", {vision_checkpoint: {...checkpoint, triggers: [{kind: "vision_unchanged", todo_id: todoId}]}}],
    ...["surface_only", "outcome_gap", "primary_goal_outcome"].map(outcome => [outcome, {delivery_outcome: outcome}] as [string, Record<string, unknown>]),
    ...["goal_id", "agent_id", "todo_id", "turn_instance_id"].map(field => [field, {[field]: "other"}] as [string, Record<string, unknown>]),
    ["effect mismatch", {settlement_identity: {...identity, effect_id: "other"}}],
  ];
  for (const [name, patch] of patches) await t.test(name, async () => {
    const root = await fixture({writeback: true, spend: true, visionCheckpoint: checkpoint});
    try {
      const path = join(root, "goals", goalId, "runs", "index.jsonl");
      const runs = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
      runs[0] = {...runs[0], ...patch};
      await writeFile(path, runs.map(run => JSON.stringify(run)).join("\n") + "\n");
      const result = await readQuotaSettlement(request(root));
      assert.equal(result.replay_phase, "open");
      assert.equal(result.completion_event, null);
    } finally { await rm(root, {recursive: true, force: true}); }
  });
});

test("monitor closeout requires the exact committed effect, not a matching observation row", async t => {
  const effect = `quota-monitor-poll:${goalId}:${agentId}:${turnId}`;
  const cases: [string, Record<string, unknown>, string][] = [
    ["committed", {}, "settled"],
    ["legacy turn-only commit", {quota_monitor_poll_commit: {effect_id: effect}}, "settled"],
    ["preview without commit", {quota_monitor_poll_commit: null}, "poll_due"],
    ["malformed commit", {quota_monitor_poll_commit: []}, "poll_due"],
    ["missing effect", {quota_monitor_poll_commit: {}}, "poll_due"],
    ["wrong commit", {quota_monitor_poll_commit: {effect_id: `${effect}:todo:other`}}, "poll_due"],
    ["wrong agent", {agent_id: "another-agent"}, "poll_due"],
    ["wrong goal", {goal_id: "another-goal"}, "poll_due"],
    ["wrong Todo", {todo_id: "todo_other"}, "poll_due"],
    ["wrong Turn", {turn_instance_id: "other-turn"}, "poll_due"],
    ["ordinary writeback", {classification: "state_refreshed"}, "poll_due"],
  ];
  for (const [name, patch, expected] of cases) {
    await t.test(name, async () => {
      const root = await fixture({monitor: true});
      try {
        const path = join(root, "goals", goalId, "runs", "index.jsonl");
        const row = JSON.parse((await readFile(path, "utf8")).trim());
        await writeFile(path, `${JSON.stringify({...row, ...patch})}\n`);
        const result = await readQuotaSettlement(request(root));
        assert.equal(result.monitor_phase, expected);
        assert.equal(result.replay_phase, expected === "settled" ? "settled" : "open");
        assert.equal((result.spend as any).payload.ok, false);
      } finally { await rm(root, {recursive: true, force: true}); }
    });
  }
});

test("a prior committed monitor poll never blocks a later unbound Turn rebind", async () => {
  const priorTurn = "turn-prior-monitor";
  const currentTurn = "turn-current-work";
  const prior = await fixture({monitor: true, turnId: priorTurn});
  const current = await fixture({guardUnbound: true, turnId: currentTurn});
  try {
    const priorLog = join(prior, "goals", goalId, "rollout-event-log.jsonl");
    const priorEvents = (await readFile(priorLog, "utf8"))
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
    priorEvents[0].details.closeout_required = true;
    await writeFile(
      priorLog,
      `${priorEvents.map((event) => JSON.stringify(event)).join("\n")}\n`,
    );
    await appendHistory(prior, current);
    const result = await readQuotaSettlement(request(prior, {
      turn_instance_id: currentTurn,
    }));
    const failure = (result.identity as any).result.failure;
    assert.equal(failure.kind, "receipt_unbound");
    assert.equal(failure.details.owed_prior_turn, undefined);
  } finally {
    await rm(prior, {recursive: true, force: true});
    await rm(current, {recursive: true, force: true});
  }
});

test("refresh recovery admission never survives a failed Turn identity", async () => {
  const root = await fixture({ guard: false, writeback: true });
  try {
    const result = await readQuotaSettlement(request(root, {
      refresh_retry: {
        vision: null, unchanged_reason: "Existing vision applies.", merge_patch: false,
        workspace_requested: false, mutation: {}, delivery_outcome: "outcome_progress",
        delivery_batch_scale: null, delivery_boundary: null, progress_observation: null,
      },
    }));
    assert.equal(result.refresh_recovery, null);
    assert.equal(result.writeback_run, null);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

async function appendSpendRun(runtimeRoot: string, extra: Record<string, unknown>) {
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    `${JSON.stringify({
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnId,
      settlement_identity: identity,
      effect_ref: `${identity.effect_id}#quota_spend`,
      ...extra,
    })}\n`,
  );
}

test("reads the complete receipt chain and workspace causality once", async () => {
  const runtimeRoot = await fixture({
    writeback: true,
    spend: true,
    completion: true,
    noFollowup: true,
    workspace: true,
    monitor: true,
  });

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal(result.found, true);
  assert.equal((result.settlement as any).payload.ok, true);
  assert.deepEqual(
    (result.settlement as any).result.receipts.map((receipt: any) => receipt.step_kind),
    ["validation", "durable_writeback", "quota_spend"],
  );
  assert.equal((result.terminal_closeout as any).payload.ok, true);
  assert.equal(result.monitor_phase, "settled");
  assert.equal(result.replay_phase, "settled");
  assert.deepEqual(result.semantic_replan_guard, {
    schema_version: "semantic_replan_guard_v0",
    scope: "legacy_unscoped",
    selected_obligation_id: null,
  });
  assert.deepEqual(result.workspace_causality, {
    schema_version: "delivery_workspace_causality_v0",
    todo_id: todoId,
    requirement: "required",
    source: "selected_todo_contract",
    reason: "declared_repository_or_write_contract",
  });
});

test("committed monitor replay closes without manufacturing delivery or spend receipts", async () => {
  const runtimeRoot = await fixture({ writeback: true, monitor: true });

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal((result.writeback as any).payload.ok, true);
  assert.equal((result.spend as any).payload.ok, false);
  assert.equal((result.settlement as any).result.failure.kind, "receipt_missing");
  assert.equal(result.monitor_phase, "settled");
  assert.equal(result.replay_phase, "settled");
  assert.equal((result.writeback_run as any).delivery_outcome, "outcome_progress");
});

test("names the unbound same-turn receipt and the repair instead of a mismatch", async () => {
  // The documented wake order runs the guard before any work item is chosen, so
  // the turn's receipt exists with no settlement binding. This read model never
  // binds one (the guard's same-turn reconciliation owns that, so there is one
  // binder), which means the caller has to be told the state and the exact
  // repair rather than the binding mismatch a "receipt todo=missing" message
  // reports.
  const runtimeRoot = await fixture({ guardUnbound: true });

  const result = await readQuotaSettlement(request(runtimeRoot));

  const failure = (result.settlement as any).result.failure;
  // The receipt exists and is well-formed, so the missing binding has its own
  // kind instead of reading as a mismatch against a second record.
  assert.equal(failure.kind, "receipt_unbound");
  assert.match(failure.reason, /carries no settlement binding yet/);
  assert.match(
    failure.reason,
    new RegExp(
      `quota should-run --turn-instance-id ${turnId} --todo-id ${todoId}`,
    ),
  );
  assert.deepEqual(failure.details, {
    binding_kind: "unbound",
    requested_binding_kind: "todo",
    turn_instance_id: turnId,
  });
});

test("names the argument-less guard reentry for a deferred explicit selection", async () => {
  // A deferred explicit selection is also identity-less, but its repair is not
  // "rebind with --todo-id": that re-enters the same preemption and defers
  // again, which is how a caller ends up looping instead of settling. The
  // retained selection tells the two unbound states apart, so the refusal can
  // name the reentry that actually binds the preemption.
  const runtimeRoot = await fixture({ guardUnbound: true, guardDeferred: true });

  const result = await readQuotaSettlement(request(runtimeRoot));

  const failure = (result.settlement as any).result.failure;
  // Both unbound states share the receipt's own failure kind; the deferred
  // selection is told apart by the repair text and the retained selection.
  assert.equal(failure.kind, "receipt_unbound");
  assert.match(failure.reason, /carries no settlement binding yet/);
  assert.match(
    failure.reason,
    new RegExp(
      `quota should-run --turn-instance-id ${turnId}(?! --todo-id)`,
    ),
  );
  assert.match(failure.reason, /without --todo-id/);
  assert.doesNotMatch(
    failure.reason,
    new RegExp(`--todo-id ${todoId}`),
  );
  assert.deepEqual(failure.details, {
    binding_kind: "unbound",
    requested_binding_kind: "todo",
    turn_instance_id: turnId,
    deferred_selection_todo_id: todoId,
  });
});

const priorTurnId = "turn-settlement-0";

/**
 * An earlier Turn of the same Agent whose guard bound the Todo and required a
 * host closeout, followed by the current Turn's unbound guard. `settled` adds
 * the earlier Turn's writeback and spend receipts.
 */
async function owedPriorTurnFixture(
  options: { settled?: boolean; closeoutRequired?: boolean } = {},
): Promise<string> {
  const runtimeRoot = await fixture({
    turnId: priorTurnId,
    writeback: options.settled,
    spend: options.settled,
  });
  const eventsPath = join(runtimeRoot, "goals", goalId, "rollout-event-log.jsonl");
  const events = (await readFile(eventsPath, "utf8"))
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  events[0].event_id = "event-guard-prior";
  events[0].details.closeout_required = options.closeoutRequired ?? true;
  await writeFile(
    eventsPath,
    `${events.map((event) => JSON.stringify(event)).join("\n")}\n`,
  );
  await appendHistory(runtimeRoot, await fixture({ guardUnbound: true }));
  return runtimeRoot;
}

test("names the earlier Turn that owes its closeout instead of a rebind that only replays", async () => {
  // While an earlier Turn owes its closeout, the guard preempts selection with
  // that Turn's recovery for every later Turn, so rebinding the current Turn
  // only replays the recovery. The refusal has to name the owed Turn.
  const runtimeRoot = await owedPriorTurnFixture();

  const result = await readQuotaSettlement(request(runtimeRoot));

  const failure = (result.settlement as any).result.failure;
  assert.equal(failure.kind, "receipt_unbound");
  assert.match(failure.reason, /carries no settlement binding yet/);
  assert.match(
    failure.reason,
    new RegExp(`earlier Turn owes its closeout \\(turn_instance_id ${priorTurnId}, todo ${todoId}`),
  );
  assert.match(failure.reason, /interaction_contract\.cli_channel\.next_cli_actions/);
  assert.doesNotMatch(
    failure.reason,
    new RegExp(`--turn-instance-id ${turnId} --todo-id`),
  );
  assert.deepEqual(failure.details, {
    binding_kind: "unbound",
    requested_binding_kind: "todo",
    turn_instance_id: turnId,
    owed_prior_turn: {
      prior_turn_instance_id: priorTurnId,
      event_id: "event-guard-prior",
      binding_kind: "todo",
      binding_id: todoId,
      settlement_effect_id: settlementIdentity({
        goal_id: goalId,
        agent_id: agentId,
        todo_id: todoId,
        turn_instance_id: priorTurnId,
      }).effect_id,
      missing_receipts: ["durable_writeback_receipt", "quota_spend_receipt"],
    },
    recovery_actions_ref: "interaction_contract.cli_channel.next_cli_actions",
  });

  // The guard's own preflight reads the same snapshot and must name the same
  // Turn and receipts; the refusal never runs a second selection rule.
  const preflight = await preflightPriorHostTurnCloseout({
    schema_version: PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA,
    runtime_root: runtimeRoot,
    goal_id: goalId,
    agent_id: agentId,
    exclude_turn_instance_id: turnId,
  });
  assert.equal(preflight.status, "candidate");
  const { missing_receipts: missingReceipts, ...owedCandidate } = failure.details.owed_prior_turn;
  assert.deepEqual(preflight.candidate, owedCandidate);
  assert.deepEqual(preflight.missing_receipts, missingReceipts);
});

test("keeps the same-turn rebind when no earlier Turn owes a closeout", async t => {
  for (const [name, options] of [
    ["settled earlier Turn", { settled: true }],
    ["earlier Turn without a closeout obligation", { closeoutRequired: false }],
  ] as const) {
    await t.test(name, async () => {
      const runtimeRoot = await owedPriorTurnFixture(options);

      const result = await readQuotaSettlement(request(runtimeRoot));

      const failure = (result.settlement as any).result.failure;
      assert.equal(failure.kind, "receipt_unbound");
      assert.match(
        failure.reason,
        new RegExp(`quota should-run --turn-instance-id ${turnId} --todo-id ${todoId}`),
      );
      assert.doesNotMatch(failure.reason, /earlier Turn/);
      assert.deepEqual(failure.details, {
        binding_kind: "unbound",
        requested_binding_kind: "todo",
        turn_instance_id: turnId,
      });
    });
  }
});

test("an unterminated prior guard is not an owed closeout before or after repair", async () => {
  const root = await fixture({turnId: priorTurnId});
  try {
    const eventsPath = join(root, "goals", goalId, "rollout-event-log.jsonl");
    const original = JSON.parse((await readFile(eventsPath, "utf8")).trim());
    original.details.closeout_required = true;
    // The guard writer stopped short of its committing newline.
    await writeFile(eventsPath, JSON.stringify(original));
    const preflightRequest = {
      schema_version: PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA,
      runtime_root: root, goal_id: goalId, agent_id: agentId,
      exclude_turn_instance_id: turnId,
    };
    const before = await preflightPriorHostTurnCloseout(preflightRequest);
    assert.equal(before.status, "none");
    // The next writer drops that tail before appending an unrelated row.
    let later = {...original, event_id: "event-later", run_id: "later-turn",
      details: {}} as Record<string, unknown>;
    await writeFile(eventsPath, `${JSON.stringify(later)}\n`);
    const after = await preflightPriorHostTurnCloseout(preflightRequest);
    assert.equal(after.status, "none");
    // A row that reached its newline is still a real owed closeout.
    await appendFile(eventsPath, `${JSON.stringify(original)}\n`);
    const committed = await preflightPriorHostTurnCloseout(preflightRequest);
    assert.equal(committed.status, "candidate");
    assert.equal((committed.candidate as Record<string, unknown>).prior_turn_instance_id, priorTurnId);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("an unbound earlier Turn never reports a later Turn as its owed predecessor", async () => {
  const root = await fixture({guardUnbound: true});
  try {
    const eventsPath = join(root, "goals", goalId, "rollout-event-log.jsonl");
    const original = JSON.parse((await readFile(eventsPath, "utf8")).split("\n")[0]!);
    const later = {...original, event_id: "later-guard", run_id: "later-turn", details: {
      todo_id: todoId, closeout_required: true,
      settlement_effect_id: settlementIdentity({goal_id: goalId, agent_id: agentId,
        todo_id: todoId, turn_instance_id: "later-turn"}).effect_id,
    }};
    // A delayed replay must not move the original Turn's causal position.
    await appendFile(eventsPath, `${JSON.stringify(later)}\n${JSON.stringify(original)}\n`);
    const result = await readQuotaSettlement(request(root));
    const failure = (result.settlement as any).result.failure;
    assert.equal(failure.kind, "receipt_unbound");
    assert.equal(failure.details.owed_prior_turn, undefined);
    assert.match(failure.reason, /quota should-run --turn-instance-id turn-settlement-1 --todo-id todo_settlement/);
    const preflight = await preflightPriorHostTurnCloseout({
      schema_version: PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA,
      runtime_root: root, goal_id: goalId, agent_id: agentId,
      exclude_turn_instance_id: turnId,
    });
    assert.equal(preflight.status, "none");
  } finally {
    await rm(root, {recursive: true, force: true});
  }
});

test("still reports a receipt bound to another work item as a mismatch", async () => {
  // The unbound state must not swallow the case where the receipt was bound and
  // the caller asked for something else: that is a real conflict, and its repair
  // is not "bind it".
  const runtimeRoot = await fixture({});
  const eventsPath = join(
    runtimeRoot,
    "goals",
    goalId,
    "rollout-event-log.jsonl",
  );
  const events = (await readFile(eventsPath, "utf8"))
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  events[0].details.todo_id = "todo_other_work_item";
  events[0].details.settlement_effect_id = settlementIdentity({
    goal_id: goalId,
    agent_id: agentId,
    todo_id: "todo_other_work_item",
    turn_instance_id: turnId,
  }).effect_id;
  await writeFile(
    eventsPath,
    `${events.map((event) => JSON.stringify(event)).join("\n")}\n`,
  );

  const result = await readQuotaSettlement(request(runtimeRoot));

  const failure = (result.settlement as any).result.failure;
  assert.equal(failure.kind, "identity_mismatch");
  assert.match(failure.reason, /receipt todo=todo_other_work_item/);
  assert.equal(failure.details, undefined);
});

test("rejects non-ENOENT settlement readback I/O failures", async () => {
  const runtimeRoot = await fixture();
  const indexPath = join(runtimeRoot, "goals", goalId, "runs", "index.jsonl");
  await rm(indexPath);
  await mkdir(indexPath);

  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot)),
    (error: unknown) =>
      typeof error === "object" &&
      error !== null &&
      "code" in error &&
      error.code === "EISDIR",
  );
});

test("recovers legacy quota commit rows by exact effect ref", async () => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    `${JSON.stringify({
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: agentId,
      effect_ref: `${identity.effect_id}#quota_spend`,
    })}\n`,
  );

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal((result.spend_run as any).effect_ref, `${identity.effect_id}#quota_spend`);
  assert.equal((result.spend as any).result.failure.kind, "receipt_missing");
});

test("rejects a writeback run persisted under another goal", async () => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    `${JSON.stringify({
      classification: "state_refreshed",
      delivery_outcome: "outcome_progress",
      goal_id: "other-goal",
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnId,
      settlement_identity: identity,
    })}\n`,
  );

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal(result.writeback_run, null);
  assert.equal((result.writeback as any).result.failure.kind, "writeback_missing");
});

test("does not pair a writeback run from another settlement effect", async () => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    `${JSON.stringify({
      classification: "state_refreshed",
      delivery_outcome: "outcome_progress",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnId,
      settlement_identity: { ...identity, effect_id: "other-effect" },
    })}\n`,
  );

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal(result.writeback_run, null);
  assert.equal((result.writeback as any).result.failure.kind, "writeback_missing");
});

test("does not pair a spend run from another settlement effect", async () => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    `${JSON.stringify({
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnId,
      effect_ref: "other-effect#quota_spend",
    })}\n`,
  );

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal(result.spend_run, null);
  assert.equal((result.spend as any).result.failure.kind, "receipt_missing");
});

test("does not pair a spend run when effect identities conflict either way", async () => {
  for (const row of [
    {
      quota_spend_commit: { effect_id: identity.effect_id },
      effect_ref: "different-effect#quota_spend",
    },
    {
      quota_spend_commit: { effect_id: "different-effect" },
      effect_ref: `${identity.effect_id}#quota_spend`,
    },
  ]) {
    const runtimeRoot = await fixture();
    await appendFile(
      join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
      `${JSON.stringify({
        classification: "quota_slot_spent",
        goal_id: goalId,
        agent_id: agentId,
        todo_id: todoId,
        turn_instance_id: turnId,
        ...row,
      })}\n`,
    );

    const result = await readQuotaSettlement(request(runtimeRoot));

    assert.equal(result.spend_run, null);
    assert.equal((result.spend as any).result.failure.kind, "receipt_missing");
  }
});

test("pairs a native spend row only when both persisted effect identities agree", async () => {
  const runtimeRoot = await fixture({ spend: true });
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    `${JSON.stringify({
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnId,
      settlement_identity: identity,
      quota_spend_commit: { effect_id: `${identity.effect_id}#quota_spend` },
      effect_ref: `${identity.effect_id}#quota_spend`,
    })}\n`,
  );

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal((result.spend as any).payload.ok, true);
  assert.equal(
    (result.spend_run as any).quota_spend_commit.effect_id,
    `${identity.effect_id}#quota_spend`,
  );
});

test("does not pair a spend row with malformed native effect metadata", async () => {
  const quotaSpendCommit = null;
  const runtimeRoot = await fixture();
  await appendSpendRun(runtimeRoot, { quota_spend_commit: quotaSpendCommit });

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal((result.spend as any).payload.ok, false);
  assert.equal((result.spend as any).result.failure.kind, "receipt_missing");
});

test("does not pair a spend row with non-object native effect metadata", async () => {
  const quotaSpendCommit: unknown[] = [];
  const runtimeRoot = await fixture();
  await appendSpendRun(runtimeRoot, { quota_spend_commit: quotaSpendCommit });

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal((result.spend as any).payload.ok, false);
  assert.equal((result.spend as any).result.failure.kind, "receipt_missing");
});

test("does not pair a spend row with malformed persisted settlement identity", async () => {
  for (const settlementIdentity of [null, [], "not-an-identity", {}]) {
    const runtimeRoot = await fixture();
    await appendFile(
      join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
      `${JSON.stringify({
        classification: "quota_slot_spent",
        goal_id: goalId,
        agent_id: agentId,
        todo_id: todoId,
        turn_instance_id: turnId,
        settlement_identity: settlementIdentity,
        effect_ref: `${identity.effect_id}#quota_spend`,
      })}\n`,
    );

    const result = await readQuotaSettlement(request(runtimeRoot));

    assert.equal(result.spend_run, null);
    assert.equal((result.spend as any).result.failure.kind, "receipt_missing");
  }
});

for (const kind of ["causal", "lifecycle"]) {
test(`${kind} no-spend closeout retains exact receipt identity and historical debits`, async () => {
  const target = { todo_id: "todo_dependency", role: "agent", status: "open",
    task_class: "continuous_monitor", material_change_generation: 2 };
  const waiting = { todo_id: todoId, role: "agent", status: "open",
    task_class: "advancement_task", resume_when: `monitor_changed:${target.todo_id}`,
    resume_ready: false, resume_monitor_generation: 2 };
  const evaluated = evaluateTodoResumeConditions({
    schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [waiting], source_items: [target],
  });
  const condition = (evaluated.conditions as Record<string, unknown>[])[0].condition;
  const proof = prepareBlockedWait({ schema_version: BLOCKED_WAIT_REQUEST_SCHEMA,
    todo_id: todoId, observed_at: "2026-09-24T10:00:00Z",
    todos: kind === "lifecycle" ? [{todo_id: todoId, role: "agent", status: "blocked", done: false,
      task_class: "advancement_task", archive_state: "active"}]
      : [{ ...waiting, resume_condition: condition }, target] });
  const options = { writeback: true, writebackOutcome: "outcome_gap", blockedRetry: proof,
    progressObservation: { schema_version: "typed_progress_observation_v0",
      result_class: "blocked", work_item_id: todoId,
      blocker_id: target.todo_id, evidence_ids: ["evidence:canonical-wait"] } };
  const runtime = await fixture(options);
  try {
    const result = await readQuotaSettlement(request(runtime));
    assert.equal((result.progress as any).state, "settled");
    assert.equal((result.progress as any).closeout_kind, "typed_blocked_writeback_no_spend");
    assert.equal(result.replay_phase, "settled");
    // Frozen historical facts stay closed after a later observation.
    await appendFile(join(runtime, "goals", goalId, "runs", "index.jsonl"),
      `${JSON.stringify({ classification: "quota_monitor_poll", goal_id: goalId,
        agent_id: agentId, todo_id: target.todo_id, turn_instance_id: "later-turn",
        material_change_generation: 3 })}\n`);
    assert.equal((await readQuotaSettlement(request(runtime))).replay_phase, "settled");
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
  for (const field of ["goal_id", "agent_id", "todo_id", "turn_instance_id"]) {
    const mismatch = await fixture(options);
    try {
      const index = join(mismatch, "goals", goalId, "runs", "index.jsonl");
      const run = JSON.parse((await readFile(index, "utf8")).trim());
      await writeFile(index, `${JSON.stringify({ ...run, [field]: "another-identity" })}\n`);
      const result = await readQuotaSettlement(request(mismatch));
      assert.notEqual((result.progress as any).state, "settled", field);
      assert.notEqual(result.replay_phase, "settled", field);
    } finally {
      await rm(mismatch, { recursive: true, force: true });
    }
  }
  const missing = await fixture({ ...options, guard: false });
  const missingWriteback = await fixture(options);
  const spent = await fixture({ ...options, spend: true });
  const malformed = await fixture({ ...options, blockedRetry: { ...proof,
    ...(kind === "lifecycle" ? {blocked_todo: {...proof.blocked_todo as object, status: "done"}}
      : {waiting_todo: { ...waiting, resume_monitor_generation: 3 }}) } });
  try {
    assert.notEqual((await readQuotaSettlement(request(missing))).replay_phase, "settled");
    const log = join(missingWriteback, "goals", goalId, "rollout-event-log.jsonl");
    const events = (await readFile(log, "utf8")).trim().split("\n").map(line => JSON.parse(line));
    await writeFile(log, `${events.filter(event => event.event_kind !== "refresh_state").map(event => JSON.stringify(event)).join("\n")}\n`);
    assert.notEqual((await readQuotaSettlement(request(missingWriteback))).replay_phase, "settled");
    const debited = await readQuotaSettlement(request(spent));
    assert.equal((debited.spend as any).payload.ok, true);
    assert.equal((debited.progress as any).closeout_kind, undefined);
    assert.equal((await readQuotaSettlement(request(malformed))).replay_phase, "open");
  } finally {
    await Promise.all([missing, missingWriteback, spent, malformed].map(path =>
      rm(path, { recursive: true, force: true })));
  }
});
}

test("a rolled calendar retry does not close the Turn without a spend", async () => {
  const runtime = await fixture({
    writeback: true,
    writebackOutcome: "outcome_gap",
    blockedRetry: {
      schema_version: "quota_blocked_retry_v0",
      source: "todo",
      todo_id: todoId,
      resume_when: "resume_at:2026-02-30T10:05:00Z",
      observed_at: "2026-02-30T10:00:00Z",
      due_at: "2026-02-30T10:05:00Z",
    },
    progressObservation: {
      schema_version: "typed_progress_observation_v0",
      result_class: "blocked",
      work_item_id: todoId,
      blocker_id: "blocker-runtime-boundary",
      evidence_ids: ["evidence-runtime-boundary"],
    },
  });
  try {
    const result = await readQuotaSettlement(request(runtime));
    assert.notEqual((result.progress as {closeout_kind?: string}).closeout_kind,
      "typed_blocked_writeback_no_spend");
    assert.notEqual(result.replay_phase, "settled");
  } finally {
    await rm(runtime, {recursive: true, force: true});
  }
});

test("accepts only an attributable typed blocker as an outcome-gap writeback", async () => {
  const qualifiedRuntime = await fixture({
    writeback: true,
    writebackOutcome: "outcome_gap",
    blockedRetry: true,
    progressObservation: {
      schema_version: "typed_progress_observation_v0",
      result_class: "blocked",
      work_item_id: todoId,
      blocker_id: "blocker-runtime-boundary",
      evidence_ids: ["evidence-runtime-boundary"],
    },
  });
  const qualified = await readQuotaSettlement(request(qualifiedRuntime));
  assert.equal((qualified.writeback as any).payload.ok, true);
  assert.equal((qualified.writeback_run as any).delivery_outcome, "outcome_gap");
  assert.equal((qualified.settlement as any).payload.ok, true);
  assert.equal((qualified.spend as any).payload.ok, false);
  assert.deepEqual((qualified.settlement as any).result.receipts.map(
    (receipt: any) => receipt.step_kind), ["validation", "durable_writeback"]);
  assert.equal((qualified.progress as any).state, "settled");
  assert.equal((qualified.progress as any).next_step, null);
  assert.equal((qualified.progress as any).closeout_kind,
    "typed_blocked_writeback_no_spend");
  assert.equal(qualified.replay_phase, "settled");

  const spentRuntime = await fixture({
    writeback: true,
    spend: true,
    writebackOutcome: "outcome_gap",
    blockedRetry: true,
    progressObservation: {
      schema_version: "typed_progress_observation_v0",
      result_class: "blocked",
      work_item_id: todoId,
      blocker_id: "blocker-runtime-boundary",
      evidence_ids: ["evidence-runtime-boundary"],
    },
  });
  const spent = await readQuotaSettlement(request(spentRuntime));
  assert.equal((spent.spend as any).payload.ok, true);
  assert.equal((spent.progress as any).closeout_kind, undefined);
  assert.deepEqual((spent.settlement as any).result.receipts.map(
    (receipt: any) => receipt.step_kind),
    ["validation", "durable_writeback", "quota_spend"]);

  const incompleteSpendRuntime = await fixture({
    writeback: true,
    writebackOutcome: "outcome_gap",
    blockedRetry: true,
    progressObservation: {
      schema_version: "typed_progress_observation_v0",
      result_class: "blocked",
      work_item_id: todoId,
      blocker_id: "blocker-runtime-boundary",
      evidence_ids: ["evidence-runtime-boundary"],
    },
  });
  await appendFile(join(incompleteSpendRuntime, "goals", goalId,
    "rollout-event-log.jsonl"), `${JSON.stringify({
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-incomplete-spend",
      event_kind: "quota_spend",
      goal_id: goalId,
      agent_id: agentId,
      run_id: turnId,
      details: {settlement_effect_id: identity.effect_id},
    })}\n`);
  const incompleteSpend = await readQuotaSettlement(request(incompleteSpendRuntime));
  assert.equal((incompleteSpend.settlement as any).payload.ok, false);
  assert.equal((incompleteSpend.progress as any).closeout_kind, undefined);

  const unscheduledRuntime = await fixture({
    writeback: true,
    writebackOutcome: "outcome_gap",
    progressObservation: {
      schema_version: "typed_progress_observation_v0",
      result_class: "blocked",
      work_item_id: todoId,
      blocker_id: "blocker-runtime-boundary",
      evidence_ids: ["evidence-runtime-boundary"],
    },
  });
  const unscheduled = await readQuotaSettlement(request(unscheduledRuntime));
  assert.equal((unscheduled.progress as any).state, "spend_required");
  assert.equal((unscheduled.progress as any).closeout_kind, undefined);

  const bareRuntime = await fixture({
    writeback: true,
    writebackOutcome: "outcome_gap",
  });
  const bare = await readQuotaSettlement(request(bareRuntime));
  assert.equal((bare.writeback as any).payload.ok, false);
  assert.equal((bare.writeback as any).result.failure.kind, "writeback_missing");
  assert.equal((bare.settlement as any).payload.ok, false);

  const mismatchedRuntime = await fixture({
    writeback: true,
    writebackOutcome: "outcome_gap",
    progressObservation: {
      schema_version: "typed_progress_observation_v0",
      result_class: "blocked",
      work_item_id: "todo_other",
      blocker_id: "blocker-runtime-boundary",
      evidence_ids: ["evidence-runtime-boundary"],
    },
  });
  const mismatched = await readQuotaSettlement(request(mismatchedRuntime));
  assert.equal((mismatched.writeback as any).payload.ok, false);
  assert.equal((mismatched.settlement as any).payload.ok, false);

  for (const evidenceIds of [
    "evidence-runtime-boundary",
    { evidence: "runtime-boundary" },
    ["evidence-runtime-boundary", "invalid evidence id"],
  ]) {
    const malformedRuntime = await fixture({
      writeback: true,
      writebackOutcome: "outcome_gap",
      progressObservation: {
        schema_version: "typed_progress_observation_v0",
        result_class: "blocked",
        work_item_id: todoId,
        blocker_id: "blocker-runtime-boundary",
        evidence_ids: evidenceIds,
      },
    });
    const malformed = await readQuotaSettlement(request(malformedRuntime));
    assert.equal((malformed.writeback as any).payload.ok, false);
  }
});

test("rejects a guard bound to another Todo", async () => {
  const runtimeRoot = await fixture();

  const result = await readQuotaSettlement(
    request(runtimeRoot, { todo_id: "todo_other" }),
  );

  assert.equal((result.identity as any).result.failure.kind, "identity_mismatch");
  assert.match(
    (result.identity as any).result.failure.reason,
    /does not match the original quota guard/,
  );
});

test("a retained Todo selects its original replan guard without rebinding it", async () => {
  const obligation = "replan-0000000000000001";
  const bound = settlementIdentity({
    goal_id: goalId,
    agent_id: agentId,
    todo_id: null,
    replan_obligation_id: obligation,
    turn_instance_id: turnId,
  });
  const runtimeRoot = await fixture();
  try {
    const log = join(runtimeRoot, "goals", goalId, "rollout-event-log.jsonl");
    const events = (await readFile(log, "utf8")).trim().split("\n")
      .map(line => JSON.parse(line));
    Object.assign(events[0].details, {
      todo_id: "",
      replan_obligation_id: obligation,
      settlement_effect_id: bound.effect_id,
      pending_action_selection_todo_id: todoId,
      semantic_replan_obligation_id: obligation,
    });
    await writeFile(log, events.map(event => JSON.stringify(event)).join("\n") + "\n");

    const result = await readQuotaSettlement(request(runtimeRoot, {
      resolve_original_binding: true,
    }));
    assert.deepEqual((result.identity as any).result.value, settlementIdentityPayload(bound));

    const mismatch = await readQuotaSettlement(request(runtimeRoot, {
      todo_id: "todo_other",
      resolve_original_binding: true,
    }));
    assert.equal((mismatch.identity as any).result.failure.kind, "identity_mismatch");
  } finally {
    await rm(runtimeRoot, {recursive: true, force: true});
  }
});

test("rejects dual Todo and replan bindings before reading settlement facts", async () => {
  const runtimeRoot = await fixture({
    writeback: true,
    spend: true,
    completion: true,
    monitor: true,
  });

  const result = await readQuotaSettlement(request(runtimeRoot, {
    replan_obligation_id: "replan-0000000000000001",
  }));

  assert.equal((result.identity as any).result.failure.kind, "invalid_identity");
  assert.equal(result.monitor_phase, null);
  assert.equal(result.replay_phase, null);
  assert.equal(result.writeback_run, null);
  assert.equal(result.spend_run, null);
});

test("identity failure cannot promote unguarded later facts to a terminal phase", async () => {
  const runtimeRoot = await fixture();
  const unguardedTodoId = "todo_unguarded";
  const unguardedIdentity = settlementIdentity({
    goal_id: goalId,
    agent_id: agentId,
    todo_id: unguardedTodoId,
    turn_instance_id: turnId,
  });
  const goalRoot = join(runtimeRoot, "goals", goalId);
  await appendFile(
    join(goalRoot, "rollout-event-log.jsonl"),
    [
      {
        schema_version: "loopx_rollout_event_v0",
        event_id: "event-unguarded-writeback",
        event_kind: "refresh_state",
        goal_id: goalId,
        agent_id: agentId,
        run_id: turnId,
        details: { settlement_effect_id: unguardedIdentity.effect_id },
      },
      {
        schema_version: "loopx_rollout_event_v0",
        event_id: "event-unguarded-spend",
        event_kind: "quota_spend",
        goal_id: goalId,
        agent_id: agentId,
        run_id: turnId,
        details: { settlement_effect_id: unguardedIdentity.effect_id },
      },
    ].map((event) => `${JSON.stringify(event)}\n`).join(""),
  );
  await appendFile(
    join(goalRoot, "runs", "index.jsonl"),
    [
      {
        classification: "quota_monitor_poll",
        material_change: true,
        goal_id: goalId,
        agent_id: agentId,
        todo_id: unguardedTodoId,
        turn_instance_id: turnId,
      },
      {
        classification: "state_refreshed",
        delivery_outcome: "outcome_progress",
        goal_id: goalId,
        agent_id: agentId,
        todo_id: unguardedTodoId,
        turn_instance_id: turnId,
        settlement_identity: unguardedIdentity,
      },
      {
        classification: "quota_slot_spent",
        goal_id: goalId,
        agent_id: agentId,
        todo_id: unguardedTodoId,
        turn_instance_id: turnId,
        settlement_identity: unguardedIdentity,
      },
    ].map((run) => `${JSON.stringify(run)}\n`).join(""),
  );

  const result = await readQuotaSettlement(
    request(runtimeRoot, { todo_id: unguardedTodoId }),
  );

  assert.equal((result.identity as any).result.failure.kind, "identity_mismatch");
  assert.equal(result.monitor_phase, null);
  assert.equal(result.replay_phase, null);
  assert.equal(result.writeback_run, null);
  assert.equal(result.spend_run, null);
});

test("a missing guard keeps complete later facts non-terminal", async () => {
  const runtimeRoot = await fixture({
    guard: false,
    writeback: true,
    spend: true,
    monitor: true,
  });

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal((result.identity as any).result.failure.kind, "receipt_missing");
  assert.equal(result.monitor_phase, null);
  assert.equal(result.replay_phase, null);
  assert.equal(result.writeback_run, null);
  assert.equal(result.spend_run, null);
});

test("infers the latest typed turn and revalidates its guard", async () => {
  const runtimeRoot = await fixture({ writeback: true });

  const result = await readQuotaSettlement(request(runtimeRoot, {
    turn_instance_id: null,
    infer_turn_instance_id: true,
  }));

  assert.equal(result.found, true);
  assert.equal((result.identity as any).result.value.turn_instance_id, turnId);
});

test("returns not-found when compatibility inference has no typed run", async () => {
  const runtimeRoot = await fixture();

  const result = await readQuotaSettlement(request(runtimeRoot, {
    turn_instance_id: null,
    infer_turn_instance_id: true,
  }));

  assert.deepEqual(result, {
    schema_version: "loopx_quota_settlement_readback_result_v0",
    found: false,
  });
});

test("rejects malformed request authority at the runtime boundary", async () => {
  const runtimeRoot = await fixture();

  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot, { agent_id: [agentId] })),
    /agent_id must be a string or null/,
  );
  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot, { runtime_root: "relative" })),
    /runtime_root must be absolute/,
  );
  await assert.rejects(
    readQuotaSettlementSnapshot("relative", goalId),
    /runtime_root must be absolute/,
  );
  await assert.rejects(
    readQuotaSettlementSnapshot(runtimeRoot, "../other-goal"),
    /goal_id must be a single path segment/,
  );
  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot, { schema_version: "future" })),
    /request schema mismatch/,
  );
  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot, { infer_turn_instance_id: "yes" })),
    /infer_turn_instance_id must be a boolean/,
  );
  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot, { allow_unbound_binding: "yes" })),
    /allow_unbound_binding must be a boolean/,
  );
});

test("fails closed on malformed settlement JSONL", async () => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    '{"classification":"quota_slot_spent"\n',
  );

  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot)),
    /settlement readback line 2 is malformed/,
  );
});

test("fails closed on valid JSON with an invalid settlement record shape", async () => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    "[]\n",
  );

  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot)),
    /settlement readback line 2 is malformed/,
  );
});

test("fails closed on a settlement event schema mismatch", async () => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "rollout-event-log.jsonl"),
    `${JSON.stringify({ schema_version: "future_rollout_event_v1" })}\n`,
  );

  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot)),
    /settlement readback line 2 is malformed/,
  );
});

// The cadence consumer must adopt the same quota owner, including its instance
// and lock lifecycle; independently reading the alias history is insufficient.
test("effective cadence scopes settlement and ACKs to its admitted Goal instance", async () => {
  const { projectSettledReplanHistory } = await import(
    "../../loopx/control_plane/work_items/replan_history_settlement.ts");
  const ref = { goal_id: goalId, goal_instance_id: instanceA };
  const root = await fixture({ writeback: true, spend: true, completion: true, goalRef: ref });
  const cadence = (binding: Record<string, unknown>, threshold = 1) => ({
    schema_version: "replan_history_request_v0", operation: "periodic",
    agent_id: agentId, monitor_agent_id: agentId, neutral_classifications: [],
    stall_threshold: 2, periodic_threshold: threshold, monitor_threshold: 6,
    streak_threshold: 5, monitor_schema: "dead_monitor_repeat_v0",
    todos: { monitors: [], advancements: [], resume: null },
    settlement_source: { runtime_root: root, goal_id: goalId, ...binding },
    runs: [
      { goal_ref: {...ref, goal_instance_id: instanceB}, agent_id: agentId,
        public_agent_id: agentId, monitor_agent_id: agentId, turn_id: "retired-ack",
        classification: "state_refreshed", generated_at: "later", observed_at: 2,
        accepted_ack: true, progress: null, monitor: {} },
      { goal_ref: ref, agent_id: agentId, public_agent_id: agentId,
        monitor_agent_id: agentId, turn_id: turnId,
        classification: "state_refreshed", generated_at: "earlier", observed_at: 1,
        accepted_ack: false, progress: null, monitor: {} },
    ],
  });
  try {
    assert.equal((await projectSettledReplanHistory(cadence({}))).trigger, null);
    await withSourceAdmission(root, instanceA, instanceA, async binding => {
      const borrowed = {...binding, borrow_source_admission: true};
      const result = await projectSettledReplanHistory(cadence(borrowed));
      assert.equal(requireJsonObject(result.trigger, "trigger").run_count, 1);
      assert.equal((await projectSettledReplanHistory(cadence(borrowed, 2))).trigger, null);
      // The caller still owns both locks and can perform its next read/commit.
      assert.equal((await readQuotaSettlement(request(root, borrowed))).found, true);
    });
    await assert.rejects(withSourceAdmission(root, instanceA, instanceB,
      binding => projectSettledReplanHistory(cadence(binding))), /stale_goal_instance/);
    await assert.rejects(projectSettledReplanHistory(cadence({goal_ref: ref})),
      /must be supplied together/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("agent cadence does not interpret another agent's malformed semantic receipt", async () => {
  const { projectSettledReplanHistory } = await import(
    "../../loopx/control_plane/work_items/replan_history_settlement.ts");
  const root = await fixture({writeback: true, spend: true, completion: true});
  const peer = "codex-peer";
  const peerTurn = "peer-turn";
  const peerIdentity = settlementIdentity({goal_id: goalId, agent_id: peer,
    todo_id: todoId, turn_instance_id: peerTurn});
  const log = join(root, "goals", goalId, "rollout-event-log.jsonl");
  const row = (agent: string, turn: string) => ({agent_id: agent, public_agent_id: agent,
    monitor_agent_id: agent, turn_id: turn, classification: "state_refreshed",
    generated_at: "2026-01-01T00:00:00Z", observed_at: 1,
    accepted_ack: false, progress: null, monitor: {}});
  const cadence = {schema_version: "replan_history_request_v0", operation: "periodic",
    agent_id: agentId, monitor_agent_id: agentId, neutral_classifications: [],
    stall_threshold: 2, periodic_threshold: 1, monitor_threshold: 6,
    streak_threshold: 5, monitor_schema: "dead_monitor_repeat_v0",
    todos: {monitors: [], advancements: [], resume: null},
    settlement_source: {runtime_root: root, goal_id: goalId},
    runs: [row(peer, peerTurn), row(agentId, turnId)]};
  try {
    const baseline = await projectSettledReplanHistory({...cadence, runs: cadence.runs.slice(1)});
    assert.equal(requireJsonObject(baseline.trigger, "trigger").run_count, 1);
    await appendFile(log, JSON.stringify({schema_version: "loopx_rollout_event_v0",
      event_id: "malformed-peer-review", event_kind: "quota_should_run",
      goal_id: goalId, agent_id: peer, run_id: peerTurn, details: {
        todo_id: todoId, settlement_effect_id: peerIdentity.effect_id,
        semantic_replan_obligation_id: "replan-0000000000000001",
        semantic_replan_review_kind: "periodic_review_due",
        semantic_replan_review_stop_condition: null,
      }}) + "\n");
    const originalLog = await readFile(log, "utf8");
    assert.deepEqual(await projectSettledReplanHistory(cadence), baseline);
    assert.equal((await projectSettledReplanHistory({...cadence,
      runs: cadence.runs.slice(0, 1)})).trigger, null, "peer work cannot qualify this agent");
    await assert.rejects(projectSettledReplanHistory({...cadence, agent_id: peer}),
      /receipt review stop condition/, "the affected agent still sees its own invalid receipt");
    await assert.rejects(projectSettledReplanHistory({...cadence, agent_id: null}),
      /receipt review stop condition/, "unscoped history still validates every contributing agent");
    await assert.rejects(projectSettledReplanHistory({...cadence, agent_id: 42}),
      /agent_id/, "invalid scope must not silently become unscoped");
    assert.equal(await readFile(log, "utf8"), originalLog, "readback cannot repair historical receipts");
  } finally {
    await rm(root, {recursive: true, force: true});
  }
});

test("tolerant replan history ignores an unterminated settlement receipt", async () => {
  const { projectSettledReplanHistory } = await import(
    "../../loopx/control_plane/work_items/replan_history_settlement.ts");
  const root = await fixture({writeback: true, spend: true, completion: true});
  const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
  const request = {
    schema_version: "replan_history_request_v0", operation: "periodic",
    agent_id: agentId, monitor_agent_id: agentId, neutral_classifications: [],
    stall_threshold: 2, periodic_threshold: 1, monitor_threshold: 6,
    streak_threshold: 5, monitor_schema: "dead_monitor_repeat_v0",
    todos: {monitors: [], advancements: [], resume: null},
    settlement_source: {runtime_root: root, goal_id: goalId,
      tolerate_malformed_history: true},
    runs: [{agent_id: agentId, public_agent_id: agentId,
      monitor_agent_id: agentId, turn_id: turnId,
      classification: "state_refreshed", generated_at: "2026-01-01T00:00:00Z",
      observed_at: 1, accepted_ack: false, progress: null, monitor: {}}],
  };
  try {
    const committed = await projectSettledReplanHistory(request);
    assert.equal(requireJsonObject(committed.trigger, "trigger").run_count, 1);
    const committedBytes = await readFile(path);
    assert.ok(committedBytes.at(-1) === 0x0a);
    await appendFile(path, Buffer.concat([
      Buffer.from('{"schema_version":"loopx_rollout_event_v0","event_kind":"quota_should_run","goal_id":"'),
      Buffer.from(goalId),
      Buffer.from('","agent_id":"unrelated","run_id":"later-turn","summary":"'),
      Buffer.from([0xff]),
      Buffer.from('"}\n'),
    ]));
    assert.deepEqual(await projectSettledReplanHistory(request), committed,
      "a damaged unrelated record stays outside the committed receipt view");
    await writeFile(path, committedBytes.subarray(0, -1));
    const uncommitted = await projectSettledReplanHistory(request);
    assert.equal(uncommitted.trigger, null,
      "an unterminated spend receipt cannot qualify settlement history");
    await appendFile(path, "\n");
    assert.deepEqual(await projectSettledReplanHistory(request), committed,
      "the append commit restores the same settlement history");
  } finally { await rm(root, {recursive: true, force: true}); }
});

test("effective cadence skips settlement read without a matching should-run receipt", async () => {
  const { projectSettledReplanHistory } = await import(
    "../../loopx/control_plane/work_items/replan_history_settlement.ts");
  const root = await fixture({guard: false});
  await appendFile(
    join(root, "goals", goalId, "runs", "index.jsonl"),
    "not-json\n",
  );
  const request = {
    schema_version: "replan_history_request_v0",
    operation: "periodic",
    agent_id: agentId,
    monitor_agent_id: agentId,
    neutral_classifications: [],
    stall_threshold: 2,
    periodic_threshold: 1,
    monitor_threshold: 6,
    streak_threshold: 5,
    monitor_schema: "dead_monitor_repeat_v0",
    todos: {monitors: [], advancements: [], resume: null},
    settlement_source: {runtime_root: root, goal_id: goalId},
    runs: [{
      agent_id: agentId,
      public_agent_id: agentId,
      monitor_agent_id: agentId,
      classification: "progress",
      generated_at: "2026-09-24T10:00:00Z",
      observed_at: 1,
      turn_id: "turn-history-only",
      accepted_ack: false,
      progress: null,
      monitor: {},
    }],
  };
  try {
    const result = await projectSettledReplanHistory(request);
    assert.equal(result.trigger, null);
  } finally {
    await rm(root, {recursive: true, force: true});
  }
});

test(
  "replan history RPC rejects a relative runtime root before empty-receipt early return",
  async () => {
    const handlers = createEffectRuntimeHandlers({
      fingerprint: "replan-history-runtime-root-test",
      requestShutdown() {},
    });
    const request = {
      schema_version: "replan_history_request_v0",
      operation: "periodic",
      agent_id: agentId,
      monitor_agent_id: agentId,
      neutral_classifications: [],
      stall_threshold: 2,
      periodic_threshold: 1,
      monitor_threshold: 6,
      streak_threshold: 5,
      monitor_schema: "dead_monitor_repeat_v0",
      todos: {monitors: [], advancements: [], resume: null},
      settlement_source: {runtime_root: "relative", goal_id: goalId},
      runs: [],
    };

    await assert.rejects(
      dispatchEffectRuntimeMethod(
        handlers,
        "work_item.replan_history.project",
        request,
      ),
      /runtime_root must be absolute/,
    );
  },
);

test("history retry qualification preserves its original aware clock and exact expiry", async () => {
  const run = {todo_id: "todo_pending001", turn_instance_id: "turn-pending",
    delivery_outcome: "outcome_gap", progress_observation: {result_class: "blocked"},
    blocked_retry: {schema_version: "quota_blocked_retry_v0", source: "turn_settlement",
      todo_id: "todo_pending001", observed_at: "2026-01-01T00:00:00.000001Z",
      due_at: "2026-01-01T00:05:00.000001Z", resume_when: "resume_at:2026-01-01T00:05:00.000001Z"}};
  const read = (runs: unknown[], observed_at: unknown) => readQuotaSettlement({
    schema_version: "loopx_quota_history_qualification_request_v0", runs, observed_at,
  });
  assert.deepEqual((await read([run], "2026-01-01T00:05:00Z")).active_retry_indexes, [0]);
  assert.deepEqual((await read([run], "2026-01-01T00:05:00.000001Z")).active_retry_indexes, []);
  assert.deepEqual((await read([run], "2026-01-01T08:05:00+08:00")).active_retry_indexes, [0]);
  for (const clock of [undefined, null, "2026-01-01T00:01:00", "2026-01-01", "2026-02-30T00:01:00Z",
    "2026-01-01T00:01:00z"]) {
    assert.deepEqual((await read([run], clock)).active_retry_indexes, []);
  }
  for (const patch of [{source: "todo"}, {todo_id: "todo_other001"}, {resume_when: null},
    {observed_at: "2026-01-01T00:00:00"}, {due_at: "2026-02-30T00:05:00Z"}]) {
    const bad = {...run, blocked_retry: {...run.blocked_retry, ...patch}};
    assert.deepEqual((await read([bad], "2026-01-01T00:01:00Z")).active_retry_indexes, []);
  }
  for (const patch of [{delivery_outcome: "outcome_progress"}, {progress_observation: null},
    {todo_id: null}, {turn_instance_id: null}, {turn_instance_id: {}},
    {turn_instance_id: true}, {turn_instance_id: 1}, {turn_instance_id: " "}]) {
    assert.deepEqual((await read([{...run, ...patch}], "2026-01-01T00:01:00Z")).active_retry_indexes, []);
  }
  // Historical Python datetime inputs retain ISO-week dates and offset seconds.
  const legacy = {...run, blocked_retry: {...run.blocked_retry,
    observed_at: "2026-W01-4T00:00:00+00:00:01", due_at: "2026-W01-4T00:05:00+00:00:01",
    resume_when: "resume_at:2026-W01-4T00:05:00+00:00:01"}};
  assert.deepEqual((await read([legacy], "2026-01-01T00:01:00Z")).active_retry_indexes, [0]);
  const legacySeparator = {...run, blocked_retry: {...run.blocked_retry,
    observed_at: "2026-01-01z00:00:00Z", due_at: "2026-01-01z00:05:00Z",
    resume_when: "resume_at:2026-01-01z00:05:00Z"}};
  assert.deepEqual((await read([legacySeparator], "2026-01-01T00:01:00Z")).active_retry_indexes, [0]);
  // Aware Python comparisons need not normalize the instant into a UTC datetime.
  for (const start of ["0001-01-01T00:00:00+08:00", "9999-12-31T23:00:00-08:00"]) {
    const due = start.replace(":00:00", ":05:00"), now = start.replace(":00:00", ":01:00");
    const edge = {...run, blocked_retry: {...run.blocked_retry,
      observed_at: start, due_at: due, resume_when: `resume_at:${due}`}};
    assert.deepEqual((await read([edge], now)).active_retry_indexes, [0]);
    assert.deepEqual((await read([edge], due)).active_retry_indexes, []);
  }
  for (const [observed_at, due_at, expected] of [
    ["2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", [0]],
    ["2026-01-01T00:00:00Z", "2026-01-01T00:30:00Z", [0]],
    ["2026-01-01T00:00:00.000001Z", "2026-01-01T00:01:00Z", []],
    ["2026-01-01T00:00:00Z", "2026-01-01T00:30:00.000001Z", []],
  ] as const) {
    const bounded = {...run, blocked_retry: {...run.blocked_retry, observed_at, due_at,
      resume_when: `resume_at:${due_at}`}};
    assert.deepEqual((await read([bounded], "2026-01-01T00:00:30Z")).active_retry_indexes, expected);
  }
  await assert.rejects(read([run], 123), /observation clock/);
});


test("pending closeout with explicit turn and todo still names an owed prior Turn", async () => {
  const runtimeRoot = await owedPriorTurnFixture();
  try {
    const pending = await readQuotaSettlement(
      request(runtimeRoot, {pending_closeout: true, turn_instance_id: turnId, todo_id: todoId}),
    );
    const failure = (pending.identity as any).result.failure;
    assert.equal(failure.kind, "receipt_unbound");
    assert.match(failure.reason, /carries no settlement binding yet/);
    assert.match(
      failure.reason,
      new RegExp(`earlier Turn owes its closeout \\(turn_instance_id ${priorTurnId}, todo ${todoId}`),
    );
    assert.doesNotMatch(failure.reason, new RegExp(`--turn-instance-id ${turnId} --todo-id`));

    const originalOnly = await readQuotaSettlement(
      request(runtimeRoot, {resolve_original_binding: true, todo_id: null}),
    );
    assert.equal(
      (originalOnly.identity as any).result.failure.reason,
      "the original Turn guard has no settlement binding",
    );
  } finally {
    await rm(runtimeRoot, {recursive: true, force: true});
  }
});

test("readback exposes matching supersede_event when records are included", async () => {
  const runtimeRoot = await fixture();
  try {
    const eventsPath = join(runtimeRoot, "goals", goalId, "rollout-event-log.jsonl");
    const existing = await readFile(eventsPath, "utf8");
    const supersede = {
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-supersede-1",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      run_id: turnId,
      event_kind: "todo_supersede",
      status: "done",
      recorded_at: "2026-06-01T00:05:00.000Z",
      details: {
        goal_id: goalId,
        agent_id: agentId,
        todo_id: todoId,
        turn_instance_id: turnId,
        settlement_effect_id: settlementIdentityPayload(identity).effect_id,
      },
    };
    await writeFile(eventsPath, existing + JSON.stringify(supersede) + "\n", "utf8");
    const readback = await readQuotaSettlement(
      request(runtimeRoot, {include_record_bodies: true}),
    );
    assert.equal((readback.supersede_event as any)?.event_kind, "todo_supersede");
    assert.equal((readback.supersede_event as any)?.run_id, turnId);
    assert.equal((readback.supersede_event as any)?.todo_id, todoId);
  } finally {
    await rm(runtimeRoot, {recursive: true, force: true});
  }
});


const selectedWritebackObligation = "replan-1111111111111111";
function todoSemanticAdmission(): JsonObject {
  return {schema_version: "loopx_rollout_event_v0", event_kind: "quota_should_run", event_id: "semantic-admitted",
    goal_id: goalId, agent_id: agentId, run_id: turnId, status: "normal_run", recorded_at: "2026-01-01T00:00:00Z",
    details: {todo_id: todoId, selected_todo_id: todoId, settlement_effect_id: identity.effect_id,
      closeout_required: true, ok: true, should_run: true,
      must_attempt_work: true, delivery_allowed: false, quiet_noop_allowed: false, interaction_mode: "autonomous_replan",
      semantic_replan_obligation_id: selectedWritebackObligation, semantic_replan_review_kind: "goal_outcome_uncovered",
      semantic_replan_review_stop_condition: "Stop at owner-only decisions."}};
}

function todoSemanticWriteback(): JsonObject {
  const packet = visionAuthoringContract().minimal_example as JsonObject;
  const vision = buildVisionCheckpoint({schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "prepare",
    goal_id: goalId, agent_id: agentId, agent_vision_packet: packet, existing_agent_vision: null,
    merge_patch: false, require_path_delta_for_durable_change: true, allow_terminal_stride_path: false}).agent_vision;
  const checkpoint = buildVisionCheckpoint({schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "finalize",
    agent_id: agentId, agent_vision: vision, existing_agent_vision: null, vision_unchanged_reason: null,
    delivery_outcome: "outcome_progress", active_state_next_action_would_update: false,
    delivery_boundary: "semantic_closeout", todo_id: todoId, completion_todo_id: null,
    autonomous_replan_recorded: true, blocked_retry: null});
  const progress = {schema_version: "typed_progress_observation_v0", result_class: "advanced", work_item_id: todoId,
    fingerprint: "1234567890abcdef", surface_id: "surface:verified", evidence_ids: ["evidence:verified"]};
  // Independent expected semantic outcomes: the typed observation advances a
  // new surface; the evidenced Vision retains the route. The selected scalar
  // outcome review accepts the surface, not the Vision decision by itself.
  const semantic = {schema_version: "replan_semantic_delta_v0", accepted: true,
    outcomes: ["new_surface", "fresh_vision_path_outcome"], satisfying_outcomes: ["new_surface"],
    required_any_of: ["new_surface", "new_hypothesis", "new_probe_family", "new_runnable_successor",
      "coverage_backed_exploration_exhausted", "new_concrete_blocker", "coverage_backed_no_followup"],
    observation_fingerprint: progress.fingerprint};
  const retry = {vision: packet, unchanged_reason: null, merge_patch: false, workspace_requested: false,
    mutation: {autonomous_replan_recorded: true}, delivery_outcome: "outcome_progress", delivery_batch_scale: null,
    delivery_boundary: "semantic_closeout", progress_observation: progress};
  return {refresh_retry: retry,
    writeback_candidate: {requested_proof: null, effect_ref: `${identity.effect_id}#durable_writeback`,
      completion_todo_id: null, completion_turn_key: null,
      run: {goal_id: goalId, agent_id: agentId, todo_id: todoId, turn_instance_id: turnId,
        settlement_identity: settlementIdentityPayload(identity), classification: "turn_run_once",
        generated_at: "2026-01-01T00:01:00Z", blocked_retry: null, delivery_outcome: "outcome_progress", delivery_boundary: "semantic_closeout", progress_observation: progress,
        agent_vision: vision, vision_checkpoint: checkpoint,
        autonomous_replan_ack: {schema_version: "autonomous_replan_ack_v0", recorded: true, semantic_delta: semantic},
        refresh_recovery: refreshRecovery(decodeRefreshRetry(retry)!, null, false, undefined, false)}}};
}

async function todoSemanticFixture(provider: "file" | "sqlite", seedTodo?: JsonObject, leases: JsonObject[] = [],
  additionalTodos: JsonObject[] = []) {
  const root = await fixture({guard: false});
  const log = join(root, "goals", goalId, "rollout-event-log.jsonl");
  const admission = todoSemanticAdmission();
  await writeFile(log, JSON.stringify(admission) + "\n");
  const {projection, todo} = await import("./shadow_file_fixture.ts");
  const {authorityUnicodeCompare} = await import("../../loopx/control_plane/coordination/authority_store_codec.ts");
  const allTodos = [seedTodo ?? {...todo(todoId), claimed_by: agentId}, ...additionalTodos]
    .sort((left, right) => authorityUnicodeCompare(String(left.todo_id), String(right.todo_id)));
  const store = provider === "sqlite"
    ? new (await import("../../loopx/control_plane/coordination/sqlite_authority_store.ts")).SqliteAuthorityStore(
      join(root, "authority", "sqlite-v0"), goalId)
    : new (await import("../../loopx/control_plane/coordination/file_authority_store.ts")).FileAuthorityStore(
      join(root, "authority", "file-v0"), goalId);
  assert.equal((await store.commitAuthority({operation_id: "seed-semantic-authority", expected_provider_revision: null,
    next_projection: {...projection(allTodos, leases), goal_id: goalId},
    events: [], receipts: []})).status, "applied");
  if (provider === "sqlite") {
    const observed = await store.storeIdentity();
    assert.equal(observed.status, "available");
    if (observed.status !== "available") throw new Error("Fixture authority unavailable");
    await (await import("../../loopx/control_plane/coordination/local_authority_provider.ts"))
      .publishLocalAuthoritySelection(root, goalId, "sqlite", observed.store_identity);
  }
  return {root, log, store, admission};
}

async function withNativeAdmission<T>(root: string, run: (binding: JsonObject, registry: string) => Promise<T>,
  agent: string | null = agentId, options: {exact?: boolean; publication?: boolean;
    registryPath?: string; declaredRuntimeRoot?: string; registryRole?: string} = {}): Promise<T> {
  const registry = options.registryPath ?? join(root, "native-project", ".loopx", "registry.json");
  await mkdir(dirname(registry), {recursive: true});
  await writeFile(registry, JSON.stringify({schema_version: "0.1", registry_role: options.registryRole ?? "project-local",
    common_runtime_root: options.declaredRuntimeRoot ?? root, goals: [{id: goalId, ...(options.exact === false ? {} : {goal_instance_id: instanceA}), status: "active",
      coordination: {registered_agents: [agentId]}}]}));
  const targets = [join(root, "goals", goalId, "runs", "index.jsonl"), registry,
    ...(options.publication === false ? [] : [shadowMaintenanceLockPath(root, goalId),
      join(root, "goals", goalId, "rollout-event-log.jsonl")])];
  const roles = ["run_index", "registry_source", "authority_maintenance", "heartbeat_receipt"];
  const locks: Awaited<ReturnType<typeof acquireFileMutationLock>>[] = [];
  for (const target of targets) locks.push(await acquireFileMutationLock(target));
  try {
    const ref = options.exact === false ? null : {goal_id: goalId, goal_instance_id: instanceA};
    return await run({...(ref === null ? {} : {goal_ref: ref}), borrow_source_admission: true, source_admission: {
      schema_version: QUOTA_NATIVE_ADMISSION_SCHEMA, registry_path: registry, planned_goal_ref: ref,
      runtime_root: root, ...(agent === null ? {} : {agent_id: agent}),
      registry_sources: [{path: registry, sha256: createHash("sha256").update(await readFile(registry)).digest("hex")}],
      locks: targets.map((target, index) => ({role: roles[index], target, pid: process.pid, token: locks[index]!.token})),
    }}, registry);
  } finally {
    for (const index of [...targets.keys()].reverse()) {
      await releaseFileMutationLock(targets[index]!, locks[index]!.token, null, true);
    }
  }
}

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: native relative runtime registration follows project, plain and global registry locations`, async () => {
    const f = await todoSemanticFixture(provider);
    await writeFile(f.log, "");
    try {
      // Native join exercises the host's real path flavor, including Win32 in
      // Windows CI. All locations declare the parent of native-project as the
      // runtime; a project .loopx registry must first leave its .loopx folder.
      const project = join(f.root, "native-project");
      const locations = [
        {path: join(project, ".loopx", "registry.json"), role: "project-local"},
        {path: join(project, "registry.json"), role: "project-local"},
        {path: join(project, "registry.global.json"), role: "global-local"},
      ];
      const logBefore = await readFile(f.log), authorityBefore = await f.store.loadAuthority();
      for (const location of locations) {
        await withNativeAdmission(f.root, async binding => {
          const result = await readQuotaSettlement(request(f.root, {...binding,
            native_work_admission: {check_prior_closeout_only: true}}));
          assert.equal((result.native_work_admission as JsonObject).prior_closeout_checked, true, location.path);
          assert.equal((result.native_work_admission as JsonObject).current_task_lease_proof, null);
          assert.deepEqual(await readFile(f.log), logBefore);
          assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
        }, agentId, {exact: false, publication: false, registryPath: location.path,
          declaredRuntimeRoot: "..", registryRole: location.role});
      }
    } finally {await rm(f.root, {recursive: true, force: true});}
  });
}

function nativeWorkDetails(work = identity, version = 120): JsonObject {
  return {todo_id: work.todo_id, selected_todo_id: work.todo_id, turn_instance_id: work.turn_instance_id,
    settlement_effect_id: work.effect_id, ok: true, should_run: true, must_attempt_work: true,
    delivery_allowed: true, quiet_noop_allowed: false, closeout_required: true, prepared_lease_required: true,
    prepared_lease_key_sha256: createHash("sha256").update("native-execution").digest("hex"),
    prepared_lease_version: version, prepared_lease_epoch: 50};
}

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: native primary positive admission uses current CAS and proves its own closeout debt`, async () => {
    const lease: JsonObject = {schema_version: "task_lease_v0", goal_id: goalId, todo_id: todoId,
      owner: agentId, status: "active", idempotency_key: "native-execution", version: 120, lease_epoch: 50,
      acquired_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:01Z", expires_at: "2099-01-01T00:00:00Z"};
    const f = await todoSemanticFixture(provider, undefined, [lease]);
    await writeFile(f.log, "");
    try {
      await withNativeAdmission(f.root, async binding => {
        const readRequest = {...request(f.root), ...binding};
        const before = await readFile(f.log), authorityBefore = await f.store.loadAuthority();
        const checked = await readQuotaSettlement({...readRequest,
          native_work_admission: {check_prior_closeout_only: true}});
        assert.deepEqual(checked.native_work_admission, {prior_closeout_checked: true, append: false, details: null,
          source_event_id: null, original_prepared: null, current_task_lease_proof: null});
        const first = await readQuotaSettlement({...readRequest, native_work_admission: {details: nativeWorkDetails()}});
        const admitted = requireJsonObject(first.native_work_admission, "native admission");
        assert.equal(admitted.append, true);
        assert.equal(admitted.source_event_id, null);
        assert.equal(Object.hasOwn(admitted.details as JsonObject, "settlement_receipt_revision"), false);
        assert.deepEqual(await readFile(f.log), before);
        assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
        const event: JsonObject = {schema_version: "loopx_rollout_event_v0", event_kind: "quota_should_run",
          event_id: "native-primary", goal_id: goalId, agent_id: agentId, run_id: turnId, status: "normal_run",
          recorded_at: "2026-01-01T00:00:00Z", goal_ref: binding.goal_ref, details: admitted.details};
        await appendFile(f.log, JSON.stringify(event) + "\n");
        const replay = await readQuotaSettlement({...readRequest, native_work_admission: {details: nativeWorkDetails()}});
        assert.equal((replay.native_work_admission as JsonObject).append, false);
        assert.deepEqual(replay.original_prepared_receipt, event);
        const cleanup = await readQuotaSettlement({...readRequest, native_task_lease_cleanup: cleanupRequest});
        assert.deepEqual(cleanup.native_task_lease_cleanup, {schema_version: NATIVE_TASK_LEASE_CLEANUP_SCHEMA,
          disposition: "retain", reason: "original_turn_owes_closeout"});
        const saved = settlementIdentity({goal_id: goalId, agent_id: agentId, todo_id: todoId,
          turn_instance_id: turnId + ".saved-candidate"});
        for (const candidate of [{check_prior_closeout_only: true}, {details: nativeWorkDetails(saved)}]) {
          await assert.rejects(readQuotaSettlement({...readRequest, turn_instance_id: saved.turn_instance_id,
            native_work_admission: candidate}), {code: "refresh_task_lease_unavailable"});
        }
        const after = await readFile(f.log);
        for (const changed of [nativeWorkDetails(identity, 119), {...nativeWorkDetails(), delivery_allowed: false}]) {
          await assert.rejects(readQuotaSettlement({...readRequest, native_work_admission: {details: changed}}),
            {code: "refresh_task_lease_unavailable"});
        }
        assert.deepEqual(await readFile(f.log), after);
        assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
      });
    } finally {await rm(f.root, {recursive: true, force: true});}
  });
  test(`${provider}: native admission preserves exact registry role, Agent, store root and retained resource fences`, async () => {
    const f = await todoSemanticFixture(provider);
    await writeFile(f.log, "");
    try {
      await withNativeAdmission(f.root, async (binding, registry) => {
        const native = requireJsonObject(binding.source_admission, "native owner");
        const before = await readFile(f.log), authorityBefore = await f.store.loadAuthority();
        const originalRegistry = await readFile(registry);
        for (const patch of [{goal_instance_id: instanceB}, {status: "complete"},
          {coordination: {registered_agents: ["other-agent"]}}]) {
          const document = JSON.parse(originalRegistry.toString("utf8"));
          document.goals[0] = {...document.goals[0], ...patch};
          const bytes = JSON.stringify(document);
          await writeFile(registry, bytes);
          const changed = {...native, registry_sources: [{path: registry,
            sha256: createHash("sha256").update(bytes).digest("hex")}]};
          await assert.rejects(readQuotaSettlement(request(f.root, {...binding, source_admission: changed,
            native_work_admission: {check_prior_closeout_only: true}})), {code: "goal_instance_conflict"});
        }
        for (const patch of [{registry_role: "public-safe-projection"}, {profile_id: "source_session_v1"},
          {common_runtime_root: join(f.root, "different-store")}, {common_runtime_root: undefined}]) {
          const bytes = JSON.stringify({...JSON.parse(originalRegistry.toString("utf8")), ...patch});
          await writeFile(registry, bytes);
          await assert.rejects(readQuotaSettlement(request(f.root, {...binding, source_admission: {...native,
            registry_sources: [{path: registry, sha256: createHash("sha256").update(bytes).digest("hex")}]},
          native_work_admission: {check_prior_closeout_only: true}})), {code: "goal_instance_conflict"});
        }
        await writeFile(registry, originalRegistry);
        for (const patch of [{locks: (native.locks as JsonObject[]).slice(0, -1)},
          {locks: (native.locks as JsonObject[]).map((lock, index) => index === 2 ? {...lock, role: "source_guard"} : lock)},
          {planned_goal_ref: {goal_id: goalId, goal_instance_id: instanceB}}, {runtime_root: join(f.root, "other-root")}]) {
          await assert.rejects(readQuotaSettlement(request(f.root, {...binding, source_admission: {...native, ...patch},
            native_work_admission: {check_prior_closeout_only: true}})), {code: "quota_source_admission_invalid"});
        }
        await assert.rejects(readQuotaSettlement(request(f.root, {...binding, agent_id: "other-agent",
          native_work_admission: {check_prior_closeout_only: true}})), {code: "quota_source_admission_invalid"});
        for (const candidate of [{check_prior_closeout_only: false}, {check_prior_closeout_only: true, details: {}},
          {details: {}, resume_lease_candidate: {requested_proof: null}}]) {
          await assert.rejects(readQuotaSettlement(request(f.root, {...binding, native_work_admission: candidate})));
        }
        assert.deepEqual(await readFile(f.log), before);
        assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
      });
      await withNativeAdmission(f.root, async binding => {
        const pure = await readQuotaSettlement(request(f.root, binding));
        assert.equal(Object.hasOwn(pure, "native_work_admission"), false);
        await assert.rejects(readQuotaSettlement(request(f.root, {...binding,
          native_work_admission: {check_prior_closeout_only: true}})), {code: "quota_source_admission_invalid"});
      }, null);
      await assert.rejects(readQuotaSettlement(request(f.root, {native_work_admission: {check_prior_closeout_only: true}})),
        {code: "quota_source_admission_invalid"});
    } finally {await rm(f.root, {recursive: true, force: true});}
  });
  test(`${provider}: alias native primary keeps absent GoalRef and general reads need no publication locks`, async () => {
    const lease: JsonObject = {schema_version: "task_lease_v0", goal_id: goalId, todo_id: todoId,
      owner: agentId, status: "active", idempotency_key: "native-execution", version: 120, lease_epoch: 50,
      acquired_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:01Z", expires_at: "2099-01-01T00:00:00Z"};
    const f = await todoSemanticFixture(provider, undefined, [lease]);
    await writeFile(f.log, "");
    try {
      await withNativeAdmission(f.root, async binding => {
        const admission = await readQuotaSettlement(request(f.root, {...binding,
          native_work_admission: {details: nativeWorkDetails()}}));
        assert.equal((admission.native_work_admission as JsonObject).append, true);
        assert.equal(Object.hasOwn(binding, "goal_ref"), false);
        assert.equal(Object.hasOwn(admission, "goal_ref"), false);
        assert.equal(Object.hasOwn((admission.native_work_admission as JsonObject).details as JsonObject, "goal_ref"), false);
      }, agentId, {exact: false});
      await withNativeAdmission(f.root, async binding => {
        const pure = await readQuotaSettlement(request(f.root, binding));
        assert.equal(Object.hasOwn(pure, "native_work_admission"), false);
        const checked = await readQuotaSettlement(request(f.root, {...binding,
          native_work_admission: {check_prior_closeout_only: true}}));
        assert.equal((checked.native_work_admission as JsonObject).current_task_lease_proof, null);
        await assert.rejects(readQuotaSettlement(request(f.root, {...binding,
          native_work_admission: {details: nativeWorkDetails()}})), {code: "quota_source_admission_invalid"});
      }, agentId, {publication: false});
    } finally {await rm(f.root, {recursive: true, force: true});}
  });
  test(`${provider}: prior native debt blocks its Todo while independent work keeps its own admission`, async () => {
    const {todo} = await import("./shadow_file_fixture.ts");
    const otherId = "todo_independent";
    const f = await todoSemanticFixture(provider, undefined, [], [{...todo(otherId), claimed_by: agentId}]);
    const original: JsonObject = {schema_version: "loopx_rollout_event_v0", event_kind: "quota_should_run",
      event_id: "original-debt", goal_id: goalId, agent_id: agentId, run_id: turnId, status: "normal_run",
      recorded_at: "2026-01-01T00:00:00Z", goal_ref: {goal_id: goalId, goal_instance_id: instanceA}, details: nativeWorkDetails()};
    await writeFile(f.log, JSON.stringify(original) + "\n");
    try {
      await withNativeAdmission(f.root, async binding => {
        const before = await readFile(f.log), authorityBefore = await f.store.loadAuthority();
        await assert.rejects(readQuotaSettlement(request(f.root, {...binding, turn_instance_id: turnId + ".saved-candidate",
          native_work_admission: {check_prior_closeout_only: true}})), {code: "refresh_task_lease_unavailable"});
        const independent = await readQuotaSettlement(request(f.root, {...binding, todo_id: otherId,
          turn_instance_id: "independent-new-turn", native_work_admission: {check_prior_closeout_only: true}}));
        assert.equal((independent.native_work_admission as JsonObject).prior_closeout_checked, true);
        assert.equal((independent.native_work_admission as JsonObject).current_task_lease_proof, null);
        assert.deepEqual(await readFile(f.log), before);
        assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
        for (const patch of [{agent_id: null}, {run_id: null}, {goal_ref: undefined},
          {goal_ref: {goal_id: goalId, goal_instance_id: "malformed-instance"}}]) {
          const malformed = JSON.stringify({...original, ...patch}) + "\n";
          await writeFile(f.log, malformed);
          await assert.rejects(readQuotaSettlement(request(f.root, {...binding,
            turn_instance_id: turnId + ".saved-candidate",
            native_work_admission: {check_prior_closeout_only: true}})), {code: "heartbeat_receipt_identity_conflict"});
          assert.equal(await readFile(f.log, "utf8"), malformed);
        }
      }, agentId, {publication: false});
    } finally {await rm(f.root, {recursive: true, force: true});}
  });
}

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: Todo-bound semantic writeback without business lease remains operation scoped`, async () => {
    const f = await todoSemanticFixture(provider);
    try {
      const logBefore = await readFile(f.log), authorityBefore = await f.store.loadAuthority();
      const input: JsonObject = {...request(f.root), ...todoSemanticWriteback()};
      const result = await readQuotaSettlement(input);
      assert.deepEqual((result.writeback_qualification as any).result.value, {
        step_kind: "durable_writeback", effect_ref: `${identity.effect_id}#durable_writeback`,
        delivery_boundary: "semantic_closeout", original_prepared: null,
        selected_obligation_id: selectedWritebackObligation});
      assert.equal((result.writeback_qualification as any).payload.ok, true);
      assert.equal(result.resume_lease_qualification, undefined);
      assert.equal(result.prepared_lease, undefined);
      assert.equal(Object.hasOwn(result, "prepared_lease"), false);
      assert.equal(result.completion_event, null);
      await assert.rejects(readQuotaSettlement({...request(f.root), resume_lease_candidate: {requested_proof: null}}),
        {code: "refresh_task_lease_unavailable"});
      const candidate = input.writeback_candidate as JsonObject, run = candidate.run as JsonObject;
      for (const patch of [
        {requested_proof: {idempotency_key: "invented-business-proof", expected_version: 1}},
        {completion_todo_id: todoId}, {completion_turn_key: turnId},
        {run: {...run, autonomous_replan_ack: {...run.autonomous_replan_ack as JsonObject,
          semantic_delta: {...(run.autonomous_replan_ack as any).semantic_delta, outcomes: ["new_concrete_blocker"]}}}},
        {run: {...run, settlement_identity: {...settlementIdentityPayload(identity), todo_id: "todo_foreign"}}},
      ]) await assert.rejects(readQuotaSettlement({...input, writeback_candidate: {...candidate, ...patch}}),
        {code: "refresh_task_lease_unavailable"});
      const retry = input.refresh_retry as JsonObject;
      const progress = run.progress_observation as JsonObject;
      for (const observation of [{...progress, work_item_id: "todo_foreign", evidence_ids: ["evidence:foreign"]},
        {...progress, work_item_id: undefined}, {...progress, evidence_ids: ["untyped evidence"]}]) {
        await assert.rejects(readQuotaSettlement({...input, refresh_retry: {...retry, progress_observation: observation},
          writeback_candidate: {...candidate, run: {...run, progress_observation: observation}}}));
      }
      // All real retry metadata must bind the actual Run, including mutation
      // values under the existing recovery digest owner.
      for (const patch of [{delivery_batch_scale: "multi_surface"},
        {mutation: {autonomous_replan_recorded: true, next_action: "Different next step"}}]) {
        await assert.rejects(readQuotaSettlement({...input, refresh_retry: {...retry, ...patch}}),
          {code: "refresh_task_lease_unavailable"});
      }
      await assert.rejects(readQuotaSettlement({...input, writeback_candidate: {...candidate, run: {...run,
        refresh_recovery: {...run.refresh_recovery as JsonObject, mutation_digest: "0".repeat(64)}}}}),
        {code: "refresh_task_lease_unavailable"});
      const scaled = {...retry, delivery_batch_scale: "single_surface"};
      const scaledResult = await readQuotaSettlement({...input, refresh_retry: scaled,
        writeback_candidate: {...candidate, run: {...run, delivery_batch_scale: "single_surface"}}});
      assert.equal((scaledResult.writeback_qualification as any).payload.ok, true);
      for (const patch of [{external_delivery: {suppress: false, resume_key: "a".repeat(64)}},
        {workspace_requested: true}, {checkpoint_read_context_id: "checkpoint:foreign"}]) {
        const changedRetry = {...retry, ...patch};
        await assert.rejects(readQuotaSettlement({...input, refresh_retry: changedRetry,
          writeback_candidate: {...candidate, run: {...run,
            refresh_recovery: refreshRecovery(decodeRefreshRetry(changedRetry)!, null, false, undefined, false)}}}),
          {code: "refresh_task_lease_unavailable"});
      }
      await assert.rejects(readQuotaSettlement({...input, refresh_retry: {...retry, unsupported_operation: true}}),
        /unsupported operation field/);
      const noProgress = {...input, refresh_retry: {...input.refresh_retry as JsonObject, progress_observation: null},
        writeback_candidate: {...candidate, run: {...run, progress_observation: null}}};
      await assert.rejects(readQuotaSettlement(noProgress), {code: "refresh_task_lease_unavailable"},
        "Proposed accepted=true cannot replace the typed observation");
      for (const patch of [{refresh_retry: undefined}, {resume_lease_candidate: {requested_proof: null}},
        {infer_turn_instance_id: true}, {allow_unbound_binding: true}, {resolve_original_binding: true},
        {pending_closeout: true}, {spend_precheck: true}, {tolerate_malformed_history: true},
        {replan_obligation_id: selectedWritebackObligation}, {native_task_lease_cleanup: cleanupRequest}]) {
        await assert.rejects(readQuotaSettlement({...input, ...patch}));
      }
      assert.deepEqual(await readFile(f.log), logBefore);
      assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
    } finally {await rm(f.root, {recursive: true, force: true});}
  });

  test(`${provider}: semantic writeback requires actual selected admission and current Todo lifetime`, async () => {
    const f = await todoSemanticFixture(provider);
    try {
      const input: JsonObject = {...request(f.root), ...todoSemanticWriteback()};
      for (const patch of [{must_attempt_work: false}, {should_run: false}, {ok: false},
        {quiet_noop_allowed: true}, {closeout_required: false}, {delivery_allowed: true},
        {interaction_mode: "control_plane_self_repair"}, {semantic_replan_obligation_id: ""},
        {semantic_replan_review_kind: undefined}, {prepared_lease_required: true},
        {prepared_lease_key_sha256: "a".repeat(64)}, {prepared_lease_version: 119}, {prepared_lease_epoch: 50}]) {
        await writeFile(f.log, JSON.stringify({...f.admission, details: {...f.admission.details as JsonObject, ...patch}}) + "\n");
        await assert.rejects(readQuotaSettlement(input), JSON.stringify(patch));
      }
    } finally {await rm(f.root, {recursive: true, force: true});}
    const {todo} = await import("./shadow_file_fixture.ts");
    for (const selected of [{...todo(todoId), claimed_by: "foreign-agent"},
      {...todo(todoId, "done"), claimed_by: null}, {...todo(todoId, "deferred"), claimed_by: agentId}]) {
      const denied = await todoSemanticFixture(provider, selected);
      try {
        await assert.rejects(readQuotaSettlement({...request(denied.root), ...todoSemanticWriteback()}),
          {code: "refresh_task_lease_unavailable"});
      } finally {await rm(denied.root, {recursive: true, force: true});}
    }
  });
}

test("semantic admission follows one causal source and cannot substitute another obligation", () => {
  const admitted = todoSemanticAdmission();
  const reservation = {...admitted, event_id: "semantic-reserved", details: {...admitted.details as JsonObject,
    closeout_required: false, should_run: false, must_attempt_work: false, quiet_noop_allowed: true}};
  const causal: JsonObject = {...admitted, details: {...admitted.details as JsonObject, settlement_receipt_revision: "work_admission"},
    causality: {source_event_id: reservation.event_id, caused_by: reservation.event_id}};
  const renewed = {...causal, event_id: "semantic-readiness", details: {...causal.details as JsonObject, settlement_receipt_revision: "lease_readiness"},
    causality: {source_event_id: causal.event_id, caused_by: causal.event_id}};
  assert.deepEqual(heartbeatSemanticAdmissionEvent([reservation, causal, renewed], renewed, identity), causal);
  assert.equal(heartbeatSemanticAdmissionEvent([reservation], reservation, identity), null);
  for (const patch of [{source_event_id: "missing", caused_by: "missing"},
    {source_event_id: causal.event_id, caused_by: "another-parent"}]) {
    const invalid = {...renewed, causality: patch};
    assert.equal(heartbeatSemanticAdmissionEvent([reservation, causal, invalid], invalid, identity), null);
  }
  const changed = {...causal, event_id: "semantic-replacement", details: {...causal.details as JsonObject,
    semantic_replan_obligation_id: "replan-2222222222222222"}};
  assert.throws(() => heartbeatSemanticAdmissionEvent([causal, changed], changed, identity),
    {code: "heartbeat_receipt_identity_conflict"});
});


for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: uncaused scalar receipt cannot downgrade the original capability admission`, async () => {
    const f = await todoSemanticFixture(provider);
    try {
      const originalDetails: JsonObject = {...f.admission.details as JsonObject};
      delete originalDetails.semantic_replan_review_kind;
      delete originalDetails.semantic_replan_review_stop_condition;
      // Use the existing capability fixture's typed transport shape. A and B
      // retain the exact same Goal, Agent, Todo, Turn, effect and obligation;
      // the later scalar receipt has no causal authority to replace A's duty.
      Object.assign(originalDetails, {semantic_replan_capability_id: "explore",
        semantic_replan_gap_id: "research-composition-0123456789abcdef",
        semantic_replan_frontier_revision: "research-composition-v0:0123456789abcdef"});
      const original: JsonObject = {...f.admission, event_id: "original-capability-admission", details: originalDetails};
      const later: JsonObject = {...f.admission, event_id: "uncaused-scalar-admission",
        recorded_at: "2026-01-01T00:00:01Z"};
      const input: JsonObject = {...request(f.root), ...todoSemanticWriteback()};
      const authorityBefore = await f.store.loadAuthority();
      await writeFile(f.log, JSON.stringify(original) + "\n");
      await assert.rejects(readQuotaSettlement(input), {code: "refresh_task_lease_unavailable"},
        "The original capability duty must use its capability owner");
      assert.equal(Object.hasOwn(later, "causality"), false);
      await writeFile(f.log, [original, later].map(row => JSON.stringify(row)).join("\n") + "\n");
      await assert.rejects(readQuotaSettlement(input), (error: unknown) => error instanceof Error && "code" in error
        && (error.code === "heartbeat_receipt_identity_conflict" || error.code === "refresh_task_lease_unavailable"),
        "An uncaused scalar receipt must not turn an original capability duty into generic progress permission");
      assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
    } finally {await rm(f.root, {recursive: true, force: true});}
  });
}


for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: semantic guard retains identical uncaused and causal original admissions`, async () => {
    const f = await todoSemanticFixture(provider);
    try {
      const original: JsonObject = {...f.admission, event_id: "original-scalar-source"};
      const repeated: JsonObject = {...f.admission, event_id: "identical-uncaused-source",
        recorded_at: "2026-01-01T00:00:01Z"};
      const causal: JsonObject = {...repeated, event_id: "causal-scalar-source",
        details: {...repeated.details as JsonObject, settlement_receipt_revision: "lease_readiness"},
        causality: {source_event_id: original.event_id, caused_by: original.event_id}};
      const input: JsonObject = {...request(f.root), ...todoSemanticWriteback()};
      for (const later of [repeated, causal]) {
        const events = [original, later];
        assert.deepEqual(heartbeatSemanticAdmissionEvent(events, later, identity), original);
        await writeFile(f.log, events.map(row => JSON.stringify(row)).join("\n") + "\n");
        const result = await readQuotaSettlement(input);
        assert.equal((result.writeback_qualification as {payload: {ok: boolean}}).payload.ok, true);
      }
    } finally {await rm(f.root, {recursive: true, force: true});}
  });

  test(`${provider}: uncaused semantic source changes reject under the same obligation`, async () => {
    const f = await todoSemanticFixture(provider);
    try {
      const scalar: JsonObject = {...f.admission, event_id: "original-scalar-source"};
      const capDetails: JsonObject = {...f.admission.details as JsonObject};
      delete capDetails.semantic_replan_review_kind;
      delete capDetails.semantic_replan_review_stop_condition;
      Object.assign(capDetails, {semantic_replan_capability_id: "explore",
        semantic_replan_gap_id: "research-composition-0123456789abcdef",
        semantic_replan_frontier_revision: "research-composition-v0:0123456789abcdef"});
      const capability: JsonObject = {...f.admission, event_id: "original-capability-source", details: capDetails};
      const cases: {original: JsonObject; patch: JsonObject}[] = [
        {original: scalar, patch: {semantic_replan_review_kind: "vision_acceptance_gap"}},
        {original: scalar, patch: {semantic_replan_review_stop_condition: "Stop at destructive owner-only decisions."}},
        {original: capability, patch: {semantic_replan_capability_id: "research"}},
        {original: capability, patch: {semantic_replan_gap_id: "research-composition-fedcba9876543210"}},
        {original: capability, patch: {semantic_replan_frontier_revision: "research-composition-v0:fedcba9876543210"}},
      ];
      const input: JsonObject = {...request(f.root), ...todoSemanticWriteback()};
      for (const {original, patch} of cases) {
        const later: JsonObject = {...original, event_id: "changed-uncaused-source", recorded_at: "2026-01-01T00:00:01Z",
          details: {...original.details as JsonObject, ...patch}};
        await writeFile(f.log, [original, later].map(row => JSON.stringify(row)).join("\n") + "\n");
        assert.throws(() => heartbeatSemanticAdmissionEvent([original, later], later, identity),
          {code: "heartbeat_receipt_identity_conflict"}, JSON.stringify(patch));
        await assert.rejects(readQuotaSettlement(input), {code: "heartbeat_receipt_identity_conflict"}, JSON.stringify(patch));
      }
    } finally {await rm(f.root, {recursive: true, force: true});}
  });
}


function todoBlockedSemanticWriteback(wait: JsonObject): JsonObject {
  const input = todoSemanticWriteback(), candidate = input.writeback_candidate as JsonObject;
  const run = candidate.run as JsonObject;
  const progress = {schema_version: "typed_progress_observation_v0", work_item_id: todoId, result_class: "blocked",
    blocker_id: "blocker:verified", fingerprint: "abcdef0123456789", evidence_ids: ["evidence:verified-blocker"]};
  const retry = {...input.refresh_retry as JsonObject, vision: null, delivery_outcome: "outcome_gap", progress_observation: progress};
  const checkpoint = buildVisionCheckpoint({schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "finalize",
    agent_id: agentId, agent_vision: null, existing_agent_vision: null, vision_unchanged_reason: null,
    delivery_outcome: "outcome_gap", active_state_next_action_would_update: false,
    delivery_boundary: "semantic_closeout", todo_id: todoId, completion_todo_id: null,
    autonomous_replan_recorded: true, blocked_retry: wait});
  return {refresh_retry: retry, writeback_candidate: {...candidate, run: {...run, agent_vision: null,
    delivery_outcome: "outcome_gap", progress_observation: progress, blocked_retry: wait, vision_checkpoint: checkpoint,
    autonomous_replan_ack: {schema_version: "autonomous_replan_ack_v0", recorded: true, semantic_delta: {
      schema_version: "replan_semantic_delta_v0", accepted: true,
      outcomes: ["new_concrete_blocker"], satisfying_outcomes: ["new_concrete_blocker"],
      required_any_of: ["new_surface", "new_hypothesis", "new_probe_family", "new_runnable_successor",
        "coverage_backed_exploration_exhausted", "new_concrete_blocker", "coverage_backed_no_followup"],
      observation_fingerprint: progress.fingerprint}},
    refresh_recovery: refreshRecovery(decodeRefreshRetry(retry)!, null, false, undefined, false)}}};
}

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: first writeback keeps external policy separate and NextAction journal only`, async () => {
    const f = await todoSemanticFixture(provider);
    try {
      const original = todoSemanticWriteback(), candidate = original.writeback_candidate as JsonObject;
      const run = candidate.run as JsonObject;
      const authorityBefore = await f.store.loadAuthority(), logBefore = await readFile(f.log);
      for (const suppress of [false, true]) {
        const retry = {...original.refresh_retry as JsonObject, external_delivery: {suppress, resume_key: null},
          mutation: {autonomous_replan_recorded: true, next_action: "Record the next useful step", next_action_basis: "verified-result"}};
        const result = await readQuotaSettlement({...request(f.root), refresh_retry: retry,
          writeback_candidate: {...candidate, run: {...run,
            refresh_recovery: refreshRecovery(decodeRefreshRetry(retry)!, null, false, undefined, false)}}});
        const qualified = result.writeback_qualification as {result: {value: JsonObject}; payload: {ok: boolean}};
        assert.equal(qualified.payload.ok, true);
        assert.deepEqual(Object.keys(qualified.result.value).sort(), ["delivery_boundary", "effect_ref", "original_prepared",
          "selected_obligation_id", "step_kind"]);
        assert.equal((result.external_delivery as JsonObject).authorized, !suppress);
        assert.equal(Object.hasOwn(qualified.result.value, "authorized"), false);
        assert.equal(Object.hasOwn(qualified.result.value, "current_task_lease_proof"), false);
      }
      assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
      assert.deepEqual(await readFile(f.log), logBefore);
    } finally {await rm(f.root, {recursive: true, force: true});}
  });

  test(`${provider}: blocked semantic writeback uses the actual clock and raw canonical wait`, async () => {
    const {todo} = await import("./shadow_file_fixture.ts");
    const selected = {...todo(todoId), task_class: "advancement_task", claimed_by: agentId,
      resume_when: "resume_at:2026-01-01T00:06:00Z"};
    const f = await todoSemanticFixture(provider, selected);
    try {
      const wait = {schema_version: "quota_blocked_retry_v0", source: "todo", todo_id: todoId,
        resume_when: selected.resume_when, observed_at: "2026-01-01T00:01:00Z", due_at: "2026-01-01T00:06:00Z"};
      const payload = todoBlockedSemanticWriteback(wait), candidate = payload.writeback_candidate as JsonObject;
      const run = candidate.run as JsonObject;
      const input = {...request(f.root), ...payload};
      const result = await readQuotaSettlement(input);
      assert.equal((result.writeback_qualification as {payload: {ok: boolean}}).payload.ok, true);
      assert.equal(Object.hasOwn(selected, "resume_condition"), false);
      assert.equal(Object.hasOwn(selected, "resume_ready"), false);
      for (const patch of [{generated_at: undefined}, {generated_at: "2026-01-01T00:02:00Z"},
        {generated_at: "2026-01-01T00:00:00Z"}, {generated_at: "2026-01-01T00:01:00"},
        {blocked_retry: null}, {blocked_retry: {...wait, todo_id: "todo_foreign"}},
        {blocked_retry: {...wait, observed_at: "2026-01-01T00:00:00Z"}},
        {blocked_retry: {...wait, due_at: "2026-01-01T00:07:00Z", resume_when: "resume_at:2026-01-01T00:07:00Z"}}]) {
        await assert.rejects(readQuotaSettlement({...input, writeback_candidate: {...candidate, run: {...run, ...patch}}}),
          {code: "refresh_task_lease_unavailable"}, JSON.stringify(patch));
      }
      const advanced = todoSemanticWriteback(), advancedCandidate = advanced.writeback_candidate as JsonObject;
      await assert.rejects(readQuotaSettlement({...request(f.root), ...advanced, writeback_candidate: {...advancedCandidate,
        run: {...advancedCandidate.run as JsonObject, blocked_retry: wait}}}), {code: "refresh_task_lease_unavailable"});
    } finally {await rm(f.root, {recursive: true, force: true});}
  });

  test(`${provider}: blocked semantic writeback checks the complete canonical dependency graph`, async () => {
    const {todo} = await import("./shadow_file_fixture.ts");
    const waiting = {...todo(todoId), task_class: "advancement_task", claimed_by: agentId, resume_when: "todo_done:todo_dependency"};
    const target = {...todo("todo_dependency"), task_class: "advancement_task", resume_when: "todo_done:todo_tail"};
    const wait = {schema_version: "quota_blocked_causal_wait_v0", source: "todo", todo_id: todoId,
      resume_when: waiting.resume_when, observed_at: "2026-01-01T00:01:00Z",
      waiting_todo: {todo_id: todoId, role: "agent", status: "open", task_class: "advancement_task", archive_state: "active",
        resume_when: waiting.resume_when, resume_ready: false},
      target_todo: {todo_id: "todo_dependency", role: "agent", status: "open", task_class: "advancement_task", archive_state: "active",
        resume_when: target.resume_when, resume_ready: false}};
    for (const cyclic of [false, true]) {
      const tail = {...todo("todo_tail"), task_class: "advancement_task",
        ...(cyclic ? {resume_when: "todo_done:todo_dependency"} : {})};
      const f = await todoSemanticFixture(provider, waiting, [], [target, tail]);
      try {
        const input = {...request(f.root), ...todoBlockedSemanticWriteback(wait)};
        if (cyclic) await assert.rejects(readQuotaSettlement(input), {code: "refresh_task_lease_unavailable"},
          "The waiting Todo must not qualify a cycle hidden beyond its direct target");
        else assert.equal(((await readQuotaSettlement(input)).writeback_qualification as {payload: {ok: boolean}}).payload.ok, true);
      } finally {await rm(f.root, {recursive: true, force: true});}
    }
  });
}


for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: no-business semantic writeback cannot discharge a negative checkpoint`, async () => {
    const {todo} = await import("./shadow_file_fixture.ts");
    const waiting = {...todo(todoId), task_class: "advancement_task", claimed_by: agentId,
      resume_when: "resume_at:2026-01-01T00:06:00Z"};
    const f = await todoSemanticFixture(provider, waiting);
    try {
      const wait = {schema_version: "quota_blocked_retry_v0", source: "todo", todo_id: todoId,
        resume_when: waiting.resume_when, observed_at: "2026-01-01T00:01:00Z", due_at: "2026-01-01T00:06:00Z"};
      const original = todoBlockedSemanticWriteback(wait), candidate = original.writeback_candidate as JsonObject;
      const run = candidate.run as JsonObject;
      const retry = {...original.refresh_retry as JsonObject, unchanged_reason: "Prior route remains."};
      const checkpoint = buildVisionCheckpoint({schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "finalize",
        agent_id: agentId, agent_vision: null, existing_agent_vision: null, vision_unchanged_reason: retry.unchanged_reason,
        delivery_outcome: "outcome_gap", active_state_next_action_would_update: false, delivery_boundary: "semantic_closeout",
        todo_id: todoId, completion_todo_id: null, autonomous_replan_recorded: true, blocked_retry: wait});
      assert.equal(checkpoint.satisfied, false);
      assert.equal(checkpoint.decision, "missing_required");
      await assert.rejects(readQuotaSettlement({...request(f.root), refresh_retry: retry,
        writeback_candidate: {...candidate, run: {...run, vision_checkpoint: checkpoint,
          refresh_recovery: refreshRecovery(decodeRefreshRetry(retry)!, null, false, undefined, false)}}}),
        {code: "refresh_task_lease_unavailable"});
    } finally {await rm(f.root, {recursive: true, force: true});}
  });
}


async function recoveryJournalFixture(root: string, goalRef?: JsonObject): Promise<{path: string; key: string}> {
  const key = `sha256:${"c".repeat(64)}`;
  const directory = join(root, "goals", goalId, "turns");
  await mkdir(directory, {recursive: true});
  const path = join(directory, `${"c".repeat(64)}.json`);
  await writeFile(path, JSON.stringify({schema_version: "loopx_turn_journal_v0", goal_id: goalId, turn_key: key,
    status: "in_progress", completed_phases: [], plan: {...(goalRef === undefined ? {} : {goal_ref: goalRef}),
      turn_envelope: {goal_id: goalId, agent_id: agentId, action: {selected_todo: {todo_id: todoId}}},
      transaction: {turn_key: key, turn_instance_id: turnId, ...(goalRef === undefined ? {} : {goal_ref: goalRef}),
        settlement_plan: {schema_version: "quota_settlement_plan_v1", identity: settlementIdentityPayload(identity)}}}}));
  return {path, key};
}

test("read-only settlement recovery attaches only verified inspection addresses and original evidence", async () => {
  const root = await fixture();
  try {
    const journal = await recoveryJournalFixture(root);
    const log = join(root, "goals", goalId, "rollout-event-log.jsonl");
    const before = await readFile(journal.path), logBefore = await readFile(log);
    const ordinary = await readQuotaSettlement(request(root));
    assert.equal(Object.hasOwn(ordinary, "settlement_recovery"), false);
    const disabled = await readQuotaSettlement(request(root, {include_readonly_recovery: false}));
    assert.deepEqual(disabled, ordinary);
    assert.equal(JSON.stringify(disabled).length, JSON.stringify(ordinary).length);
    const result = await readQuotaSettlement(request(root, {include_readonly_recovery: true}));
    const recovery = requireJsonObject(result.settlement_recovery, "fixture recovery");
    assert.deepEqual(Object.keys(recovery).sort(), ["instruction", "next_cli_actions_argv", "original_settlement_readback", "required", "settlement_identity"]);
    assert.equal(recovery.required, true);
    assert.deepEqual(recovery.settlement_identity, settlementIdentityPayload(identity));
    assert.deepEqual(recovery.original_settlement_readback, {identity: ordinary.identity, settlement: ordinary.settlement,
      progress: ordinary.progress, replay_phase: ordinary.replay_phase, original_prepared_receipt: ordinary.original_prepared_receipt,
      original_prepared_event_id: ordinary.original_prepared_event_id, original_prepared_receipt_sha256: ordinary.original_prepared_receipt_sha256});
    assert.ok(JSON.stringify(recovery.original_settlement_readback).length < JSON.stringify(ordinary).length);
    assert.deepEqual(recovery.next_cli_actions_argv, [
      ["turn", "inspect-journal", "--goal-id", goalId, "--agent-id", agentId, "--turn-key", journal.key],
      ["task-lease", "inspect", "--goal-id", goalId, "--todo-id", todoId]]);
    assert.equal(String(recovery.instruction).includes("refresh-state --execution-loss-closeout"), false);
    assert.deepEqual(await readFile(journal.path), before);
    assert.deepEqual(await readFile(log), logBefore);
    const snapshot = await readQuotaSettlementSnapshot(root, goalId);
    assert.throws(() => readQuotaSettlementFromSnapshot(request(root, {include_readonly_recovery: true}), snapshot), /live journal read/);
    for (const incompatible of [{resume_lease_candidate: {requested_proof: null}}, {writeback_candidate: {}},
      {native_task_lease_cleanup: cleanupRequest}, {infer_turn_instance_id: true}, {allow_unbound_binding: true},
      {resolve_original_binding: true}, {pending_closeout: true}, {refresh_retry: {}}, {heartbeat_reentry_guard: {}},
      {spend_precheck: true}, {tolerate_malformed_history: true}, {agent_id: null}, {todo_id: null},
      {turn_instance_id: null}, {replan_obligation_id: selectedWritebackObligation}, {todo_id: " todo_settlement "}]) {
      await assert.rejects(readQuotaSettlement(request(root, {include_readonly_recovery: true, ...incompatible})));
    }
    await assert.rejects(readQuotaSettlement(request(root, {include_readonly_recovery: "true"})));
    await writeFile(journal.path, "{truncated");
    assert.deepEqual(await readQuotaSettlement(request(root)), ordinary);
    assert.deepEqual(await readQuotaSettlement(request(root, {include_readonly_recovery: false})), ordinary);
    await assert.rejects(readQuotaSettlement(request(root, {include_readonly_recovery: true})), {code: "journal_readback_conflict"});
    await rm(journal.path);
    await assert.rejects(readQuotaSettlement(request(root, {include_readonly_recovery: true})), {code: "refresh_task_lease_unavailable"});
  } finally {await rm(root, {recursive: true, force: true});}
});

test("read-only recovery describes administrative requirements only for original modern positive epoch admission", async () => {
  for (const epoch of [50, undefined]) {
    const root = await fixture();
    try {
      await recoveryJournalFixture(root);
      const log = join(root, "goals", goalId, "rollout-event-log.jsonl");
      const original: JsonObject = {schema_version: "loopx_rollout_event_v0", event_kind: "quota_should_run", event_id: "original-modern",
        goal_id: goalId, agent_id: agentId, run_id: turnId, status: "normal_run", details: {todo_id: todoId,
          selected_todo_id: todoId, settlement_effect_id: identity.effect_id, closeout_required: true,
          ok: true, should_run: true, must_attempt_work: true, delivery_allowed: true, quiet_noop_allowed: false,
          prepared_lease_required: true, prepared_lease_key_sha256: "a".repeat(64), prepared_lease_version: 119,
          ...(epoch === undefined ? {} : {prepared_lease_epoch: epoch})}};
      await writeFile(log, JSON.stringify(original) + "\n");
      const recovery = requireJsonObject((await readQuotaSettlement(request(root, {include_readonly_recovery: true}))).settlement_recovery,
        "fixture recovery");
      assert.equal(String(recovery.instruction).includes("refresh-state --execution-loss-closeout"), epoch !== undefined);
      if (epoch !== undefined) assert.match(String(recovery.instruction), /current registered control writer and exact current active lease\/CAS/);
      assert.deepEqual((recovery.next_cli_actions_argv as string[][]).map(argv => argv.slice(0, 2)), [["turn", "inspect-journal"], ["task-lease", "inspect"]]);
    } finally {await rm(root, {recursive: true, force: true});}
  }
});

test("read-only recovery preserves the original Source lifetime and never falls back to an alias journal", async () => {
  const goalRef = {goal_id: goalId, goal_instance_id: instanceA};
  const root = await fixture({goalRef});
  try {
    const journal = await recoveryJournalFixture(root, goalRef);
    await withSourceAdmission(root, instanceA, instanceA, async binding => {
      const input = request(root, {...binding, borrow_source_admission: true, include_readonly_recovery: true});
      const result = await readQuotaSettlement(input);
      assert.deepEqual((result.settlement_recovery as JsonObject).goal_ref, goalRef);
      await recoveryJournalFixture(root);
      await assert.rejects(readQuotaSettlement(input), {code: "refresh_task_lease_unavailable"});
      await recoveryJournalFixture(root, {goal_id: goalId, goal_instance_id: instanceB});
      await assert.rejects(readQuotaSettlement(input), {code: "refresh_task_lease_unavailable"});
    });
    assert.ok(journal.path);
  } finally {await rm(root, {recursive: true, force: true});}
  const alias = await fixture();
  try {
    await recoveryJournalFixture(alias, goalRef);
    await assert.rejects(readQuotaSettlement(request(alias, {include_readonly_recovery: true})), {code: "refresh_task_lease_unavailable"});
  } finally {await rm(alias, {recursive: true, force: true});}
});

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: exact active preparation cannot pass an accountable negative checkpoint`, async () => {
    const {todo} = await import("./shadow_file_fixture.ts");
    const leaseKey = "exact-active-accountable";
    const lease: JsonObject = {schema_version: "task_lease_v0", goal_id: goalId, todo_id: todoId, owner: agentId,
      status: "active", idempotency_key: leaseKey, version: 120, lease_epoch: 50,
      acquired_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:01:00Z", expires_at: "2099-01-01T00:00:00Z"};
    const f = await todoSemanticFixture(provider, {...todo(todoId), claimed_by: agentId, task_class: "advancement_task"}, [lease]);
    try {
      const positive = {...f.admission, details: {todo_id: todoId, selected_todo_id: todoId, settlement_effect_id: identity.effect_id,
        closeout_required: true, prepared_lease_required: true, prepared_lease_key_sha256: createHash("sha256").update(leaseKey).digest("hex"),
        prepared_lease_version: 119, prepared_lease_epoch: 50, ok: true, should_run: true,
        must_attempt_work: true, delivery_allowed: true, quiet_noop_allowed: false}};
      await writeFile(f.log, JSON.stringify(positive) + "\n");
      const original = todoSemanticWriteback(), candidate = original.writeback_candidate as JsonObject, run = candidate.run as JsonObject;
      const retry = {...original.refresh_retry as JsonObject, vision: null, unchanged_reason: "Prior route remains."};
      const checkpoint = buildVisionCheckpoint({schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "finalize",
        agent_id: agentId, agent_vision: null, existing_agent_vision: null, vision_unchanged_reason: retry.unchanged_reason,
        delivery_outcome: "outcome_progress", active_state_next_action_would_update: false, delivery_boundary: "semantic_closeout",
        todo_id: todoId, completion_todo_id: null, autonomous_replan_recorded: true, blocked_retry: null});
      assert.equal(checkpoint.decision, "missing_required");
      assert.equal(checkpoint.satisfied, false);
      await assert.rejects(readQuotaSettlement({...request(f.root), refresh_retry: retry, writeback_candidate: {...candidate,
        requested_proof: {idempotency_key: leaseKey, expected_version: 120}, run: {...run, agent_vision: null,
          vision_checkpoint: checkpoint, refresh_recovery: refreshRecovery(decodeRefreshRetry(retry)!, null, false, undefined, false)}}}),
        {code: "refresh_task_lease_unavailable"});
    } finally {await rm(f.root, {recursive: true, force: true});}
  });
}
