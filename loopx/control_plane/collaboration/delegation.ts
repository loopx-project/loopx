/** Explicit local execution bindings. Registration/messages alone grant no launch.
 * These are host observations; canonical task/Turn/acceptance remain authoritative. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {acceptanceValidationEffects, type AcceptanceCompletionRequirements} from "../goals/acceptance_contract.ts";
import {normalizeTodoCompletionValidationDeclaration} from "../todos/completion_validation_declaration.ts";
import {readTurnSelectionRejection, turnSelectionRejectionState} from "../turn_driver/selection_rejection.ts";
import { BARE_SHA256_PATTERN, ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

function requireThat(ok: unknown, message: string): asserts ok {
  if (!ok) throw new EffectRuntimeRequestError(message);
}
function text(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 4096;
}

/** The canonical acceptance reader resolves owner scope before this plan.
 * A null requirement is out of scope/disabled, never an unbound-task fallback.
 * Private declarations must match the current Todo authority, also on readback. */
export function delegationValidationPlan(params: JsonObject): JsonObject {
  const binding = requireJsonObject(params.binding, "delegation binding");
  const basis = requireJsonObject(params.basis, "canonical validation basis");
  const todo = requireJsonObject(basis.todo, "canonical delegation Todo");
  requireThat(basis.status === "loaded" && text(basis.provider_revision)
    && todo.todo_id === binding.todo_id, "delegation requires a current matching canonical Todo");
  requireThat(Object.hasOwn(basis, "completion_requirements"), "canonical acceptance scope required");
  const requirements = basis.completion_requirements === null ? null
    : requireJsonObject(basis.completion_requirements, "canonical acceptance requirements");
  if (requirements !== null) requireThat(requirements.todo_id === todo.todo_id
    && Array.isArray(requirements.criteria) && requirements.criteria.length > 0,
  "delegation requires matching owner acceptance criteria");
  const unavailable = (reason: string) => ({todo_id: todo.todo_id, state: "unbound",
    source: null, reason, effects: [], canonical_done: false});
  const effects: JsonObject[] = requirements === null ? []
    : acceptanceValidationEffects(requirements as AcceptanceCompletionRequirements, todo)
      .map(row => ({...requireJsonObject(row.effect, "acceptance validation effect"), criterion_id: row.criterion_id}));
  if (todo.completion_validation_required === true) {
    if (params.declaration === null) return unavailable("completion_validation_declaration_unavailable");
    const declaration = requireJsonObject(params.declaration, "private validation declaration");
    const normalized = normalizeTodoCompletionValidationDeclaration(declaration, {
      strict_fields: true, require_command: true, require_canonical_input: true,
    });
    if (!normalized.ok || canonicalAuthoritySha256(declaration) !== todo.completion_validation_sha256)
      return unavailable("completion_validation_declaration_mismatch");
    effects.push({kind: "caller_validation", validation_command: normalized.value.validation_command,
      validation_argv: normalized.value.validation_command_argv,
      validation_label: normalized.value.validation_label,
      validation_timeout_seconds: normalized.value.validation_timeout_seconds,
      validation_declaration_sha256: todo.completion_validation_sha256,
      task_repository: todo.task_repository ?? null});
  } else {
    requireThat(params.declaration === null && todo.completion_validation_sha256 == null,
      "Todo without canonical validation authority cannot supply a declaration");
    if (requirements === null) return unavailable("independent_delegation_validation_required");
  }
  const source = requirements === null ? "todo_validation" : "goal_acceptance";
  // Definition identity, not a stored success or independent-verifier receipt.
  // Host validation must pass before exposing this path-free current observation.
  const observation = {source,
    basis_sha256: canonicalAuthoritySha256({todo_id: todo.todo_id, requirements, effects}),
    check_count: effects.length,
    pinned_file_count: effects.reduce((count, effect) => count
      + (Array.isArray(effect.validation_files) ? effect.validation_files.length : 0), 0)};
  return {todo_id: todo.todo_id, state: "ready", source, observation,
    effects, canonical_done: todo.done === true && todo.status === "done"};
}
export function selectDelegationBinding(params: JsonObject): JsonObject {
  const config = requireJsonObject(params.config, "delegation configuration");
  requireThat(config.schema_version === "loopx_local_delegation_v0", "unsupported delegation configuration");
  requireThat(Array.isArray(config.bindings) && config.bindings.length <= 100, "bounded bindings required");
  const rows = config.bindings.map(value => requireJsonObject(value, "delegation binding"));
  requireThat(new Set(rows.map(row => row.id)).size === rows.length, "duplicate binding identity");
  const binding = rows.find(row => row.id === params.binding_id);
  requireThat(binding, "delegation binding unavailable");
  requireThat(new TextEncoder().encode(JSON.stringify(binding)).length <= 16000, "delegation binding exceeds limit");
  requireThat([binding.id, binding.agent_id, binding.todo_id, binding.workspace].every(text), "binding identity/workspace required");
  requireThat(Array.isArray(binding.requesters) && binding.requesters.includes(params.agent_id)
    && binding.agent_id !== params.agent_id, "caller has no delegation grant");
  requireThat(Array.isArray(binding.host_args) && binding.host_args.length > 0
    && binding.host_args.every(text), "operator host arguments required");
  requireThat(Number.isInteger(binding.timeout_seconds) && Number(binding.timeout_seconds) >= 1
    && Number(binding.timeout_seconds) <= 3600, "bounded execution timeout required");
  requireThat(Array.isArray(binding.output_refs) && binding.output_refs.length > 0
    && binding.output_refs.length <= 20 && binding.output_refs.every(ref => text(ref)
      && !ref.startsWith("/") && !ref.includes("\\") && !ref.split("/").includes("..")), "bounded relative output refs required");
  return binding;
}

