/** Authority preparation for the planned-transfer composition.
 *
 * This owner closes canonical writes and pins the complete native journal. It
 * does not certify Host/effect drain, portable-artifact custody or destination
 * admission. The product coordinator must obtain those owners' decisions before
 * exposing a cutover. No public activation entry is registered here.
 */
import {mkdir, readFile, stat} from "node:fs/promises";
import {isAbsolute, join, resolve} from "node:path";
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import type {GoalTransferHoldCode} from "../goals/planned_transfer_contract.ts";
import {durableWriteJson} from "../effect_runtime_io.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {exportAuthorityArchive, verifyAuthorityArchive, type AuthorityArchiveSummary} from "./authority_archive.ts";
import {AUTHORITY_ARCHIVE_SCHEMA} from "./authority_archive_read.ts";
import {indexCoordinationProjection} from "./coordination_projection.ts";
import {canonicalTaskLease} from "./task_lease_state.ts";
import {openLocalAuthorityStoreHandle, requireLocalAuthorityRuntimeRoot} from "./local_authority_provider.ts";
import {loadLegacyCoordinationWriterFence, preparePlannedTransferWriterFenceUnderLock,
  restorePlannedTransferWriterFenceUnderLock, PLANNED_TRANSFER_WRITER_FENCE_SCHEMA,
  isPlannedTransferAdmissionFence,
  type PlannedTransferWriterFence} from "./legacy_writer_fence.ts";
import {readShadowManagementState, shadowManagementDirectory, withShadowMaintenanceLock} from "./shadow_management.ts";
import {PREPARATION_SCHEMA, checkGoalTransferSourceContext, readGoalTransferSnapshot,
  withVerifiedGoalTransferContext} from "../goals/planned_transfer.ts";
import {reviewProtectedGoalTransferInputs, writeProtectedGoalTransfer} from "../goals/planned_transfer_payload.ts";
import type {AuthorityStore} from "./authority_store.ts";
import {newGoalCreationBinding} from "./new_goal_initialization.ts";
import {readPromotionReceipt} from "./promotion_receipt.ts";
import {NEW_GOAL_WRITER_FENCE_SCHEMA} from "./legacy_writer_fence.ts";
import {readGoalTurnHistoryForTransfer, readGoalTurnJournalsForTransfer} from "../turn_driver/turn_journal_query.ts";
import {goalTransferHistory, readGoalTransferHistoryJson, readTransferValidationDeclarations} from "../goals/planned_transfer_history.ts";
import {readGoalQuotaHistoryForTransfer} from "../quota/accounting_artifact_transaction.ts";
import {observeLocalWorkspaceHostIdentity} from "../work_items/task_lease_workspace.ts";

export {PREPARATION_SCHEMA} from "../goals/planned_transfer.ts";
const REGISTERED_SOURCE_PREVIEW_SCHEMA = "loopx_planned_transfer_source_preview_v0";
// Local to this Goal-owned preparation protocol; no destination grant or new
// runtime lifecycle vocabulary is introduced by this retained decision.
const CANCELLATION_SCHEMA = "loopx_planned_transfer_source_cancellation_v0";

/** The codec calls while holding its original registry transaction. Ordinary
 * metadata can no longer reopen, remove or redirect a prepared Goal. Changes
 * to other Goals remain legal; the registry is not frozen as a whole. This
 * consumes codec-decoded facts and a byte witness, never another JSON parser.
 */
