/** Reviewed continuation of original work in a newly admitted local runtime.
 *
 * The cutover receipt owns admission. This module reads its immutable context
 * and current Goal/Task owners; it creates no session, lease or second ledger.
 * Historical source bindings and receipts are context, never executable proof.
 */
import {dirname, join, relative, resolve} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys,
  requireAuthorityStoreId} from "../coordination/authority_store_codec.ts";
import {loadLegacyCoordinationWriterFence, isPlannedTransferAdmissionFence, PLANNED_TRANSFER_RUNTIME_FENCE_SCHEMA} from "../coordination/legacy_writer_fence.ts";
import {openLocalAuthorityStoreHandle, requireLocalAuthorityRuntimeRoot} from "../coordination/local_authority_provider.ts";
import {indexCoordinationProjection} from "../coordination/coordination_projection.ts";
import {readGoalTransferSnapshot, checkGoalTransferSourceContext} from "./planned_transfer.ts";
import {readGoalTransferHistoryJson, readTransferValidationDeclarations} from "./planned_transfer_history.ts";
import {repositoryMaterialMapping, type TransferRepository} from "./planned_transfer_repository.ts";
import {normalizeTodoCompletionValidationDeclaration} from "../todos/completion_validation_declaration.ts";
import {verifyGoalTransferExecutionWorkspace} from "./planned_transfer_workspace.ts";

const LOCAL_TRANSFER_CAPABILITIES = ["filesystem_read", "filesystem_write", "shell"] as const;

function reject(message: string): never {throw new EffectRuntimeConflictError(message);}
function same(a: unknown, b: unknown): boolean {return canonicalAuthorityBytes(a).equals(canonicalAuthorityBytes(b));}

/** Reuse bootstrap's primary Goal document vocabulary. Other material/provider
 * contracts need their own mapping; never omit them to obtain admission. */
export function localTransferGoalDocuments(original: JsonObject): JsonObject[] {
  if (!Array.isArray(original.authority_sources)) reject("Original Goal sources require their owning continuation profile");
  return original.authority_sources.map(raw => {
    const source = requireJsonObject(raw, "original Goal document");
    if (!hasExactAuthorityKeys(source, ["kind", "path", "role"]) || source.kind !== "goal_doc" ||
        source.role !== "primary_goal_document" || typeof source.path !== "string" || !source.path.trim()) {
      reject("Original Goal source requires its owning material mapping before preparation");
    }
    return source;
  });
}

/** The same positive configuration profile runs before preparation and again
 * at cutover. Unsupported dependencies must not strand a fenced source. */
export function localTransferGoalConfiguration(original: JsonObject): JsonObject {
  const keys = ["id", "display_name", "domain", "status", "role", "parent_goal_id", "repo", "state_file",
    "authority_sources", "adapter", "spawn_policy", "coordination", "execution_profile", "next_probe", "guards",
    "creation_operation_id", "activation"];
  if (Object.keys(original).some(key => !keys.includes(key)) || original.parent_goal_id != null) {
    reject("Source Goal configuration requires its owning continuation profile");
  }
  if (original.activation !== undefined && requireJsonObject(original.activation, "original activation").state !== "active") {
    reject("A stopped original Goal requires its lifecycle decision before target activation");
  }
  const adapter = requireJsonObject(original.adapter, "source adapter");
  const spawn = requireJsonObject(original.spawn_policy, "source spawn policy");
  const coordination = requireJsonObject(original.coordination, "source coordination");
  if (!hasExactAuthorityKeys(adapter, ["kind", "status"]) || adapter.kind !== "generic_project_goal_v0" ||
      spawn.allowed !== false ||
      Object.keys(coordination).some(key => !["write_scope", "requires_parent_approval", "registered_agents", "storage_target", "agent_model"].includes(key)) ||
      coordination.agent_model !== "peer_v1" ||
      !Array.isArray(coordination.registered_agents) || coordination.registered_agents.length !== 1 ||
      coordination.registered_agents.some(agent => typeof agent !== "string") || !Array.isArray(coordination.write_scope)) {
    reject("Local Task transfer needs its qualified single-Agent configuration; required materials, capabilities or Host bindings need their original owner");
  }
  localTransferGoalDocuments(original);
  const {storage_target: _originalCreationTarget, ...currentCoordination} = coordination;
  return {domain: original.domain, role: original.role, parent_goal_id: null,
    spawn_policy: spawn, coordination: currentCoordination,
    ...(original.execution_profile === undefined ? {} : {execution_profile: original.execution_profile}),
    ...(original.guards === undefined ? {} : {guards: original.guards})};
}

