/**
 * One owner for the goal rollout-event log location and its receipt reads.
 *
 * The log is a goal-level runtime artifact that more than one transaction
 * reads: the settlement readback, the receipt-bound scheduler follow-up, and
 * prior-host-Turn closeout resolution.  Resolving the path and reading its
 * receipts lives here so a reader cannot invent a second path rule or a
 * different tolerance for malformed lines.
 */
import { relative, resolve, sep } from "node:path";

import type { JsonObject } from "./effect_program.ts";
import { EffectRuntimeRequestError } from "./effect_runtime_errors.ts";
import { jsonObject, requireNonEmptyString } from "./runtime_decode.ts";
import { readReceiptLogSnapshot } from "./runtime/receipt_log_snapshot.ts";

export const ROLLOUT_EVENT_SCHEMA_VERSION = "loopx_rollout_event_v0";
export const HEARTBEAT_RECEIPT_EVENT_KIND = "quota_should_run";
export type HeartbeatReceiptStatus = "fresh" | "missing" | "stale";

/**
 * One parse of a Goal's rollout-event log.
 *
 * Receipt discovery is intentionally tolerant: an unrelated malformed line
 * cannot erase a valid heartbeat receipt. Settlement readback is intentionally
 * strict: once a receipt creates a closeout obligation, malformed persisted
 * state must fail closed. Keeping the first strict error beside the valid
 * records lets both readers share one physical read without weakening either
 * contract.
 */
export interface GoalRolloutEventSnapshot {
  readonly runtimeRoot: string;
  readonly goalId: string;
  readonly events: readonly JsonObject[];
  /** Physical line of each event, in `events` order. */
  readonly eventLines: readonly number[];
  /** Intact members of damaged lines, with their line numbers. */
  readonly malformedLines: readonly Readonly<{
    line: number;
    members: readonly (readonly [string, unknown])[];
    /** Identity members the damaged line contained but could not read. */
    unreadable: readonly string[];
    /** A key name was lost to damage, so recovered members may be incomplete. */
    lostKeyName: boolean;
    /** A separator or container boundary was unknowable after damage. */
    ambiguous: boolean;
  }>[];
  readonly firstStrictErrorLine: number | null;
  /** A complete final row that never received its committing newline. */
  readonly unterminatedRecordLine: number | null;
}

/** Reject a goal id that is not one path segment, before any log read. */
export function goalPathSegment(value: unknown): string {
  const label = "goal_id";
  const result = requireNonEmptyString(value, label).trim();
  if (
    result === "." ||
    result === ".." ||
    result.includes("/") ||
    result.includes("\\")
  ) {
    throw new EffectRuntimeRequestError(
      `${label} must be a single path segment`,
      "invalid_goal_id",
    );
  }
  return result;
}

/** Resolve one goal's rollout-event log inside `runtime_root`. */
export function goalRolloutEventLogPath(
  runtimeRoot: string,
  goalId: string,
): string {
  const root = resolve(runtimeRoot);
  const path = resolve(
    root,
    "goals",
    goalPathSegment(goalId),
    "rollout-event-log.jsonl",
  );
  const child = relative(root, path);
  if (child === "" || child === ".." || child.startsWith(`..${sep}`)) {
    throw new EffectRuntimeRequestError(
      "rollout event log path escapes runtime_root",
      "invalid_rollout_event_log_path",
    );
  }
  return path;
}

/** Read and parse the rollout log once, retaining strict-read diagnostics. */
export async function readGoalRolloutEventSnapshot(
  runtimeRoot: string,
  goalId: string,
): Promise<GoalRolloutEventSnapshot | null> {
  const snapshot = await readReceiptLogSnapshot(
    goalRolloutEventLogPath(runtimeRoot, goalId),
    ROLLOUT_EVENT_SCHEMA_VERSION,
    ADMISSION_IDENTITY_MEMBERS,
  );
  return snapshot === null ? null : {
    runtimeRoot, goalId, events: snapshot.records,
    eventLines: snapshot.recordLines,
    malformedLines: snapshot.malformedLines,
    firstStrictErrorLine: snapshot.firstErrorLine,
    unterminatedRecordLine: snapshot.unterminatedRecordLine,
  };
}

