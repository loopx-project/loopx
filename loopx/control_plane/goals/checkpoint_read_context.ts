/** Read basis for a missing decision/qualification checkpoint, not an execution/permission lease.
 * The commit effect holds source/index claims and the real provider fence. */
import {settlementIdentity, type JsonObject} from "../effect_program.ts";
import {createHash} from "node:crypto";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import type {LocalAuthorityProviderDependencies} from "../coordination/local_authority_provider.ts";
import {LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
  LOCAL_COORDINATION_TODO_SNAPSHOT_PAGE_REQUEST_SCHEMA} from "../coordination/coordination_state_contract.generated.ts";
import {taskLeaseExecutionFacts} from "../coordination/task_lease_state.ts";
import {isUnqualifiedBlockedReplanWriteback} from "../quota/settlement_phase.ts";
import {todoResumeDependencyId} from "../todos/resume_condition.ts";
import {claimAllowsAgent} from "../todos/agent_scope.ts";
import {evaluateTodoResumeConditions, normalizeTodoResumeWhen,
  TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION, TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION} from "../todos/resume_condition.ts";
import {BLOCKED_WAIT_REQUEST_SCHEMA, isCausalBlockedWait, prepareBlockedWait} from "../quota/blocked_wait.ts";
import {parseIsoTimestamp} from "../runtime_timestamp.ts";
import {checkpointContinuationRequired, heartbeatReceiptDetails, normalizeHeartbeatReplanObligationId, resultFreshnessRequired} from "../quota/heartbeat_receipt_identity.ts";
import {visionAuthoringContract, openTodoProgressValidationContract} from "./vision_checkpoint.ts";
import {normalizeTodoCompletionValidationDeclaration} from "../todos/completion_validation_declaration.ts";
import {hostRetryPolicyCheck} from "../turn_driver/turn_journal.ts";
import {EffectiveAction} from "../quota/effective_action.generated.ts";
import {originalTurnRequestsNoFollowup} from "../todos/completion_state.ts";
import {terminalValidationRecovery} from "../todos/completion_validation_plan.ts";
import {parseExactGoalRef} from "./goal_instance_identity.ts";
import {decodeTodoCompletionFenceRequest, evaluateTodoCompletionFence,
  TODO_COMPLETION_FENCE_REQUEST_SCHEMA} from "../todos/completion_fence.ts";

const checkpointAuthorityInstructions =
  "Current primary Goal documents govern requirements; plans, Todos and prior read contexts are derived records. " +
  "A changed Goal is not proof of projection corruption. Reconcile the candidate and derived work with the current requirements; " +
  "never restore an existing Goal document from an old plan or read context to make a result pass. " +
  "Conflicting primary documents require owner resolution. File write isolation belongs to the host; a read receipt grants no document-edit authority.";

const RECEIPT_SCHEMA = "checkpoint_read_context_v3";
const checkpointBasisIntact = (receipt: JsonObject, basis: JsonObject | null): boolean => basis !== null &&
  canonicalAuthoritySha256(receipt.versions) === canonicalAuthoritySha256(
    Object.fromEntries(Object.entries(basis).map(([key, value]) => [key, canonicalAuthoritySha256(value)])));

export function checkpointReceiptName(effect: string, work: boolean): string {
  return createHash("sha256").update(effect + (work ? ":delivery_result" : "")).digest("hex") + ".json";
}

/** The attempt stores the index row containing this digest. Exclude that recursive marker only. */
export function originalWorkReceiptDigest(receipt: JsonObject): string {
  return canonicalAuthoritySha256(Object.fromEntries(Object.entries(receipt)
    .filter(([key]) => key !== "commit_attempt")));
}

/** A result-only read can observe its original Todo completion at append. */
export function isCommittedResultContext(value: unknown): boolean {
  const context = jsonObject(value);
  return context?.ok === true && context.result_committed === true && context.versions === undefined;
}

/** Test the basis of this work append, never the later state at settlement. */
export function inFlightWorkAuthorized(value: unknown, identity: JsonObject): boolean {
  const basis = jsonObject(value);
  const todo = jsonObject(basis?.todo), lease = jsonObject(basis?.execution_lease);
  return todo !== null && todo.todo_id === identity.todo_id && todo.status === "open" &&
    claimAllowsAgent({claim: typeof todo.claimed_by === "string" ? todo.claimed_by : null,
      excluded: ids(todo.excluded_agents, "excluded_agents")}, String(identity.agent_id)) &&
    (lease === null || lease.active === true && lease.owner === identity.agent_id);
}

export type CheckpointReadPurpose = "supplement_checkpoint" | "first_delivery" | "delivery_result";
export type CheckpointDecisionScope = "agent_lane" | "goal";

/** The receipt version owns its basis interpretation, including historical reads. */
export function checkpointReadReceiptPurpose(value: unknown): CheckpointReadPurpose | null {
  const receipt = jsonObject(value);
  if (receipt?.schema_version === "checkpoint_read_context_v1" &&
      (receipt.purpose == null || receipt.purpose === "supplement_checkpoint")) return "supplement_checkpoint";
  if (receipt?.schema_version === "checkpoint_read_context_v2" &&
      (receipt.purpose === "first_delivery" || receipt.purpose === "delivery_result")) return receipt.purpose;
  if (receipt?.schema_version === RECEIPT_SCHEMA &&
      (receipt.purpose === "supplement_checkpoint" || receipt.purpose === "first_delivery" ||
       receipt.purpose === "delivery_result")) return receipt.purpose;
  return null;
}

function purpose(request: JsonObject): CheckpointReadPurpose {
  const value = request.purpose ?? "supplement_checkpoint";
  if (value !== "supplement_checkpoint" && value !== "first_delivery" && value !== "delivery_result") {
    throw new EffectRuntimeRequestError("unsupported checkpoint read purpose");
  }
  return value;
}
// Exact presentation fields only. Unknown future fields remain part of the basis.
const DISPLAY_FIELDS = new Set(["index", "source_section", "schema_version"]);
const todoFacts = (todo: JsonObject): JsonObject => Object.fromEntries(
  Object.entries(todo).filter(([key]) => !DISPLAY_FIELDS.has(key)),
);

function semanticCheckpointSource(value: unknown): unknown {
  const source = jsonObject(value), goal = jsonObject(source?.registry_goal);
  if (source === null || goal === null) return value;
  // Exact presentation field only; identity, permissions and unknown fields remain.
  return {...source, registry_goal: Object.fromEntries(Object.entries(goal)
    .filter(([key]) => key !== "display_name"))};
}

