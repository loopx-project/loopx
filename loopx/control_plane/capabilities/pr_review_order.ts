/** Direction and scoped intent belong to PR review; GitHub IO grants no authority. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

export type PrReviewOrder = "forward" | "reverse";
type PrReviewOrderSource = "agent_override" | "goal_override" | "machine_default" | "capability_default";
function fail(message: string): never {throw new EffectRuntimeRequestError(message);}
function ownerLogins(value: unknown): string[] {
  if (!Array.isArray(value)) return fail("owner_logins must be an array of GitHub logins");
  return [...new Set(value.map(login => {
    if (typeof login !== "string" || !/^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?(?:\[bot\])?$/i.test(login)) {
      return fail("owner_logins requires GitHub logins, not URLs or organization membership");
    }
    return login.toLowerCase();
  }))];
}
export function prReviewOrder(value: unknown): PrReviewOrder {
  if (value === "forward" || value === "reverse") return value;
  return fail("review_order must be forward or reverse");
}
/** v0 persisted preferences remain readable; new writes use only review_order. */
export function normalizePrReviewConfiguration(value: unknown, allowAgents: boolean): JsonObject {
  const raw = requireJsonObject(value, "PR review configuration");
  const allowed = new Set(["wait_for_ci", "review_order", "review_priority", "owner_logins", ...(allowAgents ? ["agent_orders"] : [])]);
  for (const key of Object.keys(raw)) if (!allowed.has(key)) fail(`unsupported pull_request_review field: ${key}`);
  const result: JsonObject = {};
  if (Object.hasOwn(raw, "owner_logins")) result.owner_logins = ownerLogins(raw.owner_logins);
  if (Object.hasOwn(raw, "wait_for_ci")) {
    if (typeof raw.wait_for_ci !== "boolean") throw new EffectRuntimeRequestError("wait_for_ci must be a boolean", "pr_review_configuration_type");
    result.wait_for_ci = raw.wait_for_ci;
  }
  if (Object.hasOwn(raw, "review_priority")) {
    if (Object.hasOwn(raw, "review_order")) fail("review_order and legacy review_priority cannot be combined");
    if (raw.review_priority !== "other-developers-first" && raw.review_priority !== "owner-first") fail("invalid legacy review_priority");
    result.review_order = raw.review_priority === "owner-first" ? "reverse" : "forward";
  } else if (Object.hasOwn(raw, "review_order")) result.review_order = prReviewOrder(raw.review_order);
  if (Object.hasOwn(raw, "agent_orders")) {
    const orders = requireJsonObject(raw.agent_orders, "agent_orders");
    result.agent_orders = Object.fromEntries(Object.entries(orders).map(([id, order]) => {
      if (!id.trim() || id !== id.trim()) fail("agent_orders requires registered Agent ids");
      return [id, prReviewOrder(order)];
    }));
  }
  return result;
}

