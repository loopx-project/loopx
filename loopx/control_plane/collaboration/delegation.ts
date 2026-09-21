/** Explicit local execution bindings. Registration/messages alone grant no launch.
 * These are host observations; canonical task/Turn/acceptance remain authoritative. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

function requireThat(ok: unknown, message: string): asserts ok {
  if (!ok) throw new EffectRuntimeRequestError(message);
}
function text(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 4096;
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

type Observation = "prepared" | "running" | "turn_returned" | "accepted" | "rejected";

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
    && /^sha256:[a-f0-9]{64}$/.test(transaction.turn_key), "Turn plan transaction requires a valid turn_key");
  return {
    schema_version: "loopx_delegation_turn_plan_decision_v0",
    state: "planned",
    turn_key: transaction.turn_key,
    reason: null,
  };
}

/** Read the actual dry-run route/profile, never infer readiness from assignment. */
export function delegationPreflight(params: JsonObject): JsonObject {
  const binding = requireJsonObject(params.binding, "binding identity");
  requireThat([binding.id, binding.agent_id, binding.todo_id].every(text), "binding identities required");
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
      authority_reason: boundedReason(authority.reason, "canonical authority unavailable"),
      authority_state: authorityState, authority_next_action: authorityNextAction,
      promotion_from_surface_allowed: false,
      executor: null, effects,
      note: "Canonical authority is unavailable, so no Turn or provider was inspected or launched. "
        + "Promote or repair authority explicitly before retrying; inspection never promotes a provider.",
    };
  }
  const preview = requireJsonObject(params.preview, "Turn preview");
  const effects = requireJsonObject(preview.effects, "preview effects");
  requireThat(preview.dry_run === true && preview.status === "preview"
    && ["host_invoked", "state_written", "quota_spent", "scheduler_acknowledged"].every(k => effects[k] === false),
  "delegation inspection requires a read-only Turn preview");
  const route = requireJsonObject(preview.route, "Turn admission route");
  const executor = requireJsonObject(preview.managed_executor, "selected executor");
  requireThat([true, false, null].includes(executor.available as boolean | null), "runtime availability required");
  requireThat(typeof route.would_invoke_host === "boolean", "Turn admission observation required");
  const eligible = route.would_invoke_host === true && route.selected_todo_id === binding.todo_id;
  const acceptance = params.acceptance === null ? null : requireJsonObject(params.acceptance, "task acceptance");
  const pinned = acceptance?.todo_id === binding.todo_id && acceptance?.state === "ready" && params.validation_files_current === true;
  const state = !eligible ? "turn_blocked" : !pinned ? "acceptance_unavailable"
    : executor.available === false ? "runtime_unavailable"
    : executor.available === null ? "runtime_unverified" : "launchable";
  return {
    schema_version: "loopx_delegation_preflight_v0", binding,
    state, turn_eligible: eligible, turn_route: route.kind,
    acceptance_ready: pinned, authority_ready: true, authority_reason: null,
    authority_state: "promoted", authority_next_action: "none",
    promotion_from_surface_allowed: false,
    executor: {host: executor.executor, available: executor.available,
      reason: executor.unavailable_reason, profile: executor.execution_profile},
    effects,
    note: "Point-in-time preflight, not an execution permit or evidence of running work. "
      + "Start rechecks admission; inspect original operations before dispatching replacements. "
      + "Runtime probes have the selected executor's scope, not remote capacity guarantees.",
  };
}
const transitions: Record<Observation, readonly Observation[]> = {
  prepared: ["running", "rejected"], running: ["turn_returned", "rejected"],
  turn_returned: ["accepted", "rejected"], accepted: [], rejected: [],
};

/** Page only the caller's existing journal. A cursor is not a fleet snapshot. */
export function delegationInventoryQuery(params: JsonObject): JsonObject {
  const limit = params.limit ?? 20;
  const cursor = params.cursor ?? null;
  requireThat(Number.isInteger(limit) && Number(limit) >= 1 && Number(limit) <= 50,
    "delegation inventory limit must be between 1 and 50");
  requireThat(cursor === null || (typeof cursor === "string" && /^[a-f0-9]{64}$/.test(cursor)),
    "invalid delegation inventory cursor");
  return {limit, cursor};
}

/** The host supplies a fresh Delegations.read result, never a saved status. */
export function delegationInventoryItem(params: JsonObject): JsonObject {
  const record = requireJsonObject(params.record, "delegation inventory record");
  requireThat(typeof record.record_id === "string" && /^[a-f0-9]{64}$/.test(record.record_id),
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
        && /^[a-f0-9]{64}$/.test(artifact.sha256), "invalid accepted artifact reference");
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
  return {status: to};
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
