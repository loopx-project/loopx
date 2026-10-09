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
import {GOAL_ACCEPTANCE_OWNED_SCHEMA, GOAL_ACCEPTANCE_SCHEMA, acceptanceKeys, acceptanceRequire, acceptanceTask,
  acceptanceText, acceptanceTodos, acceptanceCompletionRequirements, bindGoalAcceptanceStateOwner,
  goalAcceptanceTodoDigest, goalAcceptanceWorkDigest, normalizeAcceptanceResults,
  normalizeGoalAcceptanceDocument, projectGoalAcceptance, readGoalAcceptance, readGoalAcceptanceAuthority,
  type AcceptanceState, type AcceptanceVerification} from "./acceptance_contract.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";
import {GOAL_ACCEPTANCE_LIFECYCLE_SCHEMA, parseGoalAcceptanceLifecycleTransition, parseWireExactGoalRef,
  readGoalAcceptanceLifecycle, sameExactGoalRef, type GoalAcceptanceLifecycle,
  type GoalAcceptanceLifecycleTransition, type WireExactGoalRef} from "./acceptance_lifecycle.ts";

const RESULT_SCHEMA = "loopx_goal_acceptance_result_v0";
const RECEIPT_SCHEMA = "loopx_goal_acceptance_operation_v0";
const LIFECYCLE_RECEIPT_SCHEMA = "loopx_goal_acceptance_lifecycle_operation_v0";
const REQUEST_FIELDS = ["goal_id", "operation_id", "actor_agent_id", "expected_provider_revision"];
const LOCAL_FIELDS = ["runtime_root", "dry_run"];

