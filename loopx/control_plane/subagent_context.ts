/** multi_subagent owns its workflow guidance; the hook mechanism stays generic. */
import { AGENT_CONTEXT_PHASES, projectAgentContext, type AgentContextProvider } from "./agent_context.ts";
import type { JsonObject } from "./effect_program.ts";
import { jsonObject, requireJsonObject, requireStringArray } from "./runtime_decode.ts";
import { isAbsolute } from "node:path";
import { delegationRuntimeFacts, selectDelegationBinding } from "./collaboration/delegation.ts";

export const subagentContextProvider: AgentContextProvider = {
  hookId: "multi_subagent.coordinator", capabilityId: "multi_subagent", revision: "v6",
  phases: AGENT_CONTEXT_PHASES,
  produce(input, config) {
    const guidance = {
      before_plan: [
        "Prefer bounded independent delegation; max_children is a configured ceiling, not live availability. Admit native children incrementally, avoid duplicate reads, and keep one parent question.",
        "Host-native child work uses loopx native-child record/read for bounded Turn receipts; these calls do not start Turns or spend quota.",
        "Choose independent work from current routes and user preferences. Runtime availability is not entrypoint admission; inspect bound delegation. Peer-activation blocks do not assess native children or delegation. Do not relaunch every route on every heartbeat.",
      ],
      before_delegate: [
        "Give each child a bounded question, sources, read/write limits, expected evidence and stopping condition; identify dependencies and the coordinator's concurrent question.",
        "For an authorized route, use its binding entrypoint and recheck the chosen runtime, execution profile and budget. Never silently substitute a runtime/model; record the selection reason and stable operation id. Report each native decision or bounded skip; missing reports stay unknown.",
      ],
      after_delegate_result: [
        "Check returned sources, omissions and contradictions against the question. Record native child result and parent review separately, then reconcile fresh Turn receipts and bound delegation receipts; missing or rejected receipts do not establish completed work.",
        "On typed agent_thread_limit_reached, stop same-Turn spawn/followup retries, mark unlaunched work incomplete, and continue useful parent work.",
        "Verify decisive sources and record accept/defer/reject with reasons. Link accepted evidence to the deliverable and run parent validation before writeback; opinions are not independent evidence.",
      ],
    }[input.phase];
    const facts: JsonObject = {
      max_children: config.max_children,
      capacity_contract: {
        schema_version: "multi_subagent_capacity_v0",
        configured_limit_kind: "upper_bound",
        live_availability: "not_observed",
      },
      model_preference: jsonObject(config.model_config),
    };
    const count = input.observations.child_count;
    if (Number.isInteger(count) && Number(count) >= 0) facts.child_count = count;
    // A signed planning observation replayed by the host wins over fresh facts.
    const delegation = input.observations.delegation_context !== undefined
      ? boundedDelegationContext(input.observations.delegation_context)
      : input.observations.delegation_facts != null
        ? projectDelegationContext(input.observations.delegation_facts, String(input.scope.agent_id), input.phase)
        : null;
    if (delegation && input.phase !== "after_delegate_result") {
      facts.delegation_context = delegation;
    }
    if (input.phase === "after_delegate_result") {
      const nativeCapacity = boundedNativeCapacityObservation(
        input.observations.native_host_capacity,
      );
      if (nativeCapacity) {
        facts.native_host_capacity = nativeCapacity;
        const contract = facts.capacity_contract as JsonObject;
        contract.live_availability = nativeCapacity.outcome === "agent_thread_limit_reached"
          ? "capacity_exhausted" : "attempt_observed";
      }
      const nativeActivity = boundedNativeChildActivity(input.observations.native_child_activity);
      if (nativeActivity) {
        facts.native_child_activity = nativeActivity;
        facts.native_receipt_observation = nativeActivity.observation;
      }
      const counts = jsonObject(input.observations.reconciliation_counts);
      facts.receipt_observation = counts ? "host_reconciled" : "not_supplied";
      if (counts) facts.reconciliation_counts = Object.fromEntries(
        Object.entries(counts).filter(([key, item]) =>
          /^[a-z_]{1,40}$/u.test(key) && Number.isInteger(item) && Number(item) >= 0),
      );
      if (delegation) facts.delegation_receipts = {
        configuration_state: delegation.configuration_state,
        observed_at: delegation.observed_at,
        operation_receipts: delegation.operation_receipts,
      };
    }
    return { guidance, facts, source_refs: [
      "goal_boundary.orchestration", "docs/integrations/host-native-child-receipts.md",
    ] };
  },
};

