/** Reviewed continuation of original work in a newly admitted local runtime.
 *
 * The cutover receipt owns admission. This module reads its immutable context
 * and current Goal/Task owners; it creates no session, lease or second ledger.
 * Historical source bindings and receipts are context, never executable proof.
 */
import {dirname, join, resolve} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys,
  requireAuthorityStoreId} from "../coordination/authority_store_codec.ts";
import {loadLegacyCoordinationWriterFence, isPlannedTransferAdmissionFence, PLANNED_TRANSFER_RUNTIME_FENCE_SCHEMA} from "../coordination/legacy_writer_fence.ts";
import {openLocalAuthorityStoreHandle, requireLocalAuthorityRuntimeRoot} from "../coordination/local_authority_provider.ts";
import {indexCoordinationProjection} from "../coordination/coordination_projection.ts";
import {readGoalTransferSnapshot, checkGoalTransferSourceContext} from "./planned_transfer.ts";
import {readGoalTransferHistoryJson} from "./planned_transfer_history.ts";

function reject(message: string): never {throw new EffectRuntimeConflictError(message);}
function same(a: unknown, b: unknown): boolean {return canonicalAuthorityBytes(a).equals(canonicalAuthorityBytes(b));}

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
    requirements: context.state_text, original_goal: context.goal, selected_todo: selected,
    todos: index.todo_ids.map(id => index.todos.get(id)!), materials, original_accounting: history.provenance,
    source_sessions_adopted: false, credentials_adopted: false,
    guidance: "Continue the original work using current Task/lease and Host authority. Historical decisions and receipts grant no execution permission."}};
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