export async function checkPlannedTransferRegistryWrite(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["before", "after", "registry_witness"])) reject("Registry transfer guard requires its codec observation");
  const before = requireJsonObject(value.before, "current registry"), after = requireJsonObject(value.after, "proposed registry");
  const witness = requireJsonObject(value.registry_witness, "registry codec witness");
  if (!hasExactAuthorityKeys(witness, ["path", "sha256"]) || typeof witness.path !== "string" ||
      !isAbsolute(witness.path) || resolve(witness.path) !== witness.path ||
      (witness.sha256 !== null && !BARE_SHA256_PATTERN.test(String(witness.sha256)))) reject("Invalid registry codec witness");
  const verify = async () => {
    let digest: string | null;
    try { digest = createHash("sha256").update(await readFile(String(witness.path))).digest("hex"); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; digest = null; }
    if (digest !== witness.sha256) reject("Registry codec observation changed");
  };
  await verify();
  // A global-local registry is a projection, never the actual source owner.
  if (before.registry_role !== "global-local") {
    const original = Array.isArray(before.goals) ? before.goals : [];
    const proposed = Array.isArray(after.goals) ? after.goals : [];
    const roots = new Set([before.common_runtime_root, after.common_runtime_root].filter(value => typeof value === "string"));
    const ids = new Set([...original, ...proposed].map(raw => requireAuthorityStoreId(requireJsonObject(raw, "Goal").id, "Goal id")));
    for (const rawRoot of roots) for (const goalId of ids) {
      const root = requireLocalAuthorityRuntimeRoot(rawRoot);
      const fence = await loadLegacyCoordinationWriterFence(root, goalId);
      if (fence.status === "failed") reject("Goal writer fence is unreadable; preserve its original registration");
      if (fence.status !== "loaded" || !(fence.fence.schema_version === PLANNED_TRANSFER_WRITER_FENCE_SCHEMA || isPlannedTransferAdmissionFence(fence.fence))) continue;
      const matches = proposed.filter(row => row !== null && typeof row === "object" && !Array.isArray(row) && (row as JsonObject).id === goalId);
      const prior = original.filter(row => row !== null && typeof row === "object" && !Array.isArray(row) && (row as JsonObject).id === goalId);
      if (isPlannedTransferAdmissionFence(fence.fence)) {
        const {requireLocalGoalTransferAdmission} = await import("../goals/planned_transfer_cutover.ts");
        await requireLocalGoalTransferAdmission(root, goalId, fence.fence);
        const beforeGoal = prior[0] as JsonObject | undefined, afterGoal = matches[0] as JsonObject | undefined;
        if (prior.length !== 1 || matches.length !== 1 || root !== before.common_runtime_root || root !== after.common_runtime_root ||
            ["repo", "state_file", "planned_transfer", "goal_instance_id", "creation_operation_id"].some(key => !same(beforeGoal?.[key] ?? null, afterGoal?.[key] ?? null)) ||
            ["registry_role", "role", "profile_id"].some(key => !same(before[key] ?? null, after[key] ?? null))) {
          reject("Admitted target lineage cannot be removed or redirected by registry metadata; use its Goal lifecycle owner");
        }
        continue;
      }
      if (prior.length === 0 && fence.fence.role === "target" && matches.length === 1) {
        const directory = join(shadowManagementDirectory(root, goalId), "planned-transfer-targets", canonicalAuthoritySha256(fence.fence.operation_id));
        const registration = await optionalJson(join(directory, "registration.json")), receipt = await optionalJson(join(directory, "receipt.json"));
        if (registration && receipt && registration.identity_sha256 === fence.fence.source_identity_sha256 && registration.registry_path === witness.path &&
            receipt.identity_sha256 === fence.fence.source_identity_sha256 && receipt.target_is_active === false &&
            receipt.execution_authority_granted === false && after.common_runtime_root === root && same(registration.goal, matches[0]) &&
            after.profile_id === undefined && after.registry_role !== "global-local" && after.role !== "global-local" &&
            ["registry_role", "role", "profile_id"].every(key => same(before[key] ?? null, after[key] ?? null))) continue;
      }
      if (prior.length === 1 && fence.fence.role === "target" && matches.length === 1 &&
          root === before.common_runtime_root && after.common_runtime_root === root &&
          ["registry_role", "role", "profile_id"].every(key => same(before[key] ?? null, after[key] ?? null))) {
        const {localTransferRegistryPublication} = await import("../goals/planned_transfer_cutover.ts");
        if (await localTransferRegistryPublication(root, goalId, String(fence.fence.operation_id), String(witness.path), matches[0] as JsonObject)) continue;
      }
      if (root !== before.common_runtime_root || prior.length !== 1 || matches.length !== 1 || !same(matches[0], prior[0]) ||
          ["common_runtime_root", "registry_role", "role", "profile_id"].some(key => !same(before[key] ?? null, after[key] ?? null))) {
        reject(`Goal registration is prepared for transfer ${fence.fence.operation_id}; recover that original operation`);
      }
    }
  }
  await verify();
  return {allowed: true};
}