type Observation = "prepared" | "running" | "turn_returned" | "accepted" | "rejected" | "stopped";

function boundedReason(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const reason = value.trim();
  return reason.length > 0 ? reason.slice(0, 4096) : fallback;
}

/** Preserve a rejected Turn plan as data before the host adapter reads its transaction. */
export function delegationTurnPlanDecision(params: JsonObject): JsonObject {
  const plan = requireJsonObject(params.plan, "Turn plan");
  if (plan.ok !== true) return {
    schema_version: "loopx_delegation_turn_plan_decision_v0",
    state: "rejected",
    turn_key: null,
    reason: boundedReason(plan.error ?? plan.reason, "Turn plan rejected without a reason"),
  };
  const transaction = requireJsonObject(plan.transaction, "Turn plan transaction");
  requireThat(typeof transaction.turn_key === "string"
    && ENVELOPED_SHA256_PATTERN.test(transaction.turn_key), "Turn plan transaction requires a valid turn_key");
  return {
    schema_version: "loopx_delegation_turn_plan_decision_v0",
    state: "planned",
    turn_key: transaction.turn_key,
    reason: null,
  };
}

/** Preserve the host owner's public diagnosis, not its private configuration.
 * Optional fields keep older host previews compatible; null means unprobed. */
export function delegationRuntimeFacts(executor: JsonObject): JsonObject {
  const facts: JsonObject = {};
  if (Object.hasOwn(executor, "runtime_probe")) {
    if (executor.runtime_probe === null) facts.runtime_probe = null;
    else {
      const probe = requireJsonObject(executor.runtime_probe, "runtime probe");
      requireThat(probe.schema_version === "managed_runtime_probe_v0"
        && typeof probe.scope === "string"
        && ["probing_interpreter", "configured_runner"].includes(probe.scope)
        && (probe.module === null || (typeof probe.module === "string"
          && probe.module.length <= 128 && /^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$/.test(probe.module)))
        && typeof probe.available === "boolean", "invalid runtime probe observation");
      facts.runtime_probe = {schema_version: probe.schema_version, scope: probe.scope,
        module: probe.module, available: probe.available};
    }
  }
  if (Object.hasOwn(executor, "unavailable_remediation")) {
    const remedies = executor.unavailable_remediation;
    requireThat(Array.isArray(remedies) && remedies.length <= 8
      && remedies.every(code => typeof code === "string" && /^[a-z][a-z0-9_]{0,79}$/.test(code)),
    "invalid runtime remediation codes");
    facts.unavailable_remediation = [...remedies];
  }
  return facts;
}

