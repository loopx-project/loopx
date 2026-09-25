// Execution is a fact read from the chat/session owner or from a host's own
// thread records. Open Todos, quota eligibility, registration, a persistent
// session or a thread binding never imply it. Attached hosts only surface turns
// they claimed from LoopX, and the claim timestamp is their only activity fact.
export type WorkspaceGoalExecution =
  | { kind: "running"; hostClaimed: boolean; hostSurfaces: string[]; lastActivityAt: string | null; quiet: boolean }
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

/** One bound host thread as observed by the host adapter (`host_thread_activity`). */
export type GoalHostThread = {
  hostSurface: string;
  state: "turn_open" | "idle" | "archived" | "unknown";
  lastEventAt: string | null;
};

/** A turn with no recorded event for this long may be silent or lost; it is never shown as live. */
export const quietTurnMinutes = 15;
/** A host can exit mid-turn without recording an end; after this long an open turn is not execution. */
export const abandonedHostTurnHours = 6;

function hostSurfacesOf(sessions: readonly GoalSessionFact[]) {
  return Array.from(new Set(sessions
    .filter((session) => session.session_mode === "attached_host" && session.host_surface)
    .map((session) => String(session.host_surface))));
}

function openHostTurns(threads: readonly GoalHostThread[], now: number) {
  return threads.filter((thread) => {
    const lastEventMs = thread.lastEventAt ? Date.parse(thread.lastEventAt) : Number.NaN;
    return thread.state === "turn_open" && !Number.isNaN(lastEventMs) && now - lastEventMs <= abandonedHostTurnHours * 3_600_000;
  });
}

/**
 * `sessions === null` means the session owner could not be read; `undefined`
 * means it has not been read yet, so only host-observed turns are known.
 */
export function goalExecution(
  sessions: readonly GoalSessionFact[] | null | undefined,
  goalId: string,
  hostThreads: readonly GoalHostThread[] = [],
  now = Date.now(),
): WorkspaceGoalExecution | undefined {
  const hostTurns = openHostTurns(hostThreads, now);
  const open = (sessions ?? []).filter((session) => session.goal_id === goalId && session.status !== "closed");
  const active = open.filter((session) => Boolean(session.active_turn_id));
  if (active.length === 0 && hostTurns.length === 0) {
    if (sessions === undefined) return undefined;
    return sessions === null ? { kind: "unknown" } : { kind: "idle", hostSurfaces: hostSurfacesOf(open) };
  }
  const lastActivityAt = [
    ...active.map((session) => session.last_activity_at || session.updated_at || ""),
    ...hostTurns.map((thread) => thread.lastEventAt ?? ""),
  ].filter(Boolean).sort().at(-1) ?? null;
  const lastActivityMs = lastActivityAt ? Date.parse(lastActivityAt) : Number.NaN;
  const hostClaimed = hostTurns.length === 0 && active.every((session) => session.session_mode === "attached_host");
  return {
    kind: "running",
    hostClaimed,
    hostSurfaces: Array.from(new Set([...hostSurfacesOf(active), ...hostTurns.map((thread) => thread.hostSurface)])),
    lastActivityAt,
    quiet: !hostClaimed && !Number.isNaN(lastActivityMs) && now - lastActivityMs > quietTurnMinutes * 60_000,
  };
}

/** Surface ids as registered by `bind-agent-thread`; anything else is shown verbatim. */
const hostSurfaceNames: Readonly<Record<string, string>> = {
  "claude-code": "Claude Code",
  "codex-app": "Codex App",
  "codex-app-ssh": "Codex App (SSH)",
  "codex-cli-tui": "Codex CLI",
  "codex-ide-plugin": "Codex IDE",
  cursor: "Cursor",
  kiro: "Kiro",
};

export function hostSurfaceLabel(surface: string) {
  return hostSurfaceNames[surface] ?? surface;
}

/** Hosts that own work for this Goal outside LoopX: bound threads plus idle attached sessions. */
export function goalHostSurfaces(goal: Pick<GoalActivityInput, "boundHostSurfaces" | "execution">): string[] {
  const attached = goal.execution && goal.execution.kind !== "unknown" ? goal.execution.hostSurfaces : [];
  return Array.from(new Set([...(goal.boundHostSurfaces ?? []), ...attached]));
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
  /** `host_surface` of each thread bound to this Goal in the status projection. */
  boundHostSurfaces?: string[];
  execution?: WorkspaceGoalExecution;
  hostThreads?: GoalHostThread[];
  needsYou?: string | null;
  state: string;
};

export function presentGoalActivity(goal: GoalActivityInput): GoalActivity {
  const execution = goal.activationState === "active" && goal.execution?.kind === "running" ? goal.execution : null;
  const running = execution !== null;
  const live = execution !== null && !execution.quiet && !execution.hostClaimed;
  if (goal.activationState === "stopped" || goal.state === "已停止") return { labelKey: "state.stopped", tone: "stopped", live: false, alsoKey: null };
  if (goal.state === "等你" || goal.needsYou) return { labelKey: "state.needsYou", tone: "attention", live, alsoKey: running ? "activity.alsoRunning" : null };
  if (goal.state === "需修复") return { labelKey: "state.needsRepair", tone: "danger", live, alsoKey: running ? "activity.alsoRunning" : null };
  if (execution?.hostClaimed) return { labelKey: "activity.hostClaimed", tone: "running", live: false, alsoKey: null };
  if (running) return { labelKey: "activity.running", tone: live ? "running" : "attention", live, alsoKey: null };
  if (goal.state === "等待条件") return { labelKey: "state.waiting", tone: "waiting", live: false, alsoKey: null };
  if (goal.state === "已安排") {
    const hostThreads = goal.hostThreads ?? [];
    const hostsIdle = hostThreads.length > 0 && hostThreads.every((thread) => thread.state === "idle" || thread.state === "archived");
    const alsoKey = goal.execution?.kind === "unknown" ? "activity.executionUnknown"
      : hostsIdle ? "activity.hostIdle"
        : goalHostSurfaces(goal).length > 0 ? "activity.inHost" : null;
    return { labelKey: "state.queued", tone: "queued", live: false, alsoKey };
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
