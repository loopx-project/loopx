/** Acceptance effects use the existing canonical head, CAS and operation journal.
 * Only trusted local owner/host adapters may invoke these mutation exports. */
import {isAbsolute} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore, AuthorityStoreHead, AuthorityStoreLoadResult} from "../coordination/authority_store.ts";
import {authorityStoreSourceAuthority} from "../coordination/authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthoritySha256,
  requireAuthorityStoreId} from "../coordination/authority_store_codec.ts";
import {CoordinationCommandReceipt} from "../coordination/command_receipt.ts";
import {withCanonicalWriter} from "../coordination/local_authority_write.ts";
import {openLocalAuthorityStore, localAuthorityOpenFailure} from "../coordination/local_authority_provider.ts";
import {GOAL_ACCEPTANCE_SCHEMA, acceptanceKeys, acceptanceRequire, acceptanceTask, acceptanceText, acceptanceTodos,
  acceptanceCompletionRequirements, goalAcceptanceTodoDigest, goalAcceptanceWorkDigest, normalizeAcceptanceResults,
  normalizeGoalAcceptanceDocument, projectGoalAcceptance, readGoalAcceptance,
  type AcceptanceState, type AcceptanceVerification} from "./acceptance_contract.ts";

const RESULT_SCHEMA = "loopx_goal_acceptance_result_v0";
const RECEIPT_SCHEMA = "loopx_goal_acceptance_operation_v0";
const REQUEST_FIELDS = ["goal_id", "operation_id", "actor_agent_id", "expected_provider_revision"];
const LOCAL_FIELDS = ["runtime_root", "dry_run"];

function mutationRequest(value: unknown, verification: boolean): JsonObject {
  const request = canonicalAuthorityObject(value, "acceptance request");
  // The verify operation is registered only for the trusted execution adapter;
  // its wire call need not impersonate an owner. Explicit Agent actors fail.
  if (verification && !Object.hasOwn(request, "actor_agent_id")) request.actor_agent_id = null;
  acceptanceKeys(request, [...REQUEST_FIELDS, ...(verification ? ["contract_digest", "revision", "results"] : ["document"])],
    [...LOCAL_FIELDS, ...(verification ? ["todo_id"] : ["disable"])]);
  acceptanceRequire(request.actor_agent_id === null, "goal acceptance mutation requires the trusted owner/host; agent actors cannot configure or attest acceptance");
  for (const field of ["goal_id", "operation_id", "expected_provider_revision"]) {
    requireAuthorityStoreId(request[field], field);
  }
  acceptanceText(request.operation_id, "acceptance operation id", 256);
  acceptanceRequire(request.dry_run === undefined || typeof request.dry_run === "boolean", "dry_run must be boolean");
  if (!verification) acceptanceRequire(request.disable === undefined || typeof request.disable === "boolean", "disable must be boolean");
  return request;
}
function source(store: AuthorityStore, result: JsonObject): JsonObject {
  return {...result, source_authority: authorityStoreSourceAuthority(store),
    decision_read_from_provider: true, legacy_fallback_used: false};
}
function failure(reason_code: string, reason: string): JsonObject & {schema_version: typeof RESULT_SCHEMA} {
  return {schema_version: RESULT_SCHEMA, status: "failed", changed: false, reason_code, reason};
}
function receiptFor(request: JsonObject, kind: "configure" | "verify") {
  // Transport location and dry-run are not operation content. Everything else,
  // including the exact supplied document/results and expected CAS, is bound.
  const content = Object.fromEntries(Object.entries(request).filter(([key]) => !LOCAL_FIELDS.includes(key)));
  const identity = {schema_version: RECEIPT_SCHEMA, operation_id: String(request.operation_id),
    goal_id: String(request.goal_id), request_sha256: canonicalAuthoritySha256({kind, ...content})};
  const receipt = new CoordinationCommandReceipt({result_schema: RESULT_SCHEMA, identity, failure,
    decode: original => ({fields: canonicalAuthorityObject(original.result, "acceptance operation result"), changed: true})});
  return {identity, receipt};
}
function current(loaded: AuthorityStoreLoadResult, request: JsonObject): AuthorityStoreHead | JsonObject {
  if (loaded.status !== "loaded") return {...loaded};
  if (loaded.provider_revision !== request.expected_provider_revision) return {
    status: "conflict", changed: false, reason_code: "goal_acceptance_provider_revision_mismatch",
    conflict_kind: "provider_revision_mismatch", current_provider_revision: loaded.provider_revision};
  acceptanceTodos(loaded.head, String(request.goal_id));
  return loaded;
}
function loaded(value: AuthorityStoreHead | JsonObject): value is AuthorityStoreHead {
  return "status" in value && value.status === "loaded";
}
async function commit(store: AuthorityStore, request: JsonObject, head: AuthorityStoreHead,
  state: AcceptanceState | null, command: ReturnType<typeof receiptFor>, kind: "configure" | "verify"): Promise<JsonObject> {
  const next = {...head.head};
  if (state !== null) next.goal_acceptance = state;
  const result = {goal_id: request.goal_id, operation_id: request.operation_id,
    goal_acceptance_contract: projectGoalAcceptance(next, String(request.goal_id))};
  if (request.dry_run === true) return source(store, {status: "planned", dry_run: true, changed: false,
    provider_revision: head.provider_revision, ...result});
  const committed = await command.receipt.commit(store, {
    operation_id: String(request.operation_id), expected_provider_revision: String(request.expected_provider_revision),
    next_projection: next,
    events: [{schema_version: RECEIPT_SCHEMA, kind: `goal_acceptance_${kind}`, goal_id: request.goal_id,
      operation_id: request.operation_id, revision: state?.revision ?? null, digest: state?.digest ?? null,
      enabled: state?.enabled ?? false}],
    receipts: [{...command.identity, result}],
  });
  // No Todo/Markdown display document changes in this transaction.
  return source(store, {...committed, projection_delivery: "not_required"});
}

