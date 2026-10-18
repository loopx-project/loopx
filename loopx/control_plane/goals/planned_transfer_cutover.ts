/** Source-online cutover between two local registered environments.
 *
 * The original source is read through its native owner, not a copied receipt.
 * Its final decision and cancellation share maintenance. The destination Goal
 * owner admits Tasks/accounting, or a separately reviewed new local runtime.
 * Neither profile copies a session or credential. This is not cross-machine
 * authorization or qualification of the complete installed user journey.
 */
import {basename, dirname, isAbsolute, join, relative, resolve, sep} from "node:path";
import {readdir, stat} from "node:fs/promises";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {durableWriteJson} from "../effect_runtime_io.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys, requireAuthorityStoreId} from "../coordination/authority_store_codec.ts";
import {visitRegisteredTransferSource} from "../coordination/planned_transfer_authority.ts";
import {readStagedGoalTransferRuns} from "../coordination/planned_transfer_target.ts";
import {auditAuthorityArchive} from "../coordination/authority_archive_audit.ts";
import {openLocalAuthorityStoreHandle, requireLocalAuthorityRuntimeRoot} from "../coordination/local_authority_provider.ts";
import {indexCoordinationProjection} from "../coordination/coordination_projection.ts";
import {loadLegacyCoordinationWriterFence, admitPlannedTransferTaskWriterUnderLock,
  PLANNED_TRANSFER_WRITER_FENCE_SCHEMA, PLANNED_TRANSFER_TASK_FENCE_SCHEMA,
  PLANNED_TRANSFER_RUNTIME_FENCE_SCHEMA, isPlannedTransferAdmissionFence} from "../coordination/legacy_writer_fence.ts";
import {shadowManagementDirectory, withShadowMaintenanceLock} from "../coordination/shadow_management.ts";
import {readGoalTransferHistoryJson, readTransferValidationDeclarations} from "./planned_transfer_history.ts";
import {requirePhysicalTransferPath, inspectTransferFile} from "./planned_transfer_container.ts";
import {decodePlannedTransferMaterial, readGoalTransferSnapshot, withVerifiedGoalTransferContext} from "./planned_transfer.ts";
import {localTransferGoalConfiguration, localTransferGoalDocuments, localTransferMappedGoalDocuments,
  requireLocalTransferTaskBindings, planReviewedTransferContinuation, readReviewedTransferContext, planTransferOriginalWork} from "./planned_transfer_continuation.ts";
import {planGoalTransferWorkspace, prepareGoalTransferExecutionWorkspace,
  readGoalTransferExecutionWorkspace} from "./planned_transfer_workspace.ts";
import {planGoalTransferRepository, goalTransferMaterialSource, type TransferRepository} from "./planned_transfer_repository.ts";
import {observeLocalWorkspaceHostIdentity} from "../work_items/task_lease_workspace.ts";
import {reviewGoalTransferMaterialInputs} from "./planned_transfer_payload.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";

const CUTOVER_SCHEMA = "loopx_local_goal_transfer_cutover_v0";
const ADMISSION_SCHEMA = "loopx_local_goal_transfer_task_admission_v0";
const RUNTIME_ADMISSION_SCHEMA = "loopx_local_goal_transfer_runtime_admission_v0";
const REVIEW_SCHEMA = "loopx_local_goal_transfer_review_v0";
function confirmation(decision: JsonObject, digest: unknown): JsonObject {
  return {operation_id: decision.operation_id, preview_sha256: digest,
    destination_name: basename(dirname(String(requireJsonObject(decision.target, "original destination").project))),
    ...(requireJsonObject(decision.continuation, "continuation").continue_work ? {continue_work: requireJsonObject(decision.continuation, "continuation").continue_work} : {})};
}
function reject(message: string): never {throw new EffectRuntimeConflictError(message);}
function same(a: unknown, b: unknown): boolean {return canonicalAuthorityBytes(a).equals(canonicalAuthorityBytes(b));}
function operationDirectory(root: string, goal: string, operation: string, source: boolean): string {
  return join(shadowManagementDirectory(root, goal), source ? "planned-transfers" : "planned-transfer-targets", canonicalAuthoritySha256(operation));
}
async function optional(path: string): Promise<JsonObject | null> {
  try {return await readGoalTransferHistoryJson(path);}
  catch (error) {if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error;}
}
async function retain(path: string, record: JsonObject): Promise<void> {
  const prior = await optional(path);
  if (prior && !same(prior, record)) reject("Original cutover identity changed; preserve the original operation");
  if (!prior) await durableWriteJson(path, record);
  if (!same(await optional(path), record)) reject("Cutover decision readback failed; retry the original operation");
}
async function locationIdentity(root: string): Promise<string> {
  const entry = await stat(await requirePhysicalTransferPath(root));
  if (!entry.isDirectory()) reject("Local target runtime must remain its original physical directory");
  return canonicalAuthoritySha256({device: entry.dev, inode: entry.ino});
}

