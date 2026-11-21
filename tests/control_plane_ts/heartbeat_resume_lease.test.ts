import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdir, mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {settlementIdentity, settlementIdentityPayload} from "../../loopx/control_plane/effect_program.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} from "../../loopx/control_plane/effect_runtime_handlers.ts";
import {
  HEARTBEAT_PREPARED_PUBLICATION_REQUEST_SCHEMA,
  qualifyHeartbeatPreparedPublication,
  qualifyHeartbeatResumeLease,
  heartbeatPreparedWorkAdmission,
  heartbeatValidationEvent,
} from "../../loopx/control_plane/quota/heartbeat_receipt_identity.ts";
import {EXECUTION_LOSS_QUALIFICATION_SCHEMA, qualifyReceiptBoundExecutionLossCloseout}
  from "../../loopx/control_plane/quota/execution_loss_closeout.ts";
import {
  QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
  readQuotaSettlement,
  readQuotaSettlementFromSnapshot,
  readQuotaSettlementForAdmittedOwnerFromSnapshot,
  readQuotaSettlementSnapshot,
} from "../../loopx/control_plane/quota/settlement_readback.ts";
import {buildVisionCheckpoint, VISION_REFRESH_REQUEST_SCHEMA, visionAuthoringContract}
  from "../../loopx/control_plane/goals/vision_checkpoint.ts";
import {decodeRefreshRetry, refreshRecovery} from "../../loopx/control_plane/quota/refresh_recovery.ts";
import {projection, todo} from "./shadow_file_fixture.ts";

const identity = settlementIdentity({goal_id: "goal-a", agent_id: "agent-a",
  todo_id: "todo_resume", turn_instance_id: "turn-original"});
const originalKey = "original-execution";
const originalPrepared = {prepared_lease_key_sha256: createHash("sha256").update(originalKey).digest("hex"),
  prepared_lease_version: 119, prepared_lease_epoch: 50};
const preparedReceipt: JsonObject = {schema_version: "loopx_rollout_event_v0",
  event_kind: "quota_should_run", event_id: "positive-prepared", goal_id: identity.goal_id,
  agent_id: identity.agent_id, run_id: identity.turn_instance_id, status: "normal_run",
  recorded_at: "2026-01-01T00:00:00Z", details: {todo_id: identity.todo_id,
    selected_todo_id: identity.todo_id, settlement_effect_id: identity.effect_id,
    closeout_required: true, prepared_lease_required: true, ok: true, should_run: true,
    must_attempt_work: true, delivery_allowed: true, quiet_noop_allowed: false, ...originalPrepared}};