function reject(message: string): never { throw new EffectRuntimeConflictError(message); }
function same(a: unknown, b: unknown): boolean { return canonicalAuthorityBytes(a).equals(canonicalAuthorityBytes(b)); }
async function optionalJson(path: string): Promise<JsonObject | null> {
  try { return await readGoalTransferHistoryJson(path); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
}
async function retain(path: string, value: JsonObject): Promise<void> {
  const prior = await optionalJson(path);
  if (prior) {
    if (!same(prior, value)) reject("Transfer preparation belongs to different source facts; recover its original operation");
    return;
  }
  await durableWriteJson(path, value);
  if (!same(await optionalJson(path), value)) reject("Transfer preparation readback failed");
}

/** First product source profile: an ordinary registered local Goal whose
 * original creation is independently readable after subsequent native writes.
 * A retained creation receipt is provenance here, never a target creation grant.
 * Imported/legacy origins need their own original-registration qualification;
 * the lower-level archive/preparation owners remain available without claiming it.
 */
async function requireRegisteredSourceOrigin(context: JsonObject, originalFence: JsonObject,
  store: AuthorityStore, goalId: string): Promise<void> {
  const goal = requireJsonObject(context.goal, "original registered Goal");
  if (goal.status !== "active") reject("Transfer requires the current registered Goal; recover its original lifecycle before preparing a move");
  const coordination = requireJsonObject(goal.coordination, "original Goal coordination");
  const target = requireJsonObject(coordination.storage_target, "original Goal storage target");
  if (originalFence.schema_version !== NEW_GOAL_WRITER_FENCE_SCHEMA || originalFence.creation_completed !== true ||
      typeof goal.creation_operation_id !== "string" || target.schema_version !== "loopx_new_goal_storage_target_v1") {
    reject("This transfer source requires its qualified original registration; imported origins need their owning provenance check");
  }
  const binding = newGoalCreationBinding(goalId, goal.creation_operation_id, target,
    resolve(String(goal.repo), String(goal.state_file)), context.registry_path);
  if (!same(originalFence, {...binding.fence, creation_completed: true}) ||
      !(await readPromotionReceipt(store, binding.identity)).matched) {
    reject("Transfer source differs from its original registered creation and retained receipt; repair that source without recreating it");
  }
}

async function registeredSourcePreview(context: JsonObject, originalFence: JsonObject,
  store: AuthorityStore, provider: string, head: {head: JsonObject; cursor: string; provider_revision: string},
  storeIdentity: string, goalId: string, operationId: string): Promise<JsonObject> {
  await requireRegisteredSourceOrigin(context, originalFence, store, goalId);
  const index = indexCoordinationProjection(head.head, goalId);
  const retainedLeases = [...index.leases].map(([todo, lease]) => canonicalTaskLease(lease, goalId, todo))
    .filter(lease => lease.status !== "released");
  const turnHistory = await readGoalTurnHistoryForTransfer(String(context.runtime_root), goalId);
  const quotaHistory = await readGoalQuotaHistoryForTransfer(String(context.runtime_root), goalId);
  const facts = {schema_version: REGISTERED_SOURCE_PREVIEW_SCHEMA, goal_id: goalId, operation_id: operationId,
    source_host_identity: await observeLocalWorkspaceHostIdentity(),
    source_context_sha256: canonicalAuthoritySha256(context), original_registration_verified: true,
    original_writer_fence_sha256: canonicalAuthoritySha256(originalFence), provider,
    provider_revision: head.provider_revision, cursor: head.cursor, source_store_identity: storeIdentity,
    retained_leases: retainedLeases, turn_history: turnHistory, quota_history: quotaHistory,
    ready_for_authority_preparation: retainedLeases.length === 0 && turnHistory.closed === true &&
      turnHistory.turn_hosts_closed === true && quotaHistory.closed === true,
    host_drain_qualified: false, portable_artifact_qualified: false, execution_authority_granted: false};
  return {...facts, preview_sha256: canonicalAuthoritySha256(facts)};
}

export async function previewRegisteredAuthorityForPlannedTransfer(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "operation_id", "source_context", "registry_witness"])) {
    reject("Registered source preview requires its complete original Goal context");
  }
  const operationId = requireAuthorityStoreId(value.operation_id, "transfer operation id");
  return visitRegisteredTransferSource(value, operationId, async source => source.preview);
}

interface RegisteredTransferSource {
  context: JsonObject;
  objective: string;
  preview: JsonObject;
  projection: JsonObject;
  preparedOperation: string | null;
}

/** A product read is bound to the same source/maintenance observation as
 * preparation. It does not rediscover authority from a persisted UI carrier. */
