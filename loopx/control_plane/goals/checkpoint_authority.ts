/** Same-head checkpoint facts and provider-owned writer fences.
 * Credentials and provider construction remain with the embedding service. */
import type {JsonObject} from "../effect_program.ts";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {authorityStoreSourceAuthority} from "../coordination/authority_store.ts";
import {goalPathSegment} from "../rollout_receipt_log.ts";
import {FileAuthorityStore} from "../coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../coordination/sqlite_authority_store.ts";
import {PostgreSqlAuthorityStore} from "../coordination/postgresql_authority_store.ts";
import type {LocalAuthorityProviderDependencies} from "../coordination/local_authority_provider.ts";
import {openRuntimeAuthorityStore, requireLocalAuthorityRuntimeRoot} from "../coordination/local_authority_provider.ts";
import {loadLegacyCoordinationWriterFence} from "../coordination/legacy_writer_fence.ts";
import {indexCoordinationProjection, validateCoordinationTodoReadModel} from "../coordination/coordination_projection.ts";
import {parseIsoTimestamp} from "../runtime_timestamp.ts";
import {readGoalAcceptance} from "./acceptance_contract.ts";
import {evaluateCheckpointReadContext} from "./checkpoint_read_context.ts";
import type {AuthorityStoreHead} from "../coordination/authority_store.ts";

function primaryGoalDocuments(source: JsonObject): JsonObject[] {
  const goal = jsonObject(source.registry_goal);
  if (!Array.isArray(goal?.authority_sources)) return [];
  const paths = new Set<string>();
  for (const value of goal.authority_sources) {
    const declaration = requireJsonObject(value, "Goal authority source");
    if (declaration.kind !== "goal_doc") continue;
    const path = resolve(requireNonEmptyString(goal.repo, "Goal project"),
      requireNonEmptyString(declaration.path, "primary Goal document path"));
    if (path !== resolve(String(source.state_file))) paths.add(path);
  }
  return [...paths].sort().map(path => {
    try {
      // Match the source reader's universal-newline text contract.
      return {path, prose: readFileSync(path, "utf8").replace(/\r\n?/g, "\n")};
    } catch {
      throw new EffectRuntimeRequestError(
        "A registered primary Goal document is unavailable; preserve the current state and locate the owner's current source, not an old plan.",
        "checkpoint_goal_source_unavailable");
    }
  });
}

/** The same provider facts feed checkpoint append and Todo result CAS checks. */
export function checkpointProviderFacts(
  goalId: string, facts: JsonObject, head: AuthorityStoreHead, storeIdentity: string,
  sourceAuthority: string, protectedDelivery: boolean,
): JsonObject {
  const source = requireJsonObject(facts.source, "checkpoint source");
  const documents = primaryGoalDocuments(source);
  const projection = indexCoordinationProjection(head.head, goalId);
  validateCoordinationTodoReadModel(head.head, goalId);
  const acceptance = readGoalAcceptance(head.head, goalId);
  const todoId = jsonObject(facts.checkpoint_identity)?.todo_id;
  const lease = protectedDelivery && typeof todoId === "string" ? projection.leases.get(todoId) : null;
  const expiry = typeof lease?.expires_at === "string" ? parseIsoTimestamp(lease.expires_at) : null;
  return {...facts,
    ...(documents.length ? {goal_documents: documents} : {}),
    ...(protectedDelivery ? {execution_lease: lease ? {...lease,
      active: lease.status === "active" && expiry !== null && expiry > new Date()} : null} : {}),
    todos: projection.todo_ids.map(id => projection.todos.get(id)!),
    acceptance: {revision: acceptance?.revision ?? null, contract_digest: acceptance?.digest ?? null,
      contract: acceptance?.document ?? null},
    provider_revision: head.provider_revision,
    source: {...source, authority: sourceAuthority, store_identity: storeIdentity},
  };
}

export async function withCheckpointAuthority(
  root: string, goalId: string, facts: JsonObject, save: (facts: JsonObject) => JsonObject,
  requireCanonical = false,
  resultOnly = false,
  completeTodoSource = false,
  dependencies: LocalAuthorityProviderDependencies = {},
): Promise<JsonObject> {
  const fence = await loadLegacyCoordinationWriterFence(root, goalId);
  if (fence.status === "failed") throw new Error(fence.reason);
  const source = requireJsonObject(facts.source, "checkpoint source");
  if (fence.status === "missing") {
    if (requireCanonical) throw new Error("first delivery freshness requires canonical authority");
    return save({...facts, source: {...source, authority: "legacy_markdown", store_identity: null}});
  }
  const store = await openRuntimeAuthorityStore(root, goalId, dependencies);
  if (!(store instanceof FileAuthorityStore) && !(store instanceof SqliteAuthorityStore) &&
      !(store instanceof PostgreSqlAuthorityStore)) {
    if (resultOnly && authorityStoreSourceAuthority(store) === "postgresql_v0") {
      const [head, identity] = await Promise.all([store.loadAuthority(), store.storeIdentity()]);
      if (head.status !== "loaded" || identity.status !== "available") {
        throw new Error("Cannot read the original result provider head and identity");
      }
      return save(checkpointProviderFacts(goalId, facts, head, identity.store_identity, "postgresql_v0", true));
    }
    throw new Error("checkpoint requires a supported provider fence");
  }
  return await store.withCheckpointHead((head, identity) => {
    return save(checkpointProviderFacts(goalId, facts, head, identity, authorityStoreSourceAuthority(store), requireCanonical || resultOnly || completeTodoSource));
  });
}

/** Called while the Python adapter holds the local source locks. Optimistic
 * receipts are allowed to go stale after this operation returns. */
export async function resolveCheckpointReadContext(value: unknown,
  dependencies: LocalAuthorityProviderDependencies = {},
): Promise<JsonObject> {
  const request = requireJsonObject(value, "checkpoint read context request");
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  const identity = requireJsonObject(request.identity, "identity");
  const goalId = goalPathSegment(identity.goal_id);
  const facts = requireJsonObject(request.facts, "checkpoint facts");
  requireNonEmptyString(requireJsonObject(facts.source, "checkpoint source").state_file, "state_file");
  const current = await withCheckpointAuthority(root, goalId, facts, current => current,
    request.purpose === "first_delivery" && request.checkpoint_continuation !== true || (request.purpose === "delivery_result" &&
      request.result_only !== true && jsonObject(request.receipt)?.result_only !== true),
    request.purpose === "delivery_result" && (request.result_only === true || jsonObject(request.receipt)?.result_only === true),
    request.purpose === "first_delivery" || request.full_frontier === true || jsonObject(request.receipt)?.full_frontier === true, dependencies);
  // Reduce the captured snapshot after releasing the read fence, as before.
  // Only commitCheckpoint holds the provider fence through its final check/save.
  return evaluateCheckpointReadContext({...request, facts: current});
}
