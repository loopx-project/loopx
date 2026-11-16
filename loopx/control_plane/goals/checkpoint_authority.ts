/** Same-head checkpoint facts and provider-owned writer fences.
 * Credentials and provider construction remain with the embedding service. */
import type {JsonObject} from "../effect_program.ts";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {authorityStoreSourceAuthority, type AuthorityStore, type AuthorityStoreHead} from "../coordination/authority_store.ts";
import {goalPathSegment} from "../rollout_receipt_log.ts";
import {FileAuthorityStore} from "../coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../coordination/sqlite_authority_store.ts";
import {PostgreSqlAuthorityStore} from "../coordination/postgresql_authority_store.ts";
import type {LocalAuthorityProviderDependencies} from "../coordination/local_authority_provider.ts";
import {openRuntimeAuthorityStore, requireLocalAuthorityRuntimeRoot} from "../coordination/local_authority_provider.ts";
import {loadLegacyCoordinationWriterFence} from "../coordination/legacy_writer_fence.ts";
import {indexCoordinationProjection, validateCoordinationTodoReadModel} from "../coordination/coordination_projection.ts";
import {parseIsoTimestamp} from "../runtime_timestamp.ts";
import {resultFreshnessRequired} from "../quota/heartbeat_receipt_identity.ts";
import {readGoalAcceptance} from "./acceptance_contract.ts";
import {checkpointBasisSnapshot, evaluateCheckpointReadContext, evaluateResultWorkBinding,
  progressQualificationBasis} from "./checkpoint_read_context.ts";
import {canonicalAuthoritySha256, parseAuthorityCursor} from "../coordination/authority_store_codec.ts";
import {COORDINATION_TODO_UPDATE_RECEIPT_SCHEMA} from "../coordination/todo_update.ts";

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

type ReopenedTodoRead = {todoId: string; readId: string; revision: string; storeIdentity: string};

/** Consider history only for an implicit, same-Todo administrative retry. */
export function reopenedTodoRead(wait: unknown, workRead: unknown): ReopenedTodoRead | null {
  const retry = jsonObject(wait), receipt = jsonObject(workRead);
  const basis = jsonObject(receipt?.work_basis), todo = jsonObject(basis?.todo);
  const source = jsonObject(basis?.source);
  return retry?.schema_version === "quota_blocked_retry_v0" && retry.source === "turn_settlement" &&
    receipt?.result_only === true && receipt.commit_attempt == null && receipt.terminal_validation_effect == null &&
    typeof receipt.read_context_id === "string" && typeof receipt.provider_revision === "string" &&
    typeof todo?.todo_id === "string" && retry.todo_id === todo.todo_id && todo.status === "open" &&
    todo.resume_when == null && typeof source?.store_identity === "string"
    ? {todoId: todo.todo_id, readId: receipt.read_context_id,
      revision: receipt.provider_revision, storeIdentity: source.store_identity} : null;
}

/** Require two consecutive lifecycle updates after the admitted snapshot.
 * Other edits and longer histories use the normal stale-read recovery. */
async function reopenedTodoHead(store: AuthorityStore, goalId: string, read: ReopenedTodoRead): Promise<string | null> {
  const head = await store.loadAuthority();
  if (head.status !== "loaded" || head.provider_revision === read.revision) return null;
  const cursor = parseAuthorityCursor(head.cursor);
  if (cursor < 3n) return null;
  const page = await store.scanCommitted(cursor === 3n ? null : (cursor - 3n).toString(), 3);
  if (page.status !== "page" || page.has_more || page.transactions.length !== 3) return null;
  const [original, blocked, reopened] = page.transactions;
  if (original!.provider_revision !== read.revision || reopened!.provider_revision !== head.provider_revision) return null;
  const rows = [original!, blocked!, reopened!].map(transaction =>
    indexCoordinationProjection(transaction.projection, goalId).todos.get(read.todoId));
  if (rows[0]?.status !== "open" || rows[1]?.status !== "blocked" || rows[2]?.status !== "open") return null;
  for (const transaction of [blocked!, reopened!]) {
    const [receipt] = transaction.receipts, [event] = transaction.events;
    if (transaction.receipts.length !== 1 || transaction.events.length !== 1 ||
        receipt?.schema_version !== COORDINATION_TODO_UPDATE_RECEIPT_SCHEMA ||
        receipt.operation_id !== transaction.operation_id || receipt.goal_id !== goalId ||
        receipt.todo_id !== read.todoId || receipt.changed !== true || !Array.isArray(event?.targets) ||
        canonicalAuthoritySha256(event?.targets) !== canonicalAuthoritySha256([`todo:${read.todoId}`])) return null;
  }
  const semantic = (row: JsonObject) => canonicalAuthoritySha256(Object.fromEntries(Object.entries(row)
    .filter(([key]) => !["status", "reason", "updated_at"].includes(key))));
  return rows.every(row => row?.resume_when == null && semantic(row!) === semantic(rows[0]!))
    ? head.provider_revision : null;
}

