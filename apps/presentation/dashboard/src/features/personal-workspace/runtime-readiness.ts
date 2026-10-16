/**
 * Readiness of the Agent runtimes the Chat service can drive.
 *
 * Availability is the service's live probe. This module only classifies it for
 * the owner: nothing is ready yet, the runtime they picked is not available
 * here, or they can proceed. Unknown stays "detecting"; an empty list is not
 * evidence that nothing is installed.
 */

export type RuntimeAgentReadiness = {
  agentId: string;
  label: string;
  available: boolean;
  unavailableReason?: string | null;
  executable?: string | null;
  environmentVariable?: string | null;
};

export type RuntimeReadiness =
  | { kind: "detecting" }
  | { kind: "ready"; selected: RuntimeAgentReadiness | null }
  | { kind: "selected_unavailable"; selected: RuntimeAgentReadiness; alternatives: RuntimeAgentReadiness[] }
  | { kind: "none_available"; missing: RuntimeAgentReadiness[] };

export function deriveRuntimeReadiness(agents: readonly RuntimeAgentReadiness[], selectedAgentId: string | null | undefined): RuntimeReadiness {
  if (agents.length === 0) return { kind: "detecting" };
  const available = agents.filter((agent) => agent.available);
  if (available.length === 0) return { kind: "none_available", missing: [...agents] };
  const selected = agents.find((agent) => agent.agentId === selectedAgentId) ?? null;
  if (selected && !selected.available) return { kind: "selected_unavailable", selected, alternatives: available };
  return { kind: "ready", selected };
}

/** What the owner has to supply for one runtime, in a form copy can name. */
export type RuntimeRequirement =
  | { kind: "executable"; name: string }
  | { kind: "environment_variable"; name: string }
  | { kind: "other"; reason: string | null };

export function runtimeRequirement(agent: RuntimeAgentReadiness): RuntimeRequirement {
  if (agent.unavailableReason === "executable_not_found" && agent.executable) return { kind: "executable", name: agent.executable };
  if (agent.unavailableReason === "api_key_not_set" && agent.environmentVariable) return { kind: "environment_variable", name: agent.environmentVariable };
  return { kind: "other", reason: agent.unavailableReason ?? null };
}

export const runtimeSetupGuideUrl = "https://github.com/loopx-project/loopx/blob/main/docs/guides/getting-started.md#codex-app-and-other-agent-setup";
