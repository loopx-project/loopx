/**
 * "Since you looked": what changed in a Goal between two of the owner's
 * visits, per RFC goal-workspace-surface-v0 §5.7.
 *
 * The browser keeps one marker per (status source, Goal) in local storage:
 * typed counts, decision ids, a bounded set of done Todo and result ids, and
 * the observation time. No titles, content or cost ever enter it, and nothing
 * is written to the service. A delta is reported only when it is material;
 * cost is omitted because the usage projection cannot qualify the interval.
 * The marker advances when the owner marks it seen or leaves the Goal after
 * the line was shown. This is not a notification channel.
 */

export const seenMarkerSchema = "goal_seen_marker_v1";
export const seenMarkerIdLimit = 200;

export type SeenMarker = {
  schema: typeof seenMarkerSchema;
  observedAt: string;
  /** `decisions` is the attention owner's blocking count (null when unknown); `results` the returned-result count. */
  counts: { open: number; blocked: number; done: number | null; decisions: number | null; results: number };
  decisionIds: string[];
  doneTodoIds: string[];
  resultIds: string[];
};

export type SeenMarkerInput = {
  observedAt: string;
  counts: { open: number; blocked: number; done: number | null; decisions: number | null };
  decisionIds: readonly string[];
  doneTodoIds: readonly string[];
  blockedTodoIds: readonly string[];
  resultIds: readonly string[];
};

export type SinceYouLookedDelta = {
  since: string;
  newlyDone: number;
  newDecisions: number;
  newlyBlocked: number;
  resultsReturned: number;
  material: boolean;
};

function bounded(ids: readonly string[]): string[] {
  return [...new Set(ids)].slice(-seenMarkerIdLimit);
}

export function buildSeenMarker(input: SeenMarkerInput): SeenMarker {
  return {
    schema: seenMarkerSchema,
    observedAt: input.observedAt,
    counts: { open: input.counts.open, blocked: input.counts.blocked, done: input.counts.done, decisions: input.counts.decisions, results: input.resultIds.length },
    decisionIds: bounded(input.decisionIds),
    doneTodoIds: bounded(input.doneTodoIds),
    resultIds: bounded(input.resultIds),
  };
}

/**
 * Stored ids are evidence of what was seen only while they cover the whole
 * set the owner projected. A capped set (more ids than the marker keeps) or a
 * partially listed one cannot tell an unretained old item from a new one.
 */
function coversWholeSet(ids: readonly string[], count: number | null): boolean {
  return count === null ? ids.length < seenMarkerIdLimit : ids.length >= count;
}

/**
 * Compare the current observation with the stored marker. Newly done work is
 * the done ids not in the marker, bounded by the growth of the done count
 * when the owner projects one, so a trimmed marker never inflates it; with
 * no done total, ids count only while the marker kept them all. New
 * decisions and results are ids not seen before while the marker covered the
 * whole set; past the cap only growth of the owner's count is evidence, so an
 * unchanged set never reports a change. Newly blocked work is the growth of
 * the blocked count. A shrinking queue is not a change to report.
 */
export function compareSeenMarker(previous: SeenMarker, current: SeenMarkerInput): SinceYouLookedDelta {
  const seenDone = new Set(previous.doneTodoIds);
  const seenDecisions = new Set(previous.decisionIds);
  const seenResults = new Set(previous.resultIds);
  const doneById = current.doneTodoIds.filter((id) => !seenDone.has(id)).length;
  let newlyDone: number;
  if (previous.counts.done !== null && current.counts.done !== null) {
    // The owner's done total is the evidence; ids can only refine it downward.
    const doneByCount = Math.max(0, current.counts.done - previous.counts.done);
    newlyDone = Math.min(doneById, doneByCount) || (doneByCount > 0 && doneById === 0 ? doneByCount : 0);
  } else {
    // Without a done total the ids are the only evidence, and only while the
    // marker kept every one it saw; a capped history cannot prove a completion.
    newlyDone = coversWholeSet(previous.doneTodoIds, null) ? doneById : 0;
  }
  const newDecisions = coversWholeSet(previous.decisionIds, previous.counts.decisions)
    ? current.decisionIds.filter((id) => !seenDecisions.has(id)).length
    : previous.counts.decisions !== null && current.counts.decisions !== null
      ? Math.max(0, current.counts.decisions - previous.counts.decisions) : 0;
  const newlyBlocked = Math.max(0, current.counts.blocked - previous.counts.blocked);
  const resultsReturned = coversWholeSet(previous.resultIds, previous.counts.results)
    ? current.resultIds.filter((id) => !seenResults.has(id)).length
    : Math.max(0, current.resultIds.length - previous.counts.results);
  return {
    since: previous.observedAt,
    newlyDone, newDecisions, newlyBlocked, resultsReturned,
    material: newlyDone > 0 || newDecisions > 0 || newlyBlocked > 0 || resultsReturned > 0,
  };
}

export function seenMarkerStorageKey(statusSource: string, goalId: string): string {
  return `loopx-pw-seen:${statusSource}:${goalId}`;
}

export type SeenMarkerStorage = Pick<Storage, "getItem" | "setItem">;

/**
 * Acquire the browser's local storage for the marker, or null when the
 * browser denies it. Reading the `localStorage` property itself can throw
 * (a SecurityError under a storage policy), so the acquisition sits inside
 * the same boundary as the reads and writes: without storage the owner only
 * loses the local look memory, never the Goal.
 */
export function seenMarkerStorage(scope: { localStorage?: SeenMarkerStorage } = typeof window === "undefined" ? {} : window): SeenMarkerStorage | null {
  try {
    const storage = scope.localStorage;
    return storage && typeof storage.getItem === "function" && typeof storage.setItem === "function" ? storage : null;
  } catch {
    return null;
  }
}

export function readSeenMarker(storage: Pick<Storage, "getItem"> | null, key: string): SeenMarker | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(key);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<SeenMarker> | null;
    if (!value || value.schema !== seenMarkerSchema || typeof value.observedAt !== "string" || !value.counts) return null;
    return {
      schema: seenMarkerSchema, observedAt: value.observedAt,
      counts: {
        open: Number(value.counts.open ?? 0), blocked: Number(value.counts.blocked ?? 0),
        done: value.counts.done === null || value.counts.done === undefined ? null : Number(value.counts.done),
        decisions: value.counts.decisions === null || value.counts.decisions === undefined ? null : Number(value.counts.decisions),
        results: Number(value.counts.results ?? 0),
      },
      decisionIds: Array.isArray(value.decisionIds) ? value.decisionIds.filter((id): id is string => typeof id === "string") : [],
      doneTodoIds: Array.isArray(value.doneTodoIds) ? value.doneTodoIds.filter((id): id is string => typeof id === "string") : [],
      resultIds: Array.isArray(value.resultIds) ? value.resultIds.filter((id): id is string => typeof id === "string") : [],
    };
  } catch {
    return null;
  }
}

export function writeSeenMarker(storage: Pick<Storage, "setItem"> | null, key: string, marker: SeenMarker): boolean {
  if (!storage) return false;
  try {
    storage.setItem(key, JSON.stringify(marker));
    return true;
  } catch {
    return false;
  }
}
