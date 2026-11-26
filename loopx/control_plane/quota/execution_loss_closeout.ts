/** Explicit administrative closeout. The immutable Run is its durable intent;
 * original admission and the current provider head stay fenced through append. */
import {createHash, randomUUID} from "node:crypto";
import {closeSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, unlinkSync, writeFileSync} from "node:fs";
import {dirname, join, resolve} from "node:path";
import {isDeepStrictEqual} from "node:util";
import {settlementIdentity, settlementIdentityPayload, type JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {appendJsonLineSync, claimFileMutationLock, mutationLockOwner, releaseFileMutationLockClaim,
  type FileMutationLockClaim} from "../effect_runtime_io.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {indexCoordinationProjection} from "../coordination/coordination_projection.ts";
import {FileAuthorityStore} from "../coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../coordination/sqlite_authority_store.ts";
import {openRuntimeAuthorityStore, requireLocalAuthorityRuntimeRoot} from "../coordination/local_authority_provider.ts";
import {requireShadowPrimaryWriteAllowed, shadowMaintenanceLockPath} from "../coordination/shadow_management.ts";
import {decodeTaskLeaseAuthority} from "../work_items/task_lease_acquire.ts";
import {legacyGoalRegistrationIsActive} from "../goals/first_party_host_runtime.ts";
import {EXECUTION_LOSS_CLOSEOUT_SCHEMA, isExecutionLossCloseout} from "./execution_loss_closeout_record.ts";
import {parseExactGoalRef} from "../goals/goal_instance_identity.ts";
import {canonicalTaskLease, canonicalLeaseTodoFact} from "../coordination/task_lease_state.ts";
import {leaseIsActive} from "../work_items/task_lease_record.ts";
import {decodeTaskLeaseProof} from "../coordination/task_lease_proof.ts";
import {heartbeatReceiptBinding, heartbeatReceiptDetails, heartbeatReceiptFactFromEvent}
  from "./heartbeat_receipt_identity.ts";
import {heartbeatPreparedWorkAdmission, type HeartbeatPreparedLease} from "./heartbeat_receipt_identity.ts";
import {nativeChildReportAdmission} from "../capabilities/native_child_admission.ts";
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {projectProgressDelta} from "../work_items/progress_delta.ts";
import {leaseRepositoryRejection} from "../work_items/task_lease_repository.ts";
import {leaseOwnerRejection} from "../work_items/task_lease_eligibility.ts";
import {goalRolloutEventLogPath} from "../rollout_receipt_log.ts";
import {parseQuotaAccountingOwner, quotaGoalRef, requireCurrentQuotaAccountingOwner,
  requireQuotaOwnerProjection, withBorrowedQuotaAccountingOwner, type QuotaAccountingOwner} from "./source_admission.ts";
import {QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA, readQuotaSettlementForAdmittedOwnerFromSnapshot,
  readQuotaSettlementSnapshot} from "./settlement_readback.ts";

export const EXECUTION_LOSS_QUALIFICATION_SCHEMA = "execution_loss_closeout_qualification_v0";

function rejectExecutionLoss(message: string): never {
  throw new EffectRuntimeRequestError(`execution-loss closeout ${message}`, "refresh_task_lease_unavailable");
}

function executionLossClock(value: unknown): bigint {
  if (typeof value !== "string") rejectExecutionLoss("requires an exact aware timestamp");
  const time = parseTodoTimestampMicros(value, true);
  if (time === null) rejectExecutionLoss("has an invalid or ambiguous timestamp");
  return time;
}

/** Pure qualification consumed only by the fenced administrative commit owner.
 * A current lease proves control-writer ownership, never original execution. */
export function qualifyReceiptBoundExecutionLossCloseout(value: unknown): JsonObject {
  const request = requireJsonObject(value, "execution-loss qualification");
  if (request.schema_version !== EXECUTION_LOSS_QUALIFICATION_SCHEMA)
    rejectExecutionLoss("qualification schema is unsupported");
  const binding = requireJsonObject(request.identity, "execution-loss identity");
  const identity = settlementIdentity({goal_id: requireNonEmptyString(binding.goal_id, "goal_id"),
    agent_id: requireNonEmptyString(binding.agent_id, "agent_id"), todo_id: requireNonEmptyString(binding.todo_id, "todo_id"),
    turn_instance_id: requireNonEmptyString(binding.turn_instance_id, "turn_instance_id")});
  if (identity.binding_kind !== "todo" || !identity.todo_id
      || !isDeepStrictEqual(settlementIdentityPayload(identity), binding))
    rejectExecutionLoss("requires the exact original Todo Turn identity");
  const receipt = requireJsonObject(request.receipt, "original heartbeat receipt");
  if (request.goal_ref === undefined) {
    if (Object.hasOwn(receipt, "goal_ref")) rejectExecutionLoss("cannot mix legacy and exact Goal ownership");
  } else {
    const current = parseExactGoalRef(request.goal_ref), original = parseExactGoalRef(receipt.goal_ref);
    if (current.kind !== "parsed" || original.kind !== "parsed"
        || current.value.goalId.value !== identity.goal_id
        || !isDeepStrictEqual(request.goal_ref, receipt.goal_ref))
      rejectExecutionLoss("requires the original exact Goal lifetime");
  }
  const details = heartbeatReceiptDetails(receipt), fact = heartbeatReceiptFactFromEvent(receipt);
  const originalBinding = heartbeatReceiptBinding(identity.goal_id, identity.agent_id, fact);
  const prepared = heartbeatPreparedWorkAdmission(receipt, identity);
  if (receipt.schema_version !== "loopx_rollout_event_v0" || receipt.event_kind !== "quota_should_run"
      || receipt.goal_id !== identity.goal_id || receipt.agent_id !== identity.agent_id
      || receipt.run_id !== identity.turn_instance_id || fact.todo_id !== identity.todo_id
      || originalBinding?.binding_kind !== "todo" || originalBinding.binding_id !== identity.todo_id
      || (fact.settlement_effect_id !== null && fact.settlement_effect_id !== identity.effect_id) || fact.closeout_required !== true
      || !prepared || prepared.prepared_lease_epoch === undefined
      || (Object.hasOwn(details, "prepared_lease_required") && details.prepared_lease_required !== true)
      || (details.semantic_replan_obligation_id != null && details.semantic_replan_obligation_id !== "")
      || nativeChildReportAdmission(details, receipt.status, identity.effect_id, "open", false)
        .report_permission !== "new_operation")
    rejectExecutionLoss("requires complete original prepared work admission");
  const todo = requireJsonObject(request.current_todo, "current Todo");
  const registered = request.registered_agents;
  if (!Array.isArray(registered) || registered.some(agent => typeof agent !== "string")
      || !registered.includes(identity.agent_id) || todo.todo_id !== identity.todo_id
      || todo.role !== "agent" || todo.task_class !== "advancement_task"
      || todo.archive_state !== "active" || todo.status !== "open"
      || todo.claimed_by !== identity.agent_id || todo.done === true)
    rejectExecutionLoss("requires the current registered control writer and unfinished Todo");
  if (leaseOwnerRejection(canonicalLeaseTodoFact(todo), identity.agent_id, registered as string[]) !== null)
    rejectExecutionLoss("current control writer is not eligible for this Todo");
  const lease = canonicalTaskLease(requireJsonObject(request.current_lease, "current task lease"),
    identity.goal_id, identity.todo_id);
  const proof = decodeTaskLeaseProof(request.control_writer_proof);
  if (!proof || leaseRepositoryRejection(todo, lease) !== null
      || lease.owner !== identity.agent_id || lease.status !== "active"
      || typeof lease.version !== "number" || !Number.isSafeInteger(lease.version)
      || typeof lease.lease_epoch !== "number" || !Number.isSafeInteger(lease.lease_epoch)
      || proof.idempotency_key !== lease.idempotency_key || proof.expected_version !== lease.version)
    rejectExecutionLoss("requires the exact current control-writer proof");
  const now = executionLossClock(request.observed_at), admittedAt = executionLossClock(receipt.recorded_at);
  const acquired = executionLossClock(lease.acquired_at), updated = executionLossClock(lease.updated_at);
  const expires = executionLossClock(lease.expires_at);
  if (admittedAt > acquired || acquired > updated || updated > now || expires <= now || expires <= updated)
    rejectExecutionLoss("current control-writer clock is stale or ambiguous");
  const currentHash = createHash("sha256").update(proof.idempotency_key).digest("hex");
  if (!fencesPreparedGeneration(prepared, lease))
    rejectExecutionLoss("cannot replace an available or ambiguous original generation");
  const progress = requireJsonObject(request.progress_observation, "blocked observation");
  if (progress.result_class !== "blocked" || progress.work_item_id !== identity.todo_id
      || typeof progress.blocker_id !== "string" || !Array.isArray(progress.evidence_ids)
      || progress.evidence_ids.length === 0
      || Object.keys(progress).some(field => !["schema_version", "result_class", "work_item_id",
        "blocker_id", "evidence_ids", "fingerprint"].includes(field)))
    rejectExecutionLoss("requires only verified blocked observation and evidence");
  projectProgressDelta({current: progress, baseline: null, window: []});
  return {schema_version: EXECUTION_LOSS_CLOSEOUT_SCHEMA, effect_id: identity.effect_id,
    todo_id: identity.todo_id, source: "turn_settlement", observed_at: request.observed_at,
    original_receipt_event_id: requireNonEmptyString(receipt.event_id, "original receipt event id"),
    original_prepared: {...prepared}, current_control_writer: {
      lease_key_sha256: currentHash, version: lease.version, lease_epoch: lease.lease_epoch,
      owner: lease.owner, store_identity: requireNonEmptyString(request.provider_identity, "provider identity"),
      provider_revision: requireNonEmptyString(request.provider_revision, "provider revision")},
    observation_fingerprint: progress.fingerprint};
}

function fencesPreparedGeneration(prepared: HeartbeatPreparedLease, lease: JsonObject): boolean {
  return prepared.prepared_lease_epoch !== undefined
    && typeof lease.lease_epoch === "number" && Number.isSafeInteger(lease.lease_epoch)
    && lease.lease_epoch > prepared.prepared_lease_epoch
    && typeof lease.version === "number" && Number.isSafeInteger(lease.version)
    && lease.version > prepared.prepared_lease_version
    && typeof lease.idempotency_key === "string" && !!lease.idempotency_key
    && createHash("sha256").update(lease.idempotency_key).digest("hex") !== prepared.prepared_lease_key_sha256;
}

/** Read-only recovery advice; commit independently verifies this proof under its writer fence. */
export async function observeFencedExecution(root: string, identity: ReturnType<typeof settlementIdentity>,
    receipt: JsonObject): Promise<JsonObject | null> {
  const prepared = heartbeatPreparedWorkAdmission(receipt, identity);
  if (!prepared || prepared.prepared_lease_epoch === undefined || !identity.todo_id
      || optionalAssignedReplan(receipt)) return null;
  const store = await openRuntimeAuthorityStore(root, identity.goal_id, {}).catch(() => null);
  if (!(store instanceof FileAuthorityStore) && !(store instanceof SqliteAuthorityStore)) return null;
  const head = await store.loadAuthority();
  if (head.status !== "loaded" || head.head.handoff_mode !== "hard_lease") return null;
  const projection = indexCoordinationProjection(head.head, identity.goal_id);
  const todo = projection.todos.get(identity.todo_id);
  const rawLease = projection.leases.get(identity.todo_id);
  if (!todo || todo.status !== "open" || todo.role !== "agent" || todo.task_class !== "advancement_task"
      || todo.archive_state !== "active" || !rawLease) return null;
  const lease = canonicalTaskLease(rawLease, identity.goal_id, identity.todo_id);
  // Expiry/release already ends the original execution. A fresh managed claim
  // may establish the administrative writer, but must never be advertised as
  // preparation to resume that execution. The commit still requires a newer
  // generation and independently verifies it under the writer fence.
  const now = new Date();
  const originalLost = lease.lease_epoch === prepared.prepared_lease_epoch
    && typeof lease.version === "number" && Number.isSafeInteger(lease.version)
    && lease.version >= prepared.prepared_lease_version
    && typeof lease.idempotency_key === "string"
    && createHash("sha256").update(lease.idempotency_key).digest("hex") === prepared.prepared_lease_key_sha256
    && !leaseIsActive(lease, now);
  if (!originalLost && !fencesPreparedGeneration(prepared, lease)) return null;
  const writerMatches = todo.claimed_by === identity.agent_id && lease.owner === identity.agent_id
    && todo.done !== true && leaseRepositoryRejection(todo, lease) === null
    && canonicalLeaseTodoFact(todo)?.excluded_agents.includes(identity.agent_id) === false;
  // Same-owner recovery can re-enter the original guard. That path records a
  // fenced takeover before any new validation/writeback; this observation alone
  // never admits work or accepts a result. Competing ownership still holds.
  if (writerMatches) return null;
  return {original_prepared_epoch: prepared.prepared_lease_epoch, current_lease_epoch: lease.lease_epoch,
    current_lease_version: lease.version, current_owner: lease.owner,
    current_control_writer_active: false, can_reclaim_control_writer: false};
}

function optionalAssignedReplan(receipt: JsonObject): boolean {
  const id = heartbeatReceiptDetails(receipt).semantic_replan_obligation_id;
  return id != null && id !== "";
}

export const EXECUTION_LOSS_COMMIT_SCHEMA = "quota_execution_loss_closeout_commit_v0";
export const EXECUTION_LOSS_COMMIT_RESULT_SCHEMA = "quota_execution_loss_closeout_result_v0";

function reject(message: string, code = "refresh_task_lease_unavailable"): never {
  throw new EffectRuntimeRequestError(`execution-loss closeout ${message}`, code);
}
const digest = (bytes: Uint8Array): string => createHash("sha256").update(bytes).digest("hex");
function optionalBytes(path: string): Buffer | null {
  try {return readFileSync(path);}
  catch (error) {if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error;}
}
const bytes = (path: string): Buffer => optionalBytes(path) ?? Buffer.alloc(0);
function rows(path: string): JsonObject[] {
  const content = bytes(path);
  if (content.length && content[content.length - 1] !== 10)
    reject("has an incomplete history tail; retain the immutable Run for inspection", "execution_loss_commit_unknown");
  return content.toString("utf8").split(/\r?\n/u).filter(line => line.trim()).map(line => {
    try {return requireJsonObject(JSON.parse(line), "closeout history row");}
    catch {reject("has malformed history", "execution_loss_commit_unknown");}
  });
}
function immutableText(path: string, content: string): void {
  const existing = optionalBytes(path);
  if (existing !== null) {
    if (existing.toString("utf8") !== content) reject("immutable artifact differs", "execution_loss_commit_unknown");
    return;
  }
  mkdirSync(dirname(path), {recursive: true, mode: 0o700});
  const temporary = `${path}.tmp-${randomUUID()}`;
  try {
    const fd = openSync(temporary, "wx", 0o600);
    try {writeFileSync(fd, content, "utf8"); fsyncSync(fd);} finally {closeSync(fd);}
    renameSync(temporary, path);
    if (process.platform !== "win32") {
      const directory = openSync(dirname(path), "r");
      try {fsyncSync(directory);} finally {closeSync(directory);}
    }
  } finally {try {unlinkSync(temporary);} catch (error) {if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;}}
}

/** No alternate writer hooks or semantic defaults enter this closed intent. */
export function decodeExecutionLossCloseoutIntent(value: unknown): JsonObject {
  const intent = requireJsonObject(value, "administrative closeout intent");
  if (Object.keys(intent).sort().join("|") !==
      ["delivery_outcome", "delivery_boundary", "progress_observation"].sort().join("|")
      || intent.delivery_outcome !== "outcome_gap" || intent.delivery_boundary !== "semantic_closeout")
    reject("permits only explicit blocked no-spend intent");
  return {...intent, progress_observation: requireJsonObject(intent.progress_observation, "blocked observation")};
}

function rejectRequestedEffects(value: unknown): void {
  const effects = requireJsonObject(value, "requested administrative effects");
  const route = ["command", "format", "subcommand_format", "registry", "runtime_root", "goal_id", "goal_instance_id",
    "project", "state_file", "classification", "execution_loss_closeout", "agent_id", "todo_id", "turn_instance_id",
    "delivery_outcome", "delivery_boundary", "delivery_batch_scale", "progress_result_class", "progress_work_item_id",
    "progress_blocker_id", "progress_evidence_ids", "task_lease_idempotency_key", "task_lease_expected_version",
    "no_global_sync", "suppress_external_sinks", "quiet", "thin", "verbose", "dry_run"];
  for (const [field, value] of Object.entries(effects)) {
    if (field === "delivery_batch_scale" && value !== null && value !== undefined && value !== "single_surface")
      reject("permits only single-surface administrative closeout");
    if (!route.includes(field) && value !== null && value !== undefined && value !== false
        && !(Array.isArray(value) && value.length === 0)) reject(`does not permit ${field}`);
  }
}

export async function commitExecutionLossCloseout(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "administrative closeout request");
  if (request.schema_version !== EXECUTION_LOSS_COMMIT_SCHEMA || request.execution_loss_closeout !== true)
    reject("requires its explicit typed operation");
  const permitted = ["schema_version", "execution_loss_closeout", "runtime_root", "identity", "goal_ref",
    "source_admission", "registry_source", "locks", "intent", "control_writer_proof", "observed_at", "requested_effects", "dry_run"];
  if (typeof request.dry_run !== "boolean") reject("requires an explicit preview or execution mode");
  if (Object.keys(request).some(field => !permitted.includes(field))) reject("has unsupported requested effects");
  rejectRequestedEffects(request.requested_effects ?? {});
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  const binding = requireJsonObject(request.identity, "original identity");
  const identity = settlementIdentity({goal_id: requireNonEmptyString(binding.goal_id, "goal_id"),
    agent_id: requireNonEmptyString(binding.agent_id, "agent_id"), todo_id: requireNonEmptyString(binding.todo_id, "todo_id"),
    turn_instance_id: requireNonEmptyString(binding.turn_instance_id, "turn_instance_id")});
  if (!isDeepStrictEqual(binding, settlementIdentityPayload(identity))) reject("identity is not canonical");
  const intent = decodeExecutionLossCloseoutIntent(request.intent);
  const owner = parseQuotaAccountingOwner({goalRefValue: request.goal_ref,
    sourceAdmissionValue: request.source_admission, runtimeRoot: root, goalId: identity.goal_id});
  if (owner.kind === "alias" && (Object.hasOwn(request, "goal_ref") || Object.hasOwn(request, "source_admission")))
    reject("legacy ownership requires absent exact-source fields");
  const registrySource = requireJsonObject(request.registry_source, "registry source");
  const registryPath = resolve(requireNonEmptyString(registrySource.path, "registry path"));
  const lifetimeSources = registrySource.lifetime_sources ?? [];
  if (!Array.isArray(lifetimeSources) || lifetimeSources.length > 1
      || (owner.kind === "exact_source" && lifetimeSources.length)) reject("legacy source lifetime is unsupported");
  const upstream = lifetimeSources.map(source => requireJsonObject(source, "legacy source lifetime"));
  if (owner.kind === "exact_source" && registryPath !== owner.admission.registryPath)
    reject("registry differs from the original source admission");
  const indexPath = join(root, "goals", identity.goal_id, "runs", "index.jsonl");
  const eventPath = goalRolloutEventLogPath(root, identity.goal_id);
  const artifact = join(dirname(indexPath), `execution-loss-${createHash("sha256").update(identity.effect_id).digest("hex")}`);
  const jsonPath = `${artifact}.json`, markdownPath = `${artifact}.md`;
  const targets = [shadowMaintenanceLockPath(root, identity.goal_id), eventPath,
    ...(owner.kind === "alias" ? [indexPath, registryPath,
      ...upstream.map(source => requireNonEmptyString(source.path, "legacy source path"))] : [])]
    .map(path => resolve(path));
  const requestDigest = canonicalAuthoritySha256({identity: binding, goal_ref: quotaGoalRef(owner), intent});
  const claims: FileMutationLockClaim[] = [];
  try {
    if (!Array.isArray(request.locks) || request.locks.length !== targets.length)
      reject("requires the existing writer/lifetime lock handoff");
    for (const [index, value] of request.locks.entries()) {
      const witness = requireJsonObject(value, "writer lock witness");
      if (resolve(String(witness.target)) !== targets[index]) reject("writer lock target differs");
      const token = requireNonEmptyString(witness.token, "lock token");
      const claim = await claimFileMutationLock(targets[index], token);
      if (!claim) reject("writer lock handoff expired");
      claims.push(claim);
      const current = await mutationLockOwner(targets[index]);
      if (current?.token !== token || current.pid !== witness.pid) reject("writer lock owner changed");
    }
    return await withBorrowedQuotaAccountingOwner(owner, async () => {
      const registryBytes = readFileSync(registryPath);
      if (digest(registryBytes) !== registrySource.sha256) reject("current registry changed");
      // The existing registry codec validated this transport observation. Bind
      // it to complete bytes; exact-source absence cannot become a legacy alias.
      const document = JSON.parse(registryBytes.toString("utf8"));
      const registry = requireJsonObject(registrySource.payload, "decoded registry source");
      const rawPayload = Array.isArray(document) && document.length === 2 ? document[1] : document;
      if (!isDeepStrictEqual(registry, rawPayload)
          || (registry.profile_id === "source_session_v1") !== (owner.kind === "exact_source"))
        reject("registry profile and receipt ownership differ");
      const goals = registry.goals;
      if (!Array.isArray(goals)) reject("registry Goal authority is unavailable");
      const matches = goals.filter(goal => jsonObject(goal)?.id === identity.goal_id).map(goal => requireJsonObject(goal, "Goal"));
      const goal = matches[0];
      if (matches.length !== 1 || !goal || (owner.kind === "alias"
          ? !legacyGoalRegistrationIsActive(goal.status ?? "active") : goal.status !== "active")
          || Object.hasOwn(goal, "activation_state")
          || (goal.activation != null && (jsonObject(goal.activation)?.schema_version != null
            && jsonObject(goal.activation)?.schema_version !== "loopx_goal_activation_v1"
            || jsonObject(goal.activation)?.state !== "active"))
          || (owner.kind === "exact_source" && goal.goal_instance_id !== owner.goalRef.goalInstanceId.value))
        reject("current Goal is stopped, unavailable or recreated");
      const sourceRoute = goal.source_registry;
      const expectedUpstream = owner.kind === "alias" && typeof sourceRoute === "string" && sourceRoute.trim()
        ? resolve(dirname(registryPath), sourceRoute.trim()) : null;
      if ((sourceRoute != null && sourceRoute !== "" && typeof sourceRoute !== "string")
          || (expectedUpstream === null ? upstream.length !== 0
            : upstream.length !== 1 || resolve(String(upstream[0].path)) !== expectedUpstream))
        reject("current legacy source route differs from its lifetime admission");
      for (const source of upstream) {
        const sourceBytes = readFileSync(String(source.path));
        const sourcePayload = requireJsonObject(source.payload, "legacy source registry");
        const raw = JSON.parse(sourceBytes.toString("utf8"));
        if (digest(sourceBytes) !== source.sha256 || !isDeepStrictEqual(sourcePayload, Array.isArray(raw) ? raw[1] : raw)
            || sourcePayload.profile_id === "source_session_v1") reject("legacy source ownership changed");
        const sourceGoals = Array.isArray(sourcePayload.goals) ? sourcePayload.goals.map(jsonObject) : [];
        const sourceGoal = sourceGoals.filter(row => row?.id === identity.goal_id);
        if (sourceGoal.length !== 1 || !legacyGoalRegistrationIsActive(sourceGoal[0]?.status ?? "active")
            || Object.hasOwn(sourceGoal[0]!, "activation_state")
            || (sourceGoal[0]?.activation != null && jsonObject(sourceGoal[0].activation)?.state !== "active"))
          reject("legacy source Goal is unavailable or stopped");
      }
      const authority = decodeTaskLeaseAuthority({handoff_mode: "hard_lease", todos: [],
        registered_agent_candidates: [jsonObject(goal.coordination)?.registered_agents, goal.registered_agents,
          jsonObject(goal.spawn_policy)?.registered_agents],
        source_receipts: [{source_id: "registry", path: registryPath, state: "file", sha256: digest(registryBytes)}]});
      rows(indexPath); rows(eventPath);
      const indexDigest = digest(bytes(indexPath)), eventDigest = digest(bytes(eventPath));
      const snapshot = await readQuotaSettlementSnapshot(root, identity.goal_id);
      const readback = readQuotaSettlementForAdmittedOwnerFromSnapshot({
        schema_version: QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA, runtime_root: root,
        goal_id: identity.goal_id, agent_id: identity.agent_id, todo_id: identity.todo_id,
        turn_instance_id: identity.turn_instance_id, infer_turn_instance_id: false, allow_unbound_binding: false,
      }, owner, snapshot, {includeOriginalPreparedReceipt: true});
      const receipt = jsonObject(readback.original_prepared_receipt);
      if (receipt === null) reject("the original Turn's positive prepared admission is unavailable");
      requireQuotaOwnerProjection(owner, receipt.goal_ref, "original heartbeat receipt");
      if (jsonObject(readback.identity)?.failure != null) reject("original settlement identity could not be verified");
      const existingBytes = optionalBytes(jsonPath);
      let existing: JsonObject | null = null;
      if (existingBytes !== null) {
        try {existing = requireJsonObject(JSON.parse(existingBytes.toString("utf8")), "immutable closeout Run");}
        catch {reject("immutable Run is empty or malformed", "execution_loss_commit_unknown");}
      }
      if (existing && jsonObject(existing.execution_loss_commit)?.request_sha256 !== requestDigest)
        reject("intent differs from the original immutable Run", "committed_writeback_payload_conflict");
      const prior = jsonObject(readback.writeback_run);
      if (prior && (!existing || !isDeepStrictEqual(prior, existing)))
        reject("cannot replace the original writeback", "committed_writeback_payload_conflict");
      if (readback.spend_run != null || readback.spend_event != null || readback.completion_event != null
          || readback.supersede_event != null || (!existing && readback.writeback_event != null))
        reject("cannot erase business completion or a committed debit");
      if (!existing && (readback.replay_phase !== "open" || jsonObject(readback.progress)?.state !== "writeback_required"))
        reject("requires the original unclosed first writeback");
      if (existing && prior && readback.replay_phase === "settled") {
        return closeoutReply(readback, root, binding, prior, owner, request.dry_run === true, true);
      }
      await requireShadowPrimaryWriteAllowed(root, identity.goal_id);
      const store = await openRuntimeAuthorityStore(root, identity.goal_id, {});
      if (!(store instanceof FileAuthorityStore) && !(store instanceof SqliteAuthorityStore))
        reject("supports only the existing File and SQLite provider fences");
      const result = await store.withCheckpointHead((head, providerIdentity) => {
        // No await from current canonical head through all durable writes.
        if (digest(readFileSync(registryPath)) !== registrySource.sha256
            || digest(bytes(indexPath)) !== indexDigest || digest(bytes(eventPath)) !== eventDigest)
          reject("sources changed before administrative commit");
        if (upstream.some(source => digest(readFileSync(String(source.path))) !== source.sha256))
          reject("legacy source changed before administrative commit");
        requireCurrentQuotaAccountingOwner(owner);
        const projection = indexCoordinationProjection(head.head, identity.goal_id);
        if (head.head.handoff_mode !== "hard_lease") reject("requires original hard-lease authority");
        const qualification = qualifyReceiptBoundExecutionLossCloseout({
          schema_version: EXECUTION_LOSS_QUALIFICATION_SCHEMA, identity: binding,
          ...(owner.kind === "exact_source" ? {goal_ref: quotaGoalRef(owner)} : {}), receipt,
          current_todo: projection.todos.get(identity.todo_id!), current_lease: projection.leases.get(identity.todo_id!),
          registered_agents: authority.registered_agents, control_writer_proof: request.control_writer_proof,
          progress_observation: intent.progress_observation, observed_at: request.observed_at,
          provider_identity: providerIdentity, provider_revision: head.provider_revision,
        });
        const buildRun = (qualified: JsonObject, observedAt: unknown): JsonObject => {
          const event = {schema_version: "loopx_rollout_event_v0",
            event_id: createHash("sha256").update(`${identity.effect_id}#execution-loss-closeout`).digest("hex"),
            event_kind: "refresh_state", goal_id: identity.goal_id, agent_id: identity.agent_id,
            todo_id: identity.todo_id, run_id: identity.turn_instance_id, status: "appended",
            recorded_at: observedAt, ...(owner.kind === "exact_source" ? {goal_ref: quotaGoalRef(owner)} : {}),
            details: {settlement_effect_id: identity.effect_id, execution_loss_closeout: qualified}};
          return {generated_at: observedAt, goal_id: identity.goal_id, agent_id: identity.agent_id,
            todo_id: identity.todo_id, turn_instance_id: identity.turn_instance_id, settlement_identity: binding,
            ...(owner.kind === "exact_source" ? {goal_ref: quotaGoalRef(owner)} : {}),
            classification: "execution_loss_closeout", delivery_outcome: "outcome_gap", delivery_batch_scale: "single_surface",
            delivery_boundary: "semantic_closeout", progress_observation: intent.progress_observation,
            blocked_retry: qualified, execution_loss_closeout: qualified,
            json_path: jsonPath, markdown_path: markdownPath,
            execution_loss_commit: {schema_version: EXECUTION_LOSS_COMMIT_SCHEMA, request_sha256: requestDigest,
              original_receipt_sha256: canonicalAuthoritySha256(receipt), event}};
        };
        if (request.dry_run === true) return {record: buildRun(qualification, request.observed_at), preview: true};
        let record = existing;
        if (!record) {
          record = buildRun(qualification, request.observed_at);
          immutableText(jsonPath, JSON.stringify(record, null, 2) + "\n");
        } else {
          const frozen = jsonObject(record.execution_loss_closeout);
          if (!frozen || !isExecutionLossCloseout(frozen, identity.todo_id)
              || frozen.effect_id !== identity.effect_id || frozen.observed_at !== record.generated_at
              || frozen.original_receipt_event_id !== receipt.event_id
              || !isDeepStrictEqual(frozen.original_prepared, qualification.original_prepared)
              || jsonObject(frozen.current_control_writer)?.owner !== identity.agent_id
              || jsonObject(frozen.current_control_writer)?.store_identity !== providerIdentity
              || frozen.observation_fingerprint !== jsonObject(intent.progress_observation)?.fingerprint
              || !isDeepStrictEqual(record, buildRun(frozen, record.generated_at)))
            reject("immutable Run does not match original administrative intent", "execution_loss_commit_unknown");
        }
        const event = requireJsonObject(requireJsonObject(record.execution_loss_commit, "closeout intent").event, "closeout receipt");
        if (event.event_kind !== "refresh_state" || event.run_id !== identity.turn_instance_id
            || jsonObject(event.details)?.settlement_effect_id !== identity.effect_id
            || !isDeepStrictEqual(jsonObject(event.details)?.execution_loss_closeout, record.execution_loss_closeout))
          reject("immutable refresh receipt is invalid", "execution_loss_commit_unknown");
        immutableText(markdownPath, `# Administrative execution-loss closeout\n\nTurn: ${identity.turn_instance_id}\nNo delivery, completion or quota spend.\n`);
        const indexed = rows(indexPath).filter(row => jsonObject(row.settlement_identity)?.effect_id === identity.effect_id);
        if (indexed.length > 1 || (indexed.length === 1 && !isDeepStrictEqual(indexed[0], record)))
          reject("indexed writeback differs", "execution_loss_commit_unknown");
        const recorded = rows(eventPath).filter(row => row.event_id === event.event_id);
        if (recorded.length > 1 || (recorded.length === 1 && !isDeepStrictEqual(recorded[0], event)))
          reject("refresh receipt differs", "execution_loss_commit_unknown");
        if (!indexed.length) appendJsonLineSync(indexPath, record);
        if (!recorded.length) appendJsonLineSync(eventPath, event);
        return {record, replayed: indexed.length === 1 && recorded.length === 1};
      });
      if (result.preview === true) {
        return closeoutReply(readback, root, binding, requireJsonObject(result.record, "preview Run"), owner, true, false);
      }
      const finalSnapshot = await readQuotaSettlementSnapshot(root, identity.goal_id);
      const final = readQuotaSettlementForAdmittedOwnerFromSnapshot({schema_version: QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
        runtime_root: root, goal_id: identity.goal_id, agent_id: identity.agent_id, todo_id: identity.todo_id,
        turn_instance_id: identity.turn_instance_id, infer_turn_instance_id: false, allow_unbound_binding: false}, owner, finalSnapshot);
      if (final.replay_phase !== "settled") reject("durable no-spend readback is incomplete", "execution_loss_commit_unknown");
      return closeoutReply(final, root, binding, requireJsonObject(result.record, "closeout Run"), owner, false,
        result.replayed === true);
    });
  } finally {for (const claim of claims.reverse()) await releaseFileMutationLockClaim(claim);}
}

function closeoutReply(readback: JsonObject, root: string, binding: JsonObject,
    record: JsonObject, owner: QuotaAccountingOwner, preview: boolean, replayed: boolean): JsonObject {
  return {schema_version: EXECUTION_LOSS_COMMIT_RESULT_SCHEMA, ok: true, mode: "execution_loss_closeout",
    classification: "execution_loss_closeout", appended: !preview && !replayed, idempotent_replay: replayed,
    dry_run: preview, runtime_root: root, goal_id: binding.goal_id, settlement_identity: binding,
    ...(owner.kind === "exact_source" ? {goal_ref: quotaGoalRef(owner)} : {}),
    execution_loss_closeout: record.execution_loss_closeout,
    ...(preview ? {would_close_without_spend: !replayed} : {json_path: record.json_path, markdown_path: record.markdown_path}),
    settlement_progress: readback.progress, replay_phase: readback.replay_phase,
    settlement_result: jsonObject(readback.settlement)?.payload};
}
