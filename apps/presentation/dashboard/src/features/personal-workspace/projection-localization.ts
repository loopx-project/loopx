export type ProjectionMessageKey =
  | "projection.agentWorkQueued"
  | "projection.agentIdle"
  | "projection.agentNeedsDecision"
  | "projection.agentPreparingNextStep"
  | "projection.agentStopped"
  | "projection.agentWaitingExternal"
  | "projection.confirmAgentDecision"
  | "projection.firstReadOnlyAdapterCheck"
  | "projection.nextUpdatePending"
  | "projection.refreshState"
  | "projection.statusRefreshNeeded"
  | "projection.todoStatusUpdated";

export type ProjectionTranslate = (
  key: ProjectionMessageKey,
  values?: Record<string, string | number>,
) => string;

export type ProjectionAgentStatus = "queued" | "idle" | "needs_you" | "stopped" | "waiting_external";

function cleanProjectionText(value: string | null | undefined) {
  return (value ?? "").replace(/\s+/gu, " ").trim();
}

function compactProjectionText(value: string, limit = 120) {
  const characters = Array.from(value);
  return characters.length <= limit ? value : `${characters.slice(0, limit - 1).join("")}…`;
}

export function projectionSentence(
  value: string | null | undefined,
  t: ProjectionTranslate,
  fallbackKey?: ProjectionMessageKey,
) {
  const cleaned = cleanProjectionText(value);
  if (!cleaned || cleaned === "暂无") {
    return fallbackKey ? t(fallbackKey) : "";
  }
  if (/refresh-state|latest_run|latest run-derived/iu.test(cleaned)) {
    return t("projection.refreshState");
  }
  if (/first read-only adapter tick|read-only adapter/iu.test(cleaned)) {
    return t("projection.firstReadOnlyAdapterCheck");
  }
  if (/todo update recorded for/iu.test(cleaned)) {
    return t("projection.todoStatusUpdated");
  }
  if (/^(loopx|python3|npm|git|run)\s|\s--[a-z0-9-]+|\b[a-z]+_[a-z_]+\b/iu.test(cleaned)) {
    return t(fallbackKey ?? "projection.agentPreparingNextStep");
  }
  return compactProjectionText(cleaned);
}

export function agentStatusSentence(status: ProjectionAgentStatus, t: ProjectionTranslate) {
  const keyByStatus: Record<ProjectionAgentStatus, ProjectionMessageKey> = {
    queued: "projection.agentWorkQueued",
    idle: "projection.agentIdle",
    needs_you: "projection.agentNeedsDecision",
    stopped: "projection.agentStopped",
    waiting_external: "projection.agentWaitingExternal",
  };
  return t(keyByStatus[status]);
}
