/** Current-work admission under the caller-held canonical writer/source locks.
 * This read neither commits a receipt nor grants a reusable execution token.
 * The adapter retains the locks through the existing Explore-log commit. */
import {resolve} from "node:path";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {claimFileMutationLock, mutationLockOwner, releaseFileMutationLockClaim} from "../effect_runtime_io.ts";
import {shadowMaintenanceLockPath, requireShadowPrimaryWriteAllowed} from "../coordination/shadow_management.ts";
import {requireCanonicalAuthority} from "../coordination/local_authority_write.ts";
import {openRuntimeAuthorityStore, requireLocalAuthorityRuntimeRoot} from "../coordination/local_authority_provider.ts";
import {FileAuthorityStore} from "../coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../coordination/sqlite_authority_store.ts";
import {indexCoordinationProjection, validateCoordinationTodoReadModel} from "../coordination/coordination_projection.ts";
import {registryAuthoritySourceCheck} from "../coordination/authority_source.ts";
import {todoUpdateAdmissionRejection} from "../coordination/todo_update_admission.ts";
import {normalizeRegisteredTodoAgents} from "../coordination/todo_agents.ts";
import {decideFirstPartyHostRuntime} from "../goals/first_party_host_runtime.ts";

export async function admitExploreWorkFinding(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "work finding commit admission");
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  const goal = requireNonEmptyString(request.goal_id, "goal_id");
  const actor = requireNonEmptyString(request.agent_id, "agent_id");
  const witness = requireJsonObject(request.lock, "canonical writer lock");
  const target = resolve(shadowMaintenanceLockPath(root, goal));
  if (witness.target !== target) throw new EffectRuntimeRequestError("work finding canonical writer lock target mismatch");
  const token = requireNonEmptyString(witness.token, "lock token");
  const claim = await claimFileMutationLock(target, token);
  if (!claim) throw new EffectRuntimeRequestError("work finding canonical writer lock expired");
  try {
    const owner = await mutationLockOwner(target);
    if (owner?.token !== token || owner.pid !== witness.pid) {
      throw new EffectRuntimeRequestError("work finding canonical writer lock owner changed");
    }
    await requireShadowPrimaryWriteAllowed(root, goal);
    await requireCanonicalAuthority(root, goal, "todo_update");
    const sourceCheck = registryAuthoritySourceCheck(request, true,
      requireNonEmptyString(request.expected_registry_sha256, "original registry SHA-256"));
    if (!await sourceCheck()) throw new EffectRuntimeRequestError("work finding Goal source changed");
    // Reuse the current Goal registration/Stop owner. This checks eligibility
    // only; no Host execution or quota effect is requested here.
    if (request.profile_id === "source_session_v1") {
      throw new EffectRuntimeRequestError("work finding requires a qualified local Goal source profile");
    }
    const source = requireJsonObject(request.registry_source, "registry_source");
    const goalDecision = decideFirstPartyHostRuntime({operation: "launch", goal_id: goal,
      authority: request.goal_authority, registry_path: source.path,
      planned_registry_witness: source, registry_witness: source});
    if (goalDecision.kind === "reject") throw new EffectRuntimeRequestError(goalDecision.code);
    if (request.explore_enabled !== true || request.yardstick_requested === true && request.yardstick_enabled !== true) {
      throw new EffectRuntimeRequestError("work finding evidence or Yardstick policy is disabled");
    }
    const store = await openRuntimeAuthorityStore(root, goal, {});
    // The shared maintenance marker excludes the shipped local command writers.
    // It is not a cross-host PostgreSQL fence or a new provider qualification.
    if (!(store instanceof FileAuthorityStore) && !(store instanceof SqliteAuthorityStore)) {
      throw new EffectRuntimeRequestError("work finding requires a supported local File or SQLite writer fence");
    }
    const current = await store.loadAuthority();
    if (current.status !== "loaded") throw new EffectRuntimeRequestError("work finding canonical authority is unavailable");
    if (current.provider_revision !== requireNonEmptyString(request.expected_provider_revision, "original provider revision")) {
      throw new EffectRuntimeRequestError("work finding authority changed; inspect again before continuing");
    }
    validateCoordinationTodoReadModel(current.head, goal);
    const index = indexCoordinationProjection(current.head, goal);
    const todoId = requireNonEmptyString(request.todo_id, "todo_id");
    const todo = index.todos.get(todoId);
    if (!todo || todo.role !== "agent" || todo.status !== "open" || todo.claimed_by !== actor) {
      throw new EffectRuntimeRequestError("work finding requires the current open claimed Agent Todo");
    }
    const proof = requireJsonObject(request.proof, "original work finding proof");
    const rejection = todoUpdateAdmissionRejection(current.head, todo, index.leases, {
      goal_id: goal, todo_id: todoId, expected_role: "agent", actor_agent_id: actor,
      registered_agents: normalizeRegisteredTodoAgents(request.registered_agents as string[]),
      operation_id: "explore-work-finding-admission", patch: {}, clear_fields: [], dry_run: true, now: new Date(),
      planning_intent: {append_explore_result_node_refs: [requireNonEmptyString(request.node_id, "node_id")]},
      lease_idempotency_key: proof.idempotency_key == null ? null : String(proof.idempotency_key),
      lease_expected_version: proof.expected_version == null ? null : Number(proof.expected_version),
    });
    if (rejection) throw new EffectRuntimeRequestError(rejection.reason, rejection.code);
    if (!await sourceCheck()) throw new EffectRuntimeRequestError("work finding Goal source changed");
    return {ok: true, provider_revision: current.provider_revision};
  } finally {
    // Borrowed marker: Python keeps it until the graph effect completes.
    await releaseFileMutationLockClaim(claim);
  }
}