/** One Agent-facing preparation composes existing owners. Paths and provider
 * come from this Goal owner; the Python caller only performs their codec IO.
 * No credentials, native sessions or optional capability settings are adopted. */
export async function planLocalGoalTransferPreparation(value: JsonObject): Promise<JsonObject> {
  const rawMaterials = value.materials;
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "operation_id", "source_context", "registry_witness", "destination_root", "materials", ...(Object.hasOwn(value, "continue_todo_id") ? ["continue_todo_id"] : []),
      ...(Object.hasOwn(value, "continue_result_file") ? ["continue_result_file"] : []),
      ...(Object.hasOwn(value, "continue_with_host_service") ? ["continue_with_host_service"] : [])]) ||
      !Array.isArray(rawMaterials) || process.platform === "win32") reject("Local preparation requires its explicit original Goal, destination and reviewed materials");
  return visitRegisteredTransferSource(value, requireAuthorityStoreId(value.operation_id, "original operation"), async source => {
    if (source.preview.ready_for_authority_preparation !== true) reject("Original work is not settled; use its existing Task/Turn owners before preparing this move");
    const destination = await requirePhysicalTransferPath(value.destination_root);
    const goal = requireJsonObject(source.context.goal, "original Goal");
    localTransferGoalConfiguration(goal);
    requireLocalTransferTaskBindings(goal, source.projection);
    const work = await planTransferOriginalWork(goal, source.projection, value.continue_todo_id, String(source.context.runtime_root), value.continue_result_file, value.continue_with_host_service);
    const overlaps = (a: string, b: string) => {
      const suffix = relative(a, b);
      return suffix === "" || (!isAbsolute(suffix) && suffix !== ".." && !suffix.startsWith(`..${sep}`));
    };
    if ([String(goal.repo), String(source.context.runtime_root)].some(path => overlaps(path, destination) || overlaps(destination, path))) {
      reject("Local destination must not overlap the original project or runtime");
    }
    let entries: string[] = [];
    try {entries = await readdir(destination);} catch (error) {if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;}
    if (entries.some(name => !["recipient", "portable", "workspace", "runtime", "agent-workspace"].includes(name))) reject("Local destination is occupied; preserve it and choose another directory");
    const materials: JsonObject[] = [];
    for (const path of rawMaterials) {
      const physical = await requirePhysicalTransferPath(path);
      materials.push({path: physical, sha256: (await inspectTransferFile(physical)).sha256});
    }
    for (const document of localTransferGoalDocuments(goal)) {
      const physical = await requirePhysicalTransferPath(resolve(String(goal.repo), String(document.path)));
      if (!materials.some(row => row.path === physical)) materials.push({path: physical, sha256: (await inspectTransferFile(physical)).sha256});
    }
    const repository = await planGoalTransferRepository(goal, source.projection,
      materials.map(row => String(row.path)));
    if (repository && entries.includes("agent-workspace") && source.preparedOperation !== value.operation_id) reject("Destination execution workspace is occupied; preserve the source and destination");
    for (const file of repository?.files ?? []) {
      const physical = await requirePhysicalTransferPath(file.source_path);
      if (!materials.some(row => row.path === physical)) materials.push({path: physical, sha256: (await inspectTransferFile(physical)).sha256});
    }
    const reviewed = await reviewGoalTransferMaterialInputs(materials, source.context, source.projection);
    // Discover reserved/colliding destinations before any source fence or key
    // directory is published, using the same workspace placement owner.
    planGoalTransferWorkspace(join(destination, "workspace"), join(destination, "runtime"),
      reviewed.materials.map((row, index) => ({...decodePlannedTransferMaterial(row, index), path: String(materials[index]!.path)})), null);
    return {source_preview_sha256: source.preview.preview_sha256, provider: source.preview.provider, materials, repository,
      ...(work ? {continue_todo_id: work.todo_id} : {}),
      ...(work?.result ? {continue_result_file: requireJsonObject(work.result, "original result").relative_path} : {}),
      ...(work?.continue_with_host_service === true ? {continue_with_host_service: true} : {}),
      project: join(destination, "workspace"), runtime_root: join(destination, "runtime"),
      recipient_directory: join(destination, "recipient"), artifact_directory: join(destination, "portable")};
  });
}

/** A retained review is only an address and an exact reviewed digest. It lets
 * the original Settings entry discover a prepared destination without accepting
 * a browser-supplied filesystem route. Every confirmation rechecks real owners. */
