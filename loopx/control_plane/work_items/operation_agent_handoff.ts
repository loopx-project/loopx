/** Agent execution is a continuation of the original typed operation, not a
 * second approval store. Python supplies locked storage and registry facts;
 * this owner decides admission, one-shot consumption and result binding. */
import type {JsonObject} from "../effect_program.ts";
import {BARE_SHA256_PATTERN, ENVELOPED_SHA256_PATTERN} from "../content_digest.ts";
import {EffectRuntimeConflictError, EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";

export const AGENT_OPERATION_REVISION = "agent-session-handoff-v0";
export const MANAGED_OPERATION_REVISION = "managed-turn-handoff-v0";
const ID = /^[A-Za-z0-9._:-]{1,200}$/;

function id(value: unknown, field: string): string {
  const result = requireNonEmptyString(value, field);
  if (!ID.test(result)) throw new EffectRuntimeRequestError(`${field} must be a compact opaque id`);
  return result;
}
function requireThat(value: unknown, message: string): asserts value {
  if (!value) throw new EffectRuntimeConflictError(message, "operation_handoff_conflict");
}
function timestamp(value: unknown): number {
  const text = requireNonEmptyString(value, "operation timestamp");
  const parsed = Date.parse(text);
  if (!/(Z|[+-]\d\d:\d\d)$/.test(text) || !Number.isFinite(parsed)) {
    throw new EffectRuntimeRequestError("operation timestamp requires a timezone");
  }
  return parsed;
}

/** Readback of an operator-selected transport. This is not a session binding,
 * a runtime qualification or an execution permit. Python supplies argv facts.
 * Accept the CLI's provider-neutral effort vocabulary; actual model support
 * remains a host qualification, not something this preflight can establish. */
export function projectManagedOperationTransport(input: JsonObject): JsonObject {
  const reason = input.host !== "codex-cli" ? "operation_transport_host_unsupported"
    : !["read-only", "workspace-write"].includes(String(input.sandbox)) ? "operation_transport_sandbox_unsupported"
    : typeof input.model !== "string" || !ID.test(input.model)
      || typeof input.reasoning_effort !== "string"
      || !["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"].includes(input.reasoning_effort)
      ? "operation_transport_profile_required" : null;
  return {reason, transport: {schema_version: "loopx_operation_transport_v0",
    kind: "owned_app_server", revision: "app-server-operation-tools-v0",
    configuration_valid: reason === null, runtime_qualified: false,
    identity_source: "native_thread_turn_metadata", human_confirmation_required: true,
    first_consumption_required: true, source_conversation_is_executor: false}};
}

export function normalizeAgentOperationExecutor(input: JsonObject): JsonObject {
  const executor = requireJsonObject(input.executor, "agent executor");
  if (executor.kind === "managed_turn") {
    const keys = ["kind", "todo_id", "session_id", "profile_digest", "model", "reasoning_effort", "revision"];
    if (Object.keys(executor).length !== keys.length || keys.some(key => !(key in executor))
      || executor.revision !== MANAGED_OPERATION_REVISION
      || typeof executor.profile_digest !== "string" || !BARE_SHA256_PATTERN.test(executor.profile_digest)) {
      throw new EffectRuntimeRequestError("managed operation executor binding is invalid");
    }
    return {kind: "managed_turn", todo_id: id(executor.todo_id, "todo_id"),
      session_id: id(executor.session_id, "session_id"), profile_digest: executor.profile_digest,
      model: id(executor.model, "model"), reasoning_effort: id(executor.reasoning_effort, "reasoning_effort"),
      revision: MANAGED_OPERATION_REVISION};
  }
  const keys = ["kind", "host_surface", "thread_id", "revision"];
  if (Object.keys(executor).length !== keys.length || keys.some(key => !(key in executor))
    || executor.kind !== "agent_session" || executor.revision !== AGENT_OPERATION_REVISION) {
    throw new EffectRuntimeRequestError("agent operation executor binding is invalid");
  }
  return {kind: "agent_session", host_surface: id(executor.host_surface, "host_surface"),
    thread_id: id(executor.thread_id, "thread_id"), revision: AGENT_OPERATION_REVISION};
}

/** Filesystem/session observations are supplied by the existing Turn session
 * owner. A registered source conversation is context/return routing, not the
 * identity of a managed executor. No transport proof is accepted by this RPC. */
export function managedOperationBindingCurrent(input: JsonObject): JsonObject {
  const parameters = requireJsonObject(input.parameters, "operation parameters");
  const executor = normalizeAgentOperationExecutor({executor: parameters.executor});
  const session = input.session == null ? null : requireJsonObject(input.session, "Turn session");
  const expectedRef = parameters.origin_goal_ref == null ? null
    : requireJsonObject(parameters.origin_goal_ref, "origin Goal ref");
  const observedRef = session?.goal_ref == null ? null : requireJsonObject(session.goal_ref, "session Goal ref");
  const sameGoalRef = expectedRef === null
    ? observedRef === null || (observedRef.goal_id === parameters.goal_id && observedRef.goal_instance_id == null)
    : observedRef !== null && expectedRef.goal_id === observedRef.goal_id
      && expectedRef.goal_instance_id === observedRef.goal_instance_id;
  return {current: executor.kind === "managed_turn" && session !== null
    && session.schema_version === "loopx_codex_cli_session_v1"
    && session.goal_id === parameters.goal_id && session.agent_id === parameters.agent_id
    && session.todo_id === executor.todo_id && session.session_id === executor.session_id
    && session.operation_transport === "app-server-operation-tools-v0"
    && session.operation_profile_digest === executor.profile_digest
    && session.operation_model === executor.model && session.operation_reasoning_effort === executor.reasoning_effort
    && sameGoalRef};
}

/** No qualified host producer is connected to the public CLI. Ambient thread
 * ids, route flags and caller-supplied "verified" fields cannot authenticate
 * a session. Keep the old RPC fail-closed until a real transport-owned issuer
 * and its owner-pinned verifier are qualified together; do not mint a local
 * bearer credential from the same untrusted environment. */
export function deriveAgentOperationActor(_input: JsonObject): never {
  throw new EffectRuntimeRequestError(
    "Original-host authentication is unavailable. Integrate the original host's session-bound tool transport; environment ids and route flags are not identity proof.",
    "operation_host_authentication_unavailable",
  );
}

/** A registry-authorized replacement may recover evidence, never inherit the
 * immutable executor or consume an unspent authorization. Caller identity
 * still requires a trusted host transport; a registry binding is not one. */
function historicalAccess(input: JsonObject, route: JsonObject, consumed: boolean): JsonObject {
  const actor = requireJsonObject(input.actor, "historical evidence actor");
  const original = Object.entries(route).every(([key, value]) => actor[key] === value);
  requireThat(original || (consumed && input.binding_current === false && input.actor_binding_current === true
    && actor.goal_id === route.goal_id && actor.agent_id === route.agent_id),
    "historical evidence actor is not the original bound session or its current recovery owner");
  const owner = Object.fromEntries(Object.keys(route).map(key => [key, id(actor[key], `actor.${key}`)]));
  return {mode: original ? "original_session" : "replacement_reconciliation", owner,
    original_route: route, permission: "historical_evidence_only", execution_allowed: false,
    authority_source: original ? "original_operation_route"
      : actor.host_surface === "loopx-managed-codex" ? "current_turn_session_binding" : "current_registry_binding"};
}

export function planAgentOperationHandoff(input: JsonObject): JsonObject {
  const proposal = requireJsonObject(input.proposal, "proposal");
  const parameters = requireJsonObject(proposal.normalized_parameters, "parameters");
  const operation = requireJsonObject(proposal.operation, "operation");
  const executor = normalizeAgentOperationExecutor({executor: parameters.executor});
  const action = input.action;
  const digests = requireJsonObject(input.digests, "locked operation digests");
  requireThat(proposal.action_kind === "operation.execute" && operation.operation_id === proposal.proposal_id,
    "agent handoff requires the original typed operation");
  requireThat(digests.payload_digest === parameters.payload_digest && parameters.payload_digest === operation.payload_digest
    && digests.projection_digest === parameters.projection_digest && parameters.projection_digest === operation.projection_digest
    && digests.confirmation_digest === operation.confirmation_digest
    && executor.revision === operation.executor_revision
    && parameters.destination_account_ref === operation.destination_account_ref
    && parameters.expires_at === operation.expires_at
    && JSON.stringify(parameters.authorized_principals) === JSON.stringify(operation.authorized_principals),
    "immutable operation binding drifted");
  const confirmation = operation.confirmation == null ? null
    : requireJsonObject(operation.confirmation, "operation confirmation");
  const claim = operation.claim == null ? null : requireJsonObject(operation.claim, "operation claim");
  const hostStart = operation.host_start == null ? null : requireJsonObject(operation.host_start, "native host start");
  const now = timestamp(input.now);
  const expires = timestamp(operation.expires_at);
  const managed = executor.kind === "managed_turn";
  const route: JsonObject = {goal_id: parameters.goal_id, agent_id: parameters.agent_id,
    ...(managed ? {host_surface: "loopx-managed-codex", thread_id: executor.session_id,
      todo_id: executor.todo_id, profile_digest: executor.profile_digest}
      : {host_surface: executor.host_surface, thread_id: executor.thread_id})};
  const base: JsonObject = {schema_version: "loopx_operation_agent_handoff_v0", operation_id: operation.operation_id,
    payload_digest: operation.payload_digest, confirmation_digest: operation.confirmation_digest,
    claim_id: claim?.claim_id ?? null, executor_revision: executor.revision, expires_at: operation.expires_at,
    route, authorization_source: "canonical_typed_operation", execution_allowed: false,
    host_delivery: hostStart ? "native_start_accepted" : "not_attempted", host_start: hostStart,
    external_write_performed: false,
    executor_kind: executor.kind, source_route: parameters.source_route ?? null,
    host_authentication_required: !managed};
  const handoff = operation.agent_handoff == null ? null
    : requireJsonObject(operation.agent_handoff, "agent handoff");
  const observed = operation.reconciliation ?? operation.outcome;
  const unknownResult = observed != null
    && requireJsonObject(observed, "observed result").outcome === "submission_unknown";
  if (action === "project" || action === "inspect") {
    const access = action === "inspect" ? historicalAccess(input, route, !!handoff) : null;
    return {...base, ...(access ? {access} : {}), outcome_digest: digests.outcome_digest ?? null,
      status: unknownResult ? "submission_unknown"
      : operation.lifecycle_state === "outcome_observed" ? "outcome_observed"
      : handoff ? "consumed_outcome_pending" : now >= expires ? "expired"
      : operation.lifecycle_state === "claimed" ? "authorized_pending" : "awaiting_confirmation",
      needs_reconciliation: unknownResult || (!!handoff && operation.lifecycle_state !== "outcome_observed")};
  }
  requireThat(confirmation?.decision === "confirm"
    && confirmation.confirmation_digest === operation.confirmation_digest && claim,
    "agent execution requires authenticated confirmation");
  if (action === "wake") {
    // A launch fence, never authentication or first-consumption authority.
    // The existing delegation owner supplies its operator grant; the native
    // host rechecks the complete effective profile immediately before resume.
    const launch = requireJsonObject(input.launch_context, "operation wake launch context");
    const selected = requireJsonObject(input.executor_route, "operation wake executor route");
    requireThat(managed && input.binding_current === true
      && launch.host === "codex-cli" && launch.operation_tools === true
      && launch.iteration_context !== "fresh"
      && Object.entries(route).every(([key, value]) => selected[key] === value)
      && selected.model === executor.model && selected.reasoning_effort === executor.reasoning_effort,
      "operation wake must resume the original managed session and profile");
    requireThat(!hostStart && !handoff && !observed
      && operation.lifecycle_state === "claimed" && proposal.status === "applying",
      "operation wake requires an unstarted, unconsumed confirmed operation");
    requireThat(now < expires && timestamp(confirmation.confirmed_at) <= now,
      "operation wake is outside the confirmation lifetime");
    return {...base, status: "wake_admitted", wake_allowed: true};
  }
  if (action === "observe_host_start") {
    const actor = requireJsonObject(input.actor, "native start actor");
    requireThat(managed && Object.entries(route).every(([key, value]) => actor[key] === value)
      && actor.model === executor.model && actor.reasoning_effort === executor.reasoning_effort
      && input.binding_current === true, "native start is not the original managed binding");
    const hostTurnId = id(actor.host_turn_id, "native host Turn");
    const turnKey = requireNonEmptyString(input.turn_key, "LoopX Turn key");
    requireThat(ENVELOPED_SHA256_PATTERN.test(turnKey), "native start requires a bound LoopX Turn key");
    // This is first-start evidence, not a launch lock or execution permit.
    // A retry must preserve the original time and causal identity, never relabel
    // a later scheduled Turn as the first confirmation-triggered continuation.
    if (hostStart) return {...base, status: "native_start_accepted", recorded: false};
    requireThat(!handoff && !observed && operation.lifecycle_state === "claimed" && proposal.status === "applying",
      "native start cannot manufacture continuation evidence after consumption or outcome");
    requireThat(now < expires && timestamp(confirmation.confirmed_at) <= now,
      "native continuation is outside the confirmation lifetime");
    const eventId = requireNonEmptyString(confirmation.event_id, "confirmation event");
    requireThat(eventId.length <= 512 && !/[\x00-\x1f]/.test(eventId), "confirmation event is invalid");
    const receipt: JsonObject = {schema_version: "loopx_operation_host_start_v0",
      operation_id: operation.operation_id, payload_digest: operation.payload_digest,
      confirmation_digest: operation.confirmation_digest,
      confirmation_event_id: eventId, confirmed_at: confirmation.confirmed_at,
      claim_id: id(claim.claim_id, "operation claim"), route, turn_key: turnKey,
      host_turn_id: hostTurnId, accepted_at: input.now, trigger_kind: "canonical_operation_inbox",
      execution_allowed: false, external_write_performed: false};
    return {...base, status: "native_start_accepted", recorded: true, write_host_start: receipt};
  }
  if (action === "consume") {
    const actor = requireJsonObject(input.actor, "execution actor");
    requireThat(Object.entries(route).every(([key, value]) => actor[key] === value),
      "execution actor is not the original bound session");
    requireThat(input.binding_current === true, "original session binding is no longer current");
    if (managed) requireThat(typeof actor.host_turn_id === "string" && ID.test(actor.host_turn_id),
      "managed operation requires its transport-owned active Turn");
    // Even a same-id retry returns no execute permission. A lost response after
    // this commit is ambiguous, never permission to submit a second order.
    if (handoff || operation.lifecycle_state === "outcome_observed") {
      return {...base, status: "already_consumed", needs_reconciliation: unknownResult || operation.lifecycle_state !== "outcome_observed"};
    }
    requireThat(operation.lifecycle_state === "claimed" && proposal.status === "applying", "operation is not claimed");
    requireThat(now < expires, "confirmed operation expired before execution consumption");
    return {...base, status: "consumed_outcome_pending", execution_allowed: true,
      write_handoff: {...base, status: "consumed_outcome_pending", consumed_at: input.now,
        ...(managed ? {host_turn_id: actor.host_turn_id} : {}),
        consumption_id: id(input.consumption_id, "consumption_id")}};
  }
  if (action === "report") {
    requireThat(handoff, "operation authorization has not been consumed");
    const access = historicalAccess(input, route, true);
    const report_provenance = {schema_version: "loopx_operation_report_provenance_v0", ...access,
      operation_id: operation.operation_id, consumption_id: handoff.consumption_id, recorded_at: input.now};
    const outcome = requireJsonObject(input.outcome, "operation outcome");
    for (const key of ["operation_id", "payload_digest", "confirmation_digest", "claim_id", "executor_revision"]) {
      requireThat(outcome[key] === base[key], "operation outcome does not match the consumed authorization");
    }
    requireThat(outcome.consumption_id === handoff.consumption_id, "operation consumption identity drifted");
    requireThat(outcome.schema_version === "loopx_operation_outcome_v0" && outcome.projection_verified === true
      && outcome.simulation === false && typeof outcome.external_write_performed === "boolean",
      "agent result must separately disclose real external effect status");
    requireThat(["executed", "not_executed", "submission_unknown"].includes(String(outcome.outcome)), "agent outcome is unsupported");
    requireThat(Array.isArray(outcome.evidence_refs) && outcome.evidence_refs.length > 0
      && outcome.evidence_refs.length <= 20 && outcome.evidence_refs.every(ref => typeof ref === "string" && ref.length > 0 && ref.length <= 512),
      "agent outcome requires bounded original evidence references");
    requireThat(outcome.outcome !== "executed" || outcome.external_write_performed === true,
      "execution completion requires a disclosed external effect");
    requireThat(outcome.outcome !== "not_executed" || outcome.external_write_performed === false,
      "a no-execution result must not hide an external effect");
    requireThat(outcome.outcome !== "submission_unknown" || outcome.external_write_performed === true,
      "an ambiguous submission must conservatively disclose a possible external effect");
    const original = operation.outcome == null ? null : requireJsonObject(operation.outcome, "original outcome");
    if (original?.outcome === "submission_unknown" && outcome.outcome !== "submission_unknown") {
      requireThat(outcome.reconciles_outcome_digest === digests.outcome_digest,
        "reconciliation must reference the exact original unknown result");
      return {...base, access, report_provenance, status: "outcome_observed", outcome, write_reconciliation: true};
    }
    return {...base, access, report_provenance,
      status: outcome.outcome === "submission_unknown" ? "submission_unknown" : "outcome_observed", outcome};
  }
  throw new EffectRuntimeRequestError("unsupported agent operation action");
}

/** Bounded attention does not discard original recovery obligations. The
 * overflow locator can be inspected directly in the canonical action store. */
export function projectAgentOperationInbox(input: JsonObject): JsonObject {
  if (!Array.isArray(input.items)) throw new EffectRuntimeRequestError("handoff items must be an array");
  const executorRoute = input.executor_route == null ? null : requireJsonObject(input.executor_route, "executor route");
  const items = input.items.map(value => requireJsonObject(value, "handoff item"))
    .filter(item => executorRoute === null || Object.entries(executorRoute)
      .every(([key, value]) => requireJsonObject(item.route, "operation route")[key] === value));
  const rank = (item: JsonObject) => item.needs_reconciliation === true ? 0 : 1;
  items.sort((a, b) => rank(a) - rank(b)
    || String(a.operation_id).localeCompare(String(b.operation_id), "en"));
  const scope = requireNonEmptyString(input.cursor_scope, "operation cursor scope");
  if (!BARE_SHA256_PATTERN.test(scope)) throw new EffectRuntimeRequestError("operation cursor scope is invalid");
  let remaining = items;
  if (input.cursor != null) {
    const cursor = requireNonEmptyString(input.cursor, "operation cursor");
    const match = /^op1:([a-f0-9]{64}):([01]):([A-Za-z0-9._:-]{1,200})$/.exec(cursor);
    if (!match || match[1] !== scope) throw new EffectRuntimeRequestError("operation cursor scope mismatch");
    const afterRank = Number(match[2]);
    remaining = items.filter(item => rank(item) > afterRank
      || (rank(item) === afterRank && String(item.operation_id).localeCompare(match[3], "en") > 0));
  }
  const page = remaining.slice(0, 20);
  const last = page.at(-1);
  const next = remaining.length > 20 && last
    ? `op1:${scope}:${rank(last)}:${id(last.operation_id, "operation_id")}` : null;
  return {items: page, pending_count: items.length, next_cursor: next,
    overflow: remaining.length > 20 ? {reason: "attention_page_capacity", count: remaining.length - 20,
      next_operation_id: remaining[20].operation_id, next_cursor: next,
      instruction: "Continue with manager-inbox read --operation-cursor. Restart without that cursor to discover new or changed work; a page boundary is not completion."} : null};
}
