/** Same-head checkpoint facts and the two shipped local provider fences.
 * Not an AuthorityStore extension contract or a checkpoint authority migration. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {authorityStoreSourceAuthority} from "../coordination/authority_store.ts";
import {goalPathSegment} from "../rollout_receipt_log.ts";
import {FileAuthorityStore} from "../coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../coordination/sqlite_authority_store.ts";
import {openRuntimeAuthorityStore, requireLocalAuthorityRuntimeRoot} from "../coordination/local_authority_provider.ts";
import {loadLegacyCoordinationWriterFence} from "../coordination/legacy_writer_fence.ts";
import {indexCoordinationProjectionTodos, validateCoordinationTodoReadModel} from "../coordination/coordination_projection.ts";
import {readGoalAcceptance} from "./acceptance_contract.ts";
import {evaluateCheckpointReadContext} from "./checkpoint_read_context.ts";

export async function withCheckpointAuthority(
  root: string, goalId: string, facts: JsonObject, save: (facts: JsonObject) => JsonObject,
): Promise<JsonObject> {
  const fence = await loadLegacyCoordinationWriterFence(root, goalId);
  if (fence.status === "failed") throw new Error(fence.reason);
  const source = requireJsonObject(facts.source, "checkpoint source");
  if (fence.status === "missing") {
    return save({...facts, source: {...source, authority: "legacy_markdown", store_identity: null}});
  }
  const store = await openRuntimeAuthorityStore(root, goalId, {});
  if (!(store instanceof FileAuthorityStore) && !(store instanceof SqliteAuthorityStore)) {
    throw new Error("checkpoint supplement requires a supported local provider fence");
  }
  return await store.withCheckpointHead((head, identity) => {
    const projection = indexCoordinationProjectionTodos(head.head, goalId);
    validateCoordinationTodoReadModel(head.head, goalId);
    const acceptance = readGoalAcceptance(head.head, goalId);
    return save({...facts,
      todos: projection.todo_ids.map(id => projection.todos.get(id)!),
      acceptance: {revision: acceptance?.revision ?? null, contract_digest: acceptance?.digest ?? null,
        contract: acceptance?.enabled ? acceptance.document : null},
      provider_revision: head.provider_revision,
      source: {...source, authority: authorityStoreSourceAuthority(store), store_identity: identity},
    });
  });
}

/** Called while the Python adapter holds the local source locks. Optimistic
 * receipts are allowed to go stale after this operation returns. */
export async function resolveCheckpointReadContext(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "checkpoint read context request");
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  const identity = requireJsonObject(request.identity, "identity");
  const goalId = goalPathSegment(identity.goal_id);
  const facts = requireJsonObject(request.facts, "checkpoint facts");
  requireNonEmptyString(requireJsonObject(facts.source, "checkpoint source").state_file, "state_file");
  const current = await withCheckpointAuthority(root, goalId, facts, current => current);
  // Reduce the captured snapshot after releasing the read fence, as before.
  // Only commitCheckpoint holds the provider fence through its final check/save.
  return evaluateCheckpointReadContext({...request, facts: current});
}