export async function readLocalGoalTransferCutoverReview(root: string, goal: string, operation: string): Promise<JsonObject | null> {
  const review = await optional(join(operationDirectory(root, goal, operation, true), "cutover-review.json"));
  if (review && (!hasExactAuthorityKeys(review, ["schema_version", "goal_id", "operation_id", "target_registry_path", "preview_sha256", "destination_name", ...(Object.hasOwn(review, "continue_todo_id") ? ["continue_todo_id"] : []),
      ...(Object.hasOwn(review, "continue_result_file") ? ["continue_result_file"] : []),
      ...(Object.hasOwn(review, "continue_with_host_service") ? ["continue_with_host_service"] : [])]) ||
      review.schema_version !== REVIEW_SCHEMA || review.goal_id !== goal || review.operation_id !== operation ||
      !BARE_SHA256_PATTERN.test(String(review.preview_sha256)) || typeof review.destination_name !== "string")) reject("Original local cutover review is unavailable; preserve its operation");
  return review;
}

export async function readRegisteredLocalCutoverReview(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "operation_id", "source_context", "registry_witness"])) reject("Read the original registered cutover review");
  return visitRegisteredTransferSource(value, requireAuthorityStoreId(value.operation_id, "original operation"), async source => {
    const review = await readLocalGoalTransferCutoverReview(String(source.context.runtime_root), String(value.goal_id), String(value.operation_id));
    if (!review || source.preparedOperation !== value.operation_id) reject("Original local cutover has no retained review");
    return review;
  });
}

export async function retainLocalGoalTransferCutoverReview(value: JsonObject): Promise<JsonObject> {
  const {execution_repository: repository, ...request} = value;
  if (repository != null) {
    const before = await planLocalGoalTransferCutover(request);
    if (before.source_final === true) reject("Original cutover is final; do not recreate its execution workspace");
    const decision = requireJsonObject(before.decision, "original cutover"), target = requireJsonObject(decision.target, "staged target");
    const source = requireJsonObject(request.source, "original source");
    const context = requireJsonObject(requireJsonObject(request.target_snapshot, "target snapshot").source_context, "target context");
    const directory = operationDirectory(String(target.runtime_root), String(decision.goal_id), String(decision.operation_id), false);
    await visitRegisteredTransferSource(source, String(decision.operation_id), current =>
      withShadowMaintenanceLock(String(target.runtime_root), String(decision.goal_id), async () => {
      const staged = await readGoalTransferHistoryJson(join(directory, "preparation.json"));
      await prepareGoalTransferExecutionWorkspace(directory,
        requireJsonObject(requireJsonObject(source.source_context, "original context").goal, "original Goal"),
        requireJsonObject(repository, "reviewed source repository") as TransferRepository, requireJsonObject(staged.protected_transport, "staged materials"),
        context, requireJsonObject(requireJsonObject(request.target_snapshot, "target snapshot").registry_witness, "target witness"), current.projection);
    }));
  }
  const plan = await planLocalGoalTransferCutover(request);
  const decision = requireJsonObject(plan.decision, "reviewed original cutover"), target = requireJsonObject(decision.target, "reviewed destination");
  if (decision.profile !== "source_online_local_runtime_v0") reject("Product confirmation requires reviewed original-work continuation");
  const source = requireJsonObject(value.source, "original source");
  return visitRegisteredTransferSource(source, String(decision.operation_id), async current => {
    if (current.preparedOperation !== decision.operation_id) reject("Original source is no longer prepared for this move");
    const review: JsonObject = {schema_version: REVIEW_SCHEMA, goal_id: decision.goal_id, operation_id: decision.operation_id,
      target_registry_path: target.registry_path, preview_sha256: plan.preview_sha256,
      destination_name: basename(dirname(String(target.project))),
      ...(value.continue_todo_id ? {continue_todo_id: value.continue_todo_id} : {}),
      ...(value.continue_result_file ? {continue_result_file: value.continue_result_file} : {})};
    if (value.continue_with_host_service === true) review.continue_with_host_service = true;
    await retain(join(operationDirectory(String(source.runtime_root), String(decision.goal_id), String(decision.operation_id), true), "cutover-review.json"), review);
    return {operation_id: review.operation_id, preview_sha256: review.preview_sha256, destination_name: review.destination_name};
  });
}

/** A positive, explicitly narrow owner profile, not absence inferred from an
 * empty backup. Unknown Goal/adapter/config fields require their actual owner.
 * Source sessions and credentials remain historical; none is auto-installed.
 */
function localAdmittedGoal(original: JsonObject, staged: JsonObject, continuation: JsonObject | null, materials: JsonObject): JsonObject {
  return {...staged, ...localTransferGoalConfiguration(original),
    authority_sources: [...localTransferMappedGoalDocuments(original, materials), ...(staged.authority_sources as JsonObject[])],
    activation: {schema_version: "loopx_goal_activation_v1", state: "active", reason: continuation ? "Reviewed original work continues in this new local runtime" : "Native local Task admission; Host continuation remains unqualified"},
    planned_transfer: {...requireJsonObject(staged.planned_transfer, "staged transfer"),
      state: continuation ? "runtime_admitted" : "task_admitted",
      identity_continuity: continuation ? "same_goal_new_runtime" : "same_goal_task_continuation",
      fresh_admission_required: true, task_write_admitted: true, ...(continuation ? {runtime_admitted: true} : {})}};
}