export async function visitRegisteredTransferSource<T>(value: JsonObject, requestedOperation: string | null,
  consume: (source: RegisteredTransferSource) => Promise<T>): Promise<T> {
  const root = requireLocalAuthorityRuntimeRoot(value.runtime_root);
  const goalId = requireAuthorityStoreId(value.goal_id, "source Goal id");
  await checkGoalTransferSourceContext(value.source_context, value.registry_witness, goalId, root);
  const runtime = await stat(root).catch((error: NodeJS.ErrnoException) => {
    if (error.code === "ENOENT" || error.code === "ENOTDIR") return null;
    throw error;
  });
  if (!runtime?.isDirectory()) reject("Original source runtime is unavailable; restore it without reinitializing it");
  return withShadowMaintenanceLock(root, goalId, () => withVerifiedGoalTransferContext(
    value.source_context, value.registry_witness, goalId, root, async (context, snapshot) => {
      const managed = await readShadowManagementState(root, goalId);
      if (managed && managed.status !== "inactive") reject("Source capture requires disposition before transfer preview");
      const current = await loadLegacyCoordinationWriterFence(root, goalId);
      if (current.status !== "loaded") reject("Original source writer fence is unavailable");
      let original = current.fence;
      const preparedOperation = original.schema_version === PLANNED_TRANSFER_WRITER_FENCE_SCHEMA
        ? requireAuthorityStoreId(original.operation_id, "prepared transfer operation") : null;
      const operationId = requestedOperation ?? preparedOperation ?? `inspect:${goalId}`;
      await requireUncancelledPreparation(join(shadowManagementDirectory(root, goalId), "planned-transfers", canonicalAuthoritySha256(operationId)));
      if (original.schema_version === PLANNED_TRANSFER_WRITER_FENCE_SCHEMA) {
        const pending = await optionalJson(join(shadowManagementDirectory(root, goalId), "planned-transfers",
          canonicalAuthoritySha256(operationId), "preparation.json"));
        if (!pending || original.role !== "source" || original.operation_id !== operationId ||
            original.source_identity_sha256 !== canonicalAuthoritySha256(pending)) reject("Source belongs to another prepared transfer; recover its original operation");
        if (pending.source_host_identity !== undefined && pending.source_host_identity !== await observeLocalWorkspaceHostIdentity()) {
          reject("Prepared source belongs to another OS installation; use its original Goal owner");
        }
        original = requireJsonObject(pending.original_writer_fence, "original source fence");
      }
      const opened = await openLocalAuthorityStoreHandle(root, goalId, {}, {existingOnly: true});
      if (opened.provider !== "file" && opened.provider !== "sqlite") reject("Transfer preview requires a qualified local File/SQLite source");
      const head = await opened.store.loadAuthority(), identity = await opened.store.storeIdentity();
      if (head.status !== "loaded" || identity.status !== "available") reject("Original source history is unavailable; restore it before transfer");
      const preview = await registeredSourcePreview(context, original, opened.store, opened.provider, head, identity.store_identity, goalId, operationId);
      if (typeof snapshot.state_objective !== "string") reject("Original Goal objective is unreadable");
      return consume({context, objective: snapshot.state_objective, preview, projection: head.head, preparedOperation});
    }));
}

/** First-party workspace reads may follow only the existing local cutover
 * proof. This read neither grants writer admission nor redirects executors.
 * Ordinary fences need no transfer snapshot or authority-history read. */
export async function readGoalTransferWorkspaceTarget(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "registry_path"])) reject("Workspace transfer read requires its registered source");
  const root = requireLocalAuthorityRuntimeRoot(value.runtime_root);
  const goal = requireAuthorityStoreId(value.goal_id, "Goal id");
  const fence = await loadLegacyCoordinationWriterFence(root, goal);
  if (fence.status === "missing") return {target: null};
  if (fence.status !== "loaded") reject("Current workspace writer boundary is unavailable");
  if (fence.fence.schema_version !== PLANNED_TRANSFER_WRITER_FENCE_SCHEMA || fence.fence.role !== "source") return {target: null};
  const snapshot = await readGoalTransferSnapshot(String(value.registry_path), goal);
  const context = requireJsonObject(snapshot.source_context, "workspace source context");
  if (context.runtime_root !== root) reject("Workspace source runtime changed");
  const view = await inspectRegisteredGoalTransfer({runtime_root: root, goal_id: goal,
    source_context: context, registry_witness: snapshot.registry_witness});
  return {target: view._local_activation_target ?? null};
}

/** The caller-facing continuation view. This local vocabulary describes this
 * Goal's actual preparation and its remaining continuation gap; it is not a
 * replacement lifecycle, provider decision or execution grant. Full native
 * Todo records retain original declarations, relations and acceptance fields. */
