/** Lower-bound observations of the original Turn, never new execution effects. */
export interface RecordedTurnEffects {
  host_invoked: boolean | null;
  state_written: boolean | null;
  quota_spent: boolean | null;
  scheduler_acknowledged: boolean | null;
}

type JsonObject = Record<string, unknown>;

function object(value: unknown): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as JsonObject : {};
}

export function recordedTurnEffects(
  journal: JsonObject,
  completedPhases: readonly string[],
  lineageConsistent: boolean,
  attemptsValid: boolean,
): RecordedTurnEffects {
  const unknown: RecordedTurnEffects = {
    host_invoked: null, state_written: null,
    quota_spent: null, scheduler_acknowledged: null,
  };
  // Foreign, corrupt or contradictory lineage cannot supply effect facts.
  if (!lineageConsistent) return unknown;
  const completed = new Set(completedPhases);
  const scheduler = object(journal.scheduler);
  // Unknown or contradictory intents cannot prove non-execution. Keep only
  // facts proved by the valid lineage's already completed checkpoints.
  if (!attemptsValid) return {
    host_invoked: completed.has("host_execute") ? true : null,
    state_written: completed.has("durable_writeback") ? true : null,
    quota_spent: completed.has("quota_spend") ? true : null,
    scheduler_acknowledged: scheduler.acknowledged === true ? true : null,
  };
  const attempts = object(journal.effect_attempts);
  const pending = (step: string) => Object.hasOwn(attempts, step);
  // An attempt is persisted BEFORE confirmation/host launch. It proves neither
  // launch nor non-launch until the host checkpoint is durable.
  const hostCount = journal.host_attempt_count;
  const hostUncertain = hostCount !== undefined
    && (!Number.isInteger(hostCount) || Number(hostCount) !== 0);
  return {
    host_invoked: completed.has("host_execute") ? true : hostUncertain ? null : false,
    state_written: completed.has("durable_writeback") ? true
      : pending("durable_writeback") || pending("terminal_closeout") ? null : false,
    quota_spent: completed.has("quota_spend") ? true
      : pending("quota_spend") ? null : false,
    // A completed outer-controller phase need not acknowledge any host cadence.
    scheduler_acknowledged: typeof scheduler.acknowledged === "boolean"
      ? scheduler.acknowledged : completed.has("quota_spend") ? null : false,
  };
}