function mutationRequest(value: unknown, verification: boolean): JsonObject {
  const request = canonicalAuthorityObject(value, "acceptance request");
  // The verify operation is registered only for the trusted execution adapter;
  // its wire call need not impersonate an owner. Explicit Agent actors fail.
  if (verification && !Object.hasOwn(request, "actor_agent_id")) request.actor_agent_id = null;
  acceptanceKeys(request, [...REQUEST_FIELDS, ...(verification ? ["contract_digest", "revision", "results"] : ["document"])],
    [...LOCAL_FIELDS, "goal_ref", ...(verification ? ["todo_id"] : ["disable"])]);
  acceptanceRequire(request.actor_agent_id === null, "goal acceptance mutation requires the trusted owner/host; agent actors cannot configure or attest acceptance");
  for (const field of ["goal_id", "operation_id", "expected_provider_revision"]) {
    requireAuthorityStoreId(request[field], field);
  }
  acceptanceText(request.operation_id, "acceptance operation id", 256);
  acceptanceRequire(request.dry_run === undefined || typeof request.dry_run === "boolean", "dry_run must be boolean");
  if (!verification) acceptanceRequire(request.disable === undefined || typeof request.disable === "boolean", "disable must be boolean");
  if (request.goal_ref !== undefined) parseWireExactGoalRef(request.goal_ref, "acceptance request goal_ref");
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
function requestGoalRef(request: JsonObject): WireExactGoalRef | null {
  return request.goal_ref === undefined
    ? null
    : parseWireExactGoalRef(request.goal_ref, "acceptance request goal_ref");
}
function lifecycleFailure(reason_code: string, reason: string): JsonObject {
  return {status: "failed", changed: false, reason_code, reason};
}
function validateCurrentLifecycle(head: JsonObject, request: JsonObject): JsonObject | null {
  const lifecycle = readGoalAcceptanceLifecycle(head, String(request.goal_id));
  const goalRef = requestGoalRef(request);
  if (lifecycle === null) {
    return null;
  }
  if (goalRef === null) return lifecycleFailure("goal_acceptance_goal_instance_missing",
    "Exact Goal acceptance authority requires goal_ref.");
  if (!sameExactGoalRef(lifecycle.goal_ref, goalRef)) return lifecycleFailure(
    "goal_acceptance_goal_instance_mismatch",
    "Goal acceptance authority belongs to another Goal instance.",
  );
  if (lifecycle.state === "retiring") return lifecycleFailure("goal_acceptance_goal_retiring",
    "The Goal instance is retiring; acceptance mutation is closed.");
  return null;
}
function current(loaded: AuthorityStoreLoadResult, request: JsonObject): AuthorityStoreHead | JsonObject {
  if (loaded.status !== "loaded") return {...loaded};
  if (loaded.provider_revision !== request.expected_provider_revision) return {
    status: "conflict", changed: false, reason_code: "goal_acceptance_provider_revision_mismatch",
    conflict_kind: "provider_revision_mismatch", current_provider_revision: loaded.provider_revision};
  acceptanceTodos(loaded.head, String(request.goal_id));
  const lifecycleFailure = validateCurrentLifecycle(loaded.head, request);
  if (lifecycleFailure) return lifecycleFailure;
  return loaded;
}
function loaded(value: AuthorityStoreHead | JsonObject): value is AuthorityStoreHead {
  return "status" in value && value.status === "loaded";
}
async function replayWithCurrentProjection(
  store: AuthorityStore,
  result: JsonObject,
  goalId: string,
): Promise<JsonObject> {
  const projectsContract = Object.hasOwn(result, "goal_acceptance_contract");
  const projectsLifecycle = Object.hasOwn(result, "goal_acceptance_lifecycle");
  if (!projectsContract && !projectsLifecycle) {
    return source(store, {...result, projection_delivery: "not_required"});
  }
  const current = await store.loadAuthority();
  const contract = current.status === "loaded"
    ? projectGoalAcceptance(current.head, goalId) : {enabled: false, authority_status: current.status};
  const lifecycle = current.status === "loaded"
    ? readGoalAcceptanceLifecycle(current.head, goalId) : null;
  return source(store, {...result,
    ...(projectsContract ? {goal_acceptance_contract: contract} : {}),
    ...(projectsLifecycle ? {goal_acceptance_lifecycle: lifecycle} : {}),
    projection_delivery: "not_required"});
}
async function commit(store: AuthorityStore, request: JsonObject, head: AuthorityStoreHead,
  state: AcceptanceState | null, command: ReturnType<typeof receiptFor>, kind: "configure" | "verify"): Promise<JsonObject> {
  const next: JsonObject = {...head.head};
  const goalRef = requestGoalRef(request);
  const lifecycle = readGoalAcceptanceLifecycle(head.head, String(request.goal_id));
  const committedState = goalRef !== null && lifecycle === null && state !== null
    ? bindGoalAcceptanceStateOwner(state, goalRef) : state;
  if (goalRef !== null && lifecycle === null) {
    next.goal_acceptance_lifecycle = {
      schema_version: GOAL_ACCEPTANCE_LIFECYCLE_SCHEMA,
      state: "active",
      goal_ref: goalRef,
    };
  }
  if (committedState !== null) next.goal_acceptance = committedState;
  const result = {goal_id: request.goal_id, operation_id: request.operation_id,
    goal_acceptance_contract: projectGoalAcceptance(next, String(request.goal_id))};
  if (request.dry_run === true) return source(store, {status: "planned", dry_run: true, changed: false,
    provider_revision: head.provider_revision, ...result});
  const committed = await command.receipt.commit(store, {
    operation_id: String(request.operation_id), expected_provider_revision: String(request.expected_provider_revision),
    next_projection: next,
    events: [{schema_version: RECEIPT_SCHEMA, kind: `goal_acceptance_${kind}`, goal_id: request.goal_id,
      operation_id: request.operation_id, revision: committedState?.revision ?? null,
      digest: committedState?.digest ?? null, enabled: committedState?.enabled ?? false}],
    receipts: [{...command.identity, result}],
  });
  // No Todo/Markdown display document changes in this transaction.
  return replayWithCurrentProjection(store, committed, String(request.goal_id));
}

export async function configureGoalAcceptance(store: AuthorityStore, value: JsonObject): Promise<JsonObject> {
  const request = mutationRequest(value, false);
  const disable = request.disable === true || request.document === null;
  acceptanceRequire(!disable || request.document === null, "disable requires document:null");
  const document = disable ? null : normalizeGoalAcceptanceDocument(request.document);
  const command = receiptFor(request, "configure");
  const replay = await command.receipt.read(store);
  if (replay) return replayWithCurrentProjection(store, replay, String(request.goal_id));
  const observation = await command.receipt.observe(store);
  if (observation.kind === "receipt") {
    return replayWithCurrentProjection(store, observation.result, String(request.goal_id));
  }
  const head = current(observation.authority, request);
  if (!loaded(head)) return source(store, head);
  const previous = readGoalAcceptance(head.head, String(request.goal_id));
  let state: AcceptanceState | null = previous;
  if (document) {
    // Historical receipts above remain replayable; new configurations must make
    // the blast radius explicit, including updates to legacy contracts.
    if (document.scope === undefined) return source(store, failure("goal_acceptance_scope_required",
      "Select selected_work with todo_ids or explicitly choose all_advancement before configuring acceptance."));
    const todos = acceptanceTodos(head.head, String(request.goal_id));
    if (document.scope?.kind === "selected_work") for (const todoId of document.scope.todo_ids) {
      const todo = todos.get(todoId);
      acceptanceRequire(todo && todo.role === "agent" && (todo.task_class == null || todo.task_class === "advancement_task"),
        "acceptance scope must reference existing Agent advancement work");
    }
    const revision = (previous?.revision ?? 0) + 1;
    acceptanceRequire(Number.isSafeInteger(revision), "acceptance revision exhausted");
    const bindings = document.bindings.map(binding => {
      const todo = todos.get(binding.todo_id);
      acceptanceRequire(todo && todo.role === "agent" && (todo.task_class == null || todo.task_class === "advancement_task"),
        "acceptance binding must reference existing Agent advancement work");
      return {...binding, todo_semantic_digest: goalAcceptanceTodoDigest(todo), revision, confirmed_by: "owner" as const};
    });
    const fields = {enabled: true, revision, digest: canonicalAuthoritySha256(document),
      document, bindings, verification: previous?.verification ?? null};
    const goalRef = requestGoalRef(request);
    state = goalRef === null
      ? {schema_version: GOAL_ACCEPTANCE_SCHEMA, ...fields}
      : {schema_version: GOAL_ACCEPTANCE_OWNED_SCHEMA, owner_goal_ref: goalRef, ...fields};
  } else if (previous) state = {...previous, enabled: false};
  return commit(store, request, head, state, command, "configure");
}

/** Host adapter only: results must come from actual execution at this provider
 * revision, never an agent-supplied accepted flag. This provides Goal readback;
 * Todo completion needs its own fresh execution and atomic completion CAS. */
export async function commitGoalAcceptanceVerification(store: AuthorityStore, value: JsonObject): Promise<JsonObject> {
  const request = mutationRequest(value, true);
  acceptanceRequire(Number.isSafeInteger(request.revision) && Number(request.revision) > 0 &&
    typeof request.contract_digest === "string" && BARE_SHA256_PATTERN.test(request.contract_digest), "invalid verification contract basis");
  const todoId = request.todo_id == null ? null : requireAuthorityStoreId(request.todo_id, "verification todo_id");
  const results = normalizeAcceptanceResults(request.results);
  const command = receiptFor(request, "verify");
  const replay = await command.receipt.read(store);
  if (replay) return replayWithCurrentProjection(store, replay, String(request.goal_id));
  const observation = await command.receipt.observe(store);
  if (observation.kind === "receipt") {
    return replayWithCurrentProjection(store, observation.result, String(request.goal_id));
  }
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
    work_digest: goalAcceptanceWorkDigest(head.head, goalId, state.document.scope), results};
  return commit(store, request, head, {...state, verification}, command, "verify");
}

