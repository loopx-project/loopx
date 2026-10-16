/** Scoped writes use the existing Goal configuration transaction. Never carry a
 * write-only pointer or enabled list from one Agent to another. */
export function rewardMemoryWriteValue(draft: Record<string, unknown>): Record<string, unknown> {
  const agent = typeof draft.agent_id === "string" ? draft.agent_id.trim() : "";
  if (agent) return draft.enabled === false ? {agent_id: agent, enabled: false}
    : {agent_id: agent, enabled: true, ...(draft.config_path ? {config_path: draft.config_path} : {})};
  return {enabled_agents: draft.enabled_agents ?? [], ...(draft.config_path ? {config_path: draft.config_path} : {})};
}

export function rewardMemoryScopeDraft(current: Record<string, unknown> | null | undefined, agent: unknown): Record<string, unknown> {
  const id = typeof agent === "string" ? agent : "";
  const rows = Array.isArray(current?.agent_bindings) ? current.agent_bindings as Record<string, unknown>[] : [];
  const row = rows.find((item) => item.agent_id === id);
  return {agent_id: id, config_path: "", enabled_agents: current?.enabled_agents ?? [],
    enabled: row ? row.enabled === true : Array.isArray(current?.enabled_agents) && current.enabled_agents.includes(id)};
}

export function rewardMemoryBindingStatus(status: unknown, chinese: boolean): string {
  const labels: Record<string, readonly [string, string]> = {
    available: ["Available", "可用"], disabled: ["Disabled", "已关闭"],
    agent_not_enabled: ["Not enabled", "尚未启用"],
    config_missing: ["Configuration missing", "缺少配置"],
    config_invalid: ["Configuration unavailable or invalid", "配置不可用或无效"],
    enablement_stale: ["Configuration changed; verify again", "配置已变化，需重新验证"],
    enablement_unverified: ["Provider verification required", "需要验证 Provider"],
  };
  return labels[String(status)]?.[chinese ? 1 : 0] ?? (chinese ? "状态未知" : "State unknown");
}
