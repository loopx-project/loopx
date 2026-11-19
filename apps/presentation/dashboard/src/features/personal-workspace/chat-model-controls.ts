import type { AgentModelDiscovery, ChatSessionSummary } from "../../data/chat";

/** A late Session response must not replace a pending owner selection. */
export function modelSelectionFromReadback(
  readback: Pick<ChatSessionSummary, "agent_model_id" | "agent_reasoning_effort" | "requested_agent_model_id" | "requested_agent_reasoning_effort">,
  requested: { model?: string; effort?: string },
) {
  const model = readback.requested_agent_model_id ?? readback.agent_model_id;
  const effort = readback.requested_agent_reasoning_effort ?? readback.agent_reasoning_effort;
  const sameModel = !requested.model || requested.model === model;
  return {
    model: sameModel ? model ?? "" : undefined,
    effort: sameModel && (!requested.effort || requested.effort === effort) ? effort ?? "" : undefined,
  };
}

/** Catalog choices are candidates; requested and observed Session settings stay distinct. */
export function chatModelControls(
  discovery: AgentModelDiscovery | null,
  selectedModel: string,
  selectedEffort: string | undefined,
  readback?: ChatSessionSummary,
) {
  const modelOptions = discovery?.model?.options ?? [];
  const nativeEfforts = modelOptions.find((option) => option.value === selectedModel)?.reasoning_efforts;
  const reasoningOptions = nativeEfforts
    ? nativeEfforts.map((value) => ({ value, label: value }))
    : discovery?.reasoning_effort?.options ?? [];
  const observedModel = readback?.agent_model_id ?? null;
  const effort = selectedEffort ?? (observedModel && observedModel === selectedModel
    ? readback?.agent_reasoning_effort ?? "" : "");
  return { modelOptions, reasoningOptions, effort, observedModel,
    observedEffort: readback?.agent_reasoning_effort ?? null,
    observedTier: readback?.agent_service_tier ?? null };
}