function lifecycleRequest(value: unknown): {
  request: JsonObject;
  transition: GoalAcceptanceLifecycleTransition;
} {
  const request = canonicalAuthorityObject(value, "goal acceptance lifecycle request");
  acceptanceKeys(request, ["goal_id", "operation_id", "actor_agent_id", "transition"], ["runtime_root"]);
  acceptanceRequire(request.actor_agent_id === null,
    "goal acceptance lifecycle transition requires the trusted source owner");
  const goalId = requireAuthorityStoreId(request.goal_id, "goal_id");
  acceptanceText(request.operation_id, "goal acceptance lifecycle operation id", 256);
  const transition = parseGoalAcceptanceLifecycleTransition(request.transition);
  const refs = transition.kind === "activate_successor"
    ? [transition.retired_goal_ref, transition.goal_ref] : [transition.goal_ref];
  acceptanceRequire(refs.every(goalRef => goalRef.goal_id === goalId),
    "goal acceptance lifecycle transition changed the Goal alias");
  return {request, transition};
}

function lifecycleReceiptFor(request: JsonObject, transition: GoalAcceptanceLifecycleTransition) {
  const identity = {
    schema_version: LIFECYCLE_RECEIPT_SCHEMA,
    operation_id: String(request.operation_id),
    goal_id: String(request.goal_id),
    request_sha256: canonicalAuthoritySha256({transition}),
  };
  const receipt = new CoordinationCommandReceipt({
    result_schema: RESULT_SCHEMA,
    identity,
    failure,
    decode: original => {
      const fields = canonicalAuthorityObject(original.result, "goal acceptance lifecycle result");
      acceptanceRequire(typeof fields.changed === "boolean",
        "goal acceptance lifecycle receipt omitted its change decision");
      return {fields, changed: fields.changed};
    },
  });
  return {identity, receipt};
}