export function prReviewConfiguration(request: JsonObject): JsonObject {
  if (request.action === "classify_owners") {
    const configured = ownerLogins(request.owner_logins ?? []);
    const reviewer = request.reviewer_login;
    if (reviewer != null && typeof reviewer !== "string") return fail("reviewer_login must be a string or null");
    const logins = [...new Set([...(reviewer ? [reviewer.toLowerCase()] : []), ...configured])];
    if (!Array.isArray(request.authors) || request.authors.some(author => typeof author !== "string")) return fail("authors must be an array of GitHub logins");
    return {owner_logins: logins, owner_authored: request.authors.map(author => logins.includes((author as string).toLowerCase()))};
  }
  if (request.action === "normalize") return normalizePrReviewConfiguration(request.configuration, request.allow_agents === true);
  if (request.action === "patch") {
    const current = request.current == null ? {} : normalizePrReviewConfiguration(request.current, true);
    const patch = request.patch == null ? {} : normalizePrReviewConfiguration(request.patch, true);
    const result = {...current, ...patch};
    if (request.agent_order_updates != null) {
      const updates = requireJsonObject(request.agent_order_updates, "agent_order_updates");
      const orders = new Map(Object.entries(requireJsonObject(result.agent_orders ?? {}, "agent_orders")));
      for (const [id, order] of Object.entries(updates)) {
        if (!Array.isArray(request.registered_agents) || !request.registered_agents.includes(id)) fail(`PR review Agent is not registered: ${id}`);
        if (order === null) orders.delete(id);
        else orders.set(id, prReviewOrder(order));
      }
      result.agent_orders = Object.fromEntries(orders);
    }
    if (request.reconcile_agents === true && result.agent_orders != null) {
      if (!Array.isArray(request.registered_agents)) fail("complete registered_agents required");
      const registered = new Set(request.registered_agents);
      result.agent_orders = Object.fromEntries(Object.entries(requireJsonObject(result.agent_orders, "agent_orders")).filter(([id]) => registered.has(id)));
    }
    prReviewConfiguration({...request, action: "resolve", goal: result, agent_id: null});
    return result;
  }
  if (request.action !== "resolve") return fail("unknown PR review configuration action");
  const defaults: JsonObject = {wait_for_ci: true, review_order: "forward"};
  const machine = request.machine == null ? defaults : {...defaults, ...normalizePrReviewConfiguration(request.machine, false)};
  const goal = request.goal == null ? null : normalizePrReviewConfiguration(request.goal, true);
  // Existing global Goal settings remain complete overrides. An Agent-only
  // patch must not freeze or change the live machine direction/CI policy.
  const globalOverride = goal !== null && (Object.hasOwn(goal, "wait_for_ci") || Object.hasOwn(goal, "review_order"));
  const config = globalOverride ? {...defaults, ...goal} : {...machine, ...(goal ?? {})};
  // Owner identity is independent of legacy complete direction/CI overrides.
  // An omitted owner list continues to inherit the live machine list.
  if (Object.hasOwn(goal ?? {}, "owner_logins") || Object.hasOwn(machine, "owner_logins")) {
    config.owner_logins = goal?.owner_logins ?? machine.owner_logins;
  }
  const orders = requireJsonObject(config.agent_orders ?? {}, "agent_orders");
  if (!Array.isArray(request.registered_agents) || request.registered_agents.some(id => typeof id !== "string")) return fail("complete registered_agents required");
  const registered = new Set(request.registered_agents as string[]);
  for (const id of Object.keys(orders)) if (!registered.has(id)) fail(`PR review Agent is not registered: ${id}`);
  const agent = request.agent_id;
  if (agent != null && (typeof agent !== "string" || !registered.has(agent))) return fail("PR review agent_id must name a registered Goal Agent");
  const selected = typeof agent === "string" && Object.hasOwn(orders, agent) ? orders[agent] : undefined;
  const source: PrReviewOrderSource = selected !== undefined ? "agent_override" : globalOverride ? "goal_override" : request.machine != null ? "machine_default" : "capability_default";
  return {...config, review_order: selected ?? config.review_order,
    order_source: source,
    agent_id: agent ?? null};
}

/** Reverse the complete forward actionable queue, never its eligibility or depth. */
export function orderPrReviewQueue(request: JsonObject): JsonObject {
  const order = prReviewOrder(request.review_order);
  if (!Array.isArray(request.items)) return fail("PR review items must be a complete array");
  const items = request.items.map(item => requireJsonObject(item, "PR review item"));
  for (const [index, item] of items.entries()) {
    if (item.index !== index) fail("PR review indices must cover the complete queue in order");
    if (item.review_action_kind !== null && typeof item.review_action_kind !== "string") fail("PR review action kind must be a string or null");
  }
  if (order === "forward") return {indices: items.map(item => item.index)};
  const actionable = items.filter(item => Boolean(item.review_action_kind)).reverse();
  const inactive = items.filter(item => !item.review_action_kind);
  return {indices: [...actionable, ...inactive].map(item => item.index)};
}
