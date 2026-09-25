// Execution is a fact read from the chat/session owner. Open Todos, quota
// eligibility, registration or a persistent session never imply it.
// Attached hosts only surface turns they claimed from LoopX; work a host starts
// on its own is invisible here, so an attached Goal is labelled as host-owned.
export type WorkspaceGoalExecution =
  | { kind: "running"; agentIds: string[]; hostSurfaces: string[]; lastActivityAt: string | null; quiet: boolean; sessionCount: number }
  | { kind: "idle"; hostSurfaces: string[] }
  | { kind: "unknown" };

export type GoalSessionFact = {
  goal_id: string;
  agent_id: string;
  active_turn_id: string | null;
  host_surface?: string | null;
  last_activity_at?: string | null;
  session_mode?: string;
  status?: string;
  updated_at?: string | null;
};

/** A claimed turn with no recorded event for this long may be silent or lost; it is never shown as live. */
export const quietTurnMinutes = 15;

function hostSurfacesOf(sessions: readonly GoalSessionFact[]) {
  return Array.from(new Set(sessions
    .filter((session) => session.session_mode === "attached_host" && session.host_surface)
    .map((session) => String(session.host_surface))));
}

/** `sessions === null` means the session owner could not be read. */
export function goalExecutionFromSessions(sessions: readonly GoalSessionFact[] | null, goalId: string, now = Date.now()): WorkspaceGoalExecution {
  if (sessions === null) return { kind: "unknown" };
  const open = sessions.filter((session) => session.goal_id === goalId && session.status !== "closed");
  const active = open.filter((session) => Boolean(session.active_turn_id));
  if (active.length === 0) return { kind: "idle", hostSurfaces: hostSurfacesOf(open) };
  const lastActivityAt = active
    .map((session) => session.last_activity_at || session.updated_at || "")
    .filter(Boolean)
    .sort()
    .at(-1) ?? null;
  const lastActivityMs = lastActivityAt ? Date.parse(lastActivityAt) : Number.NaN;
  return {
    kind: "running",
    agentIds: Array.from(new Set(active.map((session) => session.agent_id))),
    hostSurfaces: hostSurfacesOf(active),
    lastActivityAt,
    quiet: !Number.isNaN(lastActivityMs) && now - lastActivityMs > quietTurnMinutes * 60_000,
    sessionCount: active.length,
  };
}

export function hostSurfaceLabel(surface: string) {
  const value = surface.toLowerCase();
  if (value.includes("cursor")) return "Cursor";
  if (value.includes("claude")) return "Claude Code";
  if (value.includes("codex")) return "Codex";
  if (value.includes("kiro")) return "Kiro";
  return surface;
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
  const live = running && goal.execution?.kind === "running" && !goal.execution.quiet;
  if (goal.activationState === "stopped" || goal.state === "已停止") return { labelKey: "state.stopped", tone: "stopped", live: false, alsoKey: null };
  if (goal.state === "等你" || goal.needsYou) return { labelKey: "state.needsYou", tone: "attention", live, alsoKey: running ? "activity.alsoRunning" : null };
  if (goal.state === "需修复") return { labelKey: "state.needsRepair", tone: "danger", live, alsoKey: running ? "activity.alsoRunning" : null };
  if (running) return { labelKey: "activity.running", tone: live ? "running" : "attention", live, alsoKey: null };
  if (goal.state === "等待条件") return { labelKey: "state.waiting", tone: "waiting", live: false, alsoKey: null };
  if (goal.state === "已安排") {
    const inHost = goal.execution?.kind === "idle" && goal.execution.hostSurfaces.length > 0;
    return { labelKey: "state.queued", tone: "queued", live: false, alsoKey: goal.execution?.kind === "unknown" ? "activity.executionUnknown" : inHost ? "activity.inHost" : null };
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