type LifecyclePlan =
  | Readonly<{
      kind: "commit";
      lifecycle: GoalAcceptanceLifecycle;
      acceptance: AcceptanceState | null;
    }>
  | Readonly<{kind: "no_change"; lifecycle: GoalAcceptanceLifecycle}>
  | Readonly<{kind: "failure"; reason_code: string; reason: string}>;

function lifecycleMismatch(reason: string): LifecyclePlan {
  return {kind: "failure", reason_code: "goal_acceptance_goal_instance_mismatch", reason};
}

function planLifecycleTransition(
  head: JsonObject,
  goalId: string,
  transition: GoalAcceptanceLifecycleTransition,
): LifecyclePlan {
  const current = readGoalAcceptanceLifecycle(head, goalId);
  if (transition.kind === "bind_existing") {
    if (current === null) {
      const acceptance = readGoalAcceptance(head, goalId);
      return {
        kind: "commit",
        lifecycle: {
          schema_version: GOAL_ACCEPTANCE_LIFECYCLE_SCHEMA,
          state: "active",
          goal_ref: transition.goal_ref,
        },
        acceptance: acceptance === null
          ? null
          : bindGoalAcceptanceStateOwner(acceptance, transition.goal_ref),
      };
    }
    if (!sameExactGoalRef(current.goal_ref, transition.goal_ref)) {
      return lifecycleMismatch("Canonical acceptance is already bound to another Goal instance.");
    }
    return current.state === "active"
      ? {kind: "no_change", lifecycle: current}
      : {kind: "failure", reason_code: "goal_acceptance_goal_retiring",
        reason: "A retiring Goal instance cannot be rebound as active."};
  }
  if (current === null) {
    return {kind: "failure", reason_code: "goal_acceptance_lifecycle_unbound",
      reason: "Bind the existing Goal instance before changing its acceptance lifecycle."};
  }
  readGoalAcceptanceAuthority(head, goalId);
  if (transition.kind === "retire") {
    if (!sameExactGoalRef(current.goal_ref, transition.goal_ref)) {
      return lifecycleMismatch("Only the active Goal instance can begin retirement.");
    }
    return current.state === "retiring"
      ? {kind: "no_change", lifecycle: current}
      : {kind: "commit", lifecycle: {...current, state: "retiring"},
        acceptance: readGoalAcceptance(head, goalId)};
  }
  if (current.state !== "retiring"
      || !sameExactGoalRef(current.goal_ref, transition.retired_goal_ref)) {
    return lifecycleMismatch("Goal acceptance successor activation requires its exact retiring predecessor.");
  }
  return {
    kind: "commit",
    lifecycle: {
      schema_version: GOAL_ACCEPTANCE_LIFECYCLE_SCHEMA,
      state: "active",
      goal_ref: transition.goal_ref,
    },
    acceptance: readGoalAcceptance(head, goalId),
  };
}

export async function transitionGoalAcceptanceLifecycle(
  store: AuthorityStore,
  value: JsonObject,
): Promise<JsonObject> {
  const {request, transition} = lifecycleRequest(value);
  const goalId = String(request.goal_id);
  const command = lifecycleReceiptFor(request, transition);
  const replay = await command.receipt.read(store);
  if (replay) return replayWithCurrentProjection(store, replay, goalId);
  const observation = await command.receipt.observe(store);
  if (observation.kind === "receipt") {
    return replayWithCurrentProjection(store, observation.result, goalId);
  }
  if (observation.authority.status !== "loaded") return source(store, {...observation.authority});
  acceptanceTodos(observation.authority.head, goalId);
  const plan = planLifecycleTransition(observation.authority.head, goalId, transition);
  if (plan.kind === "failure") return source(store, failure(plan.reason_code, plan.reason));
  const result = {
    goal_id: goalId,
    operation_id: request.operation_id,
    changed: plan.kind === "commit",
    goal_acceptance_lifecycle: plan.lifecycle,
    goal_acceptance_contract: projectGoalAcceptance(
      plan.kind === "commit"
        ? {
            ...observation.authority.head,
            goal_acceptance_lifecycle: plan.lifecycle,
            ...(plan.acceptance === null ? {} : {goal_acceptance: plan.acceptance}),
          }
        : observation.authority.head,
      goalId,
    ),
  };
  const next: JsonObject = {...observation.authority.head, goal_acceptance_lifecycle: plan.lifecycle};
  if (plan.kind === "commit" && plan.acceptance !== null) next.goal_acceptance = plan.acceptance;
  const committed = await command.receipt.commit(store, {
    operation_id: String(request.operation_id),
    expected_provider_revision: observation.authority.provider_revision,
    next_projection: next,
    events: plan.kind === "commit" ? [{
      schema_version: LIFECYCLE_RECEIPT_SCHEMA,
      kind: `goal_acceptance_lifecycle_${transition.kind}`,
      goal_id: goalId,
      operation_id: request.operation_id,
      goal_ref: plan.lifecycle.goal_ref,
      state: plan.lifecycle.state,
    }] : [],
    receipts: [{...command.identity, result}],
  });
  return replayWithCurrentProjection(store, committed, goalId);
}