export function requireLocalTransferTaskBindings(original: JsonObject, projection: JsonObject): void {
  const coordination = requireJsonObject(original.coordination, "original coordination");
  const supported = new Set<string>(LOCAL_TRANSFER_CAPABILITIES);
  for (const todo of indexCoordinationProjection(projection, String(original.id)).todos.values()) {
    if (todo.done === true || todo.archive_state === "archive") continue;
    if ((todo.required_capabilities !== undefined && !Array.isArray(todo.required_capabilities)) ||
        (Array.isArray(todo.required_capabilities) && todo.required_capabilities.some(capability => !supported.has(String(capability)))) ||
        (todo.claimed_by && !(coordination.registered_agents as unknown[]).includes(todo.claimed_by))) {
      reject("Original work needs an unsupported capability or Agent binding; retain its owning continuation hold");
    }
  }
}

/** This opt-in composes existing Task validation and native execution owners.
 * It is local to this prepared move, never a Chat role or a copied Host grant. */
export async function planTransferOriginalWork(original: JsonObject, projection: JsonObject, id: unknown, root: string): Promise<JsonObject | null> {
  if (id === undefined) return null;
  const todoId = requireAuthorityStoreId(id, "original continuation Task");
  const task = indexCoordinationProjection(projection, String(original.id)).todos.get(todoId);
  const agents = requireJsonObject(original.coordination, "original Agent registration").registered_agents as unknown[];
  if (!task || task.done === true || task.archive_state === "archive" || task.status !== "open" ||
      task.claimed_by !== agents[0] || !Array.isArray(task.required_write_scopes) || !task.required_write_scopes.length) {
    reject("Automatic continuation requires the original open Task, registered Agent and declared write scopes");
  }
  const retained = (await readTransferValidationDeclarations(root, String(original.id), projection)).find(row => row.todo_id === todoId);
  const declaration = normalizeTodoCompletionValidationDeclaration(retained ? requireJsonObject(retained.declaration, "original independent validator") : {});
  if (!declaration.ok || !declaration.value.validation_command_argv) {
    reject("Automatic continuation requires the original Task's independent argv validation declaration");
  }
  return {todo_id: todoId, agent_id: agents[0], host: "codex-cli", sandbox: "workspace-write", validation_workspace: "reviewed_working_tree",
    validation_sha256: canonicalAuthoritySha256(declaration.value)};
}

/** Read the admitted operation and the current Task; downstream owners still
 * acquire the lease and decide the exact Turn. This projection grants nothing. */
export async function readGoalTransferExecutionPlan(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "operation_id"])) reject("Read the original transfer execution identity");
  const root = requireLocalAuthorityRuntimeRoot(value.runtime_root), goal = requireAuthorityStoreId(value.goal_id, "Goal");
  const fence = await loadLegacyCoordinationWriterFence(root, goal);
  if (fence.status !== "loaded" || fence.fence.schema_version !== PLANNED_TRANSFER_RUNTIME_FENCE_SCHEMA ||
      fence.fence.operation_id !== value.operation_id) reject("Original destination runtime is not admitted");
  const {requireLocalGoalTransferAdmission} = await import("./planned_transfer_cutover.ts");
  const admission = await requireLocalGoalTransferAdmission(root, goal, fence.fence);
  const decision = requireJsonObject(admission.decision, "original cutover");
  const continuation = requireJsonObject(decision.continuation, "original continuation");
  if (!continuation.continue_work) return {plan: null};
  const work = requireJsonObject(continuation.continue_work, "reviewed original work");
  const {snapshot, materials} = await readReviewedTransferContext(admission, "readback");
  const execution = requireJsonObject(continuation.execution_workspace, "original execution workspace");
  await verifyGoalTransferExecutionWorkspace(execution, materials, false);
  const opened = await openLocalAuthorityStoreHandle(root, goal, {}, {existingOnly: true});
  const head = await opened.store.loadAuthority();
  if (head.status !== "loaded") reject("Current original work is unavailable");
  const task = indexCoordinationProjection(head.head, goal).todos.get(String(work.todo_id));
  if (!task || task.claimed_by !== work.agent_id) reject("Original Task no longer belongs to its reviewed Agent");
  const retained = (await readTransferValidationDeclarations(root, goal, head.head)).find(row => row.todo_id === work.todo_id);
  const declaration = normalizeTodoCompletionValidationDeclaration(retained ? requireJsonObject(retained.declaration, "original independent validator") : {});
  if (!declaration.ok || !declaration.value.validation_command_argv ||
      canonicalAuthoritySha256(declaration.value) !== work.validation_sha256) reject("Original independent acceptance changed; retain its current owner");
  return {plan: {registry_path: admission.target_registry_path, runtime_root: root, goal_id: goal,
    agent_id: work.agent_id, todo_id: work.todo_id, project: execution.path,
    turn_instance_id: `transfer-${canonicalAuthoritySha256([decision.operation_id, decision.source_preparation_sha256, work.todo_id])}`,
    host: work.host, sandbox: work.sandbox, task_status: task.status,
    write_scopes: task.required_write_scopes, available_capabilities: [...LOCAL_TRANSFER_CAPABILITIES],
    validation_argv: declaration.value.validation_command_argv,
    validation_timeout_seconds: declaration.value.validation_timeout_seconds,
    activation_state: snapshot.activation_state}};
}