/** Return strict settlement input, or fail on the first malformed line. */
export function strictGoalRolloutEvents(
  snapshot: GoalRolloutEventSnapshot | null,
): readonly JsonObject[] {
  if (snapshot !== null && snapshot.firstStrictErrorLine !== null) {
    throw new EffectRuntimeRequestError(
      `settlement readback line ${snapshot.firstStrictErrorLine} is malformed`,
      "malformed_settlement_state",
    );
  }
  return snapshot?.events ?? [];
}

/** Qualify heartbeat receipts without discarding diagnostic quota audit rows. */
export function goalHeartbeatReceiptsFromSnapshot(
  snapshot: GoalRolloutEventSnapshot | null,
  goalId: string,
  agentId?: string | null,
): JsonObject[] | null {
  if (snapshot === null) return null;
  if (snapshot.goalId !== goalId) {
    throw new EffectRuntimeRequestError(
      "rollout event snapshot does not belong to the requested goal",
      "rollout_event_snapshot_scope_mismatch",
    );
  }
  return heartbeatReceiptsFromEvents(snapshot.events, goalId, agentId);
}

/** The heartbeat receipt filter over events a caller already read. */
export function heartbeatReceiptsFromEvents(
  events: readonly JsonObject[],
  goalId: string,
  agentId?: string | null,
): JsonObject[] {
  return events.filter((event) =>
    event.event_kind === HEARTBEAT_RECEIPT_EVENT_KIND &&
    event.goal_id === goalId &&
    // A failed invocation without a real Turn identity is an audit, not a
    // receipt that can grant host writeback or supersede an admitted Turn.
    typeof event.run_id === "string" && event.run_id.trim().length > 0 &&
    (agentId === undefined || event.agent_id === agentId)
  );
}

/**
 * Read the heartbeat receipts this goal persisted for one Agent.
 *
 * The read mirrors the established non-strict reader: a malformed or
 * differently versioned line is skipped rather than allowed to erase or
 * manufacture a receipt, and an absent log is `null` because "no receipts yet"
 * and "no log yet" are different facts for a caller that must report state.
 */
export async function readGoalHeartbeatReceipts(
  runtimeRoot: string,
  goalId: string,
  agentId?: string | null,
): Promise<JsonObject[] | null> {
  return goalHeartbeatReceiptsFromSnapshot(
    await readGoalRolloutEventSnapshot(runtimeRoot, goalId),
    goalId,
    agentId,
  );
}

/** A receipt without an exact Goal instance belongs to the legacy goal scope. */
function receiptMatchesGoalRef(event: JsonObject, goalRef: JsonObject | null): boolean {
  if (goalRef === null) return !Object.hasOwn(event, "goal_ref");
  const eventGoalRef = jsonObject(event.goal_ref);
  return eventGoalRef !== null &&
    eventGoalRef.goal_id === goalRef.goal_id &&
    eventGoalRef.goal_instance_id === goalRef.goal_instance_id;
}

/**
 * One qualified receipt view for both originating and superseding reads.
 *
 * The originating Turn and every candidate successor must come from the same
 * set: a heartbeat receipt with a real Turn identity in this Goal/Agent and
 * this exact Goal instance scope. Diagnostic audit rows and rows from another
 * Goal lifetime therefore cannot renew or retire this transaction.
 */
function scopedHeartbeatReceipts(
  snapshot: GoalRolloutEventSnapshot,
  goalId: string,
  agentId: string,
  goalRef: JsonObject | null,
): JsonObject[] {
  if (snapshot.goalId !== goalId) {
    throw new EffectRuntimeRequestError(
      "rollout event snapshot does not belong to the requested goal",
      "rollout_event_snapshot_scope_mismatch",
    );
  }
  return heartbeatReceiptsFromEvents(snapshot.events, goalId, agentId).filter(
    (event) => receiptMatchesGoalRef(event, goalRef),
  );
}

