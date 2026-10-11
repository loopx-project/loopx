/** One read model for every count the Goal workspace shows, so the vitals strip
 * and the work board cannot disagree. Pure: it performs no I/O and reads no
 * clock; the caller supplies already-parsed owner facts and `observedAt`.
 *
 * Liveness stays with the execution observation owner: a claim, a status or a
 * registration is never activity, and unknown stays unknown. A missing
 * measurement is `null`, never zero. Nothing here is a lifetime figure. */

export const GOAL_WORKSPACE_DIGEST_SCHEMA = "goal_workspace_digest_v0";

/** Display classes for open and done work. Local to this reducer until a second
 * consumer adopts the digest; then they register with the vocabulary owner. */
export const GOAL_WORK_DISPLAY_CLASSES = [
  "running_observed", "waiting_dependency", "open", "blocked", "deferred", "done",
] as const;
export type GoalWorkDisplayClass = typeof GOAL_WORK_DISPLAY_CLASSES[number];

export type DigestTodo = {
  todoId: string;
  done: boolean;
  status?: string | null;
  taskClass?: string | null;
  dependencies?: readonly string[];
};

export type DigestAttention = { blocking: boolean; updatedAt?: string | null };

/** Counts from the attention owner's Goal group, with coverage of its item list.
 * A bounded list can carry exact totals but cannot establish the global oldest. */
export type DigestAttentionSummary = {
  blocking: number | null;
  optional: number | null;
  coverage: DigestCoverage;
};

export type DigestUsage = {
  costUsd24h?: number; costUsd7d?: number; durationMs24h?: number; durationMs7d?: number;
};

/** `observed` is true when a managed Turn or a host-recorded thread event backs
 * the running state; a host claim alone is not execution. */
export type DigestExecution =
  | { kind: "running"; observed: boolean; quiet: boolean; lastActivityAt: string | null }
  | { kind: "idle" }
  | { kind: "unknown" };

export type DigestActivity = { actor: string; at: string | null };

export type GoalWorkspaceDigestInput = {
  observedAt: string;
  activation: "active" | "stopped";
  todos: readonly DigestTodo[];
  /** Owner-projected done count; the item list carries only recent completions. */
  doneCount?: number | null;
  /** Owner-projected open count of the listed group; more than listed means truncation. */
  openCount?: number | null;
  /** Todos whose running step the run timeline reports. */
  runningTodoIds?: readonly string[];
  /** `null` when the attention owner could not be read. */
  attention: readonly DigestAttention[] | null;
  attentionSummary?: DigestAttentionSummary;
  usage?: DigestUsage | null;
  /** `undefined` while the session owner has not been read. */
  execution?: DigestExecution;
  /** Observed per-actor execution facts such as host-recorded thread events.
   * Todo write times and claim times are hints, not activity. */
  activity?: readonly DigestActivity[];
};

export type DigestCoverage = "complete" | "partial" | "unknown";

export type GoalWorkspaceDigest = {
  schema: typeof GOAL_WORKSPACE_DIGEST_SCHEMA;
  observedAt: string;
  lifecycle: { activation: "active" | "stopped"; finishingTurn: boolean };
  work: {
    open: number;
    /** `null` when the owner projects no done count. */
    done: number | null;
    total: number | null;
    classes: Record<GoalWorkDisplayClass, number>;
    coverage: DigestCoverage;
    listedOpen: number;
    projectedOpen: number | null;
  };
  needsYou:
    | { availability: "ok"; blocking: number | null; optional: number | null;
        coverage: DigestCoverage; listed: number; oldestWaitingSince: string | null }
    | { availability: "unavailable" };
  /** Agent time and Cost always share one window. */
  usage: {
    window: "7d" | "24h" | null;
    agentTimeMs: number | null;
    costUsd: number | null;
    /** No owner coverage signal exists yet; the cell makes no completeness claim. */
    coverage: "unknown";
  };
  lastActivity: { at: string; actor: string } | null;
};

/** The work the board lists: every Agent Todo except standing monitors. */
export function isGoalWorkTodo(todo: Pick<DigestTodo, "taskClass">): boolean {
  return todo.taskClass !== "continuous_monitor";
}