/** Private readback. Only goal_acceptance_contract is safe to project publicly. */
export async function inspectGoalAcceptance(
  store: AuthorityStore,
  goalId: string,
  todoId?: string,
  goalRef?: WireExactGoalRef,
): Promise<JsonObject> {
  const head = await store.loadAuthority();
  if (head.status !== "loaded") return source(store, {...head});
  const lifecycle = readGoalAcceptanceLifecycle(head.head, goalId);
  if (lifecycle !== null) {
    acceptanceRequire(goalRef !== undefined, "exact Goal acceptance inspection requires goal_ref");
    acceptanceRequire(sameExactGoalRef(lifecycle.goal_ref, goalRef),
      "goal acceptance inspection belongs to another Goal instance");
  } else {
    acceptanceRequire(goalRef === undefined || goalRef.goal_id === goalId,
      "goal acceptance inspection Goal identity mismatch");
  }
  const todos = acceptanceTodos(head.head, goalId);
  const authority = readGoalAcceptanceAuthority(head.head, goalId);
  const state = authority.kind === "legacy" ? authority.state
    : authority.owner_matches && authority.lifecycle.state === "active" ? authority.state : null;
  const tasks = [...todos.entries()].filter(([key]) => todoId === undefined || key === todoId).map(([key, todo]) => ({
    todo_id: key, todo_semantic_digest: goalAcceptanceTodoDigest(todo),
    ...(state?.enabled ? acceptanceTask(key, todo, state) : {state: "unbound", criterion_ids: [], applicable: false}),
  }));
  return source(store, {status: "loaded", provider_revision: head.provider_revision,
    revision: state?.revision ?? null, contract_digest: state?.digest ?? null,
    contract: state?.enabled ? state.document : null, tasks,
    ...(todoId === undefined ? {} : {todo: todos.get(todoId) ?? null,
      completion_requirements: acceptanceCompletionRequirements(head.head, goalId, todoId)}),
    goal_acceptance_contract: projectGoalAcceptance(head.head, goalId)});
}

async function local(value: unknown, kind: "inspect" | "configure" | "verify" | "lifecycle"): Promise<JsonObject> {
  let store: AuthorityStore | undefined;
  try {
    const request = canonicalAuthorityObject(value, "local acceptance request");
    const root = requireAuthorityStoreId(request.runtime_root, "runtime_root");
    acceptanceRequire(isAbsolute(root), "runtime_root must be absolute");
    const goalId = requireAuthorityStoreId(request.goal_id, "goal_id");
    const run = async () => {
      store = await openLocalAuthorityStore(root, goalId);
      if (kind === "inspect") return inspectGoalAcceptance(store, goalId,
        request.todo_id == null ? undefined : requireAuthorityStoreId(request.todo_id, "todo_id"),
        request.goal_ref === undefined ? undefined : parseWireExactGoalRef(request.goal_ref, "acceptance request goal_ref"));
      if (kind === "configure") return configureGoalAcceptance(store, request);
      return kind === "verify"
        ? commitGoalAcceptanceVerification(store, request)
        : transitionGoalAcceptanceLifecycle(store, request);
    };
    return kind === "inspect" ? await run() : await withCanonicalWriter(root, goalId, request.dry_run === true, run);
  } catch (error) {
    // Preserve the canonical task guard's diagnosis on exact private reads.
    // An unbound/stale task must never look like an absent contract eligible
    // for independent validation, or an authority that needs re-promotion.
    const taskReason = kind === "inspect" && error instanceof AuthorityStoreProtocolError
      && ["goal_acceptance_unbound", "goal_acceptance_stale"].includes(error.message)
      ? error.message : null;
    const result = {...failure(taskReason ?? (error instanceof AuthorityStoreProtocolError ? "goal_acceptance_invalid_request" : "goal_acceptance_effect_failed"),
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
export async function commitLocalGoalAcceptanceLifecycleTransition(value: unknown): Promise<JsonObject> {
  return local(value, "lifecycle");
}
