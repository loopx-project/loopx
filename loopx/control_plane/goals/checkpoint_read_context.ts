/** Read basis for a missing decision/qualification checkpoint, not an execution/permission lease.
 * The commit effect holds source/index claims and the real provider fence. */
import {settlementIdentity, type JsonObject} from "../effect_program.ts";
import {createHash} from "node:crypto";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {isUnqualifiedBlockedReplanWriteback} from "../quota/settlement_phase.ts";
import {claimAllowsAgent} from "../todos/agent_scope.ts";
import {resultFreshnessRequired, checkpointContinuationRequired} from "../quota/heartbeat_receipt_identity.ts";
import {visionAuthoringContract} from "./vision_checkpoint.ts";
import {hostRetryPolicyCheck} from "../turn_driver/turn_journal.ts";
import {originalTurnRequestsNoFollowup} from "../todos/completion_state.ts";
import {terminalValidationRecovery} from "../todos/completion_validation_plan.ts";
import {decodeTodoCompletionFenceRequest, evaluateTodoCompletionFence,
  TODO_COMPLETION_FENCE_REQUEST_SCHEMA} from "../todos/completion_fence.ts";

const checkpointAuthorityInstructions =
  "Current primary Goal documents govern requirements; plans, Todos and prior read contexts are derived records. " +
  "A changed Goal is not proof of projection corruption. Reconcile the candidate and derived work with the current requirements; " +
  "never restore an existing Goal document from an old plan or read context to make a result pass. " +
  "Conflicting primary documents require owner resolution. File write isolation belongs to the host; a read receipt grants no document-edit authority.";

const RECEIPT_SCHEMA = "checkpoint_read_context_v1";
const FIRST_RECEIPT_SCHEMA = "checkpoint_read_context_v2";
export type CheckpointReadPurpose = "supplement_checkpoint" | "first_delivery" | "delivery_result";
export type CheckpointDecisionScope = "agent_lane" | "goal";

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