/** Only the explicitly reviewed original work may validate pending files.
 * This chooses its workspace, never grants a validation effect or Task lease;
 * the ordinary terminal transaction still admits and rechecks those effects. */
export async function validateGoalTransferWorkingState(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "todo_id", "workspace_path", "declaration_sha256"])) reject("Original validation requires its exact workspace and declaration");
  const root = requireLocalAuthorityRuntimeRoot(value.runtime_root), goal = requireAuthorityStoreId(value.goal_id, "Goal");
  const fence = await loadLegacyCoordinationWriterFence(root, goal);
  if (fence.status !== "loaded" || fence.fence.schema_version !== PLANNED_TRANSFER_RUNTIME_FENCE_SCHEMA) return {admitted: false};
  const {requireLocalGoalTransferAdmission} = await import("./planned_transfer_cutover.ts");
  const admission = await requireLocalGoalTransferAdmission(root, goal, fence.fence);
  const continuation = requireJsonObject(requireJsonObject(admission.decision, "cutover").continuation, "continuation");
  if (!continuation.continue_work) return {admitted: false};
  const work = requireJsonObject(continuation.continue_work, "reviewed original work");
  if (work.todo_id !== value.todo_id || work.validation_workspace !== "reviewed_working_tree" ||
      work.validation_sha256 !== value.declaration_sha256) return {admitted: false};
  const {plan: rawPlan} = await readGoalTransferExecutionPlan({runtime_root: root, goal_id: goal, operation_id: fence.fence.operation_id});
  const plan = requireJsonObject(rawPlan, "current original execution plan");
  return {admitted: plan.activation_state === "active" && plan.task_status === "open" &&
    typeof value.workspace_path === "string" && resolve(value.workspace_path) === plan.project};
}

/** Live document references follow the already reviewed material mapping.
 * Historical source context and receipts keep their original paths/bytes. */
export function localTransferMappedGoalDocuments(original: JsonObject, materials: JsonObject): JsonObject[] {
  if (!Array.isArray(materials.materials) || !Array.isArray(materials.workspace_destinations)) reject("Original material mapping is unavailable");
  return localTransferGoalDocuments(original).map(source => {
    const path = relative(String(original.repo), resolve(String(original.repo), String(source.path)));
    const material = (materials.materials as JsonObject[]).find(row => row.source_relative_path === path);
    const destination = (materials.workspace_destinations as JsonObject[]).find(row => row.id === material?.id);
    if (!material || !destination) reject("Required Goal document has no reviewed destination material; preserve the source");
    return {...source, path: destination.relative_path};
  });
}

/** Local extension of the existing cutover profile, explicitly reviewed in its
 * preview. Current registration and execution permissions remain independent. */
export function planReviewedTransferContinuation(mode: unknown, original: JsonObject,
  context: JsonObject, preview: JsonObject, materials: JsonObject): JsonObject | null {
  if (mode === undefined || mode === "task_only") return null;
  if (mode !== "reviewed_context") reject("Unsupported Goal transfer continuation profile");
  const coordination = requireJsonObject(original.coordination, "original Agent registration");
  if (!Array.isArray(coordination.registered_agents) || coordination.registered_agents.length !== 1 ||
      preview.ready_for_authority_preparation !== true ||
      requireJsonObject(preview.turn_history, "source Turn and Host closure").turn_hosts_closed !== true) {
    reject("Reviewed continuation requires the original single Agent and closed native source owners");
  }
  return {kind: "reviewed_context", agent_id: requireAuthorityStoreId(coordination.registered_agents[0], "original Agent"),
    source_context_sha256: canonicalAuthoritySha256(context), materials_manifest_sha256: canonicalAuthoritySha256(materials),
    source_sessions_adopted: false, credentials_adopted: false};
}