function ids(value: unknown, label: string): string[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an array`);
  return [...new Set(value.map(item => requireNonEmptyString(item, label)))].sort();
}

function dependencies(todo: JsonObject): string[] {
  const result = ids(todo.depends_on_todo_ids, "depends_on_todo_ids");
  if (todo.depends_on_todo_id != null) result.push(requireNonEmptyString(todo.depends_on_todo_id, "depends_on_todo_id"));
  // These are typed resume tokens, never a search through task prose.
  const target = todoResumeDependencyId(todo.resume_when);
  if (target !== null) result.push(target);
  return [...new Set(result)].sort();
}

export function requireCompleteCheckpointGoalSource(facts: JsonObject): JsonObject {
  const metadata = requireJsonObject(facts.frontmatter, "frontmatter");
  if (metadata.goal_source_completeness !== undefined) {
    if (metadata.goal_source_completeness !== "todo_projection_only") {
      throw new EffectRuntimeRequestError("unsupported Goal source completeness marker");
    }
    throw new EffectRuntimeRequestError(
      "Restore the Goal owner's actual document via loopx-project; a Todo-only projection cannot supply the Goal basis. Retry the original Turn without work or spend.",
      "checkpoint_goal_source_incomplete",
    );
  }
  return metadata;
}

export function checkpointBasisSnapshot(request: JsonObject): JsonObject {
  const identity = requireJsonObject(request.identity, "identity");
  const facts = requireJsonObject(request.facts, "facts");
  const metadata = requireCompleteCheckpointGoalSource(facts);
  // Re-entry retains the original result, not permission for a new direction.
  // Consume the live registry facts under the existing source guard, including
  // cached receipt checks, before a direction Host or writeback can run.
  const compute = jsonObject(jsonObject(jsonObject(facts.source)?.registry_goal)?.quota)?.compute;
  if (purpose(request) === "first_delivery" && typeof compute === "number" && compute <= 0) {
    throw new EffectRuntimeRequestError(
      "Automatic Goal turns are paused by compute quota. Preserve the original result and receipts; resume the Goal explicitly before continuing this Turn.",
      "checkpoint_goal_paused",
    );
  }
  if (!Array.isArray(facts.todos)) throw new EffectRuntimeRequestError("todos must be complete source records");
  const records = facts.todos.map(value => todoFacts(requireJsonObject(value, "todo")));
  const byId = new Map<string, JsonObject>();
  for (const todo of records) {
    // Anonymous legacy rows cannot be dependencies; retain them in an obligation frontier.
    if (todo.todo_id == null) continue;
    const id = requireNonEmptyString(todo.todo_id, "todo_id");
    if (byId.has(id)) throw new EffectRuntimeRequestError("checkpoint basis has duplicate Todo identities");
    byId.set(id, todo);
  }
  const todoId = identity.todo_id;
  const task = typeof todoId === "string" ? byId.get(todoId) : null;
  if (typeof todoId === "string" && !task) throw new EffectRuntimeRequestError("checkpoint Todo is absent; restore its authoritative record before rereading");
  let frontier: JsonObject[] | null = null;
  const scope = request.checkpoint_continuation === true && purpose(request) !== "delivery_result"
    ? (!task || originalTurnRequestsNoFollowup(task, identity) ? "goal" : "agent_lane")
    : request.decision_scope;
  if (purpose(request) === "first_delivery" || request.full_frontier === true) {
    if (scope !== "agent_lane" && scope !== "goal") {
      throw new EffectRuntimeRequestError("first delivery requires agent_lane or goal decision scope");
    }
    const agent = requireNonEmptyString(identity.agent_id, "agent_id");
    // Recompute the complete membership. The caller cannot omit new work.
    frontier = records.filter(todo => scope === "goal" || todo.role === "user" ||
      claimAllowsAgent({claim: typeof todo.claimed_by === "string" ? todo.claimed_by : null,
        excluded: ids(todo.excluded_agents, "excluded_agents")}, agent));
    frontier = frontier.map(todo => ({todo, key: String(todo.todo_id ?? canonicalAuthoritySha256(todo))}))
      .sort((a, b) => a.key.localeCompare(b.key)).map(({todo}) => todo);
  }
  const selected = new Map<string, JsonObject>();
  const pending = [...ids(request.dependency_todo_ids, "dependency_todo_ids"),
    ...(task ? dependencies(task) : []), ...(frontier ?? []).flatMap(dependencies)];
  while (pending.length) {
    const id = pending.shift()!;
    if (selected.has(id) || id === todoId) continue;
    const todo = byId.get(id);
    if (!todo) throw new EffectRuntimeRequestError(`checkpoint dependency ${id} is absent`);
    selected.set(id, todo);
    pending.push(...dependencies(todo));
  }
  const documents = facts.goal_documents as JsonObject[] | undefined;
  const goal = {
    frontmatter: Object.fromEntries(Object.entries(metadata).filter(([key]) => key !== "updated_at")),
    prose: documents?.length ? documents.map(document => document.prose).join("\n\n") : facts.goal_prose,
    acceptance: facts.acceptance,
    user_todos: records.filter(todo => todo.role === "user"),
    ...(documents?.length ? {state_prose: facts.goal_prose,
      primary_documents: documents.map(document => ({path: document.path,
        sha256: createHash("sha256").update(String(document.prose)).digest("hex")}))} : {}),
  };
  const basis: JsonObject = {
    todo: task ?? records,
    goal,
    dependencies: [...selected.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([, todo]) => todo),
    agent_vision: facts.agent_vision,
    source: facts.source,
  };
  if (frontier !== null) {
    basis.frontier = {scope, todos: frontier};
  }
  if (purpose(request) !== "supplement_checkpoint") {
    basis.execution_lease = facts.execution_lease ?? null;
  }
  // New judgments use semantic inputs. A check of v1/v2 must retain its original
  // full snapshot interpretation; never silently rebind an old read or attempt.
  const historical = request.phase === "check" && checkpointReadReceiptPurpose(request.receipt) !== null &&
    jsonObject(request.receipt)?.schema_version !== RECEIPT_SCHEMA;
  if (!historical) {
    basis.source = semanticCheckpointSource(basis.source);
    const lease = jsonObject(basis.execution_lease);
    if (lease !== null) basis.execution_lease = taskLeaseExecutionFacts(lease);
    // status/active, generation, owner, scopes and unknown fields remain inputs.
    // Current proof/version/CAS still belong to the native execution owner.
  }
  return {basis, provider_revision: facts.provider_revision ?? null,
    versions: Object.fromEntries(Object.entries(basis).map(([key, value]) =>
    [key, canonicalAuthoritySha256(value)]))};
}

function checkpointSnapshotOrRejection(request: JsonObject): JsonObject {
  try {
    return checkpointBasisSnapshot(request);
  } catch (error) {
    // This is a known source-state rejection, not malformed terminal input.
    // Return it through the caller's existing zero-effect rejection path.
    if (!(error instanceof EffectRuntimeRequestError) ||
        !["checkpoint_goal_source_incomplete", "checkpoint_goal_paused"].includes(error.code)) throw error;
    return {ok: false, error_code: error.code, reread_required: false,
      error: error.message, recommended_action: error.message};
  }
}

const rejected = (code: string, changed: string[] = []): JsonObject => ({
  ok: false, error_code: code, reread_required: code !== "checkpoint_context_not_missing", changed_components: changed,
  error: code === "checkpoint_context_not_missing"
    ? `${code}: this committed writeback has no missing checkpoint for this recovery. ` +
      "Preserve the committed writeback and quota receipt; do not repeat this recovery or quota spend. " +
      "For later authorized work, inspect current state through the normal guard with a fresh Turn identity. " +
      "This rejection grants no work or completion authority."
    : `${code}: run checkpoint-context for the same Goal/Agent/Todo or obligation/Turn, ` +
    "using the original purpose. Recheck the candidate for delivery_result, or judge the direction for first_delivery/supplement_checkpoint. " +
    "Use the returned identity only with that new decision. Retain successful result and quota receipts.",
});

const unknownDirectionLaunch = (): JsonObject => ({
  ...rejected("checkpoint_direction_launch_outcome_unknown"), reread_required: false,
  error: "checkpoint_direction_launch_outcome_unknown: Preserve the original direction attempt and resolve its Host outcome through the original owner. " +
    "While that outcome is unknown, do not dispatch, spend or replace its receipt to retry.",
});

function verifiedPriorDirectionResponse(receipt: JsonObject): JsonObject | null {
  if (checkpointReadReceiptPurpose(receipt) === null) return null;
  const proof = jsonObject(receipt.prior_direction_response);
  const binding = jsonObject(proof?.binding), decision = jsonObject(proof?.decision);
  return proof && Number.isInteger(proof.attempt) && Number(proof.attempt) > 0 &&
    Number(proof.attempt) <= Number(receipt.direction_host_attempt_count) && decision && binding &&
    typeof proof.read_context_id === "string" && !!proof.read_context_id &&
    binding.read_context_id === proof.read_context_id && decision.read_context_id === proof.read_context_id &&
    binding.identity_sha256 === canonicalAuthoritySha256(receipt.identity ?? null) &&
    binding.decision_sha256 === canonicalAuthoritySha256(decision) ? proof : null;
}

/** Preserve a returned response as evidence of outcome, never as a new judgment. */
function retireDirectionResponse(receipt: JsonObject): JsonObject | null {
  const decision = jsonObject(receipt.current_agent_decision);
  if (decision && Number.isInteger(receipt.direction_host_attempt_count) && Number(receipt.direction_host_attempt_count) > 0) {
    const checked = evaluateResultWorkBinding({phase: "direction_response", receipt,
      identity: receipt.identity, decision});
    if (checked.ok === true && canonicalAuthoritySha256(checked.binding) ===
        canonicalAuthoritySha256(receipt.current_agent_binding ?? null)) {
      return {attempt: receipt.direction_host_attempt_count, read_context_id: receipt.read_context_id,
        binding: checked.binding, decision};
    }
    // A pre-qualification response remains evidence of a known old outcome,
    // never a judgment for the new read. Preserve its original binding only.
    if (receipt.current_progress_qualification === undefined) {
      const binding = jsonObject(receipt.current_agent_binding);
      const legacy = verifiedPriorDirectionResponse({...receipt, prior_direction_response: {
        attempt: receipt.direction_host_attempt_count, read_context_id: receipt.read_context_id, binding, decision}});
      if (legacy && (binding?.retained_result_binding_sha256 === undefined ||
          binding.retained_result_binding_sha256 === canonicalAuthoritySha256(receipt.retained_result_binding))) return legacy;
    }
  }
  return verifiedPriorDirectionResponse(receipt);
}

/** Qualify only a machine-verifiable blocked outcome against the current graph.
 * Preserve the original work receipt and its versions for durable replay. */
function currentBlockedWaitMatches(request: JsonObject, identity: JsonObject,
  receipt: JsonObject, projected: JsonObject, changed: string[]): boolean {
  const wait = jsonObject(request.blocked_wait);
  const original = jsonObject(receipt.work_basis);
  const basis = jsonObject(projected.basis);
  const originalTodo = jsonObject(original?.todo), todo = jsonObject(basis?.todo);
  const timed = wait?.schema_version === "quota_blocked_retry_v0" && wait.source === "todo";
  const reopened = wait?.schema_version === "quota_blocked_retry_v0" && wait.source === "turn_settlement";
  if (request.purpose !== "delivery_result" || receipt.schema_version !== RECEIPT_SCHEMA ||
      receipt.result_only !== true || !checkpointBasisIntact(receipt, original) ||
      originalTodo === null || todo === null ||
      typeof projected.provider_revision !== "string" ||
      changed.some(key => key !== "todo" && (timed || reopened || key !== "dependencies")) ||
      wait === null || !inFlightWorkAuthorized(original, identity)) return false;

  const facts = requireJsonObject(request.facts, "checkpoint facts");
  const currentTodos = facts.todos;
  if (!Array.isArray(currentTodos)) return false;
  if (reopened) {
    if (facts.reopened_todo_work_read_id !== receipt.read_context_id ||
        !inFlightWorkAuthorized(basis, identity) || todo.resume_when != null ||
        typeof wait.due_at !== "string" || !(Date.parse(wait.due_at) > Date.now())) return false;
    const currentWait = prepareBlockedWait({schema_version: BLOCKED_WAIT_REQUEST_SCHEMA,
      todos: currentTodos, todo_id: identity.todo_id, observed_at: wait.observed_at, allow_turn_settlement_retry: true});
    return canonicalAuthoritySha256(currentWait) === canonicalAuthoritySha256(wait);
  }

  const targetId = jsonObject(wait.target_todo)?.todo_id;
  if (timed) {
    if (wait.todo_id !== identity.todo_id || todo.resume_when !== wait.resume_when ||
        typeof todo.resume_when !== "string" || !todo.resume_when.startsWith("resume_at:") ||
        !["open", "deferred"].includes(String(todo.status)) ||
        todo.role !== "agent" || todo.archive_state !== "active" ||
        originalTodo.done !== false || todo.done !== (todo.status === "deferred") ||
        !inFlightWorkAuthorized({...basis, todo: {...todo, status: "open"}}, identity)) return false;
  } else if (!isCausalBlockedWait(wait, String(identity.todo_id)) ||
      typeof targetId !== "string" || todo.resume_when !== `todo_done:${targetId}` ||
      todo.claimed_by !== identity.agent_id || !inFlightWorkAuthorized(basis, identity)) return false;

  if (changed.length && originalTodo.resume_when != null) return false;
  if (changed.length) {
    const originalSuccessors = ids(originalTodo.successor_todo_ids, "successor_todo_ids");
    const currentSuccessors = ids(todo.successor_todo_ids, "successor_todo_ids");
    // The canonical presentation marks both deferred and completed rows as done.
    // The checks above permit only the exact open-to-deferred timer transition.
    const waitFields = ["resume_when", "successor_todo_ids", "updated_at", ...(timed ? ["status", "done"] : [])];
    const unchangedTodo = (value: JsonObject) => Object.fromEntries(Object.entries(value)
      .filter(([key]) => !waitFields.includes(key)));
    if (canonicalAuthoritySha256(unchangedTodo(originalTodo)) !== canonicalAuthoritySha256(unchangedTodo(todo))) return false;
    if (timed) {
      if (!originalSuccessors.every(id => currentSuccessors.includes(id)) ||
          !currentSuccessors.every(id => id !== identity.todo_id && currentTodos.some(row => row.todo_id === id))) return false;
    } else {
      if (canonicalAuthoritySha256(currentSuccessors) !==
          canonicalAuthoritySha256([...new Set([...originalSuccessors, targetId])].sort())) return false;
      const currentDependencies = basis?.dependencies;
      if (!Array.isArray(currentDependencies) || !currentDependencies.some(item => item.todo_id === targetId) ||
          canonicalAuthoritySha256(currentDependencies.filter(item => item.todo_id !== targetId)) !==
            canonicalAuthoritySha256(original?.dependencies)) return false;
    }
  }

  const evaluated = evaluateTodoResumeConditions({schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [todo], source_items: currentTodos, kinds: [timed ? "resume_at" : "todo_done"],
    ...(timed ? {evaluated_at: new Date().toISOString()} : {})});
  const condition = jsonObject((evaluated.conditions as JsonObject[])[0]?.condition);
  if (condition?.satisfied !== false || condition.invalid_state != null) return false;
  const waiting = {...todo, resume_condition: condition, resume_ready: false};
  try {
    const currentWait = prepareBlockedWait({schema_version: BLOCKED_WAIT_REQUEST_SCHEMA,
      todos: currentTodos.map(item => item.todo_id === identity.todo_id ? waiting : item),
      todo_id: identity.todo_id, observed_at: wait.observed_at, allow_turn_settlement_retry: false});
    return canonicalAuthoritySha256(currentWait) === canonicalAuthoritySha256(wait);
  } catch (error) {
    if (!(error instanceof EffectRuntimeRequestError)) throw error;
    return false;
  }
}

export function evaluateCheckpointReadContext(value: unknown): JsonObject {
  const request = requireJsonObject(value, "checkpoint read context");
  const identity = requireJsonObject(request.identity, "identity");
  const token = request.read_context_id;
  const use = purpose(request);
  if (request.phase === "read") {
    const scoped = jsonObject(jsonObject(request.facts)?.source)?.goal_ref != null;
    if (scoped && ((use === "delivery_result" && request.result_only !== true) ||
        (use === "first_delivery" && request.checkpoint_continuation !== true))) {
      // Exact-source recovery consumes the original protected admission;
      // reading a lifecycle registry must not opt a new Turn into this policy.
      return rejected("checkpoint_first_delivery_not_admitted");
    }
    const previous = jsonObject(request.receipt);
    const previousBasis = jsonObject(previous?.work_basis ?? previous?.decision_basis);
    if (scoped && use !== "supplement_checkpoint" && previous !== null &&
        canonicalAuthoritySha256(jsonObject(previousBasis?.source)?.goal_ref ?? null) !==
          canonicalAuthoritySha256(jsonObject(jsonObject(request.facts)?.source)?.goal_ref)) {
      return {...rejected("delivery_work_goal_instance_mismatch"), reread_required: false};
    }
    if (use === "delivery_result" &&
        jsonObject(jsonObject(request.receipt)?.terminal_validation_effect)?.status === "pending") {
      return {...rejected("delivery_validation_outcome_unknown"), reread_required: false,
        error: "Original validation may have executed; reconcile its outcome before replacing the work read."};
    }
    if (use === "supplement_checkpoint" && request.checkpoint_continuation === true &&
        previous?.commit_attempt != null) {
      return {...rejected("checkpoint_commit_unknown"), reread_required: false,
        error: "Inspect the original qualified append before replacing its direction read."};
    }
    if (use !== "supplement_checkpoint") {
      const receipt = jsonObject(request.receipt);
      const attempt = jsonObject(receipt?.commit_attempt);
      const indexed = jsonObject(attempt?.index_record);
      const prior = jsonObject(request.prior);
      // A proved writeback is not a completed Todo. Historical result-only
      // Turns and settled delegated progress may still need terminal validation.
      // Its original append/artifacts are inspected by the IO owner before
      // this read; only matching proof on those lanes may release the marker.
      // Pending current checkpoint lanes retain exact writeback replay.
      const committedWork = use === "delivery_result" && request.result_only === true &&
        prior !== null && indexed !== null &&
        jsonObject(prior.result_work_context)?.read_context_id === receipt?.read_context_id &&
        canonicalAuthoritySha256(prior.settlement_identity) === canonicalAuthoritySha256(identity) &&
        canonicalAuthoritySha256(prior.refresh_recovery) === canonicalAuthoritySha256(indexed.refresh_recovery) &&
        canonicalAuthoritySha256(prior.result_work_context) === canonicalAuthoritySha256(indexed.result_work_context);
      if (receipt?.commit_attempt != null && (!committedWork ||
          (request.checkpoint_continuation !== false &&
            !(request.settlement_complete === true && request.quota_spent === true)))) {
        return {...rejected(committedWork ? "delivery_result_already_committed" : "checkpoint_commit_unknown"), reread_required: false,
          error: "A first delivery append may have started; inspect the original Turn and artifacts before another judgment."};
      }
      if ((request.prior != null && !(use === "delivery_result" && request.result_only === true)) || request.admitted_turn !== true) {
        return rejected("checkpoint_first_delivery_not_admitted");
      }
    } else {
      const prior = requireJsonObject(request.prior, "committed writeback");
      const checkpoint = jsonObject(prior.vision_checkpoint);
      const missingQualification = request.writeback_committed === true && request.quota_spend_committed === false &&
        typeof identity.replan_obligation_id === "string" &&
        jsonObject(request.semantic_replan_guard)?.selected_obligation_id === identity.replan_obligation_id &&
        isUnqualifiedBlockedReplanWriteback(prior, settlementIdentity({
          goal_id: requireNonEmptyString(identity.goal_id, "goal_id"),
          agent_id: requireNonEmptyString(identity.agent_id, "agent_id"),
          turn_instance_id: requireNonEmptyString(identity.turn_instance_id, "turn_instance_id"),
          replan_obligation_id: identity.replan_obligation_id,
        }));
      if ((checkpoint?.decision !== "missing_required" || checkpoint.satisfied !== false) && !missingQualification) {
        return rejected("checkpoint_context_not_missing");
      }
    }
    const projected = checkpointSnapshotOrRejection(request);
    if (projected.ok === false) return projected;
    const task = jsonObject(requireJsonObject(projected.basis, "basis").todo);
    const readScope = jsonObject(requireJsonObject(projected.basis, "basis").frontier)?.scope ?? request.decision_scope;
    if (request.checkpoint_continuation === true && use !== "delivery_result" &&
        request.full_frontier !== true) {
      return rejected("checkpoint_direction_context_invalid");
    }
    if (use === "first_delivery" && request.checkpoint_continuation === true &&
        !(task?.status === "done" && [identity.effect_id, identity.turn_instance_id].includes(task.completion_turn_key))) {
      return {...rejected("checkpoint_result_not_committed"), reread_required: false};
    }
    if (use === "delivery_result" && task?.status === "done" &&
        [identity.effect_id, identity.turn_instance_id].includes(task.completion_turn_key)) {
      return {...rejected("delivery_result_already_committed"), reread_required: false,
        original_read_context_id: jsonObject(request.receipt)?.read_context_id ?? null,
        error: "This Turn's result is already committed. Keep its original read identity for replay and read first_delivery for the pending direction."};
    }
    const prior = jsonObject(request.prior);
    const ownerReview = jsonObject(projected.basis)?.goal as JsonObject;
    const previousGoal = jsonObject(jsonObject(request.receipt)?.versions)?.goal;
    const reviewAuthority = ownerReview.primary_documents !== undefined ||
      (previousGoal !== undefined && previousGoal !== jsonObject(projected.versions)?.goal);
    const originalOutcome = prior === null ? null : Object.fromEntries([
      "classification", "delivery_outcome", "delivery_batch_scale", "recommended_action", "vision_checkpoint",
      "autonomous_replan_ack", "settlement_identity", "progress_observation"].filter(key => prior[key] !== undefined)
      .map(key => [key, prior[key]]));
    const previousReceipt = jsonObject(request.receipt);
    const previousPurpose = checkpointReadReceiptPurpose(previousReceipt);
    const previousResponse = request.checkpoint_continuation === true && use !== "delivery_result" &&
      previousReceipt && canonicalAuthoritySha256(previousReceipt.identity ?? null) === canonicalAuthoritySha256(identity)
      ? retireDirectionResponse(previousReceipt) : null;
    return {ok: true, ...projected, ...(reviewAuthority ? {instructions: checkpointAuthorityInstructions} : {}),
      ...(originalOutcome ? {original_outcome: originalOutcome} : {}),
      ...(use !== "delivery_result" ? {vision_authoring: visionAuthoringContract()} : {}), receipt: {
      schema_version: RECEIPT_SCHEMA, purpose: use,
      ...(use !== "supplement_checkpoint" ? {decision_scope: readScope} : {}),
      read_context_id: requireNonEmptyString(token, "read_context_id"),
      ...(request.full_frontier === true ? {full_frontier: true, decision_scope: readScope} : {}),
      identity, dependency_todo_ids: ids(request.dependency_todo_ids, "dependency_todo_ids"),
      versions: projected.versions, provider_revision: projected.provider_revision,
      ...(use === "delivery_result" ? {work_basis: projected.basis} : {}),
      ...(request.checkpoint_continuation === true && use !== "delivery_result" ? {decision_basis: projected.basis} : {}),
      ...(request.checkpoint_continuation === true && use !== "delivery_result" ? {vision_authoring: visionAuthoringContract()} : {}),
      ...(request.checkpoint_continuation === true && use !== "delivery_result" && originalOutcome ? {original_outcome: originalOutcome} : {}),
      ...(request.checkpoint_continuation === true && use !== "delivery_result" &&
        canonicalAuthoritySha256(previousReceipt?.identity ?? null) === canonicalAuthoritySha256(identity) ?
        Object.fromEntries(["direction_host_attempt_count", "direction_host_failure", "direction_stopped", "direction_stops",
          ...(previousPurpose === "first_delivery" || previousPurpose === "supplement_checkpoint"
            ? ["direction_host_prepared_attempt", "direction_host_returned_attempt"] : [])]
          .filter(key => previousReceipt?.[key] !== undefined)
          .map(key => [key, previousReceipt?.[key]])) : {}),
      ...(use === "delivery_result" && request.result_only === true ? {result_only: true} : {}),
      ...(previousResponse ? {prior_direction_response: previousResponse} : {}),
    }};
  }
  if (request.phase !== "check") throw new EffectRuntimeRequestError("unknown checkpoint read context phase");
  if (typeof token !== "string" || !token.trim()) return rejected("checkpoint_read_context_required");
  const receipt = jsonObject(request.receipt);
  if (request.checkpoint_continuation === true && use !== "delivery_result" &&
      (receipt?.full_frontier !== true || !["agent_lane", "goal"].includes(String(receipt.decision_scope)))) {
    return rejected("checkpoint_direction_context_invalid");
  }
  if (receipt === null || checkpointReadReceiptPurpose(receipt) !== use ||
      receipt.read_context_id !== token ||
      (use !== "supplement_checkpoint" && receipt.purpose !== use)) {
    return rejected("checkpoint_read_context_unknown_or_replaced");
  }
  if (canonicalAuthoritySha256(receipt.identity) !== canonicalAuthoritySha256(identity)) {
    return rejected("checkpoint_read_context_identity_mismatch");
  }
  if (use === "first_delivery" && request.checkpoint_continuation !== true && request.decision_scope != null &&
      request.decision_scope !== receipt.decision_scope) {
    return rejected("checkpoint_read_context_scope_mismatch");
  }
  if (use === "first_delivery" && receipt.commit_attempt != null) {
    return {...rejected("checkpoint_commit_unknown"), reread_required: false,
      error: "The original direction append is uncertain; read back its original Turn before retrying."};
  }
  const projected = checkpointSnapshotOrRejection({...request, dependency_todo_ids: receipt.dependency_todo_ids,
    ...(receipt.full_frontier === true ? {full_frontier: true, decision_scope: receipt.decision_scope} : {}),
    ...(use === "first_delivery" ? {decision_scope: receipt.decision_scope} : {})});
  if (projected.ok === false) return projected;
  const basis = requireJsonObject(projected.basis, "basis");
  const committedTask = jsonObject(basis.todo);
  if (use === "delivery_result" && receipt.result_only === true && committedTask?.status === "done" &&
      [identity.effect_id, identity.turn_instance_id].includes(committedTask.completion_turn_key)) {
    return {ok: true, read_context_id: token, result_committed: true};
  }
  const expected = requireJsonObject(receipt.versions, "receipt versions");
  const current = requireJsonObject(projected.versions, "current versions");
  const changed = Object.keys(current).filter(key => current[key] !== expected[key]);
  // A previously qualified wait still needs rechecking if the source reverted.
  const checkBlockedWait = request.blocked_wait != null && (changed.length > 0 || request.require_blocked_wait === true);
  if (checkBlockedWait
    ? !currentBlockedWaitMatches(request, identity, receipt, projected, changed)
    : changed.length > 0) {
    if (use === "delivery_result" && receipt.result_only === true && receipt.commit_attempt == null &&
        request.blocked_wait == null && jsonObject(basis.source)?.goal_ref == null &&
        changed.length === 2 && changed.includes("todo") && changed.includes("execution_lease") &&
        committedTask !== null && releasedWaitForOriginalTodo(identity, committedTask, jsonObject(basis.execution_lease))) {
      return {...rejected("checkpoint_read_context_stale", changed),
        retry_args: ["--delivery-boundary", "semantic_closeout"],
        remove_flags: ["--agent-vision-json", "--vision-state", "--vision-summary", "--vision-role-scope",
          "--vision-acceptance", "--vision-advancement-policy", "--vision-replan-trigger", "--vision-dreaming-policy",
          "--vision-last-patch", "--vision-todo-delta", "--vision-unchanged-reason"],
        instructions: "Execute read_command for this same Turn and revalidate the existing result and its evidence against the deferred basis. " +
          "If verified, retain only the result with --delivery-boundary semantic_closeout and retry_args. " +
          "Replace earlier values for those flags. Omit the old Vision and unchanged reason using remove_flags. " +
          "Use the original Turn's post-result direction actions returned by the writeback to judge the current wait before any debit. " +
          "If the result cannot be verified, preserve the wait without spending."};
    }
    return rejected("checkpoint_read_context_stale", changed);
  }
  return {ok: true, read_context_id: token, versions: changed.length ? expected : current,
    ...(checkBlockedWait && request.require_blocked_wait !== true ? {blocked_wait_validated: true} : {}),
    ...(request.checkpoint_continuation === true && use === "supplement_checkpoint" &&
      receipt.retained_result_binding !== undefined ? {retained_result_binding: receipt.retained_result_binding} : {}),
    ...(use !== "supplement_checkpoint" ? {purpose: use, decision_scope: receipt.decision_scope} : {})};
}

/** Work input and validation share the existing read-receipt identity owner. */
// Existing Host result vocabulary, local to direction transition/reentry rules.
const temporaryDirectionResults = new Set(["wait", "user_action_required", "iteration_failed"]);

function progressValidationBasis(basis: JsonObject): JsonObject {
  const todo = requireJsonObject(basis.todo, "current progress Todo");
  const declaration = normalizeTodoCompletionValidationDeclaration(todo);
  if (!declaration.ok) throw new EffectRuntimeRequestError(declaration.summary, "delivery_validation_binding_mismatch");
  const acceptance = jsonObject(jsonObject(basis.goal)?.acceptance);
  return {progress_validation: openTodoProgressValidationContract(),
    todo_completion: {required: todo.completion_validation_required === true,
      declaration_sha256: todo.completion_validation_sha256 ?? null,
      revision: todo.completion_validation_revision ?? null,
      inline_declaration_sha256: canonicalAuthoritySha256(declaration.value)},
    goal_acceptance: {revision: acceptance?.revision ?? null, contract_digest: acceptance?.contract_digest ?? null,
      coverage_sha256: canonicalAuthoritySha256(acceptance?.contract ?? null)}};
}

function releasedWaitForOriginalTodo(identity: JsonObject, todo: JsonObject, lease: JsonObject | null): boolean {
  return todo.status === "deferred" && todo.role === "agent" &&
    (todo.archive_state == null || todo.archive_state === "active") &&
    todo.todo_id === identity.todo_id && todo.claimed_by === identity.agent_id &&
    claimAllowsAgent({claim: String(todo.claimed_by), excluded: ids(todo.excluded_agents, "excluded_agents")},
      String(identity.agent_id)) &&
    typeof todo.resume_when === "string" && normalizeTodoResumeWhen({
      schema_version: TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION, resume_when: todo.resume_when,
    }) === todo.resume_when &&
    lease?.status === "released" && lease.active === false && lease.owner === identity.agent_id &&
    lease.goal_id === identity.goal_id && lease.todo_id === identity.todo_id &&
    typeof lease.lease_epoch === "number" && Number.isSafeInteger(lease.lease_epoch) && lease.lease_epoch > 0 &&
    typeof lease.idempotency_key === "string" && lease.idempotency_key.length > 0 &&
    typeof lease.released_at === "string" && parseIsoTimestamp(lease.released_at) !== null &&
    lease.released_at === todo.updated_at;
}

/** The original active lease may end with a timed wait after the result is retained. */
function retainedResultPrecedesTimedWait(identity: JsonObject, original: JsonObject, basis: JsonObject,
  todo: JsonObject, lease: JsonObject, writeback: JsonObject): boolean {
  const originalTodo = jsonObject(original.todo), originalLease = jsonObject(original.execution_lease);
  const wait = String(todo.resume_when);
  const due = wait.startsWith("resume_at:") ? parseIsoTimestamp(wait.slice("resume_at:".length)) : null;
  const retainedAt = typeof writeback.generated_at === "string" ? parseIsoTimestamp(writeback.generated_at) : null;
  const releasedAt = typeof lease.released_at === "string" ? parseIsoTimestamp(lease.released_at) : null;
  if (originalTodo === null || originalLease === null || originalLease.status !== "active" ||
      originalTodo.claimed_by !== identity.agent_id || originalTodo.done !== false ||
      originalTodo.resume_when != null || todo.done !== true ||
      !inFlightWorkAuthorized(original, identity) || due === null || due.valueOf() <= Date.now() ||
      retainedAt === null || releasedAt === null || retainedAt > releasedAt) return false;

  const {status: _status, active: _active, released_at: _releasedAt, ...releasedExecution} = lease;
  const {status: _originalStatus, active: _originalActive, ...originalExecution} = originalLease;
  if (canonicalAuthoritySha256(releasedExecution) !== canonicalAuthoritySha256(originalExecution)) return false;
  const stableTodo = (value: JsonObject) => Object.fromEntries(Object.entries(value)
    .filter(([key]) => !["status", "done", "resume_when", "reason", "updated_at"].includes(key)));
  if (canonicalAuthoritySha256(stableTodo(todo)) !== canonicalAuthoritySha256(stableTodo(originalTodo))) return false;
  const {todo: _oldTodo, execution_lease: _oldLease, ...oldBasis} = original;
  const {todo: _todo, execution_lease: _lease, ...currentBasis} = basis;
  return canonicalAuthoritySha256(oldBasis) === canonicalAuthoritySha256(currentBasis);
}

/** Authorize a fresh direction using the exact retained result and a proven wait. */
function releasedWaitAuthorizesDirection(request: JsonObject, identity: JsonObject, work: JsonObject,
  retained: JsonObject, original: JsonObject, basis: JsonObject, todo: JsonObject, lease: JsonObject | null,
): boolean {
  const writeback = jsonObject(request.result_writeback);
  const checkpoint = jsonObject(writeback?.vision_checkpoint);
  return work.result_only === true && lease !== null && releasedWaitForOriginalTodo(identity, todo, lease) &&
    writeback?.delivery_outcome === "outcome_progress" && checkpoint?.delivery_boundary === "semantic_closeout" &&
    checkpoint.decision === "missing_required" && checkpoint.required === true && checkpoint.satisfied === false &&
    canonicalAuthoritySha256(writeback) === retained.index_record_sha256 &&
    canonicalAuthoritySha256(writeback.settlement_identity) === canonicalAuthoritySha256(identity) &&
    canonicalAuthoritySha256(writeback.result_work_context) === canonicalAuthoritySha256(retained.result_work_context) &&
    ((canonicalAuthoritySha256(todo) === canonicalAuthoritySha256(original.todo) &&
      canonicalAuthoritySha256(lease) === canonicalAuthoritySha256(original.execution_lease)) ||
      retainedResultPrecedesTimedWait(identity, original, basis, todo, lease, writeback));
}

function currentProgressQualification(request: JsonObject): JsonObject {
  const identity = requireJsonObject(request.identity, "progress identity");
  const work = requireJsonObject(request.receipt, "original work receipt");
  const retained = requireJsonObject(request.retained_result_binding, "retained result binding");
  const direction = requireJsonObject(request.direction_receipt, "current direction receipt");
  const current = requireJsonObject(request.current_work, "current work snapshot");
  const restored = evaluateResultWorkBinding({phase: "restore", identity, receipt: work});
  if (restored.ok !== true || canonicalAuthoritySha256(work) !== retained.work_receipt_sha256 ||
      canonicalAuthoritySha256(retained.settlement_identity) !== canonicalAuthoritySha256(identity) ||
      canonicalAuthoritySha256(direction.identity) !== canonicalAuthoritySha256(identity) ||
      !checkpointBasisIntact(direction, jsonObject(direction.decision_basis))) {
    throw new EffectRuntimeRequestError("Current progress does not bind its original R/work and direction", "checkpoint_direction_binding_mismatch");
  }
  const original = requireJsonObject(restored.basis, "original work basis");
  const basis = requireJsonObject(current.basis, "current work basis");
  const todo = requireJsonObject(basis.todo, "current progress Todo");
  const originalLease = jsonObject(original.execution_lease), lease = jsonObject(basis.execution_lease);
  const committedResult = isCommittedResultContext(retained.result_work_context);
  const authorized = committedResult
    ? work.result_only === true && todo.todo_id === identity.todo_id && todo.status === "done" &&
      todo.claimed_by === identity.agent_id &&
      [identity.effect_id, identity.turn_instance_id].includes(todo.completion_turn_key) &&
      typeof todo.completion_receipt_id === "string" && todo.completion_receipt_id.length > 0
    : (inFlightWorkAuthorized(basis, identity) &&
      (originalLease === null || (lease !== null && lease.lease_epoch === originalLease.lease_epoch &&
        lease.idempotency_key === originalLease.idempotency_key))) ||
      releasedWaitAuthorizesDirection(request, identity, work, retained, original, basis, todo, lease);
  if (!authorized || canonicalAuthoritySha256(original.source) !== canonicalAuthoritySha256(basis.source)) {
    throw new EffectRuntimeRequestError("Original R work authority is unavailable; preserve R and recover its source/owner/lease", "checkpoint_direction_source_changed");
  }
  const originalVersions = requireJsonObject(work.versions, "original work versions");
  const versions = requireJsonObject(current.versions, "current work versions");
  const changed = [...new Set([...Object.keys(originalVersions), ...Object.keys(versions)])]
    .filter(key => originalVersions[key] !== versions[key]).sort();
  const validation = progressValidationBasis(basis), originalValidation = progressValidationBasis(original);
  const effect = jsonObject(work.terminal_validation_effect);
  const passed = effect?.schema_version === "terminal_validation_effect_v1" && effect.status === "passed" &&
    BARE_SHA256_PATTERN.test(String(effect.binding_sha256)) && jsonObject(effect.updates) !== null &&
    effect.updates_sha256 === canonicalAuthoritySha256(effect.updates);
  return {settlement_identity: identity, retained_result_binding_sha256: canonicalAuthoritySha256(retained),
    retained_result_artifacts_sha256: canonicalAuthoritySha256(request.retained_result_artifacts),
    direction_read_context_id: direction.read_context_id, direction_basis_sha256: canonicalAuthoritySha256(direction.decision_basis),
    original_work_versions: originalVersions, current_work_versions: versions, changed_work_components: changed,
    validation_contract: validation, validation_contract_sha256: canonicalAuthoritySha256(validation),
    original_validation_refs: {work_receipt_sha256: retained.work_receipt_sha256,
      terminal_effect_sha256: effect === null ? null : canonicalAuthoritySha256(effect)},
    original_validation_reusable: !committedResult && passed && changed.length === 0 &&
      canonicalAuthoritySha256(originalValidation) === canonicalAuthoritySha256(validation)};
}

/** Strip only the Agent judgment; the IO owner rechecks every base component. */
export function progressQualificationBasis(value: unknown): JsonObject | null {
  const proof = jsonObject(value);
  return proof === null ? null : Object.fromEntries(Object.entries(proof).filter(([key]) => key !== "judgment_sha256"));
}

export function evaluateResultWorkBinding(value: unknown): JsonObject {
  const request = requireJsonObject(value, "result work binding");
  if (request.phase === "progress_qualification") return {ok: true, qualification: currentProgressQualification(request)};
  if (request.phase === "completion_identity") {
    const supplied = requireJsonObject(request.identity, "completion identity");
    const identity = settlementIdentity({
      goal_id: requireNonEmptyString(request.goal_id, "goal_id"),
      agent_id: requireNonEmptyString(request.agent_id, "agent_id"),
      todo_id: requireNonEmptyString(request.todo_id, "todo_id"),
      turn_instance_id: requireNonEmptyString(supplied.turn_instance_id, "turn_instance_id"),
    });
    if (["goal_id", "agent_id", "todo_id", "turn_instance_id", "effect_id"].some(
      field => supplied[field] !== identity[field as keyof typeof identity],
    )) throw new EffectRuntimeRequestError("completion identity does not match the original scoped Turn");
    const todo = requireJsonObject(request.todo, "completion Todo");
    // Managed journals store the raw Turn id; interactive callers store its
    // scoped effect id. Preserve history only for this already committed result.
    const originalReplay = todo.status === "done" && todo.todo_id === identity.todo_id &&
      todo.claimed_by === identity.agent_id &&
      (todo.completion_turn_key === identity.turn_instance_id || todo.completion_turn_key === identity.effect_id);
    const key = originalReplay ? String(todo.completion_turn_key) : identity.effect_id;
    const candidate = jsonObject(request.candidate);
    const unchanged = candidate !== null &&
      // A done projection cannot prove a supplied lease matches the original
      // request. Keep that proof with the canonical receipt owner after release.
      candidate.task_lease_idempotency_key == null && candidate.task_lease_expected_version == null &&
      ["evidence", "note", "reason"].every(field => candidate[field] == null || candidate[field] === todo[field]) &&
      ["result_file", "next_agent_todo", "next_user_todo", "next_claimed_by", "next_task_class",
        "next_action_kind", "next_task_repository", "next_required_capabilities", "next_continuation_policy",
        "next_excluded_agents", "next_user_task_class", "decision_outcome"]
        .every(field => candidate[field] == null || candidate[field] === "") &&
      (candidate.successor_todo_ids == null || (Array.isArray(candidate.successor_todo_ids) && candidate.successor_todo_ids.length === 0)) &&
      candidate.clear_claim !== true && candidate.self_merged !== true &&
      (candidate.role == null || candidate.role === todo.role);
    if (originalReplay && unchanged &&
        typeof todo.completion_receipt_id === "string" && todo.completion_receipt_id.length > 0) {
      const fence = evaluateTodoCompletionFence(decodeTodoCompletionFenceRequest({
        schema_version: TODO_COMPLETION_FENCE_REQUEST_SCHEMA, projection_source: "materialized",
        todo, goal_id: identity.goal_id, todo_id: identity.todo_id,
        requested_no_followup: candidate.no_followup === true,
        requested_completion_turn_key: key, requested_completion_identity_source: "turn_settlement",
      }));
      if (fence.outcome === "replay") return {completion_turn_key: key, replay: {
        ok: true, completed: true, changed: false, idempotent_replay: true,
        dry_run: candidate.dry_run === true, goal_id: identity.goal_id, todo_id: identity.todo_id,
        status: "done", completion_continuation: fence.completion_continuation,
        completion_receipt_id: todo.completion_receipt_id ?? null, updated_at: todo.updated_at ?? null,
      }};
    }
    return {completion_turn_key: key, replay: null};
  }
  if (request.phase === "direction_terminal_intent") {
    const todo = requireJsonObject(request.todo, "completion Todo");
    const identity = requireJsonObject(request.identity, "completion identity");
    return {no_followup: originalTurnRequestsNoFollowup(todo, identity)};
  }
  if (request.phase === "replan_stage") {
    const receipt = jsonObject(request.heartbeat_receipt);
    const enabled = checkpointContinuationRequired(receipt);
    const pending = jsonObject(jsonObject(request.prior)?.vision_checkpoint)?.replan_stage === "awaiting_direction";
    // The semantic qualifier still checks the delta against the original admitted source.
    const boundAck = request.explicit_ack === true &&
      normalizeHeartbeatReplanObligationId(heartbeatReceiptDetails(receipt).semantic_replan_obligation_id) !== null;
    const deferred = enabled && request.has_read_context !== true && !boundAck &&
      (request.requested === true || pending);
    return {deferred, qualify: deferred ? false : request.requested === true || (enabled && pending)};
  }
  if (request.phase === "direction_dispatch") {
    const receipt = requireJsonObject(request.receipt, "direction receipt");
    const failure = receipt.direction_host_failure;
    let knownTemporaryStop = false;
    if (receipt.direction_stopped === true) {
      const stops = receipt.direction_stops;
      // Explicit recovery is not a fresh grant. Unknown historical stops and
      // actual Host Stop reasons stay closed; a read alone never rearms them.
      if (request.explicit_resume !== true || !Array.isArray(stops) || !stops.length ||
          stops.some(value => {
            const stop = jsonObject(value);
            return !stop || !Number.isInteger(stop.attempt) || Number(stop.attempt) < 1 ||
              Number(stop.attempt) > Number(receipt.direction_host_attempt_count) ||
              !temporaryDirectionResults.has(String(stop.result_kind)) ||
              Object.hasOwn(stop, "stop_reason");
          })) return {...rejected("checkpoint_direction_stopped"), reread_required: false,
        error: "checkpoint_direction_stopped: The original result is retained. Inspect the stop record; " +
          "a temporary stop can proceed only through the original Host's explicit recovery."};
      knownTemporaryStop = stops.some(value => jsonObject(value)?.attempt === receipt.direction_host_attempt_count);
    }
    if (failure !== undefined) {
      const policy = hostRetryPolicyCheck({host_failure: failure,
        host_attempt_count: receipt.direction_host_attempt_count});
      if (policy?.outcome === "failed") return rejected(String(policy.reason));
    }
    const count = receipt.direction_host_attempt_count ?? 0;
    if (!Number.isInteger(count) || Number(count) < 0) return rejected("checkpoint_direction_attempt_invalid");
    const readPurpose = checkpointReadReceiptPurpose(receipt);
    if (Number(count) > 0 &&
        (readPurpose === "first_delivery" || readPurpose === "supplement_checkpoint") &&
        receipt.direction_host_prepared_attempt === count) {
      return request.explicit_resume === true && receipt.direction_host_returned_attempt !== count &&
        !knownTemporaryStop && failure === undefined && receipt.current_agent_decision == null
        ? {ok: true, attempt: count, reuse_prepared: true} : unknownDirectionLaunch();
    }
    const returned = verifiedPriorDirectionResponse(receipt);
    const knownPriorReturn = returned?.attempt === count && returned.read_context_id !== receipt.read_context_id;
    const knownUnboundReturn = request.explicit_resume === true && receipt.direction_host_returned_attempt === count;
    if (Number(count) > 0 && readPurpose !== null && !knownPriorReturn && !knownUnboundReturn &&
        failure === undefined && !knownTemporaryStop && receipt.current_agent_decision == null) {
      return unknownDirectionLaunch();
    }
    return {ok: true, attempt: Number(count) + 1};
  }
  if (request.phase === "direction_launch_context") {
    const receipt = requireJsonObject(request.receipt, "direction receipt");
    const work = jsonObject(request.work_receipt);
    // Historical/feature-off callers keep their original raw launch witness.
    // No new source semantics are inferred for an older work receipt.
    if (receipt.schema_version !== RECEIPT_SCHEMA || work?.schema_version !== RECEIPT_SCHEMA) {
      return {ok: true, refresh_launch_witness: false};
    }
    if (!["first_delivery", "supplement_checkpoint"].includes(String(checkpointReadReceiptPurpose(receipt))) ||
        receipt.full_frontier !== true || !["agent_lane", "goal"].includes(String(receipt.decision_scope)) ||
        canonicalAuthoritySha256(receipt.identity) !== canonicalAuthoritySha256(request.identity)) {
      return rejected("checkpoint_direction_context_invalid");
    }
    if (!checkpointBasisIntact(receipt, jsonObject(receipt.decision_basis))) {
      return rejected("checkpoint_direction_basis_corrupt");
    }
    const original = evaluateResultWorkBinding({...request, phase: "restore", receipt: work});
    if (original.ok !== true) return original;
    if (Object.hasOwn(request, "retained_result_content")) {
      const content = jsonObject(request.retained_result_content);
      const digests = jsonObject(jsonObject(receipt.retained_result_binding)?.result_artifact_digests);
      if (!content || typeof content.json_text !== "string" || typeof content.markdown !== "string"
          || createHash("sha256").update(content.json_text).digest("hex") !== digests?.json_sha256
          || createHash("sha256").update(content.markdown).digest("hex") !== digests?.markdown_sha256) {
        return rejected("checkpoint_direction_binding_mismatch");
      }
    }
    // Rebinding a launch witness cannot move the original source, store,
    // registry route, actor configuration or unknown source facts. Both v3
    // receipts already carry their own semantic source interpretation.
    if (canonicalAuthoritySha256(jsonObject(original.basis)?.source) !==
        canonicalAuthoritySha256(jsonObject(receipt.decision_basis)?.source)) {
      return {...rejected("checkpoint_direction_source_changed"), reread_required: false,
        error: "checkpoint_direction_source_changed: The retained result and direction have different semantic sources. Reconcile the original source, store and route " +
          "through their owner before continuing; a new read or launch witness cannot adopt a replacement source."};
    }
    return {ok: true, refresh_launch_witness: true};
  }
  if (request.phase === "direction_launch_failure") {
    const receipt = {...requireJsonObject(request.receipt, "direction receipt")};
    const attempt = receipt.direction_host_attempt_count;
    const code = requireNonEmptyString(request.error_code, "direction launch refusal code");
    if (request.not_started !== true || !Number.isInteger(attempt) || Number(attempt) < 1 ||
        !["first_delivery", "supplement_checkpoint"].includes(String(checkpointReadReceiptPurpose(receipt)))) {
      return unknownDirectionLaunch();
    }
    // Only explicit pre-launch source refusals can be re-judged on an explicit
    // same-Turn resume. Other refusals retain a hard stop; no prose guessing.
    const recoverable = ["stale_goal_authority", "checkpoint_read_context_stale",
      "checkpoint_read_context_unknown_or_replaced"].includes(code);
    receipt.direction_stopped = true;
    receipt.direction_stops = [...(Array.isArray(receipt.direction_stops) ? receipt.direction_stops : []), {
      attempt, result_kind: "iteration_failed", launch_refusal_code: code,
      ...(recoverable ? {} : {stop_reason: code}),
    }];
    delete receipt.direction_host_failure;
    return {ok: true, receipt};
  }
  if (request.phase === "direction_admission") {
    const current = requireJsonObject(request.current, "current direction admission");
    const identity = requireJsonObject(request.identity, "original direction identity");
    const recovery = jsonObject(current.unsettled_host_turn_recovery);
    const eligible = current.ok === true && current.should_run === true && current.status_health_ok === true &&
      current.goal_id === identity.goal_id && current.requires_user_action === false &&
      current.blocked_action_scope == null && jsonObject(current.quota)?.state === "eligible";
    const sameRecovery = current.effective_action === EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY &&
      recovery?.scope === "current_turn" && recovery.repair === "durable_writeback" &&
      recovery.turn_instance_id === identity.turn_instance_id && recovery.binding_kind === "todo" &&
      recovery.binding_id === identity.todo_id;
    const sameWork = current.effective_action === request.planned_effective_action &&
      jsonObject(current.selected_todo)?.todo_id === identity.todo_id &&
      jsonObject(jsonObject(current.interaction_contract)?.agent_channel)?.delivery_allowed === true;
    return eligible && (sameRecovery || sameWork) ? {ok: true} : rejected("checkpoint_direction_admission_rejected");
  }
  if (request.phase === "direction_return") {
    const receipt = {...requireJsonObject(request.receipt, "direction receipt")};
    const decision = requireJsonObject(request.decision, "direction decision");
    const attempt = receipt.direction_host_attempt_count;
    if (!Number.isInteger(attempt) || Number(attempt) < 1) return rejected("checkpoint_direction_attempt_invalid");
    delete receipt.direction_host_returned_attempt;
    if (temporaryDirectionResults.has(String(decision.result_kind))) {
      delete receipt.direction_host_failure;
      receipt.direction_stopped = true;
      receipt.direction_stops = [...(Array.isArray(receipt.direction_stops) ? receipt.direction_stops : []), {
        attempt, result_kind: decision.result_kind,
        ...(decision.stop_reason ? {stop_reason: decision.stop_reason} : {}),
      }];
      return {ok: true, stopped: true, receipt};
    }
    const bound = evaluateResultWorkBinding({...request, phase: "direction_response"});
    if (bound.ok !== true) return bound;
    receipt.current_agent_decision = decision;
    receipt.current_agent_binding = bound.binding;
    if (bound.current_progress_qualification !== undefined) receipt.current_progress_qualification = bound.current_progress_qualification;
    delete receipt.direction_host_failure;
    delete receipt.direction_stopped;
    return {ok: true, stopped: false, receipt};
  }
  if (request.phase === "direction_response") {
    const receipt = requireJsonObject(request.receipt, "direction receipt");
    const decision = requireJsonObject(request.decision, "direction decision");
    const purpose = receipt.purpose ?? "supplement_checkpoint";
    if (!["first_delivery", "supplement_checkpoint"].includes(String(purpose)) ||
        checkpointReadReceiptPurpose(receipt) !== purpose ||
        receipt.full_frontier !== true || !["agent_lane", "goal"].includes(String(receipt.decision_scope))) {
      return rejected("checkpoint_direction_context_invalid");
    }
    if (receipt.read_context_id !== decision.read_context_id ||
        canonicalAuthoritySha256(receipt.identity) !== canonicalAuthoritySha256(request.identity)) {
      return rejected("checkpoint_direction_binding_mismatch");
    }
    if (!checkpointBasisIntact(receipt, jsonObject(receipt.decision_basis))) {
      return rejected("checkpoint_direction_basis_corrupt");
    }
    if (jsonObject(requireJsonObject(receipt.decision_basis, "direction basis").frontier)?.scope !== receipt.decision_scope) {
      return rejected("checkpoint_direction_context_invalid");
    }
    const vision = jsonObject(decision.agent_vision);
    const unchanged = typeof decision.vision_unchanged_reason === "string" ? decision.vision_unchanged_reason.trim() : "";
    if ((vision !== null) === (unchanged.length > 0)) return rejected("checkpoint_direction_decision_required");
    if (vision === null && jsonObject(requireJsonObject(receipt.decision_basis, "direction basis").agent_vision) === null) {
      return rejected("checkpoint_direction_baseline_required");
    }
    const retained = jsonObject(receipt.retained_result_binding);
    if (receipt.retained_result_binding !== undefined && (retained === null ||
        canonicalAuthoritySha256(retained.settlement_identity) !== canonicalAuthoritySha256(request.identity))) {
      return rejected("checkpoint_direction_binding_mismatch");
    }
    let qualification: JsonObject | null = null;
    if (retained?.result_work_receipt_sha256 !== undefined || receipt.current_progress_qualification !== undefined) {
      const proof = progressQualificationBasis(receipt.current_progress_qualification);
      if (proof === null || proof.direction_read_context_id !== receipt.read_context_id ||
          proof.retained_result_binding_sha256 !== canonicalAuthoritySha256(retained) ||
          proof.retained_result_artifacts_sha256 !== canonicalAuthoritySha256(receipt.retained_result_artifacts) ||
          proof.direction_basis_sha256 !== canonicalAuthoritySha256(receipt.decision_basis) ||
          proof.validation_contract_sha256 !== canonicalAuthoritySha256(proof.validation_contract)) {
        return rejected("checkpoint_direction_binding_mismatch");
      }
      qualification = {...proof, judgment_sha256: canonicalAuthoritySha256({read_context_id: receipt.read_context_id,
        vision: decision.agent_vision ?? null, unchanged_reason: decision.vision_unchanged_reason ?? null})};
    }
    return {ok: true, binding: {read_context_id: receipt.read_context_id,
      identity_sha256: canonicalAuthoritySha256(request.identity), decision_sha256: canonicalAuthoritySha256(decision),
      ...(retained ? {retained_result_binding_sha256: canonicalAuthoritySha256(retained)} : {}),
      ...(qualification ? {current_progress_qualification_sha256: canonicalAuthoritySha256(qualification)} : {})},
      ...(qualification ? {current_progress_qualification: qualification} : {})};
  }
  if (request.phase === "direction_policy") return {
    required: checkpointContinuationRequired(jsonObject(request.heartbeat_receipt)),
  };
  if (request.phase === "legacy_commit_policy") {
    const transaction = requireJsonObject(request.transaction, "locked completion transaction");
    if (transaction.schema_version !== "loopx_todo_completion_transaction_result_v0") {
      throw new EffectRuntimeRequestError("Locked completion transaction schema mismatch");
    }
    const fence = requireJsonObject(transaction.fence, "locked completion fence");
    const state = jsonObject(transaction.completion_state);
    const upgrade = transaction.decision === "commit" && fence.status === "done" &&
      ["same_turn_terminal_upgrade", "lifecycle_reentry_terminal_upgrade"].includes(String(fence.reason)) &&
      state?.continuation === "no_followup" && typeof transaction.completion_identity_key === "string" &&
      transaction.completion_identity_key.length > 0;
    if (upgrade) {
      const todo = requireJsonObject(request.todo, "original committed Todo");
      const candidate = requireJsonObject(request.candidate, "Legacy closeout candidate");
      for (const field of ["evidence", "note", "reason"] as const) {
        if (candidate[field] != null && candidate[field] !== todo[field]) {
          return rejected("delivery_validation_binding_mismatch");
        }
      }
    }
    return {required: !upgrade, recover_terminal_lease: upgrade};
  }
  if (request.phase === "legacy_admission") {
    const events = Array.isArray(request.events) ? request.events.map(event => requireJsonObject(event, "admission event")) : [];
    const scopedEvents = events.filter(event => event.event_kind === "quota_should_run" &&
      canonicalAuthoritySha256(event.goal_ref ?? null) === canonicalAuthoritySha256(request.goal_ref ?? null) &&
      jsonObject(event.details)?.todo_id === request.todo_id);
    const original = scopedEvents.filter(event => event.agent_id === request.agent_id &&
      [event.run_id, jsonObject(event.details)?.settlement_effect_id].includes(request.completion_turn_key));
    if (original.length && !original.some(resultFreshnessRequired)) return {required: false};
    const protectedEvents = scopedEvents.filter(resultFreshnessRequired);
    if (!protectedEvents.length) return {required: false};
    const match = protectedEvents.filter(event => event.agent_id === request.agent_id &&
      [event.run_id, jsonObject(event.details)?.settlement_effect_id].includes(request.completion_turn_key));
    // Re-reading an admitted Turn records another observation, not a new
    // admission identity. Every matching observation must retain that Turn.
    if (match.length === 0 || request.identity == null ||
        match.some(event => jsonObject(request.identity)?.turn_instance_id !== event.run_id) ||
        jsonObject(request.identity)?.agent_id !== match[0].agent_id ||
        jsonObject(request.identity)?.todo_id !== request.todo_id) return rejected("delivery_work_context_required");
    return {required: true};
  }
  if (request.phase === "source_recovery_admission") {
    const receipt = jsonObject(request.heartbeat_receipt);
    return resultFreshnessRequired(receipt) || checkpointContinuationRequired(receipt)
      ? {ok: true}
      : {...rejected("checkpoint_first_delivery_not_admitted"), reread_required: false};
  }
  if (request.phase === "policy") return {required: resultFreshnessRequired(jsonObject(request.heartbeat_receipt))};
  if (request.phase === "admission") {
    if (request.todo_id == null) return {policy: null};
    if (!["legacy_markdown", "file_v0", "sqlite_v0", "postgresql_v0"].includes(String(request.source_authority))) {
      throw new EffectRuntimeRequestError("Result freshness requires a supported Todo authority", "delivery_provider_unsupported");
    }
    if (request.goal_ref != null) {
      if (parseExactGoalRef(request.goal_ref).kind !== "parsed") {
        throw new EffectRuntimeRequestError("Result freshness admission requires an exact GoalRef");
      }
      // Scoped work keeps its existing lifecycle; historical marker readers
      // still require their original policy. This is only new admission.
      return {policy: null};
    }
    return {policy: "turn_result_freshness_required_v1"};
  }
  const identity = requireJsonObject(request.identity, "result work identity");
  const receipt = requireJsonObject(request.receipt, "result work receipt");
  if (checkpointReadReceiptPurpose(receipt) !== "delivery_result" ||
      canonicalAuthoritySha256(receipt.identity) !== canonicalAuthoritySha256(identity)) {
    return rejected("delivery_work_identity_mismatch");
  }
  const basis = requireJsonObject(receipt.work_basis, "persisted work basis");
  if (Object.hasOwn(request, "goal_ref") &&
      canonicalAuthoritySha256(jsonObject(basis.source)?.goal_ref ?? null) !== canonicalAuthoritySha256(request.goal_ref)) {
    return rejected("delivery_work_goal_instance_mismatch");
  }
  const versions = requireJsonObject(receipt.versions, "persisted work versions");
  if (Object.keys(basis).some(key => canonicalAuthoritySha256(basis[key]) !== versions[key]) ||
      Object.keys(versions).some(key => !Object.hasOwn(basis, key))) {
    return rejected("delivery_work_basis_corrupt");
  }
  const readId = requireNonEmptyString(receipt.read_context_id, "persisted result read identity");
  if (request.phase === "begin_terminal_validation" || request.phase === "finish_terminal_validation") {
    return terminalValidationRecovery(request, receipt);
  }
  if (request.phase === "restore") return {ok: true, basis,
    ...(jsonObject(basis.goal)?.primary_documents !== undefined ? {instructions: checkpointAuthorityInstructions} : {}),
    read_context_id: readId, settlement_identity: identity};
  const terminal = jsonObject(request.terminal_input);
  const candidate = terminal ? terminalResultCandidate(terminal) : requireJsonObject(request.candidate, "result candidate");
  const candidateDigest = canonicalAuthoritySha256(candidate);
  if (request.phase === "bind_validation") {
    const validation = terminal ? terminalResultValidation(terminal) : requireJsonObject(request.validation, "result validation");
    if (validation.ok !== true) return rejected("delivery_validation_not_passed");
    return {ok: true, binding: {schema_version: "result_validation_binding_v1", identity,
      read_context_id: receipt.read_context_id, candidate_sha256: candidateDigest,
      validation_sha256: canonicalAuthoritySha256(validation)}};
  }
  if (request.phase !== "check_validation") throw new EffectRuntimeRequestError("unknown result work binding phase");
  const binding = requireJsonObject(request.binding, "result validation binding");
  if (binding.schema_version !== "result_validation_binding_v1" ||
      canonicalAuthoritySha256(binding.identity) !== canonicalAuthoritySha256(identity) ||
      binding.read_context_id !== receipt.read_context_id || binding.candidate_sha256 !== candidateDigest ||
      binding.validation_sha256 !== canonicalAuthoritySha256(terminal ? terminalResultValidation(terminal) : request.validation)) {
    return rejected("delivery_validation_binding_mismatch");
  }
  return {ok: true, read_context_id: receipt.read_context_id};
}

/** Bind the actual result bytes/workspace and issued native validation contract. */
export function terminalResultCandidate(input: JsonObject): JsonObject {
  return {result: input.completion_result ?? null,
    evidence: input.evidence ?? null, note: input.note ?? null};
}
export function terminalResultValidation(input: JsonObject): JsonObject {
  return {ok: true, declaration: input.validation_declaration ?? null,
    declaration_sha256: input.validation_declaration_sha256 ?? null,
    receipt: input.validation_receipt ?? null,
    acceptance_source: input.goal_acceptance_source_binding ?? null,
    acceptance_receipts: input.goal_acceptance_validation_receipts ?? null};
}

/** Runtime admission for Legacy closeout shares the original quota receipt owner. */
export async function executeResultWorkBinding(value: unknown,
  dependencies: LocalAuthorityProviderDependencies = {},
): Promise<JsonObject> {
  let request = requireJsonObject(value, "result work request");
  if (request.phase === "recover") {
    const {recoverOriginalWorkReceipt} = await import("./checkpoint_work_receipt.ts");
    return recoverOriginalWorkReceipt(request, dependencies);
  }
  if (request.phase === "admission" && Object.hasOwn(request, "runtime_root")) {
    // Reuse the complete source owner, but keep its Todo body inside this
    // runtime. Admission consumes only the validated authority and policy.
    const {readCanonicalSnapshot} = await import("../coordination/canonical_snapshot_page.ts");
    const source = await readCanonicalSnapshot({
      schema_version: LOCAL_COORDINATION_TODO_SNAPSHOT_PAGE_REQUEST_SCHEMA,
      runtime_root: request.runtime_root, goal_id: request.goal_id,
      include_leases: false, projection_readback: null, after: null,
    }, dependencies);
    if (source.status !== "page") {
      // Preserve the existing Python canonical-read exception adapter.
      return {source_failure: {schema_version: LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
        ...Object.fromEntries(["status", "reason_code", "reason", "source_authority",
          "decision_read_from_provider", "legacy_fallback_used", "goal_id", "operation",
          "write_admitted", "recovery"].filter(key => Object.hasOwn(source, key))
          .map(key => [key, source[key]]))}};
    }
    request = {...request, source_authority: source.source_authority};
  }
  const result = evaluateResultWorkBinding(request);
  if (request.phase === "legacy_commit_policy" && result.recover_terminal_lease === true) {
    const {verifyReentrySettlement} = await import("../coordination/todo_terminal_reentry.ts");
    const {localTodoCompletionIdentity} = await import("../todos/completion_fence.ts");
    const goal = requireNonEmptyString(request.goal_id, "goal_id");
    const todo = requireNonEmptyString(request.todo_id, "todo_id");
    const key = requireNonEmptyString(requireJsonObject(request.transaction, "transaction").completion_identity_key, "completion identity");
    if (key !== localTodoCompletionIdentity(goal, todo)) {
      await verifyReentrySettlement(requireNonEmptyString(request.runtime_root, "runtime_root"), {
        goal_id: goal, todo_id: todo, actor_agent_id: requireNonEmptyString(request.agent_id, "agent_id"),
        requested_completion_turn_key: key,
      });
    }
  }
  return result;
}