function ids(value: unknown, label: string): string[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an array`);
  return [...new Set(value.map(item => requireNonEmptyString(item, label)))].sort();
}

function dependencies(todo: JsonObject): string[] {
  const result = ids(todo.depends_on_todo_ids, "depends_on_todo_ids");
  if (todo.depends_on_todo_id != null) result.push(requireNonEmptyString(todo.depends_on_todo_id, "depends_on_todo_id"));
  // These are typed resume tokens, never a search through task prose.
  if (typeof todo.resume_when === "string") {
    const match = /^(?:todo_done|monitor_changed):([^:]+)$/.exec(todo.resume_when);
    if (match) result.push(match[1]);
  }
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
  if (purpose(request) === "first_delivery" || request.full_frontier === true) {
    const scope = request.decision_scope;
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
    basis.frontier = {scope: request.decision_scope, todos: frontier};
  }
  if (purpose(request) !== "supplement_checkpoint") {
    basis.execution_lease = facts.execution_lease ?? null;
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
  ok: false, error_code: code, reread_required: true, changed_components: changed,
  error: `${code}: run checkpoint-context for the same Goal/Agent/Todo or obligation/Turn, ` +
    "using the original purpose. Recheck the candidate for delivery_result, or judge the direction for first_delivery/supplement_checkpoint. " +
    "Use the returned identity only with that new decision. Retain successful result and quota receipts.",
});

export function evaluateCheckpointReadContext(value: unknown): JsonObject {
  const request = requireJsonObject(value, "checkpoint read context");
  const identity = requireJsonObject(request.identity, "identity");
  const token = request.read_context_id;
  const use = purpose(request);
  if (request.phase === "read") {
    if (use === "delivery_result" &&
        jsonObject(jsonObject(request.receipt)?.terminal_validation_effect)?.status === "pending") {
      return {...rejected("delivery_validation_outcome_unknown"), reread_required: false,
        error: "Original validation may have executed; reconcile its outcome before replacing the work read."};
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
    if (request.checkpoint_continuation === true && use !== "delivery_result" &&
        (request.full_frontier !== true || request.decision_scope !== "goal")) {
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
    return {ok: true, ...projected, ...(reviewAuthority ? {instructions: checkpointAuthorityInstructions} : {}),
      ...(originalOutcome ? {original_outcome: originalOutcome} : {}),
      ...(use !== "delivery_result" ? {vision_authoring: visionAuthoringContract()} : {}), receipt: {
      schema_version: use !== "supplement_checkpoint" ? FIRST_RECEIPT_SCHEMA : RECEIPT_SCHEMA,
      ...(use !== "supplement_checkpoint" ? {purpose: use, decision_scope: request.decision_scope} : {}),
      read_context_id: requireNonEmptyString(token, "read_context_id"),
      ...(request.full_frontier === true ? {full_frontier: true, decision_scope: request.decision_scope} : {}),
      identity, dependency_todo_ids: ids(request.dependency_todo_ids, "dependency_todo_ids"),
      versions: projected.versions, provider_revision: projected.provider_revision,
      ...(use === "delivery_result" ? {work_basis: projected.basis} : {}),
      ...(request.checkpoint_continuation === true && use !== "delivery_result" ? {decision_basis: projected.basis} : {}),
      ...(request.checkpoint_continuation === true && use !== "delivery_result" ? {vision_authoring: visionAuthoringContract()} : {}),
      ...(request.checkpoint_continuation === true && use !== "delivery_result" && originalOutcome ? {original_outcome: originalOutcome} : {}),
      ...(request.checkpoint_continuation === true && use !== "delivery_result" &&
        canonicalAuthoritySha256(jsonObject(request.receipt)?.identity ?? null) === canonicalAuthoritySha256(identity) ?
        Object.fromEntries(["direction_host_attempt_count", "direction_host_failure", "direction_stopped"]
          .filter(key => jsonObject(request.receipt)?.[key] !== undefined)
          .map(key => [key, jsonObject(request.receipt)?.[key]])) : {}),
      ...(use === "delivery_result" && request.result_only === true ? {result_only: true} : {}),
    }};
  }
  if (request.phase !== "check") throw new EffectRuntimeRequestError("unknown checkpoint read context phase");
  if (typeof token !== "string" || !token.trim()) return rejected("checkpoint_read_context_required");
  const receipt = jsonObject(request.receipt);
  if (request.checkpoint_continuation === true && use !== "delivery_result" &&
      (receipt?.full_frontier !== true || receipt.decision_scope !== "goal")) {
    return rejected("checkpoint_direction_context_invalid");
  }
  if (receipt?.schema_version !== (use !== "supplement_checkpoint" ? FIRST_RECEIPT_SCHEMA : RECEIPT_SCHEMA) ||
      receipt.read_context_id !== token ||
      (use !== "supplement_checkpoint" && receipt.purpose !== use)) {
    return rejected("checkpoint_read_context_unknown_or_replaced");
  }
  if (canonicalAuthoritySha256(receipt.identity) !== canonicalAuthoritySha256(identity)) {
    return rejected("checkpoint_read_context_identity_mismatch");
  }
  if (use === "first_delivery" && request.decision_scope != null &&
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
  const committedTask = jsonObject(requireJsonObject(projected.basis, "basis").todo);
  if (use === "delivery_result" && receipt.result_only === true && committedTask?.status === "done" &&
      [identity.effect_id, identity.turn_instance_id].includes(committedTask.completion_turn_key)) {
    return {ok: true, read_context_id: token, result_committed: true};
  }
  const expected = requireJsonObject(receipt.versions, "receipt versions");
  const current = requireJsonObject(projected.versions, "current versions");
  const changed = Object.keys(current).filter(key => current[key] !== expected[key]);
  if (changed.length) return rejected("checkpoint_read_context_stale", changed);
  return {ok: true, read_context_id: token, versions: current,
    ...(use !== "supplement_checkpoint" ? {purpose: use, decision_scope: receipt.decision_scope} : {})};
}

/** Work input and validation share the existing read-receipt identity owner. */
export function evaluateResultWorkBinding(value: unknown): JsonObject {
  const request = requireJsonObject(value, "result work binding");
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
    const enabled = checkpointContinuationRequired(jsonObject(request.heartbeat_receipt));
    const pending = jsonObject(jsonObject(request.prior)?.vision_checkpoint)?.replan_stage === "awaiting_direction";
    const deferred = enabled && request.has_read_context !== true && (request.requested === true || pending);
    return {deferred, qualify: deferred ? false : request.requested === true || (enabled && pending)};
  }
  if (request.phase === "direction_dispatch") {
    const receipt = requireJsonObject(request.receipt, "direction receipt");
    const failure = receipt.direction_host_failure;
    if (receipt.direction_stopped === true) return rejected("checkpoint_direction_stopped");
    if (failure !== undefined) {
      const policy = hostRetryPolicyCheck({host_failure: failure,
        host_attempt_count: receipt.direction_host_attempt_count});
      if (policy?.outcome === "failed") return rejected(String(policy.reason));
    }
    const count = receipt.direction_host_attempt_count ?? 0;
    if (!Number.isInteger(count) || Number(count) < 0) return rejected("checkpoint_direction_attempt_invalid");
    return {ok: true, attempt: Number(count) + 1};
  }
  if (request.phase === "direction_response") {
    const receipt = requireJsonObject(request.receipt, "direction receipt");
    const decision = requireJsonObject(request.decision, "direction decision");
    const purpose = receipt.purpose ?? "supplement_checkpoint";
    if (!["first_delivery", "supplement_checkpoint"].includes(String(purpose)) ||
        receipt.schema_version !== (purpose === "first_delivery" ? FIRST_RECEIPT_SCHEMA : RECEIPT_SCHEMA) ||
        receipt.full_frontier !== true || receipt.decision_scope !== "goal") return rejected("checkpoint_direction_context_invalid");
    if (receipt.read_context_id !== decision.read_context_id ||
        canonicalAuthoritySha256(receipt.identity) !== canonicalAuthoritySha256(request.identity)) {
      return rejected("checkpoint_direction_binding_mismatch");
    }
    if (receipt.decision_basis == null || canonicalAuthoritySha256(receipt.versions) !==
        canonicalAuthoritySha256(Object.fromEntries(Object.entries(requireJsonObject(receipt.decision_basis, "direction basis"))
          .map(([key, value]) => [key, canonicalAuthoritySha256(value)])))) {
      return rejected("checkpoint_direction_basis_corrupt");
    }
    const vision = jsonObject(decision.agent_vision);
    const unchanged = typeof decision.vision_unchanged_reason === "string" ? decision.vision_unchanged_reason.trim() : "";
    if ((vision !== null) === (unchanged.length > 0)) return rejected("checkpoint_direction_decision_required");
    if (vision === null && jsonObject(requireJsonObject(receipt.decision_basis, "direction basis").agent_vision) === null) {
      return rejected("checkpoint_direction_baseline_required");
    }
    return {ok: true, binding: {read_context_id: receipt.read_context_id,
      identity_sha256: canonicalAuthoritySha256(request.identity), decision_sha256: canonicalAuthoritySha256(decision)}};
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
  if (request.phase === "policy") return {required: resultFreshnessRequired(jsonObject(request.heartbeat_receipt))};
  if (request.phase === "admission") {
    if (request.todo_id == null) return {policy: null};
    if (!["legacy_markdown", "file_v0", "sqlite_v0", "postgresql_v0"].includes(String(request.source_authority))) {
      throw new EffectRuntimeRequestError("Result freshness requires a supported Todo authority", "delivery_provider_unsupported");
    }
    return {policy: "turn_result_freshness_required_v1"};
  }
  const identity = requireJsonObject(request.identity, "result work identity");
  const receipt = requireJsonObject(request.receipt, "result work receipt");
  if (receipt.schema_version !== FIRST_RECEIPT_SCHEMA || receipt.purpose !== "delivery_result" ||
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
export async function executeResultWorkBinding(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "result work request");
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
