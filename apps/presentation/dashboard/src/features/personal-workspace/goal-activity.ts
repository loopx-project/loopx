// Execution is a fact read from the chat/session owner. Open Todos, quota
// eligibility, registration or a persistent session never imply it.
export type WorkspaceGoalExecution =
  | { kind: "running"; agentIds: string[]; lastActivityAt: string | null; sessionCount: number }
  | { kind: "idle" }
  | { kind: "unknown" };

export type GoalSessionFact = {
  goal_id: string;
  agent_id: string;
  active_turn_id: string | null;
  last_activity_at?: string | null;
  updated_at?: string | null;
};

/** `sessions === null` means the session owner could not be read. */
export function goalExecutionFromSessions(sessions: readonly GoalSessionFact[] | null, goalId: string): WorkspaceGoalExecution {
  if (sessions === null) return { kind: "unknown" };
  const active = sessions.filter((session) => session.goal_id === goalId && Boolean(session.active_turn_id));
  if (active.length === 0) return { kind: "idle" };
  const lastActivityAt = active
    .map((session) => session.last_activity_at || session.updated_at || "")
    .filter(Boolean)
    .sort()
    .at(-1) ?? null;
  return {
    kind: "running",
    agentIds: Array.from(new Set(active.map((session) => session.agent_id))),
    lastActivityAt,
    sessionCount: active.length,
  };
}

export type GoalActivityTone = "running" | "attention" | "danger" | "waiting" | "queued" | "quiet" | "stopped";

export type GoalActivity = {
  /** i18n key for the primary line. */
  labelKey: string;
  tone: GoalActivityTone;
  /** True only for a verified active turn; drives the live indicator. */
  live: boolean;
  /** Secondary fact that must stay visible alongside the primary one. */
  alsoKey: string | null;
};

type GoalActivityInput = {
  activationState: "active" | "stopped";
  execution?: WorkspaceGoalExecution;
  needsYou?: string | null;
  state: string;
};

export function presentGoalActivity(goal: GoalActivityInput): GoalActivity {
  const running = goal.activationState === "active" && goal.execution?.kind === "running";
  if (goal.activationState === "stopped" || goal.state === "已停止") return { labelKey: "state.stopped", tone: "stopped", live: false, alsoKey: null };
  if (goal.state === "等你" || goal.needsYou) return { labelKey: "state.needsYou", tone: "attention", live: running, alsoKey: running ? "activity.alsoRunning" : null };
  if (goal.state === "需修复") return { labelKey: "state.needsRepair", tone: "danger", live: running, alsoKey: running ? "activity.alsoRunning" : null };
  if (running) return { labelKey: "activity.running", tone: "running", live: true, alsoKey: null };
  if (goal.state === "等待条件") return { labelKey: "state.waiting", tone: "waiting", live: false, alsoKey: null };
  if (goal.state === "已安排") {
    return { labelKey: "state.queued", tone: "queued", live: false, alsoKey: goal.execution?.kind === "unknown" ? "activity.executionUnknown" : null };
  }
  if (goal.state === "已完成") return { labelKey: "state.completed", tone: "quiet", live: false, alsoKey: null };
  return { labelKey: "state.quiet", tone: "quiet", live: false, alsoKey: null };
}

const identityHues = [212, 262, 330, 24, 150, 190, 44, 290] as const;

/** Stable, readable identity for scanning; never a status signal. */
export function goalIdentity(goalId: string, title: string): { glyph: string; hue: number } {
  let hash = 2_166_136_261;
  for (const character of goalId) {
    hash ^= character.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16_777_619);
  }
  const glyph = Array.from(title.trim().replace(/^[^\p{L}\p{N}]+/u, ""))[0]?.toLocaleUpperCase() ?? "#";
  return { glyph, hue: identityHues[(hash >>> 0) % identityHues.length] };
}