const todoValidationReasons = new Set([
  "independent_delegation_validation_required", "completion_validation_declaration_unavailable",
  "completion_validation_declaration_mismatch",
]);

/** Project the existing validation owner's facts, never its commands or private
 * errors. Matching identity and file readiness retain the original admission. */
function delegationAcceptanceFacts(todoId: unknown, acceptance: JsonObject | null, filesCurrent: unknown) {
  const matching = acceptance?.todo_id === todoId;
  const ready = matching && acceptance?.state === "ready" && filesCurrent === true;
  if (ready) return {acceptance_ready: true, acceptance_reason_code: null, acceptance_next_action: "none"};
  if (matching && acceptance?.state === "unbound" && typeof acceptance.reason === "string"
      && todoValidationReasons.has(acceptance.reason)) {
    return {acceptance_ready: false, acceptance_reason_code: acceptance.reason,
      acceptance_next_action: "review_original_todo_validation"};
  }
  if (matching && acceptance?.state === "ready" && filesCurrent === false) {
    return {acceptance_ready: false, acceptance_reason_code: "validation_files_unavailable",
      acceptance_next_action: "restore_original_validation_files"};
  }
  return {acceptance_ready: false, acceptance_reason_code: "acceptance_binding_unavailable",
    acceptance_next_action: "review_original_task_acceptance"};
}