export async function withCheckpointAuthority(
  root: string, goalId: string, facts: JsonObject, save: (facts: JsonObject) => JsonObject,
  requireCanonical = false,
  resultOnly = false,
  completeTodoSource = false,
  dependencies: LocalAuthorityProviderDependencies = {},
  reopenedRead: ReopenedTodoRead | null = null,
): Promise<JsonObject> {
  // A transported facts object cannot assert provider history.
  facts = {...facts, reopened_todo_work_read_id: null};
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
  const reopenedRevision = reopenedRead && await reopenedTodoHead(store, goalId, reopenedRead);
  return await store.withCheckpointHead((head, identity) => {
    const verified = reopenedRead && head.provider_revision === reopenedRevision && identity === reopenedRead.storeIdentity;
    return save(checkpointProviderFacts(goalId, {...facts,
      reopened_todo_work_read_id: verified ? reopenedRead.readId : null,
    }, head, identity, authorityStoreSourceAuthority(store), requireCanonical || resultOnly || completeTodoSource));
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
  const resultOnly = request.phase === "read" && request.purpose === "delivery_result" &&
      Object.hasOwn(request, "heartbeat_receipt")
    ? resultFreshnessRequired(jsonObject(request.heartbeat_receipt)) : request.result_only === true;
  const supplementOwner = (request.purpose ?? "supplement_checkpoint") === "supplement_checkpoint" &&
    request.checkpoint_continuation === true &&
    (request.phase === "read" || jsonObject(request.receipt)?.commit_attempt != null ||
      jsonObject(request.receipt)?.retained_result_binding != null)
    ? await import("./checkpoint_commit.ts") : null;
  const supplementRows = supplementOwner ? supplementOwner.readCheckpointHistoryRows(root, goalId) : null;
  if (supplementOwner && supplementRows) {
    const sourceRef = jsonObject(facts.source)?.goal_ref;
    const inspected = supplementOwner.inspectCheckpointAttemptFromRows({runtime_root: root, identity,
      purpose: "supplement_checkpoint", ...(sourceRef != null ? {goal_ref: sourceRef} : {})}, supplementRows);
    if (inspected.status === "committed") {
      return request.phase === "read" ? inspected :
        {...requireJsonObject(inspected.context, "committed direction context"), status: "committed"};
    }
    // Proved absent may replace a read; unknown inspection throws before any
    // new source judgment. The caller persists only the accepted new receipt.
    if (inspected.receipt !== undefined) request.receipt = inspected.receipt;
  }
  const current = await withCheckpointAuthority(root, goalId, facts, current => current,
    request.purpose === "first_delivery" && request.checkpoint_continuation !== true || (request.purpose === "delivery_result" &&
      !resultOnly && jsonObject(request.receipt)?.result_only !== true),
    request.purpose === "delivery_result" && (resultOnly || jsonObject(request.receipt)?.result_only === true),
    request.purpose === "first_delivery" || request.full_frontier === true || jsonObject(request.receipt)?.full_frontier === true, dependencies,
    reopenedTodoRead(request.blocked_wait, request.receipt));
  // Reduce the captured snapshot after releasing the read fence, as before.
  // Only commitCheckpoint holds the provider fence through its final check/save.
  const result = evaluateCheckpointReadContext({...request, result_only: resultOnly, facts: current});
  if (result.ok === true && request.restore_issued_work === true &&
      request.phase === "read" && request.purpose === "delivery_result") {
    const receipt = requireJsonObject(result.receipt, "issued work receipt");
    const restored = evaluateResultWorkBinding({phase: "restore", identity, receipt,
      goal_ref: jsonObject(facts.source)?.goal_ref ?? null});
    return restored.ok === true ? {...restored, receipt} : restored;
  }
  if (result.ok === true && supplementOwner && supplementRows &&
      (request.phase === "read" || jsonObject(request.receipt)?.retained_result_binding != null)) {
    // Stay within the existing resolve RPC. The caller still holds its source
    // locks; no receipt is returned or persisted for changed retained bytes.
    let prior = request.prior;
    if (prior == null) {
      const sourceRef = jsonObject(facts.source)?.goal_ref;
      const retained = supplementRows.filter(row => jsonObject(row.settlement_identity)?.effect_id === identity.effect_id &&
        row.result_work_context != null && canonicalAuthoritySha256(row.goal_ref ?? null) === canonicalAuthoritySha256(sourceRef ?? null));
      if (retained.length !== 1) throw new EffectRuntimeRequestError("Original R is unavailable or ambiguous", "checkpoint_commit_unknown");
      prior = retained[0];
    }
    const verified = supplementOwner.inspectCheckpointReplayFromRows({runtime_root: root, goal_id: goalId, prior,
      include_retained_binding: true, include_retained_content: request.include_retained_content === true}, supplementRows);
    if (verified.retained_result_binding !== undefined) {
      const direction = requireJsonObject(request.phase === "read" ? result.receipt : request.receipt, "direction receipt");
      if (request.phase === "read") direction.retained_result_binding = verified.retained_result_binding;
      else if (canonicalAuthoritySha256(direction.retained_result_binding) !== canonicalAuthoritySha256(verified.retained_result_binding)) {
        throw new EffectRuntimeRequestError("Direction does not bind the original retained result and work evidence", "checkpoint_commit_unknown");
      }
      const currentWork = checkpointBasisSnapshot({...request, phase: "read", purpose: "delivery_result", full_frontier: false, facts: current});
      const checked = evaluateResultWorkBinding({phase: "progress_qualification", identity,
        receipt: verified.retained_work_receipt, retained_result_binding: verified.retained_result_binding,
        retained_result_artifacts: verified.retained_result_artifacts, result_writeback: prior,
        direction_receipt: direction, current_work: currentWork});
      if (request.phase === "read") {
        direction.current_progress_qualification = checked.qualification;
        direction.retained_result_artifacts = verified.retained_result_artifacts;
        result.instructions = [result.instructions,
          "Read the exact retained_result_artifacts before judging this R under the current basis; keep final-completion validation separate and never replace the retained candidate."]
          .filter(value => typeof value === "string" && value.length > 0).join(" ");
        direction.instructions = result.instructions;
      } else if (canonicalAuthoritySha256(progressQualificationBasis(direction.current_progress_qualification)) !==
          canonicalAuthoritySha256(checked.qualification) ||
          canonicalAuthoritySha256(direction.retained_result_artifacts) !== canonicalAuthoritySha256(verified.retained_result_artifacts)) {
        return {ok: false, error_code: "checkpoint_read_context_stale", reread_required: true,
          changed_components: ["current_progress_qualification"],
          error: "R current-progress proof is missing or changed; reread and judge this same retained result."};
      }
      result.retained_result_binding = verified.retained_result_binding;
      result.current_progress_qualification = direction.current_progress_qualification;
      result.retained_result_artifacts = verified.retained_result_artifacts;
      // Ephemeral evidence from the same verified buffers, never another copy
      // in the direction receipt, Q record or a separate artifact read RPC.
      if (verified.retained_result_content !== undefined) result.retained_result_content = verified.retained_result_content;
    }
  }
  return result;
}