export async function inspectRegisteredGoalTransfer(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "source_context", "registry_witness"])) {
    reject("Transfer inspection requires its registered source observation");
  }
  const {inspectAdmittedGoalTransferDestination} = await import("../goals/planned_transfer_continuation.ts");
  const destination = await inspectAdmittedGoalTransferDestination(value);
  if (destination !== null) return destination;
  return visitRegisteredTransferSource(value, null, async source => {
    const {context, preview, projection, preparedOperation} = source;
    const goal = requireJsonObject(context.goal, "original Goal");
    const coordination = requireJsonObject(goal.coordination, "original coordination");
    const todos = indexCoordinationProjection(projection, String(goal.id));
    const holds: Array<{code: GoalTransferHoldCode; owner: string}> = [];
    if ((preview.retained_leases as JsonObject[]).length) holds.push({code: "retained_leases", owner: "task_lease"});
    const turns = requireJsonObject(preview.turn_history, "original Turn history");
    if (turns.closed !== true || turns.turn_hosts_closed !== true) holds.push({code: "turn_or_host_unsettled", owner: "turn_host"});
    if (requireJsonObject(preview.quota_history, "original quota history").closed !== true) holds.push({code: "quota_unsettled", owner: "quota"});
    const cutover = preparedOperation === null ? null : await optionalJson(join(shadowManagementDirectory(String(context.runtime_root), String(goal.id)),
      "planned-transfers", canonicalAuthoritySha256(preparedOperation), "cutover.json"));
    const {readLocalGoalTransferCutoverReview} = await import("../goals/planned_transfer_cutover.ts");
    const review = preparedOperation === null ? null : await readLocalGoalTransferCutoverReview(String(context.runtime_root), String(goal.id), preparedOperation);
    if (cutover) {
      const {readLocalGoalTransferContinuation} = await import("../goals/planned_transfer_cutover.ts");
      const current = await readLocalGoalTransferContinuation(cutover);
      const work = current.work as JsonObject | undefined;
      // Runtime admission does not settle the explicitly selected Task. Keep
      // its original confirmation reachable after a failed/lost continuation;
      // a completed Task and the default admission-only profile need no retry.
      const originalWorkPending = review?.continue_todo_id !== undefined && Array.isArray(work?.todos) &&
        (work.todos as JsonObject[]).some(todo => todo.todo_id === review.continue_todo_id &&
          todo.done !== true && todo.archive_state !== "archive" && todo.status === "open");
      return {ok: true, schema_version: "loopx_planned_transfer_operation_view_v0", goal_id: goal.id,
        source_state: "cutover_recorded", operation_id: preparedOperation, source_provider: preview.provider, source_revision: preview.provider_revision,
        authority_preparation_ready: false, cancel_available: false, continuation_available: false,
        local_cutover: {source_final: true, target_task_admitted: current.target_task_admitted,
          target_runtime_admitted: current.target_runtime_admitted,
          ...(current.target_activation_state ? {target_activation_state: current.target_activation_state} : {})},
        ...(review && (!current.target_runtime_admitted || originalWorkPending) ? {_local_cutover_review: review} : {}),
        ...(current._local_activation_target ? {_local_activation_target: current._local_activation_target} : {}),
        work: current.work ?? {objective: source.objective, requirements: context.state_text,
          agents: coordination.registered_agents ?? [], todos: todos.todo_ids.map(id => todos.todos.get(id)!)},
        holds: [{code: "transfer_journey_incomplete" satisfies GoalTransferHoldCode, owner: "goal_transfer"}]};
    }
    // Staging still has no lawful destination grant. A green source preview,
    // a copied Task or a released lease must never erase that product gap.
    holds.push(review ? {code: "transfer_journey_incomplete", owner: "goal_transfer"}
      : {code: "destination_admission_unavailable", owner: "goal_admission"});
    return {ok: true, schema_version: "loopx_planned_transfer_operation_view_v0", goal_id: goal.id,
      source_state: preparedOperation === null ? "available" : "prepared", operation_id: preparedOperation,
      source_provider: preview.provider, source_revision: preview.provider_revision,
      authority_preparation_ready: preview.ready_for_authority_preparation,
      cancel_available: preparedOperation !== null, continuation_available: false,
      ...(review ? {_local_cutover_review: review} : {}),
      work: {objective: source.objective, requirements: context.state_text,
        agents: coordination.registered_agents ?? [], todos: todos.todo_ids.map(id => todos.todos.get(id)!)}, holds};
  });
}

/** The maintenance guard serializes preparation against every ordinary native
 * writer. Expiration does not settle a lease: every retained token must have
 * been released through its original owner before this operation can fence it.
 *
 * Retrying the same operation recovers the original capture after interruption.
 * It never reopens the source or rewrites a receipt following later changes.
 */
