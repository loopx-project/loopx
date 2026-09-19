/** multi_subagent owns its workflow guidance; the hook mechanism stays generic. */
import { AGENT_CONTEXT_PHASES, projectAgentContext, type AgentContextProvider } from "./agent_context.ts";
import type { JsonObject } from "./effect_program.ts";
import { jsonObject, requireJsonObject } from "./runtime_decode.ts";

export const subagentContextProvider: AgentContextProvider = {
  hookId: "multi_subagent.coordinator", capabilityId: "multi_subagent", revision: "v3",
  phases: AGENT_CONTEXT_PHASES,
  produce(input, config) {
    const guidance = {
      before_plan: [
        "Prefer parallel delegation for bounded independent work when useful; avoid duplicate reads. Keep one decision-relevant coordinator question.",
        "Native child tools can read loopx agent-context at before_delegate and after_delegate_result; these calls do not start Turns or spend quota.",
        "Authorized routes are observations, not obligations. Use a ready route only when it fits; blocked/unknown routes never block native work. No heartbeat must use every route.",
      ],
      before_delegate: [
        "Give each child a bounded question, sources, read/write limits, expected evidence and stopping condition; identify dependencies and the coordinator's concurrent question.",
        "For an authorized route, use its binding entrypoint and recheck the chosen runtime, execution profile and budget. Never silently substitute a runtime/model; record the selection reason and stable operation id. Preferences and readiness observations are not execution receipts.",
      ],
      after_delegate_result: [
        "Check returned sources, omissions and contradictions against the question. Reconcile native child receipts and any freshly read bound delegation operation receipts; missing, unavailable or rejected receipts do not establish completed work.",
        "Verify decisive sources and record accept/defer/reject with reasons. Link accepted evidence to the deliverable and run parent validation before writeback; opinions are not independent evidence.",
      ],
    }[input.phase];
    const facts: JsonObject = {
      max_children: config.max_children,
      model_preference: jsonObject(config.model_config),
    };
    const count = input.observations.child_count;
    if (Number.isInteger(count) && Number(count) >= 0) facts.child_count = count;
    const delegation = boundedDelegationContext(input.observations.delegation_context);
    if (delegation && input.phase !== "after_delegate_result") {
      facts.delegation_context = delegation;
    }
    if (input.phase === "after_delegate_result") {
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
      "goal_boundary.orchestration", "docs/integrations/codex-subagent-orchestration.md",
    ] };
  },
};

function boundedDelegationContext(value: unknown): JsonObject | null {
  const source = jsonObject(value);
  if (!source || source.schema_version !== "loopx_delegation_context_v0") return null;
  const configurationState = String(source.configuration_state ?? "");
  if (!["not_configured", "ready", "blocked"].includes(configurationState)) return null;
  const observedAt = String(source.observed_at ?? "");
  if (!/^\d{4}-\d{2}-\d{2}T[^\s]{1,40}$/u.test(observedAt)) return null;
  const boundedCount = (input: unknown) => Number.isInteger(input) && Number(input) >= 0
    ? Math.min(Number(input), 10_000) : 0;
  const identifier = (input: unknown) => typeof input === "string"
    && /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$/u.test(input) ? input : null;
  const routes = Array.isArray(source.routes) ? source.routes.slice(0, 6).flatMap(item => {
    const route = jsonObject(item);
    if (!route) return [];
    const bindingId = identifier(route.binding_id), agentId = identifier(route.agent_id);
    const todoId = identifier(route.todo_id), runtimeId = identifier(route.runtime_id);
    const readiness = String(route.readiness ?? "");
    if (!bindingId || !agentId || !todoId || !runtimeId
      || !["ready", "blocked", "unknown"].includes(readiness)) return [];
    const compact: JsonObject = {
      binding_id: bindingId, agent_id: agentId, todo_id: todoId,
      runtime_id: runtimeId, readiness,
    };
    const executorKind = identifier(route.executor_kind);
    const profile = typeof route.execution_profile === "string"
      && /^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}$/u.test(route.execution_profile)
      ? route.execution_profile : null;
    const reason = identifier(route.reason_code);
    if (executorKind) compact.executor_kind = executorKind;
    if (profile) compact.execution_profile = profile;
    if (reason) compact.reason_code = reason;
    return [compact];
  }) : [];
  const rawReceipts = jsonObject(source.operation_receipts);
  const operationReceipts: JsonObject = {};
  if (rawReceipts) {
    for (const key of ["observed", "prepared", "running", "turn_returned", "accepted",
      "rejected", "unavailable", "recovery_required"]) {
      if (Number.isInteger(rawReceipts[key]) && Number(rawReceipts[key]) >= 0) {
        operationReceipts[key] = Math.min(Number(rawReceipts[key]), 10_000);
      }
    }
    if (rawReceipts.has_more === true) operationReceipts.has_more = true;
  }
  const result: JsonObject = {
    schema_version: "loopx_delegation_context_v0",
    configuration_state: configurationState,
    observed_at: observedAt,
    authorized_count: boundedCount(source.authorized_count),
    projected_count: 0,
    entrypoint: "loopx delegation",
    routes: [],
  };
  if (rawReceipts) result.operation_receipts = operationReceipts;
  const reason = identifier(source.reason_code);
  if (reason) result.reason_code = reason;
  const projectedRoutes: JsonObject[] = [];
  for (const route of routes) {
    const candidate = { ...result, projected_count: projectedRoutes.length + 1,
      routes: [...projectedRoutes, route] };
    if (new TextEncoder().encode(JSON.stringify(candidate)).length > 900) break;
    projectedRoutes.push(route);
  }
  result.projected_count = projectedRoutes.length;
  result.routes = projectedRoutes;
  if (boundedCount(source.authorized_count) > projectedRoutes.length
    || routes.length > projectedRoutes.length) result.routes_truncated = true;
  return result;
}

export function evaluateSubagentContext(value: unknown): JsonObject | null {
  const input = requireJsonObject(value, "subagent context");
  const policy = jsonObject(input.orchestration) ?? {};
  const enabled = policy.mode === "multi_subagent" && policy.spawn_allowed === true
    && Number.isInteger(policy.max_children) && Number(policy.max_children) > 0;
  return projectAgentContext({
    phase: input.phase, scope: input.scope, observations: input.observations ?? {},
    capabilities: { multi_subagent: { ...policy, enabled } },
  }, [subagentContextProvider]);
}


export function describeSubagentContext(): JsonObject {
  return { supported_phases: [...subagentContextProvider.phases],
    target: "coordinator", activation: "with_capability", receipt_required: true };
}