function boundedNativeCapacityObservation(value: unknown): JsonObject | null {
  const source = jsonObject(value);
  if (!source || source.schema_version !== "native_subagent_capacity_observation_v0") {
    return null;
  }
  const operation = String(source.operation ?? "");
  const outcome = String(source.outcome ?? "");
  if (!["spawn", "followup"].includes(operation)
    || !["succeeded", "agent_thread_limit_reached"].includes(outcome)) {
    return null;
  }
  const result: JsonObject = {
    schema_version: "native_subagent_capacity_observation_v0",
    operation,
    outcome,
  };
  const childCount = source.child_count;
  if (Number.isInteger(childCount) && Number(childCount) >= 0) {
    result.child_count = Math.min(Number(childCount), 10_000);
  }
  if (outcome === "agent_thread_limit_reached") {
    result.retry_same_turn = false;
    result.reason_code = "agent_thread_limit_reached";
    result.recovery_actions = [
      "continue_parent_work",
      "defer_unlaunched_children",
      "retry_after_capacity_change",
    ];
  }
  return result;
}

function boundedNativeChildActivity(value: unknown): JsonObject | null {
  const source = jsonObject(value);
  if (!source || source.schema_version !== "native_subagent_activity_v0"
    || source.entrypoint_scope !== "host_native_child_tools") return null;
  const observation = String(source.observation ?? "");
  if (!["unknown", "coordinator_reported", "host_observed", "mixed"].includes(observation)) return null;
  const count = (key: string) => Number.isInteger(source[key]) && Number(source[key]) >= 0
    ? Math.min(Number(source[key]), 10_000) : 0;
  const result: JsonObject = {
    schema_version: "native_subagent_activity_v0",
    entrypoint_scope: "host_native_child_tools",
    observation,
    host_attested: observation === "host_observed",
    configured_limit_kind: "upper_bound",
    configured_limit: count("configured_limit"),
    attempted_count: count("attempted_count"),
    launched_count: count("launched_count"),
    skipped_count: count("skipped_count"),
    capacity_rejected_count: count("capacity_rejected_count"),
    host_failed_count: count("host_failed_count"),
    parent_accepted_count: count("parent_accepted_count"),
  };
  if (source.retry_same_turn === false) result.retry_same_turn = false;
  return result;
}

const MAX_ROUTES = 6;
const DELEGATION_SCHEMA = "loopx_delegation_context_v0";
const RECEIPT_STATUSES = ["prepared", "running", "turn_returned", "accepted", "rejected", "stopped", "unavailable"];
const identifier = (input: unknown): string | null => typeof input === "string"
  && /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$/u.test(input) ? input : null;
function check(ok: unknown): asserts ok {
  if (!ok) throw new Error("delegation facts rejected");
}

/** Planning discloses scope only; inspect the same binding for full probe and
 * remedies. The route budget and the contribution budget stay unchanged. */
function boundDelegation(source: { configuration_state: string; observed_at: string;
  authorized_count: number; routes: JsonObject[]; operation_receipts?: JsonObject;
  reason_code?: string }): JsonObject {
  const result: JsonObject = {
    schema_version: DELEGATION_SCHEMA,
    configuration_state: source.configuration_state,
    observed_at: source.observed_at,
    authorized_count: source.authorized_count,
    projected_count: 0,
    entrypoint: "loopx delegation",
    execution_scope: "bound_delegation", preflight: "required",
    routes: [],
  };
  if (source.operation_receipts) result.operation_receipts = source.operation_receipts;
  if (source.reason_code) result.reason_code = source.reason_code;
  const projectedRoutes: JsonObject[] = [];
  for (const route of source.routes) {
    const candidate = { ...result, projected_count: projectedRoutes.length + 1,
      routes: [...projectedRoutes, route] };
    if (new TextEncoder().encode(JSON.stringify(candidate)).length > 900) break;
    projectedRoutes.push(route);
  }
  result.projected_count = projectedRoutes.length;
  result.routes = projectedRoutes;
  if (source.authorized_count > projectedRoutes.length
    || source.routes.length > projectedRoutes.length) result.routes_truncated = true;
  return result;
}

