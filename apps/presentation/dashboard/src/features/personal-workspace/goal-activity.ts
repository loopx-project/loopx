// Execution is a fact read from the chat/session owner or from a host's own
// thread records. Open Todos, quota eligibility, registration, a persistent
// session or a thread binding never imply it. Attached hosts only surface turns
// they claimed from LoopX, and the claim timestamp is their only activity fact.
// A turn whose session mode is missing or outside the closed set is neither:
// the owner reports it open, but not who executes it, so it is `unrecognized`.
export type WorkspaceGoalExecution =
  | {
    kind: "running";
    /** Which fact leads: managed or host-observed execution, a bare attached claim, or only turns of unrecognized mode. */
    work: Exclude<GoalWorkKind, "none">;
    hostSurfaces: string[];
    lastActivityAt: string | null;
    claimedAt: string | null;
    quiet: boolean;
    /** Open turns whose mode is unrecognized; reported beside a leading fact instead of joining it. */
    unrecognizedModeTurns: number;
  }
  | { kind: "idle"; hostSurfaces: string[] }
  | { kind: "unknown" };

export type GoalSessionFact = {
  goal_id: string | null;
  agent_id: string;
  active_turn_id: string | null;
  host_surface?: string | null;
  last_activity_at?: string | null;
  /** Raw owner value; only the two explicit wire modes establish execution or a claim. */
  session_mode?: string | null;
  status?: string;
  updated_at?: string | null;
};

export function taskSessionStatus(
  session: Pick<GoalSessionFact, "active_turn_id" | "status">,
  outcome: { done: boolean; hasResult: boolean },
) {
  if (session.active_turn_id) return "running";
  if (session.status === "resume_failed") return "failed";
  return outcome.done || outcome.hasResult ? "completed" : "waiting";
}

/** One bound host thread as observed by the host adapter (`host_thread_activity`). */
export type GoalHostThread = {
  agentId?: string | null;
  hostSurface: string;
  state: "turn_open" | "idle" | "archived" | "unknown";
  lastEventAt: string | null;
  deliveryWindow?: {
    state: "fresh" | "stale" | "missing" | "unknown";
    reason?: string | null;
    ageHours?: number | null;
  } | null;
};