export async function prepareAuthorityForPlannedTransfer(value: JsonObject): Promise<JsonObject> {
  const contextBound = value.source_context !== undefined;
  const registered = value.registered_source_preview_sha256 !== undefined;
  const protectedExport = value.protected_transport !== undefined;
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "operation_id", "expected_provider_revision",
      "expected_writer_fence_sha256", "execute", ...(contextBound ? ["source_context", "registry_witness"] : []),
      ...(registered ? ["registered_source_preview_sha256"] : []),
      ...(protectedExport ? ["protected_transport", "source_package_version"] : [])]) || value.execute !== true || (registered && !contextBound) ||
      (protectedExport && !registered) ||
      typeof value.expected_writer_fence_sha256 !== "string" || !BARE_SHA256_PATTERN.test(value.expected_writer_fence_sha256)) {
    reject("Transfer authority preparation requires an explicit execution and the complete reviewed source identity");
  }
  const root = requireLocalAuthorityRuntimeRoot(value.runtime_root);
  const goal = requireAuthorityStoreId(value.goal_id, "transfer Goal id");
  const operation = requireAuthorityStoreId(value.operation_id, "transfer operation id");
  if (!(await stat(root)).isDirectory()) reject("Original source runtime is unavailable; do not recreate its preparation");
  const expectedRevision = requireAuthorityStoreId(value.expected_provider_revision, "reviewed source revision");
  const request = {goal_id: goal, operation_id: operation, expected_provider_revision: expectedRevision,
    expected_writer_fence_sha256: value.expected_writer_fence_sha256};
  const directory = join(shadowManagementDirectory(root, goal), "planned-transfers", canonicalAuthoritySha256(operation));
  return withShadowMaintenanceLock(root, goal, async () => {
    await requireUncancelledPreparation(directory);
    if (await optionalJson(join(directory, "cutover.json"))) reject("Source cutover is final; continue its original destination operation");
    const prepare = async (context?: JsonObject) => {
    const managed = await readShadowManagementState(root, goal);
    if (managed && managed.status !== "inactive") reject("Source capture requires disposition before planned transfer");
    const current = await loadLegacyCoordinationWriterFence(root, goal);
    if (current.status !== "loaded") reject("Transfer requires the original verified canonical writer fence");
    const opened = await openLocalAuthorityStoreHandle(root, goal, {}, {existingOnly: true});
    if (opened.provider !== "file" && opened.provider !== "sqlite") reject("Planned transfer requires a qualified local File or SQLite source");
    const head = await opened.store.loadAuthority(), identity = await opened.store.storeIdentity();
    if (head.status !== "loaded" || identity.status !== "available") reject("Transfer source authority is unavailable; restore it before preparing a move");
    if (head.provider_revision !== expectedRevision) reject("Source authority changed; review a fresh transfer preview");
    const index = indexCoordinationProjection(head.head, goal);
    const leases = [...index.leases].map(([todo, lease]) => canonicalTaskLease(lease, goal, todo));
    if (leases.some(lease => lease.status !== "released")) {
      reject("Source retains an unreleased lease; settle its original operation before planned transfer");
    }
    const turnHistory = await readGoalTurnHistoryForTransfer(root, goal);
    if (turnHistory.closed !== true) reject("Source Turn effects are not closed; settle or recover their original operations before preparing transfer");
    if (turnHistory.turn_hosts_closed !== true) reject("Source Turn Hosts are not proven drained; recover their original process supervision before preparing transfer");
    const quotaHistory = await readGoalQuotaHistoryForTransfer(root, goal);
    if (quotaHistory.closed !== true) reject("Source quota effects are not closed; recover the original debit or void before preparing transfer");
    // Validate the new carrier's addresses and per-file bounds before
    // changing the original writer fence.
    const validations = await readTransferValidationDeclarations(root, goal, head.head);
    const runtimeHistory = goalTransferHistory(root, goal, turnHistory, quotaHistory, validations);
    const pendingPath = join(directory, "preparation.json");
    const pending = await optionalJson(pendingPath);
    const facts = {schema_version: PREPARATION_SCHEMA, request,
      ...(registered ? {source_host_identity: await observeLocalWorkspaceHostIdentity()} : {}),
      source_provider: opened.provider, source_store_identity: identity.store_identity,
      source_cursor: head.cursor, source_projection_sha256: canonicalAuthoritySha256(head.head),
      source_turn_history: turnHistory,
      source_quota_history: quotaHistory,
      ...(validations.length ? {source_validation_declarations: validations} : {}),
      ...(context ? {source_context: context, source_context_sha256: canonicalAuthoritySha256(context)} : {})};
    let priorFence: JsonObject;
    if (pending) {
      priorFence = requireJsonObject(pending.original_writer_fence, "original source writer fence");
      if (!same(pending, {...facts, original_writer_fence: priorFence})) reject("Transfer source or original operation changed");
      // A carrier is written before the fence, so an interrupted first call may
      // still find the exact original fence. Neither case permits other bytes.
      const expectedFence = sourcePreparationFence(goal, operation, pending);
      if (!same(current.fence, priorFence) && !same(current.fence, expectedFence)) reject("Source writer fence changed during transfer preparation");
    } else {
      if (current.fence.schema_version === PLANNED_TRANSFER_WRITER_FENCE_SCHEMA) reject("Source is already prepared by another transfer operation");
      priorFence = current.fence;
    }
    const preparation = {...facts, original_writer_fence: priorFence};
    if (canonicalAuthoritySha256(priorFence) !== value.expected_writer_fence_sha256) reject("Source writer fence changed; review a fresh transfer preview");
    if (registered) {
      const preview = await registeredSourcePreview(context!, priorFence, opened.store, opened.provider,
        head, identity.store_identity, goal, operation);
      if (value.registered_source_preview_sha256 !== preview.preview_sha256) reject("Reviewed transfer source preview changed; review the current source without executing it");
    }
    const protectedInputs = protectedExport ? await reviewProtectedGoalTransferInputs(value.protected_transport, context!, value.source_package_version, head.head) : undefined;
    await mkdir(directory, {recursive: true, mode: 0o700});
    await retain(pendingPath, preparation);
    await preparePlannedTransferWriterFenceUnderLock(root, goal, priorFence, sourcePreparationFence(goal, operation, preparation));
    // Export reuses the native retained-journal owner and its independent audit.
    // The fence stays closed on every error, including interrupted publication.
    const archive = join(directory, "authority.ndjson");
    let captured: AuthorityArchiveSummary;
    try { captured = await verifyAuthorityArchive(archive); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      captured = await exportAuthorityArchive(opened.store, goal, archive);
    }
    if (captured.goal_id !== goal || captured.source_store_identity !== identity.store_identity ||
        captured.source_provider_revision !== head.provider_revision || captured.commits !== head.cursor ||
        captured.projection_sha256 !== facts.source_projection_sha256) reject("Transfer archive differs from its prepared source");
    const result: JsonObject = {schema_version: PREPARATION_SCHEMA, operation_id: operation, goal_id: goal,
      ...(registered ? {source_host_identity: facts.source_host_identity} : {}),
      authority_write_fenced: true, authority_archive: {...captured}, archive,
      runtime_history: runtimeHistory,
      ...(context ? {source_context_sha256: canonicalAuthoritySha256(context)} : {}),
      ...(registered ? {original_registration_verified: true} : {}),
      host_drain_qualified: false, portable_artifact_qualified: false, execution_authority_granted: false};
    await retain(join(directory, "receipt.json"), result);
    return protectedInputs ? {...result, protected_artifact: await writeProtectedGoalTransfer(protectedInputs,
      result, context!, join(directory, "encrypted-export-key.json"))} : result;
    };
    return contextBound ? withVerifiedGoalTransferContext(value.source_context, value.registry_witness, goal, root, prepare) : prepare();
  });
}