/** Read the actual dry-run route/profile, never infer readiness from assignment. */
export function delegationPreflight(params: JsonObject): JsonObject {
  const binding = requireJsonObject(params.binding, "binding identity");
  requireThat([binding.id, binding.agent_id, binding.todo_id].every(text), "binding identities required");
  // Missing observations preserve older host compatibility. Filesystem facts
  // cannot establish canonical authority or executor readiness.
  if (params.workspace !== undefined) {
    const workspace = requireJsonObject(params.workspace, "workspace observation");
    requireThat(typeof workspace.state === "string"
      && ["available", "missing", "not_directory", "unavailable"].includes(workspace.state),
    "invalid workspace observation");
    if (workspace.state !== "available") {
      requireThat(params.authority === null && params.preview === null
        && params.acceptance === null && params.validation_files_current === false,
      "unavailable workspace cannot claim authority, Turn or task acceptance inspection");
      return {
        schema_version: "loopx_delegation_preflight_v0",
        binding: {id: binding.id, agent_id: binding.agent_id, todo_id: binding.todo_id},
        state: "workspace_unavailable", workspace_state: workspace.state,
        workspace_next_action: "review_operator_workspace_binding",
        turn_eligible: false, turn_route: null, acceptance_ready: false,
        acceptance_reason_code: null, acceptance_next_action: "none",
        authority_ready: null, authority_reason: null, authority_state: "uninspected",
        authority_next_action: "none", promotion_from_surface_allowed: false,
        executor: null,
        effects: {host_invoked: false, state_written: false, quota_spent: false,
          scheduler_acknowledged: false},
        note: "Review the original operator-owned workspace binding, then retry inspection. "
          + "Authority, acceptance and runtime were not inspected. No workspace is created, "
          + "binding retargeted or worker launched.",
      };
    }
  }
  const authority = params.authority === undefined
    ? {ready: true, reason: null}
    : requireJsonObject(params.authority, "canonical authority readiness");
  requireThat(typeof authority.ready === "boolean", "canonical authority readiness required");
  if (authority.ready === false) {
    const authorityState = authority.state ?? "unavailable";
    requireThat(authorityState === "promotion_required" || authorityState === "unavailable",
      "unavailable authority transition state required");
    const authorityNextAction = authority.next_action ?? (authorityState === "promotion_required"
      ? "preview_reviewed_goal_authority_promotion" : "repair_canonical_authority");
    requireThat(authorityNextAction === "preview_reviewed_goal_authority_promotion"
      || authorityNextAction === "repair_canonical_authority", "invalid authority next action");
    requireThat(params.preview === null && params.acceptance === null
      && params.validation_files_current === false,
    "unavailable authority cannot claim a Turn preview or task acceptance");
    const effects = {host_invoked: false, state_written: false, quota_spent: false,
      scheduler_acknowledged: false};
    return {
      schema_version: "loopx_delegation_preflight_v0", binding,
      state: "authority_unavailable", turn_eligible: false, turn_route: null,
      acceptance_ready: false, authority_ready: false,
      acceptance_reason_code: null, acceptance_next_action: "none",
      authority_reason: boundedReason(authority.reason, "canonical authority unavailable"),
      authority_state: authorityState, authority_next_action: authorityNextAction,
      promotion_from_surface_allowed: false,
      executor: null, effects,
      note: "Current canonical authority cannot be confirmed, so no executable permission is returned. "
        + "No host was launched and no state was written or quota spent. "
        + "Promote or repair authority explicitly before retrying; inspection never promotes a provider.",
    };
  }
  const preview = requireJsonObject(params.preview, "Turn preview");
  const effects = requireJsonObject(preview.effects, "preview effects");
  if (preview.ok === false && preview.selection_rejection !== undefined) {
    const refusal = readTurnSelectionRejection(preview.selection_rejection, binding.todo_id);
    const refusalState = turnSelectionRejectionState(refusal.state);
    requireThat(preview.effects_scope === "current_invocation"
      && ["host_invoked", "state_written", "quota_spent", "scheduler_acknowledged"].every(k => effects[k] === false)
      && refusal.schema_version === "loopx_turn_selection_rejection_v0"
      && refusal.source === "quota.should-run" && refusal.requested_todo_id === binding.todo_id
      && refusalState !== null
      && preview.error_code === `turn_todo_selection_${refusalState}`,
    "delegation inspection requires a matching effect-free selection refusal");
    const acceptance = params.acceptance === null ? null : requireJsonObject(params.acceptance, "task acceptance");
    return {
      schema_version: "loopx_delegation_preflight_v0", binding, state: "turn_blocked",
      turn_eligible: false, turn_route: null, turn_blocker: refusal,
      ...delegationAcceptanceFacts(binding.todo_id, acceptance, params.validation_files_current),
      authority_ready: true, authority_reason: null, authority_state: "promoted",
      authority_next_action: "none", promotion_from_surface_allowed: false,
      executor: null, effects,
      note: "Quota refused this exact Todo before host or executor inspection. Read status/check with the bound workspace scan root; do not retarget this inspection or bypass repair.",
    };
  }
  requireThat(preview.dry_run === true && preview.status === "preview"
    && ["host_invoked", "state_written", "quota_spent", "scheduler_acknowledged"].every(k => effects[k] === false),
  "delegation inspection requires a read-only Turn preview");
  const route = requireJsonObject(preview.route, "Turn admission route");
  const executor = requireJsonObject(preview.managed_executor, "selected executor");
  requireThat([true, false, null].includes(executor.available as boolean | null), "runtime availability required");
  requireThat(typeof route.would_invoke_host === "boolean", "Turn admission observation required");
  const eligible = route.would_invoke_host === true && route.selected_todo_id === binding.todo_id;
  const acceptance = params.acceptance === null ? null : requireJsonObject(params.acceptance, "task acceptance");
  const acceptanceFacts = delegationAcceptanceFacts(binding.todo_id, acceptance, params.validation_files_current);
  const pinned = acceptanceFacts.acceptance_ready;
  const state = !eligible ? "turn_blocked" : !pinned ? "acceptance_unavailable"
    : executor.available === false ? "runtime_unavailable"
    : executor.available === null ? "runtime_unverified" : "launchable";
  return {
    schema_version: "loopx_delegation_preflight_v0", binding,
    state, turn_eligible: eligible, turn_route: route.kind,
    ...acceptanceFacts, authority_ready: true, authority_reason: null,
    authority_state: "promoted", authority_next_action: "none",
    promotion_from_surface_allowed: false,
    executor: {host: executor.executor, available: executor.available,
      reason: executor.unavailable_reason, profile: executor.execution_profile,
      ...(executor.operation_transport ? {operation_transport: executor.operation_transport} : {}),
      ...delegationRuntimeFacts(executor)},
    effects,
    note: "Point-in-time preflight, not an execution permit or evidence of running work. "
      + "Start rechecks admission; inspect original operations before dispatching replacements. "
      + "Runtime probes have the selected executor's scope, not remote capacity guarantees.",
  };
}
const transitions: Record<Observation, readonly Observation[]> = {
  prepared: ["running", "rejected", "stopped"], running: ["turn_returned", "rejected", "stopped"],
  turn_returned: ["accepted", "rejected", "stopped"], accepted: [], rejected: [], stopped: [],
};