export async function configureGoalAcceptance(store: AuthorityStore, value: JsonObject): Promise<JsonObject> {
  const request = mutationRequest(value, false);
  const disable = request.disable === true || request.document === null;
  acceptanceRequire(!disable || request.document === null, "disable requires document:null");
  const document = disable ? null : normalizeGoalAcceptanceDocument(request.document);
  const command = receiptFor(request, "configure");
  const replay = await command.receipt.read(store);
  if (replay) return source(store, {...replay, projection_delivery: "not_required"});
  const observation = await command.receipt.observe(store);
  if (observation.kind === "receipt") return source(store, {...observation.result, projection_delivery: "not_required"});
  const head = current(observation.authority, request);
  if (!loaded(head)) return source(store, head);
  const previous = readGoalAcceptance(head.head, String(request.goal_id));
  let state: AcceptanceState | null = previous;
  if (document) {
    const todos = acceptanceTodos(head.head, String(request.goal_id));
    const revision = (previous?.revision ?? 0) + 1;
    acceptanceRequire(Number.isSafeInteger(revision), "acceptance revision exhausted");
    const bindings = document.bindings.map(binding => {
      const todo = todos.get(binding.todo_id);
      acceptanceRequire(todo && todo.role === "agent" && (todo.task_class == null || todo.task_class === "advancement_task"),
        "acceptance binding must reference existing Agent advancement work");
      return {...binding, todo_semantic_digest: goalAcceptanceTodoDigest(todo), revision, confirmed_by: "owner" as const};
    });
    state = {schema_version: GOAL_ACCEPTANCE_SCHEMA, enabled: true, revision,
      digest: canonicalAuthoritySha256(document), document, bindings, verification: previous?.verification ?? null};
  } else if (previous) state = {...previous, enabled: false};
  return commit(store, request, head, state, command, "configure");
}

/** Host adapter only: results must come from actual execution at this provider
 * revision, never an agent-supplied accepted flag. This provides Goal readback;
 * Todo completion needs its own fresh execution and atomic completion CAS. */