function timeOf(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function measured(value: number | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function classifyOpenWork(
  todo: DigestTodo,
  openIds: ReadonlySet<string>,
  runningIds: ReadonlySet<string>,
  executionObserved: boolean,
): GoalWorkDisplayClass {
  if (todo.status === "deferred") return "deferred";
  if (todo.status === "blocked") return "blocked";
  if (executionObserved && runningIds.has(todo.todoId)) return "running_observed";
  // Only a listed, unfinished prerequisite proves waiting; an unlisted one may be done.
  if ((todo.dependencies ?? []).some((id) => id !== todo.todoId && openIds.has(id))) return "waiting_dependency";
  return "open";
}

export function compileGoalWorkspaceDigest(input: GoalWorkspaceDigestInput): GoalWorkspaceDigest {
  const execution = input.execution;
  const executionObserved = execution?.kind === "running" && execution.observed && !execution.quiet;

  const work = input.todos.filter(isGoalWorkTodo);
  const openWork = work.filter((todo) => !todo.done);
  const openIds = new Set(openWork.map((todo) => todo.todoId));
  const runningIds = new Set(input.runningTodoIds ?? []);
  const classes = Object.fromEntries(GOAL_WORK_DISPLAY_CLASSES.map((name) => [name, 0])) as Record<GoalWorkDisplayClass, number>;
  for (const todo of openWork) classes[classifyOpenWork(todo, openIds, runningIds, executionObserved)] += 1;
  const listedDone = work.length - openWork.length;
  const done = typeof input.doneCount === "number" && input.doneCount >= 0
    ? Math.max(input.doneCount, listedDone) : null;
  classes.done = done ?? listedDone;
  const listedOpen = input.todos.filter((todo) => !todo.done).length;
  const projectedOpen = typeof input.openCount === "number" && input.openCount >= 0 ? input.openCount : null;
  const truncated = projectedOpen !== null && projectedOpen > listedOpen;
  const workCoverage: DigestCoverage = projectedOpen === null ? "unknown" : truncated ? "partial" : "complete";

  let needsYou: GoalWorkspaceDigest["needsYou"];
  if (input.attention === null) {
    needsYou = { availability: "unavailable" };
  } else {
    const blocking = input.attention.filter((item) => item.blocking);
    const count = (value: number | null | undefined, minimum: number): number | null =>
      typeof value === "number" && Number.isSafeInteger(value) && value >= minimum ? value : null;
    const blockingCount = count(input.attentionSummary?.blocking, blocking.length);
    const optionalCount = count(input.attentionSummary?.optional, input.attention.length - blocking.length);
    const coverage: DigestCoverage = blockingCount === null || optionalCount === null ? "unknown"
      : blockingCount + optionalCount > input.attention.length ? "partial"
        : input.attentionSummary?.coverage ?? "unknown";
    const oldest = blocking
      .map((item) => ({ raw: item.updatedAt ?? null, at: timeOf(item.updatedAt) }))
      .filter((item): item is { raw: string; at: number } => item.at !== null)
      .sort((left, right) => left.at - right.at)[0];
    needsYou = {
      availability: "ok",
      blocking: blockingCount,
      optional: optionalCount,
      coverage,
      listed: input.attention.length,
      oldestWaitingSince: coverage === "complete" ? oldest?.raw ?? null : null,
    };
  }

  const usage = input.usage ?? null;
  const wide = usage && (measured(usage.durationMs7d) !== null || measured(usage.costUsd7d) !== null);
  const narrow = usage && (measured(usage.durationMs24h) !== null || measured(usage.costUsd24h) !== null);
  const window = wide ? "7d" as const : narrow ? "24h" as const : null;

  const candidates: { actor: string; at: string; time: number }[] = [];
  if (execution?.kind === "running" && execution.observed) {
    const time = timeOf(execution.lastActivityAt);
    if (time !== null) candidates.push({ actor: "", at: execution.lastActivityAt!, time });
  }
  for (const fact of input.activity ?? []) {
    const time = timeOf(fact.at);
    if (time !== null) candidates.push({ actor: fact.actor, at: fact.at!, time });
  }
  // The newest fact wins; at equal times a named actor is more useful than the anonymous Turn.
  const latest = candidates.sort((left, right) => right.time - left.time || Number(Boolean(right.actor)) - Number(Boolean(left.actor)))[0];

  return {
    schema: GOAL_WORKSPACE_DIGEST_SCHEMA,
    observedAt: input.observedAt,
    lifecycle: { activation: input.activation, finishingTurn: input.activation === "stopped" && executionObserved },
    work: {
      open: openWork.length,
      done,
      total: done === null || workCoverage !== "complete" ? null : done + openWork.length,
      classes,
      coverage: workCoverage,
      listedOpen,
      projectedOpen,
    },
    needsYou,
    usage: {
      window,
      agentTimeMs: window === "7d" ? measured(usage?.durationMs7d) : window === "24h" ? measured(usage?.durationMs24h) : null,
      costUsd: window === "7d" ? measured(usage?.costUsd7d) : window === "24h" ? measured(usage?.costUsd24h) : null,
      coverage: "unknown",
    },
    lastActivity: latest ? { at: latest.at, actor: latest.actor } : null,
  };
}