/** Coverage stays with the rows so a bounded sample cannot prove all hosts idle. */
export type GoalHostThreadActivity = {
  completeness: "complete" | "incomplete";
  threads: GoalHostThread[];
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

/** Newest recorded time in one group; the group's own clock, never another mode's. */
function newestRecordedTime(sessions: readonly GoalSessionFact[]) {
  return sessions
    .map((session) => session.last_activity_at || session.updated_at || "")
    .filter(Boolean)
    .sort()
    .at(-1) ?? null;
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
  hostThreadActivity?: GoalHostThreadActivity,
  now = Date.now(),
): WorkspaceGoalExecution | undefined {
  const hostTurns = openHostTurns(hostThreadActivity?.threads ?? [], now);
  const open = (sessions ?? []).filter((session) => session.goal_id === goalId && session.status !== "closed");
  const active = open.filter((session) => Boolean(session.active_turn_id));
  if (active.length === 0 && hostTurns.length === 0) {
    if (sessions === undefined) return undefined;
    return sessions === null ? { kind: "unknown" } : { kind: "idle", hostSurfaces: hostSurfacesOf(open) };
  }
  // Claims retain their own clock. Only managed activity and observed host
  // events can refresh execution, including in mixed-source observations. A
  // turn of unrecognized mode contributes to neither clock.
  const managedTurns = active.filter((session) => session.session_mode === "managed_runtime");
  const attachedTurns = active.filter((session) => session.session_mode === "attached_host");
  const unrecognizedModeTurns = active.length - managedTurns.length - attachedTurns.length;
  const claimedAt = newestRecordedTime(attachedTurns);
  const lastActivityAt = [
    newestRecordedTime(managedTurns),
    ...hostTurns.map((thread) => thread.lastEventAt),
  ].filter(Boolean).sort().at(-1) ?? null;
  const lastActivityMs = lastActivityAt ? Date.parse(lastActivityAt) : Number.NaN;
  const work = managedTurns.length > 0 || hostTurns.length > 0 ? "executing"
    : attachedTurns.length > 0 ? "claimed" : "unrecognized";
  return {
    kind: "running",
    work,
    hostSurfaces: Array.from(new Set([...hostSurfacesOf(active), ...hostTurns.map((thread) => thread.hostSurface)])),
    lastActivityAt,
    claimedAt,
    quiet: work === "executing" && !Number.isNaN(lastActivityMs) && now - lastActivityMs > quietTurnMinutes * 60_000,
    unrecognizedModeTurns,
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

/** Cadence evidence shown separately from execution; unknown/no-expectation is not an alert. */
export function goalHostDeliveryWindows(goal: Pick<GoalActivityInput, "hostThreadActivity">) {
  const windows = (goal.hostThreadActivity?.threads ?? [])
    .filter((thread) => thread.deliveryWindow?.state === "stale" || thread.deliveryWindow?.state === "missing" || thread.deliveryWindow?.state === "fresh")
    .map((thread) => ({
      agentId: thread.agentId ?? null,
      hostSurface: thread.hostSurface,
      state: thread.deliveryWindow!.state as "fresh" | "stale" | "missing",
      ageHours: thread.deliveryWindow?.ageHours ?? null,
    }));
  const rank = { stale: 0, missing: 1, fresh: 2 } as const;
  return windows.sort((a, b) => rank[a.state] - rank[b.state]);
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
  hostThreadActivity?: GoalHostThreadActivity;
  needsYou?: string | null;
  state: string;
};

/**
 * The unfinished-work fact a Goal carries, in the vocabulary its chip and lane
 * already show. `executing` needs a managed turn's own activity; an attached
 * host claim alone is `claimed` and is never counted as execution; an open turn
 * whose mode cannot be read is `unrecognized`, which is neither.
 */
export type GoalWorkKind = "executing" | "claimed" | "unrecognized" | "none";

/** `none` covers an idle, unreadable or absent execution, so only a live turn is actionable work. */
export function goalWorkKind(goal: Pick<GoalActivityInput, "execution">): GoalWorkKind {
  if (goal.execution?.kind !== "running") return "none";
  return goal.execution.work;
}

export function presentGoalActivity(goal: GoalActivityInput): GoalActivity {
  const work = goal.activationState === "active" ? goalWorkKind(goal) : "none";
  const execution = work === "none" ? null : goal.execution as Extract<WorkspaceGoalExecution, { kind: "running" }>;
  const running = work === "executing" || work === "claimed";
  const live = work === "executing" && execution !== null && !execution.quiet;
  if (goal.activationState === "stopped" || goal.state === "已停止") return { labelKey: "state.stopped", tone: "stopped", live: false, alsoKey: null };
  if (goal.state === "等你" || goal.needsYou) return { labelKey: "state.needsYou", tone: "attention", live, alsoKey: running ? "activity.alsoRunning" : work === "unrecognized" ? "activity.alsoModeUnrecognized" : null };
  if (goal.state === "需修复") return { labelKey: "state.needsRepair", tone: "danger", live, alsoKey: running ? "activity.alsoRunning" : work === "unrecognized" ? "activity.alsoModeUnrecognized" : null };
  // A turn of unrecognized mode stays visible beside the leading fact rather
  // than being counted as managed execution or as a host claim.
  const modeAlsoKey = execution && execution.work !== "unrecognized" && execution.unrecognizedModeTurns > 0 ? "activity.alsoModeUnrecognized" : null;
  if (work === "claimed") return { labelKey: "activity.hostClaimed", tone: "running", live: false, alsoKey: modeAlsoKey };
  if (work === "unrecognized") return { labelKey: "activity.modeUnrecognized", tone: "attention", live: false, alsoKey: null };
  if (work === "executing") return { labelKey: "activity.running", tone: live ? "running" : "attention", live, alsoKey: modeAlsoKey };
  if (goal.state === "等待条件") return { labelKey: "state.waiting", tone: "waiting", live: false, alsoKey: null };
  if (goal.state === "已安排") {
    const hostThreads = goal.hostThreadActivity?.threads ?? [];
    const hostsIdle = goal.hostThreadActivity?.completeness === "complete"
      && hostThreads.length > 0 && hostThreads.every((thread) => thread.state === "idle" || thread.state === "archived");
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