export async function commitGoalAcceptanceVerification(store: AuthorityStore, value: JsonObject): Promise<JsonObject> {
  const request = mutationRequest(value, true);
  acceptanceRequire(Number.isSafeInteger(request.revision) && Number(request.revision) > 0 &&
    typeof request.contract_digest === "string" && /^[a-f0-9]{64}$/.test(request.contract_digest), "invalid verification contract basis");
  const todoId = request.todo_id == null ? null : requireAuthorityStoreId(request.todo_id, "verification todo_id");
  const results = normalizeAcceptanceResults(request.results);
  const command = receiptFor(request, "verify");
  const replay = await command.receipt.read(store);
  if (replay) return source(store, {...replay, projection_delivery: "not_required"});
  const observation = await command.receipt.observe(store);
  if (observation.kind === "receipt") return source(store, {...observation.result, projection_delivery: "not_required"});
  const head = current(observation.authority, request);
  if (!loaded(head)) return source(store, head);
  const goalId = String(request.goal_id);
  const state = readGoalAcceptance(head.head, goalId);
  acceptanceRequire(state?.enabled, "goal acceptance is disabled");
  if (state.revision !== request.revision || state.digest !== request.contract_digest) return source(store,
    failure("goal_acceptance_contract_stale", "acceptance contract changed while validation ran"));
  let criterionIds = state.document.criteria.map(item => item.id);
  if (todoId !== null) {
    const task = acceptanceTask(todoId, acceptanceTodos(head.head, goalId).get(todoId), state);
    acceptanceRequire(task.state === "ready", task.reason_code);
    criterionIds = task.criterion_ids;
  }
  normalizeAcceptanceResults(results, criterionIds);
  const verification: AcceptanceVerification = {operation_id: String(request.operation_id),
    contract_revision: state.revision, contract_digest: state.digest, todo_id: todoId,
    work_digest: goalAcceptanceWorkDigest(head.head, goalId), results};
  return commit(store, request, head, {...state, verification}, command, "verify");
}

/** Private readback. Only goal_acceptance_contract is safe to project publicly. */
export async function inspectGoalAcceptance(store: AuthorityStore, goalId: string, todoId?: string): Promise<JsonObject> {
  const head = await store.loadAuthority();
  if (head.status !== "loaded") return source(store, {...head});
  const todos = acceptanceTodos(head.head, goalId);
  const state = readGoalAcceptance(head.head, goalId);
  const tasks = [...todos.entries()].filter(([key]) => todoId === undefined || key === todoId).map(([key, todo]) => ({
    todo_id: key, todo_semantic_digest: goalAcceptanceTodoDigest(todo),
    ...(state?.enabled ? acceptanceTask(key, todo, state) : {state: "unbound", criterion_ids: [], applicable: false}),
  }));
  return source(store, {status: "loaded", provider_revision: head.provider_revision,
    revision: state?.revision ?? null, contract_digest: state?.digest ?? null,
    contract: state?.enabled ? state.document : null, tasks,
    ...(todoId === undefined ? {} : {completion_requirements: acceptanceCompletionRequirements(head.head, goalId, todoId)}),
    goal_acceptance_contract: projectGoalAcceptance(head.head, goalId)});
}

async function local(value: unknown, kind: "inspect" | "configure" | "verify"): Promise<JsonObject> {
  let store: AuthorityStore | undefined;
  try {
    const request = canonicalAuthorityObject(value, "local acceptance request");
    const root = requireAuthorityStoreId(request.runtime_root, "runtime_root");
    acceptanceRequire(isAbsolute(root), "runtime_root must be absolute");
    const goalId = requireAuthorityStoreId(request.goal_id, "goal_id");
    const run = async () => {
      store = await openLocalAuthorityStore(root, goalId);
      if (kind === "inspect") return inspectGoalAcceptance(store, goalId,
        request.todo_id == null ? undefined : requireAuthorityStoreId(request.todo_id, "todo_id"));
      return kind === "configure" ? configureGoalAcceptance(store, request) : commitGoalAcceptanceVerification(store, request);
    };
    return kind === "inspect" ? await run() : await withCanonicalWriter(root, goalId, request.dry_run === true, run);
  } catch (error) {
    const result = {...failure(error instanceof AuthorityStoreProtocolError ? "goal_acceptance_invalid_request" : "goal_acceptance_effect_failed",
      error instanceof Error ? error.message : "acceptance effect failed"),
      decision_read_from_provider: false, legacy_fallback_used: false, ...localAuthorityOpenFailure(error)};
    return store ? {...result, source_authority: authorityStoreSourceAuthority(store)} : result;
  }
}
export async function inspectLocalGoalAcceptance(value: unknown): Promise<JsonObject> {
  return local(value, "inspect");
}
export async function commitLocalGoalAcceptance(value: unknown): Promise<JsonObject> {
  return local(value, "configure");
}
export async function commitLocalGoalAcceptanceVerification(value: unknown): Promise<JsonObject> {
  return local(value, "verify");
}