/** Page only the caller's existing journal. A cursor is not a fleet snapshot. */
export function delegationInventoryQuery(params: JsonObject): JsonObject {
  const limit = params.limit ?? 20;
  const cursor = params.cursor ?? null;
  requireThat(Number.isInteger(limit) && Number(limit) >= 1 && Number(limit) <= 50,
    "delegation inventory limit must be between 1 and 50");
  requireThat(cursor === null || (typeof cursor === "string" && BARE_SHA256_PATTERN.test(cursor)),
    "invalid delegation inventory cursor");
  return {limit, cursor};
}

/** The host supplies a fresh Delegations.read result, never a saved status. */
export function delegationInventoryItem(params: JsonObject): JsonObject {
  const record = requireJsonObject(params.record, "delegation inventory record");
  requireThat(typeof record.record_id === "string" && BARE_SHA256_PATTERN.test(record.record_id),
    "invalid delegation record address");
  requireThat(record.operation_id === null || (typeof record.operation_id === "string"
    && /^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/.test(record.operation_id)), "invalid delegation operation identity");
  if (params.observation === null) return {
    record_id: record.record_id, operation_id: record.operation_id,
    status: "unavailable", recovery_required: null,
    error: "delegation_readback_unavailable",
  };
  const observation = requireJsonObject(params.observation, "current delegation readback");
  requireThat(observation.operation_id === record.operation_id && record.operation_id !== null,
    "delegation inventory identity mismatch");
  requireThat(Object.hasOwn(transitions, String(observation.status)), "invalid delegation observation");
  requireThat([observation.request_id, observation.agent_id, observation.todo_id].every(text),
    "delegation request and task identities required");
  requireThat(typeof observation.worker_active === "boolean"
    && typeof observation.recovery_required === "boolean", "current worker observation required");
  const result: JsonObject = {
    record_id: record.record_id, operation_id: observation.operation_id,
    request_id: observation.request_id, agent_id: observation.agent_id, todo_id: observation.todo_id,
    status: observation.status, worker_active: observation.worker_active,
    recovery_required: observation.recovery_required,
  };
  if (observation.status === "accepted") {
    requireThat(Array.isArray(observation.artifacts) && observation.artifacts.length > 0,
      "accepted inventory requires current artifacts");
    result.artifacts = observation.artifacts.map(value => {
      const artifact = requireJsonObject(value, "accepted artifact");
      requireThat(text(artifact.ref) && typeof artifact.sha256 === "string"
        && BARE_SHA256_PATTERN.test(artifact.sha256), "invalid accepted artifact reference");
      return {ref: artifact.ref, sha256: artifact.sha256};
    });
  }
  return result;
}