/** The registry codec calls this under its original transaction. This is only
 * the exact native proposal, while the runtime fence is still closed. */
export async function localTransferRegistryPublication(root: string, goal: string,
  operation: string, registry: string, proposed: JsonObject): Promise<boolean> {
  const intent = await optional(join(operationDirectory(root, goal, operation, false), "admission.json"));
  if (!intent || ![ADMISSION_SCHEMA, RUNTIME_ADMISSION_SCHEMA].includes(String(intent.schema_version)) || intent.target_registry_path !== registry ||
      !same(intent.goal, proposed)) return false;
  const decision = requireJsonObject(intent.decision, "native source cutover");
  return decision.schema_version === CUTOVER_SCHEMA && decision.goal_id === goal && decision.operation_id === operation &&
    same(await optional(join(operationDirectory(String(decision.source_runtime_root), goal, operation, true), "cutover.json")), decision);
}

/** Read-only admission from the existing Goal-owned cutover receipt.
 * It proves this physical runtime's retained lineage, not a copied grant. */
export async function requireLocalGoalTransferAdmission(root: string, goal: string, fence: JsonObject): Promise<JsonObject> {
  if (!isPlannedTransferAdmissionFence(fence)) reject("Target admission fence is required");
  const directory = operationDirectory(root, goal, String(fence.operation_id), false);
  const receipt = await readGoalTransferHistoryJson(join(directory, "admission-receipt.json"));
  const decision = requireJsonObject(receipt.decision, "admitted original cutover");
  const target = requireJsonObject(decision.target, "admitted destination");
  const intent = await readGoalTransferHistoryJson(join(directory, "admission.json"));
  const runtime = fence.schema_version === PLANNED_TRANSFER_RUNTIME_FENCE_SCHEMA;
  if (receipt.schema_version !== (runtime ? RUNTIME_ADMISSION_SCHEMA : ADMISSION_SCHEMA) ||
      canonicalAuthoritySha256(receipt) !== fence[runtime ? "runtime_admission_sha256" : "task_admission_sha256"] ||
      !same(intent, receipt) || decision.goal_id !== goal || decision.operation_id !== fence.operation_id ||
      target.runtime_root !== root || target.identity_sha256 !== fence.source_identity_sha256 ||
      target.store_identity !== fence.store_identity) reject("Original target admission changed; recover its original operation");
  if (target.host_identity !== await observeLocalWorkspaceHostIdentity() || target.runtime_identity !== await locationIdentity(root)) {
    reject("Target admission belongs to another host or recreated runtime; use its Goal lifecycle owner");
  }
  const opened = await openLocalAuthorityStoreHandle(root, goal, {}, {existingOnly: true});
  const identity = await opened.store.storeIdentity();
  if (opened.provider !== target.provider || identity.status !== "available" || identity.store_identity !== fence.store_identity) {
    reject("Admitted target provider lineage changed; preserve later state");
  }
  return receipt;
}

/** Path-free initiating-entry readback from the current native destination.
 * Missing/disconnected state is an error, never the old source's cached result.
 */
export async function readLocalGoalTransferContinuation(decision: JsonObject): Promise<JsonObject> {
  const goal = requireAuthorityStoreId(decision.goal_id, "original Goal");
  const target = requireJsonObject(decision.target, "recorded cutover destination");
  const root = await requirePhysicalTransferPath(target.runtime_root);
  // A read must not recreate a disconnected runtime just to acquire its lock.
  // Provider/codec snapshots and exact before/after witnesses own this view;
  // a changing observation refuses instead of holding a mutation lock.
  const physical = await locationIdentity(root);
  const fence = await loadLegacyCoordinationWriterFence(root, goal);
    if (fence.status !== "loaded" || fence.fence.operation_id !== decision.operation_id ||
        fence.fence.source_identity_sha256 !== target.identity_sha256) reject("Original destination continuation binding is unavailable");
    if (fence.fence.schema_version === PLANNED_TRANSFER_WRITER_FENCE_SCHEMA && fence.fence.role === "target") {
      if (!same(fence, await loadLegacyCoordinationWriterFence(root, goal)) || physical !== await locationIdentity(root)) {
        reject("Original destination changed during readback");
      }
      return {target_task_admitted: false, target_runtime_admitted: false, work: null};
    }
    const admission = await requireLocalGoalTransferAdmission(root, goal, fence.fence);
    if (!same(admission.decision, decision)) reject("Original destination admission differs from source-final");
    const snapshot = fence.fence.schema_version === PLANNED_TRANSFER_RUNTIME_FENCE_SCHEMA
      ? (await readReviewedTransferContext(admission, "readback")).snapshot
      : await readGoalTransferSnapshot(String(target.registry_path), goal);
      const opened = await openLocalAuthorityStoreHandle(root, goal, {}, {existingOnly: true});
      const head = await opened.store.loadAuthority();
      if (head.status !== "loaded") reject("Original destination work is unavailable");
      const index = indexCoordinationProjection(head.head, goal);
      const context = requireJsonObject(snapshot.source_context, "current target Goal");
      const currentGoal = requireJsonObject(context.goal, "current Goal");
      const admittedGoal = requireJsonObject(admission.goal, "admitted Task Goal");
      if (snapshot.registry_profile !== null || snapshot.registry_role === "global-local" || context.runtime_root !== root ||
          currentGoal.goal_instance_id !== undefined || currentGoal.id !== goal ||
          ["repo", "state_file", "planned_transfer"].some(key => !same(currentGoal[key], admittedGoal[key]))) {
        reject("Current target Goal requires its owning lifecycle; do not mix its original Task lineage");
      }
      const coordination = requireJsonObject(currentGoal.coordination, "current Agent registration");
      const after = await opened.store.loadAuthority();
      if (after.status !== "loaded" || after.provider_revision !== head.provider_revision ||
          !same(fence, await loadLegacyCoordinationWriterFence(root, goal)) || physical !== await locationIdentity(root) ||
          !same(snapshot, await readGoalTransferSnapshot(String(target.registry_path), goal))) {
        reject("Original destination changed during readback; retry its current state");
      }
      return {target_task_admitted: true, target_runtime_admitted: fence.fence.schema_version === PLANNED_TRANSFER_RUNTIME_FENCE_SCHEMA,
        target_activation_state: snapshot.activation_state,
        ...(fence.fence.schema_version === PLANNED_TRANSFER_RUNTIME_FENCE_SCHEMA ? {_local_activation_target: {
          registry_path: target.registry_path, runtime_root: root, goal_id: goal, operation_id: decision.operation_id,
        }} : {}),
        work: {objective: snapshot.state_objective, requirements: context.state_text,
        agents: coordination.registered_agents, todos: index.todo_ids.map(id => index.todos.get(id)!)}};
}