/** Read current registration without inventing a new-Goal creation receipt.
 * The physical runtime/store binding is checked by the existing cutover owner.
 * Stopping, revoking or redirecting this Goal immediately stops Host admission;
 * a harmless display-name change and unrelated Goals do not invalidate it. */
export async function readReviewedTransferContext(receipt: JsonObject,
  purpose: "execution" | "readback" = "execution"): Promise<{snapshot: JsonObject; context: JsonObject; materials: JsonObject}> {
  const decision = requireJsonObject(receipt.decision, "original cutover decision");
  const continuation = requireJsonObject(decision.continuation, "reviewed continuation admission");
  const admitted = requireJsonObject(receipt.goal, "admitted Goal");
  if (decision.profile !== "source_online_local_runtime_v0" || continuation.kind !== "reviewed_context" ||
      continuation.source_sessions_adopted !== false || continuation.credentials_adopted !== false) {
    reject("Original cutover has no reviewed Host continuation admission");
  }
  const snapshot = await readGoalTransferSnapshot(String(receipt.target_registry_path), String(decision.goal_id));
  const current = requireJsonObject(requireJsonObject(snapshot.source_context, "current Goal context").goal, "current Goal");
  // Readback observes the existing Goal activation owner; it cannot mint an
  // execution grant. Host/runtime and admission retain the strict default.
  const activationAllowed = snapshot.activation_state === "active" ||
    (purpose === "readback" && snapshot.activation_state === "stopped");
  if (snapshot.registry_profile !== null || snapshot.registry_role === "global-local" || !activationAllowed ||
      ["id", "repo", "state_file", "planned_transfer", "goal_instance_id", "creation_operation_id", "status",
        "domain", "role", "spawn_policy", "coordination", "guards", "execution_profile", "authority_sources"]
        .some(key => !same(current[key] ?? null, admitted[key] ?? null))) {
    reject("Current Goal registration no longer matches its reviewed continuation; use its Goal owner");
  }
  const context = await readGoalTransferHistoryJson(join(dirname(resolve(String(current.repo), String(current.state_file))), "TRANSFER_CONTEXT.json"));
  if (canonicalAuthoritySha256(context) !== continuation.source_context_sha256) {
    reject("Original reviewed continuation context changed; preserve its operation");
  }
  const materials = await readGoalTransferHistoryJson(join(dirname(resolve(String(current.repo), String(current.state_file))), "TRANSFER_MATERIALS.json"));
  if (canonicalAuthoritySha256(materials) !== continuation.materials_manifest_sha256) {
    reject("Original reviewed material mapping changed; preserve its operation");
  }
  return {snapshot, context, materials};
}

/** Complete private context for a current Host request. It is guidance only:
 * the ordinary Turn and Task/lease owners still decide actual work and effects.
 * No authority or native database is reconstructed from these historical bytes. */