export function transitionDelegationObservation(params: JsonObject): JsonObject {
  const from = params.from as Observation, to = params.to as Observation;
  requireThat(Object.hasOwn(transitions, from) && Object.hasOwn(transitions, to), "invalid delegation observation");
  requireThat(from === to || transitions[from].includes(to), "invalid delegation observation transition");
  if (to === "accepted") requireThat(params.canonical_done === true
    && params.acceptance_ready === true && params.artifacts_current === true,
  "accepted return requires current canonical completion and artifacts");
  if (to === "accepted" && from !== "accepted" && wakesItsConversation(params)) {
    return {status: to, wake_intent: delegationWakeIntent(params)};
  }
  return {status: to};
}

type StopPhase = "requested" | "acknowledged" | "settled" | "unknown";
const openStopPhases: readonly StopPhase[] = ["requested", "acknowledged"];
/** What the Host transport read back about everything the operation's Turn launched. */
type HostProcessDrain = "not_launched" | "drained" | "draining" | "unattributable";
const hostProcessDrains: readonly HostProcessDrain[] = ["not_launched", "drained", "draining", "unattributable"];
/** What canonical authority proved about the hard lease the stopped execution may hold.
 *
 * `unchecked` until the stop resolves it, which happens only once the execution
 * is proven gone; it never means that no lease was owed. `not_owed` is proven
 * by canonical authority for the execution's own identity, not by its record.
 */
type StopLease = "unchecked" | "not_owed" | "released" | "release_unproven" | "obligation_unproven";
const stopLeases: readonly StopLease[] = ["unchecked", "not_owed", "released", "release_unproven", "obligation_unproven"];
/** The next step for the host: resolve the lease from canonical authority, or record a receipt phase. */
type DelegationStopStep =
  | {action: "resolve_lease"}
  | {action: "record"; phase: StopPhase; terminal: boolean; reason: string};

function stopRecord(phase: StopPhase, reason: string): DelegationStopStep {
  return {action: "record", phase, terminal: !openStopPhases.includes(phase), reason};
}

/** Advance one stop request from host release facts; a receipt is never inferred from time.
 *
 * The stopped execution is gone once the operation lock is free, the member's
 * Turn lane was released by the stopped worker's process group, and the Host
 * transport reads everything the Turn launched as drained or never launched.
 * A worker and its lane can let go while the Host supervisor is still
 * terminating the Host, so their release proves nothing about the Host. The
 * host reads the lane from its holder record and never takes it, so a
 * legitimate Turn is not refused, and a holder it cannot attribute is not
 * released.
 *
 * Only then may the host resolve the execution's hard lease: its release hands
 * the member's Todo on, so it waits for the execution, and a receipt never
 * becomes terminal before that lease is resolved. ``settled`` needs the
 * acknowledgement of a process that held the operation lock, the execution
 * gone and the lease released or proven not owed. Everything released without
 * an acknowledgement means the named holder vanished before recording what it
 * observed, which is ``unknown`` rather than a fake settlement; with a Host
 * that cannot be attributed nothing more can be learned, so it is ``unknown``
 * without touching the lease. An acknowledged stop whose Host cannot be
 * attributed stays open for a later read with the same identity. A grace
 * timeout on its own moves nothing: a worker still holding a lock still runs.
 */