function delegationRoute(binding: JsonObject, executor: JsonObject,
  readiness: string, runtimeReadiness: string): JsonObject[] {
  const bindingId = identifier(binding.id), agentId = identifier(binding.agent_id);
  const todoId = identifier(binding.todo_id), runtimeId = identifier(executor.executor);
  if (!bindingId || !agentId || !todoId || !runtimeId) return [];
  const compact: JsonObject = {
    binding_id: bindingId, agent_id: agentId, todo_id: todoId, runtime_id: runtimeId,
    readiness, runtime_readiness: runtimeReadiness,
  };
  const executorKind = identifier(executor.executor_kind);
  const profile = typeof executor.execution_profile === "string" ? executor.execution_profile.trim() : "";
  const reason = identifier(executor.unavailable_reason);
  if (executorKind) compact.executor_kind = executorKind;
  if (/^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}$/u.test(profile)) compact.execution_profile = profile;
  if (reason) compact.reason_code = reason;
  const probe = jsonObject(delegationRuntimeFacts(executor).runtime_probe);
  if (probe) compact.probe_scope = probe.scope;
  return [compact];
}

function operationReceipts(inventory: JsonObject): JsonObject {
  check(Array.isArray(inventory.items));
  const rows = inventory.items.flatMap(item => { const row = jsonObject(item); return row ? [row] : []; });
  const summary: JsonObject = { observed: inventory.items.length };
  for (const key of RECEIPT_STATUSES) {
    const count = rows.filter(row => (row.status ? String(row.status) : "unavailable") === key).length;
    if (count) summary[key] = count;
  }
  const recovery = rows.filter(row => row.recovery_required === true).length;
  if (recovery) summary.recovery_required = recovery;
  if (inventory.has_more === true) summary.has_more = true;
  return summary;
}

/** Classify the host's raw delegation facts once. The configuration remains the
 * execution-grant owner; this observation never admits, launches or accepts work.
 * Unreadable or malformed facts degrade to a blocked observation, never a failure. */
export function projectDelegationContext(
  value: unknown, agentId: string, phase: string,
  observedAt = `${new Date().toISOString().slice(0, 19)}+00:00`,
): JsonObject {
  const receipts = phase === "after_delegate_result";
  try {
    const facts = requireJsonObject(value, "delegation facts");
    check(facts.config_error == null);
    const config = requireJsonObject(facts.config, "delegation configuration");
    check(Array.isArray(config.bindings) && config.bindings.every(row => jsonObject(row)));
    const registered = facts.registered_agent_ids;
    check(registered === null || (Array.isArray(registered) && registered.includes(agentId)));
    const executors = Array.isArray(facts.executors) ? facts.executors : [];
    const bindings = config.bindings.flatMap(row => {
      const candidate = jsonObject(row);
      if (!candidate || !Array.isArray(candidate.requesters) || !candidate.requesters.includes(agentId)) return [];
      const binding = selectDelegationBinding({ config, binding_id: candidate.id ?? null, agent_id: agentId });
      check(Array.isArray(registered) && registered.includes(binding.agent_id)
        && typeof binding.workspace === "string" && isAbsolute(binding.workspace));
      return [binding];
    });
    const routes = bindings.slice(0, MAX_ROUTES).flatMap(binding => {
      const hostArgs = requireStringArray(binding.host_args, "binding host arguments");
      const match = executors.map(item => jsonObject(item)).find(item => item && Array.isArray(item.host_args)
        && item.host_args.length === hostArgs.length && item.host_args.every((arg, index) => arg === hostArgs[index]));
      const executor = requireJsonObject(match?.executor, "executor projection");
      const available = executor.available;
      return delegationRoute(binding, {
        ...executor, executor: executor.executor || "unknown",
        executor_kind: executor.executor_kind || "generic",
        unavailable_reason: executor.unavailable_reason ? String(executor.unavailable_reason).trim() : "",
      }, available === false ? "blocked" : "unknown",
        available === true ? "ready" : available === false ? "blocked" : "unknown");
    });
    return boundDelegation({
      configuration_state: "ready", observed_at: observedAt, authorized_count: bindings.length, routes,
      ...(receipts ? { operation_receipts: operationReceipts(
        requireJsonObject(facts.operation_inventory, "operation inventory")) } : {}),
    });
  } catch {
    return boundDelegation({
      configuration_state: "blocked", observed_at: observedAt, authorized_count: 0, routes: [],
      ...(receipts ? { operation_receipts: { observed: 0 } } : {}),
      reason_code: "delegation_context_unavailable",
    });
  }
}