/** Plan and retain one final local source decision. There is no confirmation
 * bypass: execution compares the full current proposal with the reviewed hash.
 * Source->target lock order is safe for this profile: a stopped target cannot
 * itself qualify as an original registered transfer source.
 */
export async function planLocalGoalTransferCutover(value: JsonObject): Promise<JsonObject> {
  const execute = value.execute === true;
  if (!hasExactAuthorityKeys(value, ["source", "target_snapshot", "target_registry",
      ...(Object.hasOwn(value, "continuation") ? ["continuation"] : []),
      ...(Object.hasOwn(value, "continue_todo_id") ? ["continue_todo_id"] : []),
      ...(Object.hasOwn(value, "continue_result_file") ? ["continue_result_file"] : []),
      ...(Object.hasOwn(value, "continue_with_host_service") ? ["continue_with_host_service"] : []),
      ...(Object.hasOwn(value, "reviewed_preview_sha256") ? ["reviewed_preview_sha256"] : []),
      ...(execute ? ["execute", "expected_preview_sha256"] : [])])) reject("Local cutover requires complete current source and target observations");
  const source = requireJsonObject(value.source, "original source request");
  if (!hasExactAuthorityKeys(source, ["runtime_root", "goal_id", "operation_id", "source_context", "registry_witness"])) reject("Local cutover requires its original registered source");
  const root = requireLocalAuthorityRuntimeRoot(source.runtime_root), goal = requireAuthorityStoreId(source.goal_id, "original Goal");
  const operation = requireAuthorityStoreId(source.operation_id, "original transfer operation");
  const sourceDirectory = operationDirectory(root, goal, operation, true);
  const snapshot = requireJsonObject(value.target_snapshot, "current target codec observation");
  const context = requireJsonObject(snapshot.source_context, "current destination context");
  const targetRoot = await requirePhysicalTransferPath(context.runtime_root);
  await locationIdentity(targetRoot);
  const targetGoal = requireJsonObject(context.goal, "current destination Goal");
  const registryPath = String(context.registry_path), registry = requireJsonObject(value.target_registry, "current destination registry");
  if (root === targetRoot || targetGoal.id !== goal || registry.common_runtime_root !== targetRoot ||
      !Array.isArray(registry.goals) || registry.goals.filter(raw => requireJsonObject(raw, "Goal").id === goal).length !== 1 ||
      !same(registry.goals.find(raw => requireJsonObject(raw, "Goal").id === goal), targetGoal)) reject("Destination registration differs from its codec observation");
  return visitRegisteredTransferSource(source, operation, async current => {
    if (current.preparedOperation !== operation || current.preview.ready_for_authority_preparation !== true) reject("Original source is not prepared and settled for this cutover");
    return withShadowMaintenanceLock(targetRoot, goal, () => withVerifiedGoalTransferContext(
      context, snapshot.registry_witness, goal, targetRoot, async () => {
        const directory = operationDirectory(targetRoot, goal, operation, false);
        const priorDecision = await optional(join(sourceDirectory, "cutover.json"));
        const intent = await optional(join(directory, "admission.json"));
        // A recorded final decision is immutable. Retry finishes only its exact
        // remaining stage; after admission, never replace later Goal/state/work.
        if (priorDecision) {
          const target = requireJsonObject(priorDecision.target, "recorded destination");
          if (priorDecision.schema_version !== CUTOVER_SCHEMA || target.runtime_root !== targetRoot || target.registry_path !== registryPath ||
              priorDecision.goal_id !== goal || priorDecision.operation_id !== operation || !intent || !same(intent.decision, priorDecision)) {
            reject("Source already chose another destination; recover its original cutover");
          }
          const priorContinuation = priorDecision.continuation as JsonObject | undefined;
          if ((value.continue_todo_id ?? null) !== ((priorContinuation?.continue_work as JsonObject | undefined)?.todo_id ?? null)) {
            reject("Original continuation Task changed; preserve the reviewed operation");
          }
          const priorResult = (priorContinuation?.continue_work as JsonObject | undefined)?.result as JsonObject | undefined;
          if ((value.continue_result_file ?? null) !== (priorResult?.relative_path ?? null)) reject("Original continuation result changed; preserve the reviewed operation");
          if ((value.continue_with_host_service ?? null) !== ((priorContinuation?.continue_work as JsonObject | undefined)?.continue_with_host_service ?? null)) reject("Original Host service continuation changed; preserve the reviewed operation");
          if ((value.continuation === "reviewed_context") !== (priorDecision.profile === "source_online_local_runtime_v0")) reject("Original cutover continuation profile changed");
          if (Object.hasOwn(value, "reviewed_preview_sha256") && value.reviewed_preview_sha256 !== canonicalAuthoritySha256(priorDecision)) reject("Original product cutover review changed");
          if (execute && value.expected_preview_sha256 !== canonicalAuthoritySha256(priorDecision)) reject("Reviewed cutover identity changed");
          const fence = await loadLegacyCoordinationWriterFence(targetRoot, goal);
          const admitted = fence.status === "loaded" && isPlannedTransferAdmissionFence(fence.fence);
          if (admitted) await requireLocalGoalTransferAdmission(targetRoot, goal, fence.fence);
          if (!admitted && !same(targetGoal, intent.staged_goal) && !same(targetGoal, intent.goal)) reject("Destination changed after final cutover; preserve it for recovery");
          return {decision: priorDecision, preview_sha256: canonicalAuthoritySha256(priorDecision),
            ...(priorDecision.continuation ? {confirmation: confirmation(priorDecision, canonicalAuthoritySha256(priorDecision))} : {}),
            source_final: true, target_task_admitted: admitted,
            target_runtime_admitted: admitted && priorDecision.profile === "source_online_local_runtime_v0", publish_registry: !admitted && same(targetGoal, intent.staged_goal),
            destination_registry: admitted ? registry : {...registry, goals: (registry.goals as JsonObject[]).map(row => row.id === goal ? intent.goal : row)}};
        }
        const pending = await readGoalTransferHistoryJson(join(sourceDirectory, "preparation.json"));
        const sourceReceipt = await readGoalTransferHistoryJson(join(sourceDirectory, "receipt.json"));
        const request = requireJsonObject(pending.request, "original source request");
        if (pending.source_projection_sha256 !== canonicalAuthoritySha256(current.projection) ||
            request.expected_provider_revision !== current.preview.provider_revision || pending.source_context_sha256 !== canonicalAuthoritySha256(current.context) ||
            pending.source_host_identity !== await observeLocalWorkspaceHostIdentity() || sourceReceipt.source_host_identity !== pending.source_host_identity) {
          reject("Prepared source changed before cutover");
        }
        const staged = await readGoalTransferHistoryJson(join(directory, "preparation.json"));
        const receipt = await readGoalTransferHistoryJson(join(directory, "receipt.json"));
        const registration = await readGoalTransferHistoryJson(join(directory, "registration.json"));
        const original = await readGoalTransferHistoryJson(join(directory, "source-preparation.json"));
        const target = requireJsonObject(staged.target, "staged target");
        if (!same(original, sourceReceipt) || !same(targetGoal, registration.goal) ||
            registration.registry_path !== registryPath || target.runtime_root !== targetRoot || target.registry_path !== registryPath ||
            receipt.runtime_history_readback_verified !== true || receipt.workspace_materials_readback_verified !== true ||
            receipt.material_readback_verified !== true || staged.source_context_sha256 !== pending.source_context_sha256 ||
            !same(sourceReceipt.authority_archive, receipt.original_authority)) reject("Destination is not the complete original protected stage");
        await readStagedGoalTransferRuns({runtime_root: targetRoot, goal_id: goal, operation_id: operation});
        const materials = requireJsonObject(staged.protected_transport, "reviewed destination materials");
        if (!Array.isArray(materials.workspace_destinations)) reject("Original workspace mapping is unavailable");
        const destinations = new Map((materials.workspace_destinations as JsonObject[]).map(row => [String(row.id), String(row.relative_path)]));
        for (const raw of materials.materials as JsonObject[]) {
          if (!destinations.has(String(raw.id))) reject("A required material has no reviewed destination");
          const path = resolve(String(target.project), destinations.get(String(raw.id))!);
          const observed = await inspectTransferFile(path);
          if (!same(observed, {sha256: raw.sha256, bytes: raw.bytes})) reject("Required destination material changed; review its original owner");
        }
        const opened = await openLocalAuthorityStoreHandle(targetRoot, goal, {}, {existingOnly: true});
        const head = await opened.store.loadAuthority(), storeIdentity = await opened.store.storeIdentity();
        if (head.status !== "loaded" || storeIdentity.status !== "available" || storeIdentity.store_identity !== receipt.target_store_identity ||
            (await auditAuthorityArchive(String(sourceReceipt.archive), opened.store,
              String(requireJsonObject(sourceReceipt.authority_archive, "original archive").archive_sha256))).status !== "matched") reject("Original target authority is missing or changed");
        if (!same(await readGoalTransferHistoryJson(resolve(String(target.project), ".loopx", "goals", goal, "TRANSFER_CONTEXT.json")), current.context) ||
            !same(await readGoalTransferHistoryJson(resolve(String(target.project), ".loopx", "goals", goal, "TRANSFER_MATERIALS.json")), materials)) {
          reject("Original destination review context changed before source-final");
        }
        const originalValidations = requireJsonObject(sourceReceipt.runtime_history, "original runtime dependencies").validation_declarations ?? [];
        if (!same(await readTransferValidationDeclarations(targetRoot, goal, head.head), originalValidations)) {
          reject("Destination independent validation declarations changed before cutover");
        }
        const originalGoal = requireJsonObject(current.context.goal, "original Goal");
        const continuation = planReviewedTransferContinuation(value.continuation, originalGoal, current.context, current.preview, materials);
        const executionWorkspace = await readGoalTransferExecutionWorkspace(directory, materials);
        if (executionWorkspace) {
          const repository = await planGoalTransferRepository(originalGoal, current.projection,
            (materials.materials as JsonObject[]).map(row => goalTransferMaterialSource(String(originalGoal.repo), requireJsonObject(executionWorkspace.repository, "original repository"), row)));
          if (!repository || !same(repository, executionWorkspace.repository)) reject("Original source repository changed after preparation; preserve its new work and use source cancellation");
          for (const row of materials.materials as JsonObject[]) {
            if (repository.files.some(file => file.material_path === row.source_relative_path)) continue;
            const path = await requirePhysicalTransferPath(goalTransferMaterialSource(String(originalGoal.repo), repository, row));
            if (!same(await inspectTransferFile(path), {sha256: row.sha256, bytes: row.bytes})) reject("Original source input changed after preparation; preserve it and use source cancellation");
          }
        }
        if (continuation && executionWorkspace) continuation.execution_workspace = executionWorkspace;
        const continueWork = await planTransferOriginalWork(originalGoal, current.projection, value.continue_todo_id, root, value.continue_result_file, value.continue_with_host_service);
        if (continueWork) {
          if (!continuation || (execute && !executionWorkspace)) reject("Automatic continuation requires its reviewed execution workspace");
          continuation.continue_work = continueWork;
        }
        const admittedGoal = localAdmittedGoal(originalGoal, targetGoal, continuation, materials);
        requireLocalTransferTaskBindings(originalGoal, current.projection);
        const decision = {schema_version: CUTOVER_SCHEMA, goal_id: goal, operation_id: operation, source_runtime_root: root,
          source_preparation_sha256: canonicalAuthoritySha256(pending), source_receipt_sha256: canonicalAuthoritySha256(sourceReceipt),
          target: {...target, identity_sha256: receipt.identity_sha256, store_identity: storeIdentity.store_identity,
            host_identity: await observeLocalWorkspaceHostIdentity(), runtime_identity: await locationIdentity(targetRoot),
            provider_revision: head.provider_revision, stage_receipt_sha256: canonicalAuthoritySha256(receipt)},
          admitted_goal: admittedGoal, history_sha256: receipt.runtime_history_sha256,
          profile: continuation ? "source_online_local_runtime_v0" : "source_online_local_task_v0",
          ...(continuation ? {continuation} : {}), native_source_writes_closed: true,
          source_sessions_adopted: false, credentials_adopted: false};
        const digest = canonicalAuthoritySha256(decision);
        if (Object.hasOwn(value, "reviewed_preview_sha256") && value.reviewed_preview_sha256 !== digest) reject("Original product cutover review changed; prepare a current destination review");
        if (execute) {
          if (value.expected_preview_sha256 !== digest) reject("Local cutover preview changed; review the current destination");
          // Intent first while target remains stopped. A crash before the source
          // decision still permits source cancellation; no target grant exists.
          const admission: JsonObject = {schema_version: continuation ? RUNTIME_ADMISSION_SCHEMA : ADMISSION_SCHEMA, decision,
            target_registry_path: registryPath, staged_goal: targetGoal, goal: admittedGoal};
          if (continueWork?.continue_with_host_service === true) {
            // This timestamp is execution evidence in the existing admission,
            // not part of the preview. Retain it across intent/source-final
            // crashes; repeating confirmation cannot replenish the window.
            const started = Date.now();
            admission.continuation_window = intent?.continuation_window ?? {started_at_ms: started,
              deadline_at_ms: started + Number(requireJsonObject(continueWork.continuation_budget, "reviewed budget").timeout_seconds) * 1000};
          }
          await retain(join(directory, "admission.json"), admission);
          await retain(join(sourceDirectory, "cutover.json"), decision);
        }
        return {decision, preview_sha256: digest, source_final: execute, target_task_admitted: false, target_runtime_admitted: false, publish_registry: execute,
          ...(continuation ? {confirmation: confirmation(decision, digest)} : {}),
          destination_registry: {...registry, goals: (registry.goals as JsonObject[]).map(row => row.id === goal ? admittedGoal : row)}};
      }));
  });
}