export function decideDelegationStop(params: JsonObject): DelegationStopStep {
  const phase = params.phase as StopPhase;
  requireThat(openStopPhases.includes(phase), "delegation stop decision requires an open stop phase");
  requireThat(typeof params.acknowledged === "boolean", "delegation stop acknowledgement fact required");
  requireThat(typeof params.operation_lock_free === "boolean" && typeof params.worker_lane_released === "boolean",
    "delegation stop release facts required");
  requireThat(hostProcessDrains.includes(params.host_process as HostProcessDrain),
    "delegation stop host process drain fact required");
  requireThat(stopLeases.includes(params.lease as StopLease), "delegation stop lease fact required");
  requireThat(params.timed_out === undefined || typeof params.timed_out === "boolean",
    "delegation stop timeout fact must be boolean");
  requireThat(phase !== "acknowledged" || params.acknowledged === true,
    "an acknowledged stop cannot lose its acknowledgement");
  const operationFree = params.operation_lock_free === true;
  const host = params.host_process as HostProcessDrain;
  const lease = params.lease as StopLease;
  const executionPending = !operationFree ? "operation_lock_still_held"
    : params.worker_lane_released !== true ? "worker_lane_release_unproven"
    : host === "draining" ? "host_process_still_running"
    : host === "unattributable" ? "host_process_drain_unproven"
    : null;
  requireThat(executionPending === null || lease === "unchecked",
    "a delegation stop resolves its lease only after its execution is proven gone");
  const leaseStep = (open: StopPhase, terminal: StopPhase, reason: string): DelegationStopStep =>
    lease === "unchecked" ? {action: "resolve_lease"}
      : lease === "release_unproven" ? stopRecord(open, "required_lease_release_unproven")
      : lease === "obligation_unproven" ? stopRecord(open, "lease_obligation_unproven")
      : stopRecord(terminal, reason);
  if (params.acknowledged === true) {
    return executionPending === null
      ? leaseStep("acknowledged", "settled", "acknowledged_worker_and_host_released")
      : stopRecord("acknowledged", executionPending);
  }
  if (executionPending === null) return leaseStep("requested", "unknown", "holder_gone_without_acknowledgement");
  if (executionPending === "host_process_drain_unproven") {
    return stopRecord("unknown", "holder_gone_without_acknowledgement");
  }
  return stopRecord("requested", operationFree ? executionPending
    : params.timed_out === true ? "holder_still_running_after_grace" : "awaiting_acknowledgement");
}

/** Whether an accepted result may produce a wake intent at all.
 *
 * Only an operation started from a conversation can be continued there. An
 * ordinary CLI/MCP delegation has no conversation, so it keeps the transition it
 * always had: no intent, no wake state, and no change to what a plain
 * `wait`/`read` returns. Producing an intent and then refusing it in the pump
 * would still widen a shared persistent projection for every caller who never
 * enabled this capability.
 */
function wakesItsConversation(params: JsonObject): boolean {
  if (params.requester == null) return false;
  const requester = requireJsonObject(params.requester, "wake requester");
  return requester.conversation != null;
}

/** The first transition to ``accepted`` is the one durable moment a requester
 * can be continued without polling.  The intent names the requester, the
 * conversation whose Turn started the operation (null when it was not started
 * from one) and the exact accepted result; it grants no Turn and is not a
 * second settlement.  The conversation is part of the intent identity, so the
 * wake cannot be consumed by another conversation of the same requester. */
function delegationWakeIntent(params: JsonObject): JsonObject {
  const requester = requireJsonObject(params.requester, "wake requester");
  requireThat([requester.goal_id, requester.agent_id, requester.operation_id, requester.request_id].every(text),
    "wake intent requires the requester and result identity");
  const goalRef = requester.goal_ref == null ? null : requireJsonObject(requester.goal_ref, "requester goal reference");
  const origin = requester.conversation == null ? null
    : requireJsonObject(requester.conversation, "requester conversation");
  requireThat(origin === null || (text(origin.session_id) && text(origin.turn_id)),
    "requester conversation requires its session and Turn");
  const conversation = origin === null ? null : {session_id: origin.session_id, turn_id: origin.turn_id};
  requireThat(Array.isArray(requester.artifacts) && requester.artifacts.length > 0, "wake intent requires accepted artifacts");
  const digests = requester.artifacts.map(value => {
    const artifact = requireJsonObject(value, "accepted artifact");
    requireThat(text(artifact.ref) && typeof artifact.sha256 === "string"
      && BARE_SHA256_PATTERN.test(artifact.sha256), "invalid accepted artifact reference");
    return {ref: artifact.ref, sha256: artifact.sha256};
  });
  return {
    schema_version: "loopx_delegation_wake_intent_v0",
    intent_id: canonicalAuthoritySha256([requester.goal_id, requester.agent_id, requester.operation_id,
      requester.request_id, digests, conversation]),
    requester: {goal_id: requester.goal_id, agent_id: requester.agent_id, goal_ref: goalRef},
    conversation,
    operation_id: requester.operation_id, request_id: requester.request_id,
  };
}