function sourcePreparationFence(goal: string, operation: string, pending: JsonObject): PlannedTransferWriterFence {
  return {schema_version: PLANNED_TRANSFER_WRITER_FENCE_SCHEMA, state: "engaged", goal_id: goal,
    role: "source", fence_id: `planned-transfer:${operation}`, operation_id: operation,
    source_identity_sha256: canonicalAuthoritySha256(pending)};
}

async function requireUncancelledPreparation(directory: string): Promise<void> {
  if (await optionalJson(join(directory, "cancelled.json"))) reject("This source preparation was cancelled; review a new transfer operation");
}

/** Abandon only a qualified, unchanged local preparation. There is no cutover
 * grant in this protocol. Future cutover must be mutually exclusive with this
 * decision under the same maintenance owner; a preparation receipt alone must
 * never authorize a destination.
 *
 * Retain the cancellation BEFORE restoring the original fence. Cold recovery
 * may finish that exact CAS, but a historical retry never rewinds later source
 * work, a different transfer, registry metadata, receipts or provider history.
 */
export async function cancelSourcePreparation(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "operation_id", "execute"]) || value.execute !== true) {
    reject("Source preparation cancellation requires its original address and explicit execution");
  }
  const root = requireLocalAuthorityRuntimeRoot(value.runtime_root);
  const goal = requireAuthorityStoreId(value.goal_id, "source Goal id"), operation = requireAuthorityStoreId(value.operation_id, "transfer operation id");
  if (!(await stat(root)).isDirectory()) reject("Original source runtime is unavailable; cancellation cannot recreate it");
  const directory = join(shadowManagementDirectory(root, goal), "planned-transfers", canonicalAuthoritySha256(operation));
  return withShadowMaintenanceLock(root, goal, async () => {
    if (await optionalJson(join(directory, "cutover.json"))) reject("Source cutover is final; cancellation cannot reopen the old source");
    const pending = await optionalJson(join(directory, "preparation.json"));
    if (!pending || pending.schema_version !== PREPARATION_SCHEMA || !hasExactAuthorityKeys(pending,
        ["schema_version", "request", "source_provider", "source_store_identity", "source_cursor", "source_projection_sha256",
          "source_turn_history", "source_quota_history", "source_context", "source_context_sha256", "original_writer_fence",
          ...(pending.source_host_identity === undefined ? [] : ["source_host_identity"]),
          ...(pending.source_validation_declarations === undefined ? [] : ["source_validation_declarations"])])) {
      reject("Cancellation requires the retained original registered source preparation");
    }
    const request = requireJsonObject(pending.request, "original preparation request");
    const context = requireJsonObject(pending.source_context, "original registered source context");
    const prior = requireJsonObject(pending.original_writer_fence, "original source fence");
    if (pending.source_host_identity !== undefined && pending.source_host_identity !== await observeLocalWorkspaceHostIdentity()) {
      reject("Prepared source belongs to another OS installation; cancellation cannot adopt that source");
    }
    if (!hasExactAuthorityKeys(request, ["goal_id", "operation_id", "expected_provider_revision", "expected_writer_fence_sha256"]) ||
        request.goal_id !== goal || request.operation_id !== operation || context.runtime_root !== root ||
        pending.source_context_sha256 !== canonicalAuthoritySha256(context) || request.expected_writer_fence_sha256 !== canonicalAuthoritySha256(prior)) {
      reject("Cancellation source identity differs from its original preparation");
    }
    const receipt = await optionalJson(join(directory, "receipt.json"));
    if (receipt && (!hasExactAuthorityKeys(receipt, ["schema_version", "operation_id", "goal_id", "authority_write_fenced",
        "authority_archive", "archive", "runtime_history", "source_context_sha256", "original_registration_verified",
        "host_drain_qualified", "portable_artifact_qualified", "execution_authority_granted",
        ...(pending.source_host_identity === undefined ? [] : ["source_host_identity"])]) ||
        receipt.schema_version !== PREPARATION_SCHEMA || receipt.operation_id !== operation || receipt.goal_id !== goal ||
        receipt.authority_write_fenced !== true || receipt.original_registration_verified !== true ||
        receipt.source_context_sha256 !== pending.source_context_sha256 || receipt.host_drain_qualified !== false ||
        receipt.portable_artifact_qualified !== false || receipt.execution_authority_granted !== false ||
        receipt.source_host_identity !== pending.source_host_identity)) {
      reject("Cancellation requires a registered preparation-only receipt; preserve any later transfer phase");
    }
    if (receipt) {
      const archive = requireJsonObject(receipt.authority_archive, "original preparation archive identity");
      if (!hasExactAuthorityKeys(archive, ["schema_version", "goal_id", "source_provider", "source_store_identity",
          "source_provider_revision", "commits", "projection_sha256", "archive_sha256"]) ||
          archive.schema_version !== AUTHORITY_ARCHIVE_SCHEMA || archive.goal_id !== goal || archive.source_provider !== pending.source_provider ||
          archive.source_store_identity !== pending.source_store_identity || archive.source_provider_revision !== request.expected_provider_revision ||
          archive.commits !== pending.source_cursor || archive.projection_sha256 !== pending.source_projection_sha256 ||
          !BARE_SHA256_PATTERN.test(String(archive.archive_sha256)) || receipt.archive !== join(directory, "authority.ndjson") ||
          !same(receipt.runtime_history, goalTransferHistory(root, goal,
            requireJsonObject(pending.source_turn_history, "original Turn history"), requireJsonObject(pending.source_quota_history, "original quota history"), pending.source_validation_declarations as JsonObject[] | undefined))) {
        reject("Preparation receipt differs from its original source identity; preserve the original operation");
      }
    }
    // Missing receipt is an interrupted preparation, not a completed move.
    // Its native pending identity and current source fence still have to match.
    const decision = {schema_version: CANCELLATION_SCHEMA, goal_id: goal, operation_id: operation,
      source_preparation_sha256: canonicalAuthoritySha256(pending),
      source_receipt_sha256: receipt ? canonicalAuthoritySha256(receipt) : null,
      original_writer_fence_sha256: canonicalAuthoritySha256(prior), preparation_cancelled: true, execution_authority_granted: false};
    const retained = await optionalJson(join(directory, "cancelled.json"));
    if (retained && !same(retained, decision)) reject("Original source cancellation decision changed; preserve its original operation");
    const prepared = sourcePreparationFence(goal, operation, pending);
    const current = await loadLegacyCoordinationWriterFence(root, goal);
    const readback = async () => {
      const observed = await loadLegacyCoordinationWriterFence(root, goal);
      return {decision, readback: {writer_fence: observed,
        original_writer_fence_restored: observed.status === "loaded" && same(observed.fence, prior)}};
    };
    if (retained && (current.status !== "loaded" || !same(current.fence, prepared))) return readback();
    if (current.status !== "loaded" || !same(current.fence, prepared)) reject("Source is not owned by this prepared operation; cancellation cannot replace its writer fence");
    const snapshot = await readGoalTransferSnapshot(String(context.registry_path), goal);
    return withVerifiedGoalTransferContext(context, snapshot.registry_witness, goal, root, async () => {
      const managed = await readShadowManagementState(root, goal);
      if (managed && managed.status !== "inactive") reject("Source capture requires disposition before preparation cancellation");
      const opened = await openLocalAuthorityStoreHandle(root, goal, {}, {existingOnly: true});
      const head = await opened.store.loadAuthority(), identity = await opened.store.storeIdentity();
      if ((opened.provider !== "file" && opened.provider !== "sqlite") || opened.provider !== pending.source_provider ||
          head.status !== "loaded" || identity.status !== "available" || identity.store_identity !== pending.source_store_identity ||
          head.provider_revision !== request.expected_provider_revision || head.cursor !== pending.source_cursor ||
          canonicalAuthoritySha256(head.head) !== pending.source_projection_sha256) reject("Prepared source authority changed; cancellation must not restore over later work");
      await requireRegisteredSourceOrigin(context, prior, opened.store, goal);
      const turns = await readGoalTurnJournalsForTransfer(root, goal), originalTurns = requireJsonObject(pending.source_turn_history, "original Turn history");
      if (!same(turns, {turns: originalTurns.turns, files: originalTurns.files, closed: originalTurns.closed}) ||
          !same(await readGoalQuotaHistoryForTransfer(root, goal), pending.source_quota_history)) reject("Prepared source settlement history changed; recover its original owner before cancellation");
      await retain(join(directory, "cancelled.json"), decision);
      await restorePlannedTransferWriterFenceUnderLock(root, goal, prepared, prior);
      return readback();
    });
  });
}