/** Decode an already-projected observation replayed from the signed envelope. */
function boundedDelegationContext(value: unknown): JsonObject | null {
  const source = jsonObject(value);
  if (!source || source.schema_version !== DELEGATION_SCHEMA) return null;
  const configurationState = String(source.configuration_state ?? "");
  if (!["not_configured", "ready", "blocked"].includes(configurationState)) return null;
  const observedAt = String(source.observed_at ?? "");
  if (!/^\d{4}-\d{2}-\d{2}T[^\s]{1,40}$/u.test(observedAt)) return null;
  const readinessOf = (input: unknown) => ["ready", "blocked", "unknown"].includes(String(input)) ? String(input) : null;
  const routes = (Array.isArray(source.routes) ? source.routes.slice(0, MAX_ROUTES) : []).flatMap(item => {
    const route = jsonObject(item);
    const readiness = route && readinessOf(route.readiness);
    if (!route || !readiness) return [];
    const runtimeReadiness = readinessOf(route.runtime_readiness) ?? readiness;
    return delegationRoute({ id: route.binding_id, agent_id: route.agent_id, todo_id: route.todo_id },
      { ...route, executor: route.runtime_id, unavailable_reason: route.reason_code },
      readiness === "blocked" ? "blocked" : "unknown", runtimeReadiness);
  });
  const rawReceipts = jsonObject(source.operation_receipts);
  const receipts: JsonObject = {};
  if (rawReceipts) {
    for (const key of ["observed", ...RECEIPT_STATUSES, "recovery_required"]) {
      if (Number.isInteger(rawReceipts[key]) && Number(rawReceipts[key]) >= 0) {
        receipts[key] = Math.min(Number(rawReceipts[key]), 10_000);
      }
    }
    if (rawReceipts.has_more === true) receipts.has_more = true;
  }
  const reason = identifier(source.reason_code);
  return boundDelegation({
    configuration_state: configurationState, observed_at: observedAt,
    authorized_count: Number.isInteger(source.authorized_count) && Number(source.authorized_count) >= 0
      ? Math.min(Number(source.authorized_count), 10_000) : 0,
    routes,
    ...(rawReceipts ? { operation_receipts: receipts } : {}),
    ...(reason ? { reason_code: reason } : {}),
  });
}

export function subagentContextConfiguration(value: unknown): JsonObject {
  const policy = jsonObject(value) ?? {};
  const enabled = policy.mode === "multi_subagent" && policy.spawn_allowed === true
    && Number.isInteger(policy.max_children) && Number(policy.max_children) > 0;
  return { ...policy, enabled };
}

export function evaluateSubagentContext(value: unknown): JsonObject | null {
  const input = requireJsonObject(value, "subagent context");
  return projectAgentContext({
    phase: input.phase, scope: input.scope, observations: input.observations ?? {},
    capabilities: { multi_subagent: subagentContextConfiguration(input.orchestration) },
  }, [subagentContextProvider]);
}


export function describeSubagentContext(): JsonObject {
  return { supported_phases: [...subagentContextProvider.phases],
    target: "coordinator", activation: "with_capability", receipt_required: true };
}