const ADMISSION_IDENTITY_MEMBERS = [
  "schema_version",
  "event_kind",
  "goal_id",
  "agent_id",
  "run_id",
  "goal_ref",
] as const;

/**
 * What a finished row still proves about the Turn that superseded this receipt.
 *
 * The row's commit fact is its own writer's newline: the log owner withdraws
 * every member of a row a *later* append had to terminate, so a torn prefix
 * cannot manufacture an admission.  What remains is whether the row still
 * carries one identity.  A duplicated identity member proves nothing, so a
 * stray write cannot invent a successor.  A row whose display field was
 * corrupted still proves the Turn its writer committed, and a row that is
 * demonstrably a receipt of this scope but whose Turn bytes are unreadable
 * proves that *some* Turn was admitted: it is not evidence for the old receipt.
 */
type CommittedSuccessor =
  | { readonly kind: "superseded"; readonly runId: string }
  | { readonly kind: "unattributed" }
  // The row belongs to this scope but its damage left an identity unknowable.
  // Absence of proof is not proof of absence: a caller must treat this receipt
  // as possibly superseding rather than as unrelated.
  | { readonly kind: "unknown" };

function committedSuccessor(
  damaged: Readonly<{
    members: readonly (readonly [string, unknown])[];
    unreadable: readonly string[];
    lostKeyName: boolean;
    ambiguous: boolean;
  }>,
  goalId: string,
  agentId: string,
  goalRef: JsonObject | null,
): CommittedSuccessor | null {
  // Every value the row shows for a member, not just the first.  A duplicate
  // that names the same value as its twin says nothing new, while a duplicate
  // that disagrees must not be allowed to erase the identity it contradicts.
  const observed = new Map<string, unknown[]>();
  for (const [key, value] of damaged.members) {
    const existing = observed.get(key);
    if (existing === undefined) observed.set(key, [value]);
    else existing.push(value);
  }
  const equal = (left: unknown, right: unknown): boolean =>
    JSON.stringify(left) === JSON.stringify(right);
  const distinct = (key: string): unknown[] => {
    const seen: unknown[] = [];
    for (const value of observed.get(key) ?? []) {
      if (!seen.some((item) => equal(item, value))) seen.push(value);
    }
    return seen;
  };
  const confirms = (key: string, expected: unknown): boolean =>
    distinct(key).includes(expected);
  const goalRefMatches = (value: unknown): boolean => {
    if (goalRef === null) return false;
    const eventGoalRef = jsonObject(value);
    return eventGoalRef !== null &&
      eventGoalRef.goal_id === goalRef.goal_id &&
      eventGoalRef.goal_instance_id === goalRef.goal_instance_id;
  };

  const unreadableScope = new Set(
    damaged.unreadable.filter(
      (key) => (ADMISSION_IDENTITY_MEMBERS as readonly string[]).includes(key),
    ),
  );
  // A foreign value only proves the row is unrelated when every copy of that
  // member is readable: the same member may be duplicated, and a damaged copy
  // that could have named this scope leaves the row unreadable rather than
  // unrelated.  Every member must be examined before uncertainty is reported:
  // a readable foreign Agent identity proves the row unrelated even when some
  // other member is damaged, while uncertainty stays local to its own member.
  const foreign = new Set<string>();
  const noteForeign = (key: string, matches: boolean): void => {
    if (!observed.has(key) || matches) return;
    if (!unreadableScope.has(key)) foreign.add(key);
  };
  noteForeign("schema_version",
    confirms("schema_version", ROLLOUT_EVENT_SCHEMA_VERSION));
  noteForeign("event_kind", confirms("event_kind", HEARTBEAT_RECEIPT_EVENT_KIND));
  noteForeign("goal_id", confirms("goal_id", goalId));
  noteForeign("agent_id", confirms("agent_id", agentId));
  noteForeign("goal_ref", goalRef === null
    ? false
    : (observed.get("goal_ref") ?? []).some(goalRefMatches));
  // One conclusively foreign member settles the row; damage in a different
  // member cannot pull an unrelated diagnostic back into this transaction.
  if (foreign.size > 0) return null;

  // Scope members whose bytes could not be read keep the row inside this
  // transaction without proving which Turn admitted it.
  for (const key of damaged.unreadable) {
    if (!(ADMISSION_IDENTITY_MEMBERS as readonly string[]).includes(key)) continue;
    if (key !== "run_id") return { kind: "unknown" };
  }
  const identityUncertain = damaged.lostKeyName || damaged.ambiguous;
  const scopeKeys = goalRef === null
    ? ["schema_version", "event_kind", "goal_id", "agent_id"]
    : ["schema_version", "event_kind", "goal_id", "agent_id", "goal_ref"];
  if (scopeKeys.some((key) => distinct(key).length === 0)) {
    // Without damage such a row simply is not one of this scope's receipts.
    // Damage can have hidden the member behind a key name or separator it
    // could not read, so the row is unreadable instead of unrelated.
    return identityUncertain ? { kind: "unknown" } : null;
  }
  // The row proves it is one of this scope's heartbeat receipts.  A duplicate
  // scope member that names two different values cannot be resolved, and the
  // same row with only one of them written would still be a successor.
  if (scopeKeys.some((key) => distinct(key).length > 1)) {
    return { kind: "unknown" };
  }
  // Only then does an unknown Turn mean "somebody else's turn superseded mine".
  if (damaged.ambiguous) return { kind: "unknown" };
  const turnValues = distinct("run_id");
  if (turnValues.length > 1) return { kind: "unattributed" };
  if (damaged.unreadable.includes("run_id")) return { kind: "unattributed" };
  const runId = turnValues[0];
  if (typeof runId === "string" && runId.trim().length > 0) {
    return { kind: "superseded", runId };
  }
  // Nothing names a Turn here.  A lost key name could have been that Turn, so
  // only a row whose key names all survived may be read as a diagnostic.
  return damaged.lostKeyName ? { kind: "unknown" } : null;
}

