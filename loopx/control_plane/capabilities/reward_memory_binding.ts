import type { JsonObject } from "../effect_program.ts";
import { createHash } from "node:crypto";
import { normalizeTodoAgent, normalizeRegisteredTodoAgents } from "../coordination/todo_agents.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject, requireBoolean, requireJsonObject, requireStringArray } from "../runtime_decode.ts";

/** Exact Agent overrides, including malformed/disabled ones, never inherit. */
export function selectRewardMemoryBinding(params: JsonObject): JsonObject {
  const policy = requireJsonObject(params.policy, "policy");
  const agent = normalizeTodoAgent(params.agent_id, "agent_id");
  if (!Object.hasOwn(policy, "agent_bindings")) return { policy };
  const bindings = jsonObject(policy.agent_bindings);
  if (!bindings) return { policy: {}, binding_agent_id: agent };
  if (Object.hasOwn(bindings, agent)) {
    const binding = jsonObject(bindings[agent]) ?? {};
    const revision = createHash("sha256").update(JSON.stringify([
      agent, binding.config_path ?? "", binding.config_digest ?? "",
    ])).digest("hex");
    return { policy: binding, binding_agent_id: agent, binding_revision: `sha256:${revision}` };
  }
  return { policy };
}

/** Configuration target admission; provider IO remains in the existing adapter. */
export function planRewardMemoryBinding(params: JsonObject): JsonObject {
  const agent = params.agent_id == null ? null : normalizeTodoAgent(params.agent_id, "agent_id");
  const registered = normalizeRegisteredTodoAgents(requireStringArray(params.registered_agents, "registered_agents"));
  const clear = requireBoolean(params.clear, "clear");
  const configRequested = requireBoolean(params.config_requested, "config_requested");
  if (agent && !registered.includes(agent)) {
    throw new EffectRuntimeRequestError("--reward-memory-agent-id must name an already registered Goal Agent");
  }
  if (agent && params.requested_agents != null) {
    throw new EffectRuntimeRequestError("--reward-memory-agent-id cannot be combined with --reward-memory-agent; it selects one independent binding");
  }
  if (clear && (configRequested || params.requested_agents != null)) {
    throw new EffectRuntimeRequestError("--clear-reward-memory-config cannot be combined with configuration inputs");
  }
  return { agent_id: agent, enabled_agents: agent ? [agent] : params.requested_agents };
}

/** Patch one route atomically while retaining every sibling and legacy field. */
export function patchRewardMemoryBinding(params: JsonObject): JsonObject {
  const current = requireJsonObject(params.current, "current");
  const agent = params.agent_id == null ? null : normalizeTodoAgent(params.agent_id, "agent_id");
  const clear = requireBoolean(params.clear, "clear");
  if (!agent && clear) return { policy: null };
  const binding = clear ? { enabled: false, experimental: true }
    : requireJsonObject(params.binding, "binding");
  if (!agent) {
    return { policy: { ...binding,
      ...(Object.hasOwn(current, "agent_bindings") ? { agent_bindings: current.agent_bindings } : {}) } };
  }
  const siblings = Object.hasOwn(current, "agent_bindings")
    ? requireJsonObject(current.agent_bindings, "agent_bindings") : {};
  return { policy: { ...current, agent_bindings: { ...siblings, [agent]: binding } } };
}

/** Aggregate read-only IO observations; never infer provider verification from
 * a configured pointer or turn a disabled Agent into an inherited route. */
export function projectRewardMemoryBindings(params: JsonObject): JsonObject {
  const summary = requireJsonObject(params.summary, "summary");
  if (!Array.isArray(params.observations)) throw new EffectRuntimeRequestError("observations must be an array");
  const observations = params.observations.map(value => requireJsonObject(value, "observation"));
  const rows = observations.map(item => ({
    agent_id: item.agent_id, binding_source: item.binding_source,
    enabled: item.enabled === true && item.configured_for_agent === true,
    status: item.status, available: item.available === true,
    binding_status: item.binding_status, config_pointer_registered: item.config_pointer_registered === true,
    automatic_ingest: item.automatic_ingest === true, automatic_recall: item.automatic_recall === true,
  }));
  const states = rows.filter(row => row.enabled).map(row => row.binding_status);
  const verified = rows.filter(row => row.available).map(row => row.agent_id).sort();
  const recorded = observations.filter(item => {
    const receipt = jsonObject(item.receipt) ?? {};
    return receipt.status === "verified" && receipt.writability_verified === true && receipt.exact_readback_verified === true;
  }).map(item => item.agent_id).sort();
  const revision = createHash("sha256").update(JSON.stringify(observations.map(item => item.binding_identity))).digest("hex");
  return { ...summary, agent_bindings: rows, enabled: rows.some(row => row.enabled),
    experimental: summary.experimental === true || rows.some(row => row.enabled),
    config_pointer_registered: rows.some(row => row.config_pointer_registered),
    binding_status: states.includes("drifted") ? "drifted" : states.includes("unavailable") ? "unavailable"
      : states.includes("unbound") ? "unbound" : states.includes("verified") ? "verified" : "unbound",
    binding_revision: `sha256:${revision}`, recorded_verified_agents: recorded,
    enablement_verified_agents: verified, effective_available: verified.length > 0,
    automatic_ingest: rows.some(row => row.automatic_ingest), automatic_recall: rows.some(row => row.automatic_recall) };
}

function declaredBinding(value: unknown): JsonObject {
  const config = requireJsonObject(value, "configuration");
  return Object.fromEntries(["enabled", "experimental", "config_pointer_registered",
    "binding_revision", "enabled_agents", "registered_agents"].map(key => [key, config[key]]));
}

/** Provider verification changes observations between preview and apply. Verify
 * the unchanged declaration plus the actual activation receipts, not equality
 * between an unverified preview and a verified runtime observation. */
export function verifyRewardMemoryBindingReadback(params: JsonObject): JsonObject {
  const actual = requireJsonObject(params.actual, "actual");
  const expected = requireStringArray(params.expected_verified_agents, "expected_verified_agents");
  const verified = requireStringArray(actual.enablement_verified_agents ?? [], "enablement_verified_agents");
  return { verified: JSON.stringify(declaredBinding(actual)) === JSON.stringify(declaredBinding(params.desired))
    && expected.every(agent => verified.includes(agent)) };
}