const currentLease: JsonObject = {schema_version: "task_lease_v0", todo_id: identity.todo_id,
  goal_id: identity.goal_id, owner: identity.agent_id, status: "active", idempotency_key: originalKey,
  version: 120, lease_epoch: 50, acquired_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:01Z", expires_at: "2099-01-01T00:00:00Z"};
const completed: JsonObject = {schema_version: "loopx_rollout_event_v0", event_kind: "todo_complete",
  event_id: "completed-original", goal_id: identity.goal_id, agent_id: identity.agent_id,
  todo_id: identity.todo_id, run_id: identity.turn_instance_id,
  details: {settlement_effect_id: identity.effect_id}};

function resume(overrides: Partial<Parameters<typeof qualifyHeartbeatResumeLease>[0]> = {}) {
  return qualifyHeartbeatResumeLease({originalReceipt: preparedReceipt, identity,
    requestedProof: {idempotency_key: originalKey, expected_version: 120},
    todo: {...todo(identity.todo_id!), claimed_by: identity.agent_id}, lease: currentLease,
    handoffMode: "hard_lease", completionEvent: null, replayPhase: "open",
    closeoutStarted: false, writebackPending: true, otherTerminalStarted: false,
    exactSource: true, observedAt: new Date("2026-01-01T00:01:00Z"), ...overrides});
}

test("resume distinguishes current CAS from frozen prepared generation", () => {
  const before = structuredClone({preparedReceipt, currentLease});
  const renewed = resume();
  assert.deepEqual(renewed, {state: "ready", original_prepared: originalPrepared,
    current_task_lease_proof: {idempotency_key: originalKey, expected_version: 120},
    lease_epoch: 50, source: "active"});
  const exact = resume({lease: {...currentLease, version: 119},
    requestedProof: {idempotency_key: originalKey, expected_version: 119}});
  assert.equal(exact.state, "ready");
  assert.deepEqual(exact.original_prepared, originalPrepared);
  assert.deepEqual({preparedReceipt, currentLease}, before);
});

test("generation and active authority refusal matrix", () => {
  const denied: [string, Partial<Parameters<typeof qualifyHeartbeatResumeLease>[0]>][] = [
    ["stale supplied CAS", {requestedProof: {idempotency_key: originalKey, expected_version: 119}}],
    ["replacement P2", {lease: {...currentLease, idempotency_key: "replacement", lease_epoch: 51, version: 121},
      requestedProof: {idempotency_key: "replacement", expected_version: 121}}],
    ["same key new epoch", {lease: {...currentLease, lease_epoch: 51}}],
    ["older version", {lease: {...currentLease, version: 118},
      requestedProof: {idempotency_key: originalKey, expected_version: 118}}],
    ["missing current epoch", {lease: {...currentLease, lease_epoch: undefined}}],
    ["active null candidate", {requestedProof: null}],
    ["expired current lease", {lease: {...currentLease, expires_at: "2026-01-01T00:00:30Z"}}],
    ["foreign current holder", {lease: {...currentLease, owner: "agent-b"}}],
    ["foreign current claim", {todo: {...todo(identity.todo_id!), claimed_by: "agent-b"}}],
    ["closeout already started", {closeoutStarted: true}],
    ["closed original Turn", {replayPhase: "settled"}],
  ];
  for (const [name, input] of denied) {
    assert.throws(() => resume(input), {code: "refresh_task_lease_unavailable"}, name);
  }
});

test("retired proof qualifies only remaining original completion writeback", () => {
  const retired = {todo: {...todo(identity.todo_id!, "done"), claimed_by: null},
    lease: {...currentLease, status: "released"}, completionEvent: completed,
    requestedProof: null, closeoutStarted: true};
  assert.deepEqual(resume(retired), {state: "ready", original_prepared: originalPrepared,
    current_task_lease_proof: {idempotency_key: originalKey, expected_version: 120},
    lease_epoch: 50, source: "retired"});
  assert.equal(resume({...retired,
    requestedProof: {idempotency_key: originalKey, expected_version: 120}}).source, "retired");
  for (const input of [
    {...retired, completionEvent: null},
    {...retired, completionEvent: {...completed, run_id: "another-turn"}},
    {...retired, completionEvent: {...completed, todo_id: "todo_foreign"}},
    {...retired, completionEvent: {...completed, goal_id: "another-goal"}},
    {...retired, completionEvent: {...completed, agent_id: "another-agent"}},
    {...retired, completionEvent: {...completed, details: {settlement_effect_id: "another-effect"}}},
    {...retired, completionEvent: {...completed, details: {...completed.details as JsonObject, todo_id: "todo_foreign"}}},
    {...retired, todo: {...todo(identity.todo_id!, "deferred"), claimed_by: null}},
    {...retired, lease: {...retired.lease, status: "active"}},
    {...retired, writebackPending: false},
    {...retired, otherTerminalStarted: true},
    {...retired, lease: {...retired.lease, lease_epoch: 51}},
  ]) assert.throws(() => resume(input), {code: "refresh_task_lease_unavailable"});
});

test("qualified terminal provider cannot reopen work or execute a replacement generation", () => {
  const terminalProviderEffect = {step_kind: "terminal_closeout", action: "resolve_prepared",
    effect_ref: `${identity.effect_id}#terminal_closeout`,
    completed_phases: ["host_execute", "typed_result", "validation", "durable_writeback", "quota_spend"]} as const;
  const pending = {closeoutStarted: true, otherTerminalStarted: true, writebackPending: false,
    terminalProviderEffect};
  assert.equal(resume(pending).source, "active");
  const retired = {...pending, todo: {...todo(identity.todo_id!, "done"), claimed_by: null},
    lease: {...currentLease, status: "released"}, requestedProof: null, completionEvent: null,
    terminalCompletion: {kind: "committed", completion: {todo_id: identity.todo_id, continuation: "no_followup"}}};
  assert.equal(resume(retired).source, "retired");
  for (const changed of [
    {...pending, requestedProof: null},
    {...pending, terminalProviderEffect: {...terminalProviderEffect, effect_ref: `${identity.effect_id}#durable_writeback`}},
    {...pending, lease: {...currentLease, idempotency_key: "replacement", lease_epoch: 51, version: 121},
      requestedProof: {idempotency_key: "replacement", expected_version: 121}},
    {...retired, terminalCompletion: null},
    {...retired, terminalCompletion: {kind: "absent"}},
    {...retired, terminalCompletion: {kind: "committed", completion: {todo_id: "todo_other", continuation: "no_followup"}}},
    {...retired, terminalProviderEffect: {...terminalProviderEffect, action: "prepare_and_execute" as const}},
    {...retired, lease: {...retired.lease, lease_epoch: 51}},
  ]) assert.throws(() => resume(changed), {code: "refresh_task_lease_unavailable"});
});

test("legacy missing epoch preserves exact history but cannot renew or rebind", () => {
  const legacyPrepared = {...originalPrepared};
  delete (legacyPrepared as Partial<typeof originalPrepared>).prepared_lease_epoch;
  const legacyReceipt = {...preparedReceipt, details: {...preparedReceipt.details as JsonObject,
    prepared_lease_epoch: undefined}};
  const exact = {originalReceipt: legacyReceipt, exactSource: false,
    lease: {...currentLease, version: 119},
    requestedProof: {idempotency_key: originalKey, expected_version: 119}};
  assert.deepEqual(resume(exact).original_prepared, legacyPrepared);
  assert.equal(resume(exact).lease_epoch, null, "An observed epoch is not a proved legacy generation");
  const legacyRetired = resume({...exact, todo: {...todo(identity.todo_id!, "done"), claimed_by: null},
    lease: {...exact.lease, status: "released"}, requestedProof: null,
    completionEvent: completed, closeoutStarted: true});
  assert.equal(legacyRetired.source, "retired");
  assert.equal(legacyRetired.lease_epoch, null);
  assert.throws(() => resume({...exact, exactSource: true}), {code: "refresh_task_lease_unavailable"});
  assert.throws(() => resume({originalReceipt: legacyReceipt, exactSource: false}),
    {code: "refresh_task_lease_unavailable"});
  assert.throws(() => resume({...exact, todo: {...todo(identity.todo_id!, "done"), claimed_by: null},
    lease: {...currentLease, status: "released"}, requestedProof: null,
    completionEvent: completed, closeoutStarted: true}), {code: "refresh_task_lease_unavailable"});
});

test("ordinary legacy prepared binding is explicit alias compatibility, never administrative admission", async () => {
  const legacy: JsonObject = {...preparedReceipt, status: "turn_run_once", details: {
    todo_id: identity.todo_id, settlement_effect_id: identity.effect_id,
    source: "loopx_turn_run_once", prepared_lease_required: true, ...originalPrepared}};
  const before = structuredClone(legacy);
  assert.equal(heartbeatPreparedWorkAdmission(legacy, identity), null);
  assert.deepEqual(heartbeatPreparedWorkAdmission(legacy, identity, true), originalPrepared);
  assert.equal(resume({originalReceipt: legacy, exactSource: false}).source, "active");
  assert.equal(resume({originalReceipt: legacy, exactSource: false,
    todo: {...todo(identity.todo_id!, "done"), claimed_by: null},
    lease: {...currentLease, status: "released"}, completionEvent: completed,
    requestedProof: null, closeoutStarted: true}).source, "retired");
  assert.throws(() => resume({originalReceipt: legacy}), {code: "refresh_task_lease_unavailable"});
  assert.throws(() => qualifyReceiptBoundExecutionLossCloseout({
    schema_version: EXECUTION_LOSS_QUALIFICATION_SCHEMA,
    identity: settlementIdentityPayload(identity), receipt: legacy,
  }), {code: "refresh_task_lease_unavailable"});
  for (const changed of [
    {...legacy, goal_ref: null},
    {...legacy, status: "normal_run"},
    {...legacy, status: "turn_run_once_extra"},
    ...["must_attempt_work", "delivery_allowed", "quiet_noop_allowed"].map(field =>
      ({...legacy, details: {...legacy.details as JsonObject, [field]: true}})),
    ...["ok", "should_run"].map(field =>
      ({...legacy, details: {...legacy.details as JsonObject, [field]: false}})),
    {...legacy, details: {...legacy.details as JsonObject, closeout_required: false}},
    {...legacy, details: {...legacy.details as JsonObject, prepared_lease_required: false}},
    {...legacy, details: {...legacy.details as JsonObject, prepared_lease_required: undefined}},
    {...legacy, details: {todo_id: identity.todo_id, settlement_effect_id: identity.effect_id,
      prepared_lease_required: true}},
  ]) assert.equal(heartbeatPreparedWorkAdmission(changed, identity, true), null);
  assert.throws(() => heartbeatPreparedWorkAdmission({...legacy,
    details: {...legacy.details as JsonObject, prepared_lease_version: undefined}}, identity, true),
    {code: "malformed_settlement_state"});
  const readiness = {...legacy, event_id: "ordinary-readiness", details: {...legacy.details as JsonObject,
    prepared_lease_version: 120, settlement_receipt_revision: "lease_readiness"},
    causality: {source_event_id: legacy.event_id, caused_by: legacy.event_id}};
  assert.deepEqual(heartbeatValidationEvent([legacy, readiness], readiness, identity, true), legacy);
  const publish = {schema_version: HEARTBEAT_PREPARED_PUBLICATION_REQUEST_SCHEMA,
    existing_receipt: readiness, original_prepared: originalPrepared, receipt_history: [legacy, readiness],
    settlement_identity: settlementIdentityPayload(identity),
    proposed_prepared_fields: {prepared_lease_required: true, ...originalPrepared}};
  assert.equal((qualifyHeartbeatPreparedPublication(publish).prepared_fields as JsonObject).prepared_lease_version, 120);
  assert.throws(() => qualifyHeartbeatPreparedPublication({...publish,
    proposed_prepared_fields: {...publish.proposed_prepared_fields, prepared_lease_version: 120}}),
    {code: "heartbeat_receipt_identity_conflict"});
  const goalRef = {goal_id: identity.goal_id, goal_instance_id: "ginst_" + "a".repeat(32)};
  assert.throws(() => qualifyHeartbeatPreparedPublication({...publish,
    existing_receipt: {...readiness, goal_ref: goalRef},
    receipt_history: [{...legacy, goal_ref: goalRef}, {...readiness, goal_ref: goalRef}]}),
    {code: "heartbeat_receipt_identity_conflict"});
  const f = await receiptFixture([legacy, readiness]);
  try {
    const result = await readQuotaSettlement({...request(f.root), resume_lease_candidate: {
      requested_proof: {idempotency_key: originalKey, expected_version: 120}}});
    assert.deepEqual(result.original_prepared_receipt, legacy);
    assert.deepEqual(result.prepared_lease, originalPrepared);
    assert.equal((result.resume_lease_qualification as JsonObject).source, "active");
    assert.equal(result.original_prepared_receipt_sha256, canonicalAuthoritySha256(legacy));
  } finally {await rm(f.root, {recursive: true, force: true});}
  assert.deepEqual(legacy, before);
});

test("publication replay is exact even when ephemeral same-generation resume is ready", async () => {
  const fields = {prepared_lease_required: true, ...originalPrepared};
  const publish = (proposal: JsonObject | null, existing = preparedReceipt, original: JsonObject | null = originalPrepared) =>
    qualifyHeartbeatPreparedPublication({schema_version: HEARTBEAT_PREPARED_PUBLICATION_REQUEST_SCHEMA,
      existing_receipt: existing, original_prepared: original, proposed_prepared_fields: proposal});
  assert.deepEqual(publish(fields), {ok: true, prepared_fields: fields});
  assert.deepEqual(publish(null), {ok: true, prepared_fields: fields});
  assert.equal(resume().state, "ready");
  for (const proposal of [{...fields, prepared_lease_version: 120},
    {...fields, prepared_lease_key_sha256: "b".repeat(64)}, {...fields, prepared_lease_epoch: 51},
    {prepared_lease_required: true}, {...fields, prepared_lease_epoch: undefined},
    {...fields, prepared_lease_required: false}]) {
    assert.throws(() => publish(proposal), {code: "heartbeat_receipt_identity_conflict"});
  }
  const changedStorage = {...preparedReceipt, details: {...fields, prepared_lease_version: 120}};
  assert.equal((publish(null, changedStorage).prepared_fields as JsonObject).prepared_lease_version, 120);
  assert.throws(() => publish(fields, changedStorage), {code: "heartbeat_receipt_identity_conflict"});
  const legacyFields: JsonObject = {...fields};
  delete legacyFields.prepared_lease_epoch;
  const legacyOriginal: JsonObject = {...originalPrepared};
  delete legacyOriginal.prepared_lease_epoch;
  const legacyStored = {...preparedReceipt, details: legacyFields};
  assert.deepEqual(publish(legacyFields, legacyStored, legacyOriginal),
    {ok: true, prepared_fields: legacyFields});
  assert.throws(() => publish(fields, legacyStored, legacyOriginal),
    {code: "heartbeat_receipt_identity_conflict"});
  const handlers = createEffectRuntimeHandlers({fingerprint: "prepared-publication", requestShutdown() {}});
  assert.deepEqual(await dispatchEffectRuntimeMethod(handlers, "quota.heartbeat_prepared_publication.qualify", {
    schema_version: HEARTBEAT_PREPARED_PUBLICATION_REQUEST_SCHEMA, existing_receipt: null,
    original_prepared: null, proposed_prepared_fields: fields}), {ok: true, prepared_fields: fields});
  for (const proposed of [{}, {prepared_lease_required: true}]) {
    await assert.rejects(dispatchEffectRuntimeMethod(handlers, "quota.heartbeat_prepared_publication.qualify", {
      schema_version: HEARTBEAT_PREPARED_PUBLICATION_REQUEST_SCHEMA, existing_receipt: null,
      original_prepared: null, proposed_prepared_fields: proposed}), {code: "heartbeat_receipt_identity_conflict"});
  }
});

test("publication computes the original proof from history and preserves later stored readiness", () => {
  const stored = {...preparedReceipt, event_id: "stored-readiness", details: {
    ...preparedReceipt.details as JsonObject, prepared_lease_version: 120, settlement_receipt_revision: "lease_readiness"},
    causality: {source_event_id: preparedReceipt.event_id, caused_by: preparedReceipt.event_id}};
  const fields = {prepared_lease_required: true, ...originalPrepared};
  const storedFields = {...fields, prepared_lease_version: 120};
  const input = {schema_version: HEARTBEAT_PREPARED_PUBLICATION_REQUEST_SCHEMA,
    existing_receipt: structuredClone(stored), original_prepared: null,
    receipt_history: [structuredClone(preparedReceipt), structuredClone(stored)],
    settlement_identity: settlementIdentityPayload(identity), proposed_prepared_fields: fields};
  const before = structuredClone(input);
  assert.deepEqual(qualifyHeartbeatPreparedPublication(input), {ok: true, prepared_fields: storedFields});
  assert.deepEqual(qualifyHeartbeatPreparedPublication({...input, proposed_prepared_fields: null}),
    {ok: true, prepared_fields: storedFields});
  assert.deepEqual(input, before);
  for (const changed of [
    {...input, proposed_prepared_fields: storedFields},
    {...input, original_prepared: {...originalPrepared, prepared_lease_version: 120}, proposed_prepared_fields: storedFields},
    {...input, existing_receipt: {...stored, details: {...stored.details, prepared_lease_version: 121}}},
    {...input, receipt_history: [preparedReceipt, preparedReceipt, stored]},
    {...input, receipt_history: [{...preparedReceipt, run_id: "other-turn"}, stored]},
    {...input, receipt_history: [{...preparedReceipt, goal_ref: {goal_id: identity.goal_id, goal_instance_id: "ginst_" + "a".repeat(32)}}, stored]},
  ]) assert.throws(() => qualifyHeartbeatPreparedPublication(changed), {code: "heartbeat_receipt_identity_conflict"});
});

function request(root: string): JsonObject {
  return {schema_version: QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA, runtime_root: root,
    goal_id: identity.goal_id, agent_id: identity.agent_id, todo_id: identity.todo_id,
    turn_instance_id: identity.turn_instance_id, infer_turn_instance_id: false,
    allow_unbound_binding: false};
}

async function receiptFixture(events: JsonObject[], handoffMode = "hard_lease", seedLease = currentLease,
  options: {provider?: "file" | "sqlite"; seedTodo?: JsonObject} = {}) {
  const root = await mkdtemp(join(tmpdir(), "prepared-source-"));
  const log = join(root, "goals", identity.goal_id, "rollout-event-log.jsonl");
  await mkdir(join(root, "goals", identity.goal_id), {recursive: true});
  await writeFile(log, events.map(row => JSON.stringify(row)).join("\n") + "\n");
  const store = options.provider === "sqlite"
    ? new (await import("../../loopx/control_plane/coordination/sqlite_authority_store.ts")).SqliteAuthorityStore(
      join(root, "authority", "sqlite-v0"), identity.goal_id)
    : new FileAuthorityStore(join(root, "authority", "file-v0"), identity.goal_id);
  const committed = await store.commitAuthority({operation_id: "seed-prepared-source",
    expected_provider_revision: null, next_projection: projection([
      options.seedTodo ?? {...todo(identity.todo_id!), claimed_by: identity.agent_id}], [seedLease], handoffMode),
    events: [], receipts: []});
  assert.equal(committed.status, "applied", JSON.stringify(committed));
  if (options.provider === "sqlite") {
    const storeIdentity = await store.storeIdentity();
    assert.equal(storeIdentity.status, "available");
    if (storeIdentity.status !== "available") throw new Error("Fixture authority identity unavailable");
    await (await import("../../loopx/control_plane/coordination/local_authority_provider.ts"))
      .publishLocalAuthoritySelection(root, identity.goal_id, "sqlite", storeIdentity.store_identity);
  }
  return {root, log, store};
}

test("historical producer replacement conflicts before resume, publication and admitted administrative read", async () => {
  const replacementKey = "replacement-execution";
  const replacementPrepared = {prepared_lease_key_sha256: createHash("sha256").update(replacementKey).digest("hex"),
    prepared_lease_version: 121, prepared_lease_epoch: 51};
  const p2Lease = {...currentLease, idempotency_key: replacementKey, version: 121, lease_epoch: 51};
  const handlers = createEffectRuntimeHandlers({fingerprint: "historical-generation", requestShutdown() {}});
  for (const causal of [true, false]) {
    const replacement: JsonObject = {...preparedReceipt, event_id: "old-producer-rebind", status: "turn_run_once",
      details: {...preparedReceipt.details as JsonObject, ...replacementPrepared},
      ...(causal ? {causality: {source_event_id: preparedReceipt.event_id, caused_by: preparedReceipt.event_id}} : {})};
    assert.equal(Object.hasOwn(replacement.details as JsonObject, "settlement_receipt_revision"), false);
    const history = [preparedReceipt, replacement];
    const f = await receiptFixture(history, "hard_lease", p2Lease);
    try {
      const before = await readFile(f.log), authorityBefore = await f.store.loadAuthority();
      const snapshot = await readQuotaSettlementSnapshot(f.root, identity.goal_id);
      assert.throws(() => readQuotaSettlementForAdmittedOwnerFromSnapshot(request(f.root), {kind: "alias"}, snapshot),
        {code: "heartbeat_receipt_identity_conflict"});
      await assert.rejects(readQuotaSettlement({...request(f.root), resume_lease_candidate: {
        requested_proof: {idempotency_key: replacementKey, expected_version: 121}}}),
        {code: "heartbeat_receipt_identity_conflict"});
      await assert.rejects(dispatchEffectRuntimeMethod(handlers, "quota.heartbeat_prepared_publication.qualify", {
        schema_version: HEARTBEAT_PREPARED_PUBLICATION_REQUEST_SCHEMA,
        existing_receipt: replacement, original_prepared: null, receipt_history: history,
        settlement_identity: settlementIdentityPayload(identity),
        proposed_prepared_fields: {prepared_lease_required: true, ...replacementPrepared}}),
        {code: "heartbeat_receipt_identity_conflict"});
      assert.deepEqual(await readFile(f.log), before);
      assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
    } finally {await rm(f.root, {recursive: true, force: true});}
  }
  const epochless = {...preparedReceipt, details: {...preparedReceipt.details as JsonObject}};
  delete (epochless.details as JsonObject).prepared_lease_epoch;
  for (const [first, changed] of [
    [preparedReceipt, {...preparedReceipt, details: {...preparedReceipt.details as JsonObject, prepared_lease_epoch: 51}}],
    [epochless, {...epochless, details: {...epochless.details, prepared_lease_version: 120}}],
    [epochless, preparedReceipt],
  ]) {
    const last = {...changed, event_id: "changed-generation"};
    assert.throws(() => heartbeatValidationEvent([first!, last], last, identity),
      {code: "heartbeat_receipt_identity_conflict"});
  }
});

test("old producer causal renewal without revision retains original version, event and hash", async () => {
  const renewed = {...preparedReceipt, event_id: "old-producer-renewed", status: "turn_run_once",
    details: {...preparedReceipt.details as JsonObject, prepared_lease_version: 120},
    causality: {source_event_id: preparedReceipt.event_id, caused_by: preparedReceipt.event_id}};
  assert.deepEqual(heartbeatValidationEvent([preparedReceipt, renewed], renewed, identity), preparedReceipt);
  const f = await receiptFixture([preparedReceipt, renewed]);
  try {
    const result = await readQuotaSettlement({...request(f.root), resume_lease_candidate: {
      requested_proof: {idempotency_key: originalKey, expected_version: 120}}});
    assert.deepEqual(result.original_prepared_receipt, preparedReceipt);
    assert.deepEqual(result.prepared_lease, originalPrepared);
    assert.equal(result.original_prepared_event_id, preparedReceipt.event_id);
    assert.equal(result.original_prepared_receipt_sha256, canonicalAuthoritySha256(preparedReceipt));
    const publication = {schema_version: HEARTBEAT_PREPARED_PUBLICATION_REQUEST_SCHEMA,
      existing_receipt: renewed, original_prepared: originalPrepared,
      receipt_history: [preparedReceipt, renewed], settlement_identity: settlementIdentityPayload(identity),
      proposed_prepared_fields: {prepared_lease_required: true, ...originalPrepared}};
    assert.equal((qualifyHeartbeatPreparedPublication(publication).prepared_fields as JsonObject).prepared_lease_version, 120);
    assert.throws(() => qualifyHeartbeatPreparedPublication({...publication,
      proposed_prepared_fields: {...publication.proposed_prepared_fields, prepared_lease_version: 120}}),
      {code: "heartbeat_receipt_identity_conflict"});
    for (const causality of [{source_event_id: "missing", caused_by: "missing"},
      {source_event_id: preparedReceipt.event_id, caused_by: "different-parent"}]) {
      assert.equal(heartbeatValidationEvent([preparedReceipt, {...renewed, causality}], {...renewed, causality}, identity), null);
    }
  } finally {await rm(f.root, {recursive: true, force: true});}
});

test("uncaused same-generation change rejects while exact repetition retains the first event and hash", async () => {
  const changed: JsonObject = {...preparedReceipt, event_id: "uncaused-renewal", status: "turn_run_once",
    details: {...preparedReceipt.details as JsonObject, prepared_lease_version: 120}};
  const history = [preparedReceipt, changed];
  const f = await receiptFixture(history);
  const handlers = createEffectRuntimeHandlers({fingerprint: "uncaused-original", requestShutdown() {}});
  const publication = (existing: JsonObject, receipt_history: JsonObject[], proposed_prepared_fields: JsonObject | null) => ({
    schema_version: HEARTBEAT_PREPARED_PUBLICATION_REQUEST_SCHEMA, existing_receipt: existing,
    original_prepared: null, receipt_history, settlement_identity: settlementIdentityPayload(identity),
    proposed_prepared_fields});
  try {
    const before = await readFile(f.log), authorityBefore = await f.store.loadAuthority();
    await assert.rejects(readQuotaSettlement({...request(f.root), resume_lease_candidate: {
      requested_proof: {idempotency_key: originalKey, expected_version: 120}}}),
      {code: "heartbeat_receipt_identity_conflict"});
    const snapshot = await readQuotaSettlementSnapshot(f.root, identity.goal_id);
    assert.throws(() => readQuotaSettlementForAdmittedOwnerFromSnapshot(request(f.root), {kind: "alias"}, snapshot),
      {code: "heartbeat_receipt_identity_conflict"});
    for (const proposal of [null, {prepared_lease_required: true, ...originalPrepared},
      {prepared_lease_required: true, ...originalPrepared, prepared_lease_version: 120}]) {
      await assert.rejects(dispatchEffectRuntimeMethod(handlers, "quota.heartbeat_prepared_publication.qualify",
        publication(changed, history, proposal)), {code: "heartbeat_receipt_identity_conflict"});
    }
    assert.deepEqual(await readFile(f.log), before);
    assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
    const repeated = {...preparedReceipt, event_id: "uncaused-exact-repeat", status: "turn_run_once"};
    assert.deepEqual(heartbeatValidationEvent([preparedReceipt], preparedReceipt, identity), preparedReceipt);
    assert.deepEqual(heartbeatValidationEvent([preparedReceipt, repeated], repeated, identity), preparedReceipt);
    await writeFile(f.log, [preparedReceipt, repeated].map(row => JSON.stringify(row)).join("\n") + "\n");
    const readback = await readQuotaSettlement({...request(f.root), resume_lease_candidate: {
      requested_proof: {idempotency_key: originalKey, expected_version: 120}}});
    assert.deepEqual(readback.original_prepared_receipt, preparedReceipt);
    assert.equal(readback.original_prepared_event_id, preparedReceipt.event_id);
    assert.equal(readback.original_prepared_receipt_sha256, canonicalAuthoritySha256(preparedReceipt));
    assert.deepEqual(readback.prepared_lease, originalPrepared);
    assert.equal((readback.resume_lease_qualification as JsonObject).state, "ready");
    assert.deepEqual(await dispatchEffectRuntimeMethod(handlers, "quota.heartbeat_prepared_publication.qualify",
      publication(repeated, [preparedReceipt, repeated], {prepared_lease_required: true, ...originalPrepared})),
      {ok: true, prepared_fields: {prepared_lease_required: true, ...originalPrepared}});
    const legacy = {...preparedReceipt, status: "turn_run_once", details: {todo_id: identity.todo_id,
      settlement_effect_id: identity.effect_id, prepared_lease_required: true, ...originalPrepared}};
    assert.deepEqual(heartbeatValidationEvent([legacy], legacy, identity, true), legacy);
    const legacyRepeat = {...legacy, event_id: "legacy-exact-repeat"};
    assert.deepEqual(heartbeatValidationEvent([legacy, legacyRepeat], legacyRepeat, identity, true), legacy);
  } finally {await rm(f.root, {recursive: true, force: true});}
});

test("prepared source follows causal positive admission instead of binding clock or effective proof", async () => {
  const binding = {...preparedReceipt, event_id: "earlier-binding", recorded_at: "2025-01-01T00:00:00Z",
    status: "blocked", details: {...preparedReceipt.details as JsonObject, closeout_required: false,
      must_attempt_work: false, delivery_allowed: false, prepared_lease_version: 80}};
  const admitted = {...preparedReceipt, details: {...preparedReceipt.details as JsonObject,
    settlement_receipt_revision: "work_admission"},
    causality: {source_event_id: binding.event_id, caused_by: binding.event_id}};
  const readiness = {...preparedReceipt, event_id: "later-readiness",
    details: {...preparedReceipt.details as JsonObject, prepared_lease_version: 120,
      settlement_receipt_revision: "lease_readiness"},
    causality: {source_event_id: admitted.event_id, caused_by: admitted.event_id}};
  const effective = {...readiness, event_id: "later-identity-upgrade",
    details: {...readiness.details, settlement_receipt_revision: "identity_upgrade"},
    causality: {source_event_id: readiness.event_id, caused_by: readiness.event_id}};
  const unrelated = {...preparedReceipt, event_id: "not-a-causal-ancestor",
    details: {...preparedReceipt.details as JsonObject, prepared_lease_version: 60}};
  const f = await receiptFixture([unrelated, binding, admitted, readiness, effective]);
  try {
    const before = await readFile(f.log);
    const authorityBefore = await f.store.loadAuthority();
    const result = await readQuotaSettlement({...request(f.root), include_record_bodies: false,
      resume_lease_candidate: {requested_proof: {idempotency_key: originalKey, expected_version: 120}}});
    assert.deepEqual(result.prepared_lease, originalPrepared);
    assert.equal(result.original_prepared_event_id, admitted.event_id);
    assert.deepEqual(result.original_prepared_receipt, admitted);
    assert.equal(result.original_prepared_receipt_sha256, canonicalAuthoritySha256(admitted));
    assert.equal(result.admission_event_id, unrelated.event_id);
    assert.equal((result.resume_lease_qualification as JsonObject).source, "active");
    assert.deepEqual((result.resume_lease_qualification as JsonObject).original_prepared, originalPrepared);
    assert.deepEqual(await readFile(f.log), before);
    assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
    await assert.rejects(readQuotaSettlement({...request(f.root), resume_lease_candidate: {
      requested_proof: {idempotency_key: originalKey, expected_version: 120}, active: true}}), /only requested_proof/);
    const snapshot = await readQuotaSettlementSnapshot(f.root, identity.goal_id);
    assert.throws(() => readQuotaSettlementFromSnapshot({...request(f.root),
      resume_lease_candidate: {requested_proof: null}}, snapshot), /live canonical authority/);
  } finally {await rm(f.root, {recursive: true, force: true});}
});

test("pending selection can precede binding but cannot become prepared admission", async () => {
  const reservation = {...preparedReceipt, event_id: "reserved", details: {
    prepared_lease_required: true, ok: true, should_run: false, must_attempt_work: false,
    delivery_allowed: false, quiet_noop_allowed: true, closeout_required: false}};
  const pending = {...reservation, event_id: "pending-selection", details: {...reservation.details,
    pending_action_selection_todo_id: identity.todo_id, pending_action_selection_state: "deferred",
    pending_action_selection_reason: "claim_required", todo_id: null, settlement_effect_id: null},
    causality: {source_event_id: reservation.event_id, caused_by: reservation.event_id}};
  const bound = {...pending, event_id: "bound-negative", details: {...pending.details,
    todo_id: identity.todo_id, settlement_effect_id: identity.effect_id, settlement_receipt_revision: "identity_upgrade"},
    causality: {source_event_id: pending.event_id, caused_by: pending.event_id}};
  const admitted = {...preparedReceipt, details: {...preparedReceipt.details as JsonObject,
    settlement_receipt_revision: "work_admission"},
    causality: {source_event_id: bound.event_id, caused_by: bound.event_id}};
  const readiness = {...admitted, event_id: "renewed-readiness", details: {...admitted.details,
    prepared_lease_version: 120, settlement_receipt_revision: "lease_readiness"},
    causality: {source_event_id: admitted.event_id, caused_by: admitted.event_id}};
  const f = await receiptFixture([reservation, pending, bound]);
  try {
    const negative = await readQuotaSettlement({...request(f.root), heartbeat_reentry_guard: preparedReceipt.details});
    assert.ok((negative.identity as any).result.value);
    assert.equal((negative.heartbeat_reentry_qualification as JsonObject).append, true);
    assert.equal(negative.original_prepared_receipt, null);
    const history = [reservation, pending, bound, admitted, readiness];
    await writeFile(f.log, history.map(row => JSON.stringify(row)).join("\n") + "\n");
    const ready = await readQuotaSettlement({...request(f.root), resume_lease_candidate: {
      requested_proof: {idempotency_key: originalKey, expected_version: 120}}});
    assert.deepEqual(ready.prepared_lease, originalPrepared);
    assert.equal(ready.original_prepared_event_id, admitted.event_id);
    assert.equal((ready.resume_lease_qualification as JsonObject).source, "active");
    for (const badParent of [
      {...pending, details: {...pending.details, pending_action_selection_todo_id: "todo_other"}},
      {...pending, run_id: "another-turn"},
      {...pending, goal_id: "another-goal"},
      {...pending, goal_ref: {goal_id: identity.goal_id, goal_instance_id: "ginst_" + "a".repeat(32)}},
    ]) assert.equal(heartbeatValidationEvent([reservation, badParent, bound, admitted, readiness], readiness, identity), null);
    assert.equal(heartbeatValidationEvent([reservation, pending, bound, admitted, {...readiness,
      causality: {source_event_id: admitted.event_id, caused_by: "different-parent"}}], {...readiness,
      causality: {source_event_id: admitted.event_id, caused_by: "different-parent"}}, identity), null);
  } finally {await rm(f.root, {recursive: true, force: true});}
});

test("absence of receipt preparedness cannot downgrade real hard lease authority", async () => {
  const noPrepared = {...preparedReceipt, details: {todo_id: identity.todo_id,
    settlement_effect_id: identity.effect_id, closeout_required: true, ok: true,
    should_run: true, must_attempt_work: true, delivery_allowed: true, quiet_noop_allowed: false}};
  const hard = await receiptFixture([noPrepared]);
  const soft = await receiptFixture([noPrepared], "soft_claim");
  try {
    const candidate = {requested_proof: {idempotency_key: originalKey, expected_version: 120}};
    await assert.rejects(readQuotaSettlement({...request(hard.root), resume_lease_candidate: candidate}),
      {code: "refresh_task_lease_unavailable"});
    const result = await readQuotaSettlement({...request(soft.root), resume_lease_candidate: candidate});
    assert.deepEqual(result.resume_lease_qualification, {state: "not_required", original_prepared: null,
      current_task_lease_proof: null, lease_epoch: null, source: null});
  } finally {await rm(hard.root, {recursive: true, force: true}); await rm(soft.root, {recursive: true, force: true});}
});

test("legacy identity upgrade retains prepared source that predated the effect id", async () => {
  const original = {...preparedReceipt, details: {...preparedReceipt.details as JsonObject}};
  delete original.details.settlement_effect_id;
  const upgraded = {...preparedReceipt, event_id: "legacy-upgraded",
    details: {...preparedReceipt.details as JsonObject, settlement_receipt_revision: "identity_upgrade"},
    causality: {source_event_id: original.event_id, caused_by: original.event_id}};
  const f = await receiptFixture([original, upgraded]);
  try {
    const result = await readQuotaSettlement(request(f.root));
    assert.deepEqual(result.original_prepared_receipt, original);
    assert.deepEqual(result.prepared_lease, originalPrepared);
    assert.equal(result.original_prepared_receipt_sha256, canonicalAuthoritySha256(original));
    await writeFile(f.log, JSON.stringify(original) + "\n" + JSON.stringify({...upgraded,
      causality: {source_event_id: "missing", caused_by: "missing"}}) + "\n");
    const broken = await readQuotaSettlement(request(f.root));
    assert.equal((broken.identity as any).result.failure.kind, "receipt_missing");
  } finally {await rm(f.root, {recursive: true, force: true});}
});


function semanticWritebackCandidate(requestedProof: JsonObject | null = null): JsonObject {
  const packet = visionAuthoringContract().minimal_example as JsonObject;
  const vision = buildVisionCheckpoint({schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "prepare",
    goal_id: identity.goal_id, agent_id: identity.agent_id, agent_vision_packet: packet,
    existing_agent_vision: null, merge_patch: false, require_path_delta_for_durable_change: true,
    allow_terminal_stride_path: false}).agent_vision;
  const checkpoint = buildVisionCheckpoint({schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "finalize",
    agent_id: identity.agent_id, agent_vision: vision, existing_agent_vision: null,
    vision_unchanged_reason: null, delivery_outcome: "outcome_progress",
    active_state_next_action_would_update: false, delivery_boundary: "semantic_closeout",
    todo_id: identity.todo_id, completion_todo_id: null, autonomous_replan_recorded: false, blocked_retry: null});
  const retry = {vision: packet, unchanged_reason: null, merge_patch: false,
    workspace_requested: false, mutation: {}, delivery_outcome: "outcome_progress", delivery_batch_scale: null,
    delivery_boundary: "semantic_closeout", progress_observation: null};
  return {refresh_retry: retry,
    writeback_candidate: {requested_proof: requestedProof, effect_ref: `${identity.effect_id}#durable_writeback`,
      completion_todo_id: null, completion_turn_key: null,
      run: {goal_id: identity.goal_id, agent_id: identity.agent_id, todo_id: identity.todo_id,
        turn_instance_id: identity.turn_instance_id, settlement_identity: settlementIdentityPayload(identity),
        classification: "turn_run_once", delivery_outcome: "outcome_progress", delivery_boundary: "semantic_closeout",
        progress_observation: null, agent_vision: vision, vision_checkpoint: checkpoint, autonomous_replan_ack: null,
        refresh_recovery: refreshRecovery(decodeRefreshRetry(retry)!, null, false, undefined, false)}}};
}

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: resume hints retain exact CAS and original-generation authority`, async () => {
    const f = await receiptFixture([preparedReceipt], "hard_lease", currentLease, {provider});
    try {
      const before = await readFile(f.log), authorityBefore = await f.store.loadAuthority();
      for (const renewal of [true, false]) {
        const result = await readQuotaSettlement({...request(f.root), resume_lease_candidate: {
          requested_proof: {idempotency_key: originalKey, expected_version: 120},
          allow_same_generation_lease_renewal: renewal, validation_recovery_turn_instance_id: identity.turn_instance_id}});
        assert.deepEqual((result.resume_lease_qualification as JsonObject).original_prepared, originalPrepared);
        assert.equal((result.resume_lease_qualification as JsonObject).state, "ready");
      }
      for (const candidate of [
        {requested_proof: {idempotency_key: originalKey, expected_version: 119}, allow_same_generation_lease_renewal: true},
        {requested_proof: {idempotency_key: originalKey, expected_version: 120},
          validation_recovery_turn_instance_id: "foreign-turn"},
      ]) await assert.rejects(readQuotaSettlement({...request(f.root), resume_lease_candidate: candidate}),
        {code: "refresh_task_lease_unavailable"});
      for (const hint of [{allow_same_generation_lease_renewal: "true"},
        {validation_recovery_turn_instance_id: " turn-original "}]) {
        await assert.rejects(readQuotaSettlement({...request(f.root), resume_lease_candidate: {
          requested_proof: {idempotency_key: originalKey, expected_version: 120}, ...hint}}));
      }
      assert.deepEqual(await readFile(f.log), before);
      assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
    } finally {await rm(f.root, {recursive: true, force: true});}
  });

  test(`${provider}: active prepared authority still requalifies actual semantic inputs`, async () => {
    const f = await receiptFixture([preparedReceipt], "hard_lease", currentLease, {provider});
    try {
      const before = await readFile(f.log), authorityBefore = await f.store.loadAuthority();
      const input = {...request(f.root), ...semanticWritebackCandidate({idempotency_key: originalKey, expected_version: 120})};
      const candidate = input.writeback_candidate as JsonObject, run = candidate.run as JsonObject;
      const retry = input.refresh_retry as JsonObject;
      const result = await readQuotaSettlement({...input, writeback_candidate: {...candidate,
        run: {...run, classification: "caller-supplied-opaque-classification"}}});
      assert.deepEqual((result.writeback_qualification as any).result.value.original_prepared, originalPrepared);
      for (const patch of [
        {vision_checkpoint: {...run.vision_checkpoint as JsonObject, satisfied: false}},
        {vision_checkpoint: {...run.vision_checkpoint as JsonObject, triggers: []}},
        {agent_vision: {...run.agent_vision as JsonObject, state: "invalid-vision-state"}},
        {agent_vision: null},
      ]) await assert.rejects(readQuotaSettlement({...input, writeback_candidate: {...candidate, run: {...run, ...patch}}}));
      const withoutVision = {...retry, vision: null};
      await assert.rejects(readQuotaSettlement({...input, refresh_retry: withoutVision,
        writeback_candidate: {...candidate, run: {...run, agent_vision: null,
          refresh_recovery: refreshRecovery(decodeRefreshRetry(withoutVision)!, null, false, undefined, false)}}}),
        {code: "refresh_task_lease_unavailable"}, "A forged satisfied checkpoint cannot replace actual Vision");
      const progress = {schema_version: "typed_progress_observation_v0", result_class: "advanced",
        work_item_id: identity.todo_id, fingerprint: "abcdef0123456789", surface_id: "surface:current",
        evidence_ids: ["evidence:current"]};
      for (const observation of [{...progress, work_item_id: "todo_foreign", evidence_ids: ["evidence:foreign"]},
        {...progress, evidence_ids: ["untyped evidence"]}]) {
        await assert.rejects(readQuotaSettlement({...input, refresh_retry: {...retry, progress_observation: observation},
          writeback_candidate: {...candidate, run: {...run, progress_observation: observation}}}));
      }
      assert.deepEqual(await readFile(f.log), before);
      assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
    } finally {await rm(f.root, {recursive: true, force: true});}
  });

  test(`${provider}: same-Turn supersede qualifies only first semantic writeback`, async () => {
    const supersede: JsonObject = {...completed, event_kind: "todo_supersede", status: "done", event_id: "supersede-original"};
    const f = await receiptFixture([preparedReceipt, supersede], "hard_lease", {...currentLease, status: "released"},
      {provider, seedTodo: {...todo(identity.todo_id!, "done"), claimed_by: null}});
    try {
      const before = await readFile(f.log), authorityBefore = await f.store.loadAuthority();
      const input = {...request(f.root), ...semanticWritebackCandidate()};
      const result = await readQuotaSettlement(input);
      assert.deepEqual((result.writeback_qualification as any).result.value, {
        step_kind: "durable_writeback", effect_ref: `${identity.effect_id}#durable_writeback`,
        delivery_boundary: "semantic_closeout", original_prepared: originalPrepared, selected_obligation_id: null});
      assert.equal((result.writeback_qualification as any).payload.ok, true);
      assert.equal(result.resume_lease_qualification, undefined);
      assert.equal(result.completion_event, null);
      assert.equal(result.original_prepared_receipt_sha256, canonicalAuthoritySha256(preparedReceipt));
      await assert.rejects(readQuotaSettlement({...request(f.root), resume_lease_candidate: {requested_proof: null}}),
        {code: "refresh_task_lease_unavailable"}, "Supersede does not grant business resume");
      const candidate = input.writeback_candidate as JsonObject;
      for (const patch of [
        {effect_ref: `${identity.effect_id}#terminal_closeout`},
        {completion_todo_id: identity.todo_id}, {completion_turn_key: identity.turn_instance_id},
        {requested_proof: {idempotency_key: originalKey, expected_version: 119}, allow_same_generation_lease_renewal: true},
        {validation_recovery_turn_instance_id: "foreign-turn"},
        {run: {...candidate.run as JsonObject, turn_instance_id: "foreign-turn"}},
        {run: {...candidate.run as JsonObject, delivery_outcome: "outcome_achieved"}},
        {run: {...candidate.run as JsonObject, vision_checkpoint: {
          ...(candidate.run as any).vision_checkpoint, satisfied: false}}},
      ]) await assert.rejects(readQuotaSettlement({...input, writeback_candidate: {...candidate, ...patch}}),
        {code: "refresh_task_lease_unavailable"});
      const snapshot = await readQuotaSettlementSnapshot(f.root, identity.goal_id);
      assert.throws(() => readQuotaSettlementFromSnapshot(input, snapshot), /live canonical authority/);
      for (const patch of [{run_id: "foreign-turn"}, {agent_id: "agent-b"}, {todo_id: "todo_foreign"},
        {goal_id: "goal-b"}, {status: "failed"}, {details: {settlement_effect_id: "foreign-effect"}}]) {
        await writeFile(f.log, [preparedReceipt, {...supersede, ...patch}].map(row => JSON.stringify(row)).join("\n") + "\n");
        await assert.rejects(readQuotaSettlement(input), {code: "refresh_task_lease_unavailable"});
      }
      await writeFile(f.log, [preparedReceipt, supersede, completed].map(row => JSON.stringify(row)).join("\n") + "\n");
      await assert.rejects(readQuotaSettlement(input), {code: "refresh_task_lease_unavailable"},
        "Supersede qualification cannot mix a business completion");
      await writeFile(f.log, before);
      assert.deepEqual(await f.store.loadAuthority(), authorityBefore);
    } finally {await rm(f.root, {recursive: true, force: true});}
  });

  test(`${provider}: supersede rejects replacement generation and epochless renewal`, async () => {
    const supersede = {...completed, event_kind: "todo_supersede", status: "done", event_id: "supersede-original"};
    for (const changed of [
      {receipt: preparedReceipt, lease: {...currentLease, idempotency_key: "replacement", lease_epoch: 51, version: 121}},
      {receipt: preparedReceipt, lease: {...currentLease, lease_epoch: 51}},
      {receipt: {...preparedReceipt, details: {...preparedReceipt.details as JsonObject,
        prepared_lease_epoch: undefined}}, lease: currentLease},
    ]) {
      const f = await receiptFixture([changed.receipt, supersede], "hard_lease", {...changed.lease, status: "released"},
        {provider, seedTodo: {...todo(identity.todo_id!, "done"), claimed_by: null}});
      try {
        await assert.rejects(readQuotaSettlement({...request(f.root), ...semanticWritebackCandidate({
          idempotency_key: changed.lease.idempotency_key, expected_version: changed.lease.version})}),
          {code: "refresh_task_lease_unavailable"});
      } finally {await rm(f.root, {recursive: true, force: true});}
    }
  });
}