export async function readGoalTransferHostContext(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "agent_id", "todo_id"])) reject("Host context requires its complete current work identity");
  const root = requireLocalAuthorityRuntimeRoot(value.runtime_root), goal = requireAuthorityStoreId(value.goal_id, "current Goal");
  const agent = requireAuthorityStoreId(value.agent_id, "current Agent"), todoId = requireAuthorityStoreId(value.todo_id, "current Task");
  const fence = await loadLegacyCoordinationWriterFence(root, goal);
  if (fence.status === "failed") reject("Current Goal fence is unreadable");
  if (fence.status !== "loaded" || fence.fence.schema_version !== PLANNED_TRANSFER_RUNTIME_FENCE_SCHEMA) return {context: null};
  const {requireLocalGoalTransferAdmission} = await import("./planned_transfer_cutover.ts");
  const admission = await requireLocalGoalTransferAdmission(root, goal, fence.fence);
  const {snapshot, context, materials: manifest} = await readReviewedTransferContext(admission);
  const continuation = requireJsonObject(requireJsonObject(admission.decision, "cutover").continuation, "continuation");
  if (continuation.agent_id !== agent) reject("Reviewed continuation belongs to another Agent");
  const execution = continuation.execution_workspace == null ? null : requireJsonObject(continuation.execution_workspace, "original execution workspace");
  if (execution) await verifyGoalTransferExecutionWorkspace(execution, manifest, false);
  const opened = await openLocalAuthorityStoreHandle(root, goal, {}, {existingOnly: true});
  const head = await opened.store.loadAuthority();
  if (head.status !== "loaded") reject("Current original work is unavailable");
  const index = indexCoordinationProjection(head.head, goal), selected = index.todos.get(todoId);
  if (!selected || selected.claimed_by !== agent || selected.done === true || selected.archive_state === "archive") {
    reject("Reviewed continuation requires this Agent's current unfinished original Task");
  }
  const destinations = manifest.workspace_destinations as JsonObject[];
  if (!Array.isArray(destinations) || !Array.isArray(manifest.materials)) reject("Original material mapping is unavailable");
  const materials = (manifest.materials as JsonObject[]).map(row => ({...row,
    relative_path: destinations.find(destination => destination.id === row.id)?.relative_path}));
  const {readStagedGoalTransferRuns} = await import("../coordination/planned_transfer_target.ts");
  const history = await readStagedGoalTransferRuns({runtime_root: root, goal_id: goal, operation_id: fence.fence.operation_id});
  const after = await opened.store.loadAuthority();
  if (after.status !== "loaded" || after.provider_revision !== head.provider_revision ||
      !same(snapshot, await readGoalTransferSnapshot(String(admission.target_registry_path), goal)) ||
      !same(fence, await loadLegacyCoordinationWriterFence(root, goal))) reject("Original work changed during continuation context read; retry its current owner");
  return {context: {kind: "reviewed_context", goal_id: goal, agent_id: agent,
    requirements: context.state_text, original_goal: context.goal,
    current_goal: requireJsonObject(snapshot.source_context, "current context").goal, selected_todo: selected,
    ...(execution ? {execution_workspace: execution.path, execution_materials: repositoryMaterialMapping(
      requireJsonObject(execution.repository, "original execution repository") as TransferRepository, manifest).map(item => ({...item.row, relative_path: item.path}))} : {}),
    todos: index.todo_ids.map(id => index.todos.get(id)!), materials, original_accounting: history.provenance,
    source_sessions_adopted: false, credentials_adopted: false,
    guidance: "Continue the original work using current Task/lease and Host authority. Read required Goal documents through current_goal and the reviewed destination material paths. When present, execution_materials maps the actual unfinished inputs within execution_workspace; original_goal retains historical source references. Historical decisions and receipts grant no execution permission."}};
}

/** The existing Settings/CLI entry also works after moving to the destination.
 * Use its admitted original decision and retained provenance; a disconnected
 * old source is neither opened nor recreated to read current destination work. */
export async function inspectAdmittedGoalTransferDestination(value: JsonObject): Promise<JsonObject | null> {
  const root = requireLocalAuthorityRuntimeRoot(value.runtime_root), goal = requireAuthorityStoreId(value.goal_id, "current Goal");
  const fence = await loadLegacyCoordinationWriterFence(root, goal);
  if (fence.status !== "loaded" || !isPlannedTransferAdmissionFence(fence.fence)) return null;
  await checkGoalTransferSourceContext(value.source_context, value.registry_witness, goal, root);
  const {requireLocalGoalTransferAdmission, readLocalGoalTransferContinuation} = await import("./planned_transfer_cutover.ts");
  const admission = await requireLocalGoalTransferAdmission(root, goal, fence.fence);
  const decision = requireJsonObject(admission.decision, "original admitted cutover");
  const current = await readLocalGoalTransferContinuation(decision);
  return {ok: true, schema_version: "loopx_planned_transfer_operation_view_v0", goal_id: goal,
    source_state: "cutover_recorded", operation_id: fence.fence.operation_id,
    authority_preparation_ready: false, cancel_available: false, continuation_available: false,
    local_cutover: {source_final: true, target_task_admitted: current.target_task_admitted,
      target_runtime_admitted: current.target_runtime_admitted,
      ...(current.target_activation_state ? {target_activation_state: current.target_activation_state} : {})}, work: current.work,
    holds: [{code: "transfer_journey_incomplete", owner: "goal_transfer"}]};
}