export async function heartbeatReceiptStatus(params: {
  runtimeRoot: string;
  goalId: string;
  agentId: string;
  turnInstanceId: string;
  goalRef?: JsonObject | null;
}): Promise<HeartbeatReceiptStatus> {
  const goalRef = params.goalRef ?? null;
  const snapshot = await readGoalRolloutEventSnapshot(
    params.runtimeRoot,
    params.goalId,
  );
  if (snapshot === null) return "missing";
  const receipts = scopedHeartbeatReceipts(
    snapshot,
    params.goalId,
    params.agentId,
    goalRef,
  );
  // An unterminated final row never committed; the next append drops those
  // bytes, so it can neither be this Turn's receipt nor supersede it.
  const receiptLines = snapshot.events
    .map((event, index) => ({ event, line: snapshot.eventLines[index] }))
    .filter(({ line }) => line !== snapshot.unterminatedRecordLine)
    .filter(({ event }) => receipts.includes(event));
  const firstIndex = receiptLines.findIndex(
    ({ event }) => event.run_id === params.turnInstanceId,
  );
  if (firstIndex < 0) return "missing";
  const originatingLine = receiptLines[firstIndex].line;
  const laterReceipt = receiptLines.slice(firstIndex + 1).some(
    ({ event }) => event.run_id !== params.turnInstanceId,
  );
  const laterAdmission = snapshot.malformedLines.some((damaged) => {
    if (damaged.line <= originatingLine) return false;
    const admitted = committedSuccessor(
      damaged, params.goalId, params.agentId, goalRef,
    );
    if (admitted === null) return false;
    if (admitted.kind === "superseded") {
      return admitted.runId !== params.turnInstanceId;
    }
    // An unreadable admission of this scope still retires the old Turn.
    return true;
  });
  return laterReceipt || laterAdmission ? "stale" : "fresh";
}