/** Codec publication precedes this final Task/accounting transition. A lost response
 * after it is a readback retry, not another grant, debit or registry rewrite. */
export async function finishLocalGoalTransferCutover(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "operation_id"])) reject("Original target cutover identity is required");
  const root = await requirePhysicalTransferPath(value.runtime_root), goal = requireAuthorityStoreId(value.goal_id, "original Goal");
  const operation = requireAuthorityStoreId(value.operation_id, "original transfer operation");
  await locationIdentity(root);
  return withShadowMaintenanceLock(root, goal, async () => {
    const directory = operationDirectory(root, goal, operation, false);
    const intent = await readGoalTransferHistoryJson(join(directory, "admission.json"));
    const decision = requireJsonObject(intent.decision, "final original source decision");
    const fence = await loadLegacyCoordinationWriterFence(root, goal);
    if (fence.status === "loaded" && isPlannedTransferAdmissionFence(fence.fence)) {
      await requireLocalGoalTransferAdmission(root, goal, fence.fence);
      return {source_final: true, target_task_admitted: true,
        target_runtime_admitted: fence.fence.schema_version === PLANNED_TRANSFER_RUNTIME_FENCE_SCHEMA, replayed: true, goal_id: goal, operation_id: operation};
    }
    if (!await localTransferRegistryPublication(root, goal, operation, String(intent.target_registry_path), requireJsonObject(intent.goal, "admitted Goal"))) {
      reject("Original source has no final cutover decision; target remains stopped");
    }
    const target = requireJsonObject(decision.target, "original target");
    if (target.host_identity !== await observeLocalWorkspaceHostIdentity() || target.runtime_identity !== await locationIdentity(root)) {
      reject("Target host or physical runtime changed before admission");
    }
    if (fence.status !== "loaded" || fence.fence.schema_version !== PLANNED_TRANSFER_WRITER_FENCE_SCHEMA || fence.fence.role !== "target" ||
        fence.fence.operation_id !== operation || fence.fence.source_identity_sha256 !== target.identity_sha256) reject("Original stopped target fence changed");
    const observed = await readGoalTransferSnapshot(String(intent.target_registry_path), goal);
    return withVerifiedGoalTransferContext(observed.source_context, observed.registry_witness, goal, root, async () => {
      if (!same(requireJsonObject(observed.source_context, "target context").goal, intent.goal)) reject("Admitted target registry readback changed");
      const opened = await openLocalAuthorityStoreHandle(root, goal, {}, {existingOnly: true});
      const identity = await opened.store.storeIdentity(), head = await opened.store.loadAuthority();
      if (identity.status !== "available" || identity.store_identity !== target.store_identity ||
          head.status !== "loaded" || head.provider_revision !== target.provider_revision) reject("Target authority changed before admission; preserve it for recovery");
      await readStagedGoalTransferRuns({runtime_root: root, goal_id: goal, operation_id: operation});
      if (decision.continuation) await readReviewedTransferContext(intent);
      await retain(join(directory, "admission-receipt.json"), intent);
      await admitPlannedTransferTaskWriterUnderLock(root, goal, fence.fence, {
        schema_version: decision.continuation ? PLANNED_TRANSFER_RUNTIME_FENCE_SCHEMA : PLANNED_TRANSFER_TASK_FENCE_SCHEMA, state: "engaged", goal_id: goal,
        fence_id: `planned-transfer-task:${operation}`, operation_id: operation,
        source_identity_sha256: target.identity_sha256,
        [decision.continuation ? "runtime_admission_sha256" : "task_admission_sha256"]: canonicalAuthoritySha256(intent), store_identity: target.store_identity,
      });
      return {source_final: true, target_task_admitted: true, target_runtime_admitted: decision.continuation !== undefined, replayed: false, goal_id: goal, operation_id: operation};
    });
  });
}