/** Reading another conversation's result cannot consume its continuation. */
export function decideDelegationWakeObservation(params: JsonObject): JsonObject {
  const intent = requireJsonObject(params.intent, "wake intent");
  const requester = requireJsonObject(intent.requester, "wake requester");
  const observer = requireJsonObject(params.observer, "wake observer");
  requireThat([observer.session_id, observer.goal_id, observer.agent_id].every(text),
    "wake observation requires its conversation and requester");
  const conversation = intent.conversation == null ? null
    : requireJsonObject(intent.conversation, "wake conversation");
  return {
    observed: conversation?.session_id === observer.session_id
      && requester.goal_id === observer.goal_id && requester.agent_id === observer.agent_id
      && canonicalAuthoritySha256(requester.goal_ref ?? null)
        === canonicalAuthoritySha256(observer.goal_ref ?? null),
  };
}

/** Repair only a false terminal observation after the exact Turn validated.
 *
 * This does not retry model work.  The host boundary must prove that the
 * caller-stable settlement identity resolved one canonical journal and that
 * the journal already completed independent validation.  Recovery returns to
 * ``turn_returned`` so the existing settlement path can complete durably.
 */
export function recoverValidatedDelegationSettlement(params: JsonObject): JsonObject {
  requireThat(params.from === "rejected", "delegation recovery requires a rejected observation");
  requireThat(params.identity_matched === true, "delegation recovery requires the exact Turn identity");
  requireThat(params.journal_status === "in_progress",
    "delegation recovery requires an unsettled Turn journal");
  requireThat(params.result_kind === "validated_progress",
    "delegation recovery requires validated progress");
  requireThat(params.task_validation_passed === true,
    "delegation recovery requires independent task validation");
  requireThat(Array.isArray(params.completed_phases)
    && JSON.stringify(params.completed_phases) === JSON.stringify([
      "host_execute", "typed_result", "validation",
    ]), "delegation recovery requires the validated settlement boundary");
  return {
    status: "turn_returned",
    recovery_kind: "settlement_only",
    host_reexecution_allowed: false,
  };
}

/** Explicit requester decision backed by two current accepted executions. */
export function recordDelegationAdoption(params: JsonObject): JsonObject {
  const source = requireJsonObject(params.source, "source execution");
  const consumer = requireJsonObject(params.consumer, "consumer execution");
  requireThat(source.status === "accepted" && consumer.status === "accepted"
    && source.operation_id !== consumer.operation_id, "adoption requires distinct accepted executions");
  const inputs = params.inputs;
  requireThat(Array.isArray(inputs), "consumer inputs required");
  const artifacts = source.artifacts;
  requireThat(Array.isArray(artifacts), "source artifacts required");
  const used = inputs.filter(raw => {
    const input = requireJsonObject(raw, "consumer input");
    if (!input.delegation) return false;
    const link = requireJsonObject(input.delegation, "delegation input");
    return link.operation_id === source.operation_id && link.relation === "uses"
      && artifacts.some(raw => {
        const artifact = requireJsonObject(raw, "source artifact");
        return artifact.ref === link.ref && artifact.sha256 === input.sha256;
      });
  });
  requireThat(used.length > 0 && params.inputs_current === true,
    "adoption requires the accepted consumer's exact current uses input");
  requireThat(Array.isArray(consumer.artifacts) && consumer.artifacts.length > 0, "consumer artifacts required");
  return {
    consumer_operation_id: consumer.operation_id, consumer_request_id: consumer.request_id,
    consumer_agent_id: consumer.agent_id, consumer_todo_id: consumer.todo_id,
    source_artifacts: used.map(raw => {
      const input = requireJsonObject(raw, "consumer input");
      const link = requireJsonObject(input.delegation, "delegation input");
      return {ref: link.ref, sha256: input.sha256};
    }),
    consumer_artifacts: consumer.artifacts.map(raw => {
      const artifact = requireJsonObject(raw, "consumer artifact");
      return {ref: artifact.ref, sha256: artifact.sha256};
    }),
  };
}
