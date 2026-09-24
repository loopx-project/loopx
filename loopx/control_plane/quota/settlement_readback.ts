import { readFile } from "node:fs/promises";
import { isAbsolute, join } from "node:path";

import {
  readGoalRolloutEventSnapshot,
  strictGoalRolloutEvents,
  type GoalRolloutEventSnapshot,
} from "../rollout_receipt_log.ts";
import {
  effectIdsMatch,
  settlementBindReduce,
  settlementFailed,
  settlementIdentity,
  settlementIdentityPayload,
  settlementPure,
  settlementReceipt,
  settlementResultPayload,
  type JsonObject,
  type SettlementIdentity,
  type SettlementResult,
} from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  jsonObject,
  requireJsonObject,
  requireNonEmptyString,
} from "../runtime_decode.ts";
import {
  normalizeDeliveryWorkspaceCausality,
  type DeliveryWorkspaceCausality,
} from "./settlement_workspace_causality.ts";
import {
  isCommittedMonitorPollEffect,
  receiptBoundMonitorPhase,
  receiptBoundReplayPhase,
} from "./settlement_phase.ts";
import { isTurnScopedSettlementOutcome } from "../work_items/delivery_outcome.ts";
import { isBoundedBlockedRetry } from "./blocked_retry.ts";
import {
  decodeRefreshRetry,
  isMaterialMonitorPoll,
  refreshRecovery,
  type RefreshRetryRequest,
} from "./refresh_recovery.ts";
import {
  heartbeatReceiptDetails as details,
  heartbeatReceiptFactFromEvent,
  normalizeHeartbeatReplanObligationId as normalizeReplanObligationId,
  normalizeHeartbeatTodoId as normalizeTodoId,
  optionalHeartbeatString as optionalString,
  selectEffectiveHeartbeatReceipt,
  type HeartbeatReceiptFact,
} from "./heartbeat_receipt_identity.ts";

import { refreshExternalDelivery } from "./refresh_external_delivery.ts";

export const QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA =
  "loopx_quota_settlement_readback_request_v0";
export const QUOTA_SETTLEMENT_READBACK_RESULT_SCHEMA =
  "loopx_quota_settlement_readback_result_v0";
export const SEMANTIC_REPLAN_GUARD_SCHEMA = "semantic_replan_guard_v0";

const TURN_INSTANCE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const AGENT_ID_PATTERN = /^[a-z][a-z0-9_.:@-]{0,79}$/;

interface ReadbackRequest {
  runtime_root: string;
  goal_id: string;
  agent_id: string | null;
  todo_id: string | null;
  turn_instance_id: string | null;
  replan_obligation_id: string | null;
  infer_turn_instance_id: boolean;
  allow_unbound_binding: boolean;
  refresh_retry: RefreshRetryRequest | null;
}

export interface QuotaSettlementReadbackSnapshot {
  readonly runtimeRoot: string;
  readonly goalId: string;
  readonly events: readonly JsonObject[];
  readonly runs: readonly JsonObject[];
  readonly eventsByTurn: ReadonlyMap<string, readonly JsonObject[]>;
  readonly runsByTurn: ReadonlyMap<string, readonly JsonObject[]>;
  readonly runsByEffectRef: ReadonlyMap<string, readonly JsonObject[]>;
  readonly runPositions: ReadonlyMap<JsonObject, number>;
}

interface ResultBundle extends JsonObject {
  result: SettlementResult;
  payload: JsonObject;
}

type SettlementProgressState = "identity_required" | "writeback_required" |
  "writeback_receipt_required" | "spend_required" | "spend_receipt_required" | "settled";

function settlementScope(
  runtimeRootValue: unknown,
  goalIdValue: unknown,
): { runtimeRoot: string; goalId: string } {
  const runtimeRoot = requireNonEmptyString(runtimeRootValue, "runtime_root");
  if (!isAbsolute(runtimeRoot)) {
    throw new EffectRuntimeRequestError("runtime_root must be absolute");
  }
  const goalId = requireNonEmptyString(goalIdValue, "goal_id");
  if (goalId === "." || goalId === ".." || goalId.includes("/") || goalId.includes("\\")) {
    throw new EffectRuntimeRequestError("goal_id must be a single path segment");
  }
  return {runtimeRoot, goalId};
}

/** Receipt verification owns progress; a durable debit alone is not settlement. */
function settlementProgress(
  identity: SettlementResult, writeback: SettlementResult, spend: SettlementResult,
  writebackRun: JsonObject | null, spendRun: JsonObject | null,
  spendSource: unknown = "heartbeat",
  blockedNoSpend = false,
): JsonObject {
  const source = spendSource ?? "heartbeat";
  if (source !== "heartbeat" && source !== "visible-goal") {
    throw new EffectRuntimeRequestError("settlement spend source is invalid", "malformed_settlement_state");
  }
  const state: SettlementProgressState = identity.failure ? "identity_required"
    : writeback.failure ? (writebackRun ? "writeback_receipt_required" : "writeback_required")
    : blockedNoSpend ? "settled"
    : spend.failure ? (spendRun ? "spend_receipt_required" : "spend_required")
    : "settled";
  return {
    schema_version: "quota_settlement_progress_v0", state,
    next_step: identity.failure ? "validation" : writeback.failure ? "durable_writeback"
      : blockedNoSpend ? null : spend.failure ? "quota_spend" : null,
    quota_spend_source: source,
    ...(blockedNoSpend ? {
      closeout_kind: "typed_blocked_writeback_no_spend",
    } : {}),
  };
}

function optionalRequestString(value: unknown, label: string): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string or null`);
  }
  return value.trim() || null;
}

function normalizeAgentId(value: unknown): string | null {
  const candidate = String(value ?? "").trim().toLowerCase().replace(/\s+/g, "-");
  return candidate && AGENT_ID_PATTERN.test(candidate) ? candidate : null;
}

function decodeRequest(value: unknown): ReadbackRequest {
  const request = requireJsonObject(value, "quota settlement readback request");
  if (request.schema_version !== QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError(
      "Quota settlement readback request schema mismatch",
    );
  }
  const {runtimeRoot, goalId} = settlementScope(
    request.runtime_root,
    request.goal_id,
  );
  if (typeof request.infer_turn_instance_id !== "boolean") {
    throw new EffectRuntimeRequestError("infer_turn_instance_id must be a boolean");
  }
  if (typeof request.allow_unbound_binding !== "boolean") {
    throw new EffectRuntimeRequestError("allow_unbound_binding must be a boolean");
  }
  return {
    runtime_root: runtimeRoot,
    goal_id: goalId,
    agent_id: optionalRequestString(request.agent_id, "agent_id"),
    todo_id: optionalRequestString(request.todo_id, "todo_id"),
    turn_instance_id: optionalRequestString(
      request.turn_instance_id,
      "turn_instance_id",
    ),
    replan_obligation_id: optionalRequestString(
      request.replan_obligation_id,
      "replan_obligation_id",
    ),
    infer_turn_instance_id: request.infer_turn_instance_id,
    allow_unbound_binding: request.allow_unbound_binding,
    refresh_retry: decodeRefreshRetry(request.refresh_retry),
  };
}

async function readJsonLines(path: string, schemaVersion?: string): Promise<JsonObject[]> {
  let content: string;
  try {
    content = await readFile(path, "utf8");
  } catch (error) {
    if (
      error !== null &&
      typeof error === "object" &&
      "code" in error &&
      error.code === "ENOENT"
    ) {
      return [];
    }
    throw error;
  }
  const records: JsonObject[] = [];
  for (const [index, line] of content.split(/\r?\n/).entries()) {
    if (!line.trim()) continue;
    try {
      const parsed: unknown = JSON.parse(line);
      const record = jsonObject(parsed);
      if (!record) throw new Error("record must be a JSON object");
      if (schemaVersion !== undefined && record.schema_version !== schemaVersion) {
        throw new Error(`schema must be ${schemaVersion}`);
      }
      records.push(record);
    } catch {
      throw new EffectRuntimeRequestError(
        `settlement readback line ${index + 1} is malformed`,
        "malformed_settlement_state",
      );
    }
  }
  return records;
}

function turnKey(
  goalId: string | null,
  agentId: string | null,
  turnInstanceId: string | null,
): string | null {
  return goalId && agentId && turnInstanceId
    ? `${goalId}\u0000${agentId}\u0000${turnInstanceId}`
    : null;
}

function appendIndexed(
  index: Map<string, JsonObject[]>,
  key: string | null,
  record: JsonObject,
): void {
  if (key === null) return;
  const existing = index.get(key);
  if (existing) existing.push(record);
  else index.set(key, [record]);
}

function indexSettlementSnapshot(
  runtimeRoot: string,
  goalId: string,
  events: readonly JsonObject[],
  runs: readonly JsonObject[],
): QuotaSettlementReadbackSnapshot {
  const eventsByTurn = new Map<string, JsonObject[]>();
  const runsByTurn = new Map<string, JsonObject[]>();
  const runsByEffectRef = new Map<string, JsonObject[]>();
  const runPositions = new Map<JsonObject, number>();
  for (const event of events) {
    appendIndexed(
      eventsByTurn,
      turnKey(
        optionalString(event.goal_id),
        optionalString(event.agent_id),
        optionalString(event.run_id),
      ),
      event,
    );
  }
  for (const [position, run] of runs.entries()) {
    runPositions.set(run, position);
    appendIndexed(
      runsByTurn,
      turnKey(
        optionalString(run.goal_id),
        normalizeAgentId(run.agent_id),
        optionalString(run.turn_instance_id),
      ),
      run,
    );
    appendIndexed(runsByEffectRef, optionalString(run.effect_ref), run);
  }
  return {
    runtimeRoot,
    goalId,
    events,
    runs,
    eventsByTurn,
    runsByTurn,
    runsByEffectRef,
    runPositions,
  };
}

/** Load one immutable settlement snapshot for one or many readbacks. */
export async function readQuotaSettlementSnapshot(
  runtimeRoot: string,
  goalId: string,
  rolloutSnapshot?: GoalRolloutEventSnapshot | null,
): Promise<QuotaSettlementReadbackSnapshot> {
  settlementScope(runtimeRoot, goalId);
  if (
    rolloutSnapshot !== undefined &&
    rolloutSnapshot !== null &&
    (
      rolloutSnapshot.runtimeRoot !== runtimeRoot ||
      rolloutSnapshot.goalId !== goalId
    )
  ) {
    throw new EffectRuntimeRequestError(
      "rollout event snapshot does not match the settlement scope",
      "settlement_snapshot_scope_mismatch",
    );
  }
  const goalRoot = join(runtimeRoot, "goals", goalId);
  const [snapshot, runs] = await Promise.all([
    rolloutSnapshot === undefined
      ? readGoalRolloutEventSnapshot(runtimeRoot, goalId)
      : Promise.resolve(rolloutSnapshot),
    readJsonLines(join(goalRoot, "runs", "index.jsonl")),
  ]);
  return indexSettlementSnapshot(
    runtimeRoot,
    goalId,
    strictGoalRolloutEvents(snapshot),
    runs,
  );
}

function indexedEvents(
  snapshot: QuotaSettlementReadbackSnapshot,
  goalId: string,
  agentId: string,
  turnInstanceId: string,
): readonly JsonObject[] {
  return snapshot.eventsByTurn.get(
    turnKey(goalId, agentId, turnInstanceId)!,
  ) ?? [];
}

function indexedRuns(
  snapshot: QuotaSettlementReadbackSnapshot,
  identity: SettlementIdentity,
): readonly JsonObject[] {
  return snapshot.runsByTurn.get(
    turnKey(identity.goal_id, identity.agent_id, identity.turn_instance_id)!,
  ) ?? [];
}

function spendCandidateRuns(
  snapshot: QuotaSettlementReadbackSnapshot,
  identity: SettlementIdentity,
  turnRuns: readonly JsonObject[],
): readonly JsonObject[] {
  const effectRuns = snapshot.runsByEffectRef.get(
    `${identity.effect_id}#quota_spend`,
  ) ?? [];
  if (effectRuns.length === 0) return turnRuns;
  return [...new Set([...turnRuns, ...effectRuns])].sort(
    (left, right) =>
      (snapshot.runPositions.get(left) ?? -1) -
      (snapshot.runPositions.get(right) ?? -1),
  );
}

function persistedIdentityMatches(
  rawIdentity: unknown,
  expectedEffectId: string,
): boolean {
  if (rawIdentity === undefined) return true;
  const identity = jsonObject(rawIdentity);
  if (!identity) return false;
  return optionalString(identity.effect_id) === expectedEffectId;
}

function quotaSpendMetadataMatches(
  rawMetadata: unknown,
  effectRef: string | null,
  expectedEffectRef: string,
): boolean {
  if (rawMetadata === undefined) return true;
  const metadata = jsonObject(rawMetadata);
  const metadataEffectId = optionalString(metadata?.effect_id);
  return metadataEffectId === expectedEffectRef &&
    (effectRef === null || metadataEffectId === effectRef);
}

function runEffectMatches(
  run: JsonObject,
  identity: SettlementIdentity,
  stepKind: "durable_writeback" | "quota_spend",
): boolean {
  const expectedEffectId = identity.effect_id;
  const expectedEffectRef = `${expectedEffectId}#${stepKind}`;
  const effectRef = optionalString(run.effect_ref);
  return persistedIdentityMatches(run.settlement_identity, expectedEffectId) &&
    (!effectRef || effectRef === expectedEffectRef) &&
    (stepKind !== "quota_spend" ||
      quotaSpendMetadataMatches(run.quota_spend_commit, effectRef, expectedEffectRef));
}

export function projectSemanticReplanGuard(
  receiptDetails: JsonObject,
): JsonObject {
  if (!Object.hasOwn(receiptDetails, "semantic_replan_obligation_id")) {
    return {
      schema_version: SEMANTIC_REPLAN_GUARD_SCHEMA,
      scope: "legacy_unscoped",
      selected_obligation_id: null,
    };
  }
  const rawObligationId = receiptDetails.semantic_replan_obligation_id;
  if (rawObligationId === "" || rawObligationId === null) {
    return {
      schema_version: SEMANTIC_REPLAN_GUARD_SCHEMA,
      scope: "turn_guard",
      selected_obligation_id: null,
    };
  }
  const selectedObligationId = normalizeReplanObligationId(rawObligationId);
  if (selectedObligationId === null) {
    throw new EffectRuntimeRequestError(
      "heartbeat receipt semantic replan guard is malformed",
      "malformed_settlement_state",
    );
  }
  return {
    schema_version: SEMANTIC_REPLAN_GUARD_SCHEMA,
    scope: "turn_guard",
    selected_obligation_id: selectedObligationId,
  };
}

function runMatchesBinding(run: JsonObject, identity: SettlementIdentity): boolean {
  return normalizeTodoId(run.todo_id) === identity.todo_id &&
    normalizeReplanObligationId(run.replan_obligation_id) ===
      identity.replan_obligation_id;
}

function findWriteback(
  runs: readonly JsonObject[],
  identity: SettlementIdentity,
): JsonObject | null {
  return [...runs].reverse().find((run) =>
    optionalString(run.goal_id) === identity.goal_id &&
    optionalString(run.turn_instance_id) === identity.turn_instance_id &&
    runMatchesBinding(run, identity) &&
    normalizeAgentId(run.agent_id) === identity.agent_id &&
    runEffectMatches(run, identity, "durable_writeback") &&
    isTurnScopedSettlementOutcome(
      run.delivery_outcome,
      run.progress_observation,
      identity.todo_id ?? identity.replan_obligation_id,
    )
  ) ?? null;
}

function findSpend(
  runs: readonly JsonObject[],
  identity: SettlementIdentity,
): JsonObject | null {
  return [...runs].reverse().find((run) =>
    run.classification === "quota_slot_spent" &&
    optionalString(run.goal_id) === identity.goal_id &&
    normalizeAgentId(run.agent_id) === identity.agent_id &&
    (
      (optionalString(run.turn_instance_id) === identity.turn_instance_id &&
        runMatchesBinding(run, identity) &&
        runEffectMatches(run, identity, "quota_spend")) ||
      // Older quota commit rows predate persisted turn bindings. Their
      // exact effect ref is the durable identity for this replay path.
      optionalString(run.effect_ref) === `${identity.effect_id}#quota_spend` &&
        runEffectMatches(run, identity, "quota_spend")
    )
  ) ?? null;
}

function findStepEvent(
  events: readonly JsonObject[],
  identity: SettlementIdentity,
  eventKind: string,
): JsonObject | null {
  return [...events].reverse().find((event) =>
    event.event_kind === eventKind &&
    optionalString(event.goal_id) === identity.goal_id &&
    optionalString(event.agent_id) === identity.agent_id &&
    optionalString(event.run_id) === identity.turn_instance_id &&
    optionalString(details(event).settlement_effect_id) === identity.effect_id
  ) ?? null;
}

/**
 * Resolve the Turn's effective receipt, or null when it persisted no guard.
 *
 * The identity rule lives in one module, so the readback resolves the effective
 * receipt through the same reduction the closeout selector uses.
 */
function effectiveHeartbeatReceipt(
  events: readonly JsonObject[],
  identity: Pick<SettlementIdentity, "goal_id" | "agent_id" | "turn_instance_id">,
): JsonObject | null {
  const entries: { fact: HeartbeatReceiptFact; value: JsonObject }[] = [];
  for (const event of events) {
    if (event.event_kind !== "quota_should_run") continue;
    if (optionalString(event.goal_id) !== identity.goal_id) continue;
    if (optionalString(event.agent_id) !== identity.agent_id) continue;
    if (optionalString(event.run_id) !== identity.turn_instance_id) continue;
    entries.push({ fact: heartbeatReceiptFactFromEvent(event), value: event });
  }
  return entries.length === 0
    ? null
    : selectEffectiveHeartbeatReceipt(identity.goal_id, identity.agent_id, entries);
}

function writebackResult(
  identity: SettlementIdentity,
  run: JsonObject | null,
  event: JsonObject | null,
): SettlementResult<JsonObject> {
  if (!run || !event) {
    return settlementFailed<JsonObject>({
      kind: "writeback_missing",
      step_kind: "durable_writeback",
      reason:
        "matching accountable refresh-state receipt is missing for the original settlement identity",
    });
  }
  const eventId = optionalString(event.event_id);
  return settlementPure(run, [settlementReceipt(
    identity,
    "durable_writeback",
    eventId ? `rollout_event:${eventId}` : null,
  )]);
}

function spendResult(
  identity: SettlementIdentity,
  run: JsonObject | null,
  event: JsonObject | null,
): SettlementResult<JsonObject> {
  if (!run || !event) {
    return settlementFailed<JsonObject>({
      kind: "receipt_missing",
      step_kind: "quota_spend",
      reason: "matching quota spend receipt is missing",
    });
  }
  const eventId = optionalString(event.event_id);
  return settlementPure(run, [settlementReceipt(
    identity,
    "quota_spend",
    eventId ? `rollout_event:${eventId}` : null,
  )]);
}

function terminalResult(
  identity: SettlementIdentity,
  event: JsonObject | null,
): SettlementResult<JsonObject> {
  if (!event || details(event).no_followup !== true) {
    return settlementFailed<JsonObject>({
      kind: "receipt_missing",
      step_kind: "terminal_closeout",
      reason: "matching terminal no-follow-up closeout receipt is missing",
    });
  }
  const eventId = optionalString(event.event_id);
  return settlementPure(event, [settlementReceipt(
    identity,
    "terminal_closeout",
    eventId ? `rollout_event:${eventId}` : null,
  )]);
}

function inferPersistedIdentity(
  runs: readonly JsonObject[],
  goalId: string,
  agentId: string,
  requestedTodoId: string | null,
  allowUnboundBinding: boolean,
): SettlementResult<JsonObject> | null {
  for (const run of [...runs].reverse()) {
    const runAgentId = normalizeAgentId(run.agent_id);
    if (runAgentId && runAgentId !== agentId) continue;
    const classification = String(run.classification ?? "").trim();
    if (
      classification === "quota_slot_voided" ||
      classification === "quota_scheduler_ack" ||
      (classification === "quota_monitor_poll" && !isMaterialMonitorPoll(run)) ||
      (classification === "state_refreshed" &&
        !isTurnScopedSettlementOutcome(
          run.delivery_outcome,
          run.progress_observation,
          normalizeTodoId(run.todo_id) ??
            normalizeReplanObligationId(run.replan_obligation_id),
        ))
    ) {
      continue;
    }
    if (
      classification !== "quota_slot_spent" &&
      !isTurnScopedSettlementOutcome(
        run.delivery_outcome,
        run.progress_observation,
        normalizeTodoId(run.todo_id) ??
          normalizeReplanObligationId(run.replan_obligation_id),
      )
    ) {
      return null;
    }
    const candidateAgentId = normalizeAgentId(run.agent_id);
    if (allowUnboundBinding && candidateAgentId !== agentId) {
      return failedIdentity(
        "persisted settlement identity mismatch: accountable run is not bound to the requesting Agent",
        "identity_mismatch",
      );
    }
    const persisted = jsonObject(run.settlement_identity) ?? {};
    let todoId = normalizeTodoId(run.todo_id);
    let replanObligationId = normalizeReplanObligationId(
      run.replan_obligation_id,
    );
    if (requestedTodoId) {
      if (todoId !== requestedTodoId) return null;
      replanObligationId = null;
    } else {
      if (!allowUnboundBinding || Object.keys(persisted).length === 0) {
        return failedIdentity(
          "unbound visible-goal settlement recovery requires a fully typed persisted identity",
          "identity_mismatch",
        );
      }
      const persistedTodoId = normalizeTodoId(persisted.todo_id);
      const persistedReplanObligationId = normalizeReplanObligationId(
        persisted.replan_obligation_id,
      );
      if (Boolean(persistedTodoId) === Boolean(persistedReplanObligationId)) {
        return failedIdentity(
          "persisted settlement identity must contain exactly one Todo or autonomous replan binding",
          "identity_mismatch",
        );
      }
      if (todoId !== persistedTodoId) {
        return failedIdentity(
          "persisted settlement identity mismatch: Todo binding differs from the accountable run",
          "identity_mismatch",
        );
      }
      if (replanObligationId !== persistedReplanObligationId) {
        return failedIdentity(
          "persisted settlement identity mismatch: autonomous replan binding differs from the accountable run",
          "identity_mismatch",
        );
      }
      todoId = persistedTodoId;
      replanObligationId = persistedReplanObligationId;
    }
    const turnInstanceId = optionalString(run.turn_instance_id);
    if (!turnInstanceId) {
      return allowUnboundBinding
        ? failedIdentity(
          "persisted settlement identity mismatch: turn_instance_id differs from the accountable run",
          "identity_mismatch",
        )
        : null;
    }
    const identity = settlementIdentity({
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnInstanceId,
      replan_obligation_id: replanObligationId,
    });
    const expectedIdentity = settlementIdentityPayload(identity);
    if (
      allowUnboundBinding &&
      optionalString(persisted.turn_instance_id) !== turnInstanceId
    ) {
      return failedIdentity(
        "persisted settlement identity mismatch: turn_instance_id differs from the accountable run",
        "identity_mismatch",
      );
    }
    for (const [field, expected] of Object.entries(expectedIdentity)) {
      const actual = optionalString(persisted[field]);
      if (
        (allowUnboundBinding && actual !== expected) ||
        (!allowUnboundBinding && actual && actual !== expected)
      ) {
        return failedIdentity(
          `persisted settlement identity mismatch: ${field} is ${actual ?? "missing"} but expected ${expected}`,
          "identity_mismatch",
        );
      }
    }
    return settlementPure(settlementIdentityPayload(identity));
  }
  return null;
}

function failedIdentity(
  reason: string,
  kind:
    | "invalid_identity"
    | "identity_mismatch"
    | "receipt_missing"
    | "receipt_unbound",
  details?: JsonObject,
) {
  return settlementFailed<JsonObject>({
    kind,
    step_kind: "validation",
    reason,
    ...(details ? { details } : {}),
  });
}

function resolveIdentity(
  request: ReadbackRequest,
  events: readonly JsonObject[],
  runs: readonly JsonObject[],
): SettlementResult<JsonObject> | null {
  let agentId = normalizeAgentId(request.agent_id);
  let todoId = normalizeTodoId(request.todo_id);
  let replanObligationId = normalizeReplanObligationId(
    request.replan_obligation_id,
  );
  let turnInstanceId = request.turn_instance_id;
  if (request.infer_turn_instance_id) {
    if (
      !agentId ||
      replanObligationId ||
      (!todoId && !request.allow_unbound_binding)
    ) return null;
    const inferred = inferPersistedIdentity(
      runs,
      request.goal_id,
      agentId,
      todoId,
      request.allow_unbound_binding,
    );
    if (inferred === null) return null;
    if (inferred.failure || inferred.value === null) return inferred;
    todoId = normalizeTodoId(inferred.value.todo_id);
    replanObligationId = normalizeReplanObligationId(
      inferred.value.replan_obligation_id,
    );
    turnInstanceId = optionalString(inferred.value.turn_instance_id);
  }
  if (turnInstanceId && !TURN_INSTANCE_ID_PATTERN.test(turnInstanceId)) {
    return failedIdentity(
      "turn_instance_id must be 1-128 public-safe letters, numbers, or ._:-",
      "invalid_identity",
    );
  }
  if (!agentId || !turnInstanceId || Boolean(todoId) === Boolean(replanObligationId)) {
    return failedIdentity(
      "turn-scoped settlement requires agent_id, turn_instance_id, and exactly one todo_id or replan_obligation_id",
      "invalid_identity",
    );
  }
  const identity = settlementIdentity({
    goal_id: request.goal_id,
    agent_id: agentId,
    todo_id: todoId,
    turn_instance_id: turnInstanceId,
    replan_obligation_id: replanObligationId,
  });
  let receiptEvent: JsonObject | null;
  try {
    receiptEvent = effectiveHeartbeatReceipt(events, identity);
  } catch (error) {
    return failedIdentity((error as Error).message, "identity_mismatch");
  }
  if (!receiptEvent) {
    return failedIdentity(
      "matching quota should-run heartbeat receipt is missing; rerun the guard with the same turn_instance_id",
      "receipt_missing",
    );
  }
  const receiptDetails = details(receiptEvent);
  const receiptTodoId = normalizeTodoId(receiptDetails.todo_id);
  const receiptReplanObligationId = normalizeReplanObligationId(
    receiptDetails.replan_obligation_id,
  );
  if (receiptTodoId === null && receiptReplanObligationId === null) {
    // A same-turn guard that ran before any work item was chosen commits a
    // receipt with no settlement binding, and the documented wake order (guard,
    // then select) produces exactly that state. This read model never binds --
    // the guard's own same-turn reconciliation owns that, so there is one
    // binder rather than two -- which means the caller has to be told the state
    // and the exact repair instead of being handed a binding mismatch it cannot
    // act on. The state also gets its own failure kind, so a consumer can branch
    // on the missing binding without reading details.binding_kind: the receipt
    // exists and is well-formed here, which is not what identity_mismatch means.
    //
    // A Turn whose explicit choice the guard already deferred is a different
    // state with a different repair: rebinding through `--todo-id` re-enters the
    // same preemption and defers again, while the guard's argument-less reentry
    // binds the preemption it is actually holding. Naming the wrong command
    // costs the caller a turn, so the repair is chosen from the retained
    // selection the guard recorded.
    const deferredSelectionTodoId = normalizeTodoId(
      receiptDetails.pending_action_selection_todo_id,
    );
    const repair = deferredSelectionTodoId === null
      ? "rebind it through the guard's same-turn reconciliation, then settle: " +
        "quota should-run --turn-instance-id " +
        `${turnInstanceId} --todo-id ${identity.todo_id ?? "<todo_id>"}`
      : "the guard deferred this turn's explicit selection instead of binding " +
        "it, so rerun the guard for the same turn without --todo-id (which " +
        "binds the preemption it is holding), then settle with the identity it " +
        `returns: quota should-run --turn-instance-id ${turnInstanceId}`;
    return failedIdentity(
      "the quota should-run receipt for this turn carries no settlement binding " +
        `yet (turn_instance_id ${turnInstanceId}); ${repair}`,
      "receipt_unbound",
      {
        binding_kind: "unbound",
        requested_binding_kind: identity.binding_kind,
        turn_instance_id: turnInstanceId,
        ...(deferredSelectionTodoId === null
          ? {}
          : { deferred_selection_todo_id: deferredSelectionTodoId }),
      },
    );
  }
  if (
    receiptTodoId !== identity.todo_id ||
    receiptReplanObligationId !== identity.replan_obligation_id
  ) {
    return failedIdentity(
      "settlement binding does not match the original quota guard: " +
        `receipt todo=${receiptTodoId ?? "missing"} and replan_obligation_id=` +
        `${receiptReplanObligationId ?? "missing"}, requested todo=` +
        `${identity.todo_id ?? "missing"} and replan_obligation_id=` +
        `${identity.replan_obligation_id ?? "missing"}`,
      "identity_mismatch",
    );
  }
  const receiptEffectId = optionalString(receiptDetails.settlement_effect_id);
  if (!effectIdsMatch(receiptEffectId, identity.effect_id)) {
    return failedIdentity(
      "settlement effect does not match the original quota guard: " +
        `receipt effect is ${receiptEffectId} but expected ${identity.effect_id}`,
      "identity_mismatch",
    );
  }
  const eventId = optionalString(receiptEvent.event_id);
  return settlementPure(
    settlementIdentityPayload(identity),
    [settlementReceipt(identity, "validation", eventId ? `rollout_event:${eventId}` : null)],
  );
}

function bundle(result: SettlementResult): ResultBundle {
  return { result, payload: settlementResultPayload(result) };
}

function failedAfterIdentity(
  identityResult: SettlementResult<JsonObject>,
): SettlementResult<JsonObject> {
  return identityResult.failure
    ? { value: null, receipts: [...identityResult.receipts], failure: { ...identityResult.failure } }
    : settlementFailed({
      kind: "invalid_identity",
      step_kind: "validation",
      reason: "quota settlement readback has no identity",
    });
}

function failedReadback(
  identityResult: SettlementResult<JsonObject>,
): JsonObject {
  const downstreamFailure = failedAfterIdentity(identityResult);
  const terminalFailure = settlementFailed<JsonObject>({
    kind: "receipt_missing",
    step_kind: "terminal_closeout",
    reason: "matching terminal no-follow-up closeout receipt is missing",
  });
  return {
    schema_version: QUOTA_SETTLEMENT_READBACK_RESULT_SCHEMA,
    found: true,
    identity: bundle(identityResult),
    writeback: bundle(downstreamFailure),
    spend: bundle(downstreamFailure),
    delivery: bundle(downstreamFailure),
    settlement: bundle(downstreamFailure),
    terminal_closeout: bundle(terminalFailure),
    terminal_settlement: bundle(downstreamFailure),
    progress: settlementProgress(identityResult, downstreamFailure, downstreamFailure, null, null),
    workspace_causality: null,
    semantic_replan_guard: null,
    writeback_run: null,
    spend_run: null,
    heartbeat_receipt: null,
    writeback_event: null,
    spend_event: null,
    completion_event: null,
    monitor_phase: null,
    replay_phase: null,
    refresh_recovery: null,
  };
}

function readQuotaSettlementFromRequest(
  request: ReadbackRequest,
  snapshot: QuotaSettlementReadbackSnapshot,
): JsonObject {
  if (
    snapshot.runtimeRoot !== request.runtime_root ||
    snapshot.goalId !== request.goal_id
  ) {
    throw new EffectRuntimeRequestError(
      "settlement snapshot does not match the readback request",
      "settlement_snapshot_scope_mismatch",
    );
  }
  const explicitAgentId = normalizeAgentId(request.agent_id);
  const explicitEvents = !request.infer_turn_instance_id &&
      explicitAgentId !== null && request.turn_instance_id !== null
    ? indexedEvents(
      snapshot,
      request.goal_id,
      explicitAgentId,
      request.turn_instance_id,
    )
    : snapshot.events;
  const identityResult = resolveIdentity(
    request,
    explicitEvents,
    snapshot.runs,
  );
  if (identityResult === null) {
    return {
      schema_version: QUOTA_SETTLEMENT_READBACK_RESULT_SCHEMA,
      found: false,
    };
  }
  if (identityResult.failure || identityResult.value === null) {
    return failedReadback(identityResult);
  }

  const identity = settlementIdentity({
    goal_id: String(identityResult.value.goal_id),
    agent_id: String(identityResult.value.agent_id),
    todo_id: optionalString(identityResult.value.todo_id),
    turn_instance_id: String(identityResult.value.turn_instance_id),
    replan_obligation_id: optionalString(identityResult.value.replan_obligation_id),
  });
  const events = indexedEvents(
    snapshot,
    identity.goal_id,
    identity.agent_id,
    identity.turn_instance_id,
  );
  const runs = indexedRuns(snapshot, identity);
  const heartbeatReceipt = effectiveHeartbeatReceipt(events, identity);
  if (heartbeatReceipt === null) {
    return failedReadback(
      failedIdentity(
        "matching quota should-run heartbeat receipt disappeared during settlement readback",
        "receipt_missing",
      ),
    );
  }
  const receiptDetails = details(heartbeatReceipt);
  const writebackRun = findWriteback(runs, identity);
  const writebackEvent = findStepEvent(events, identity, "refresh_state");
  const spendRun = findSpend(
    spendCandidateRuns(snapshot, identity, runs),
    identity,
  );
  const spendEvent = findStepEvent(events, identity, "quota_spend");
  const completionEvent = findStepEvent(events, identity, "todo_complete");

  const writeback = writebackResult(identity, writebackRun, writebackEvent);
  const spend = spendResult(identity, spendRun, spendEvent);
  // The exact Turn-bound blocked writeback is itself a durable no-spend
  // closeout. It cannot certify Todo completion or become delivery progress.
  // A spend already committed for this identity remains an ordinary spend
  // settlement, so readback never erases a historical debit.
  const blockedNoSpend = writeback.failure === null &&
    spendRun === null && spendEvent === null &&
    identity.binding_kind === "todo" &&
    writebackRun !== null && writebackRun.delivery_outcome === "outcome_gap" &&
    isBoundedBlockedRetry(writebackRun.blocked_retry, identity.todo_id) &&
    isTurnScopedSettlementOutcome(
      writebackRun.delivery_outcome,
      writebackRun.progress_observation,
      identity.todo_id,
    );
  const terminalCloseout = terminalResult(identity, completionEvent);
  const withWriteback = settlementBindReduce(identityResult, writeback);
  const settled = blockedNoSpend ? withWriteback : settlementBindReduce(withWriteback, spend);
  const terminalSettlement = settlementBindReduce(settled, terminalCloseout);
  const monitorPoll = [...runs].reverse().find((run) =>
    run.classification === "quota_monitor_poll" &&
    optionalString(run.goal_id) === identity.goal_id &&
    optionalString(run.agent_id) === identity.agent_id &&
    optionalString(run.turn_instance_id) === identity.turn_instance_id &&
    normalizeTodoId(run.todo_id) === identity.todo_id &&
    isCommittedMonitorPollEffect(jsonObject(run.quota_monitor_poll_commit)?.effect_id, identity)
  ) ?? null;
  const nestedCausality = typeof receiptDetails.delivery_workspace_causality === "object" &&
      receiptDetails.delivery_workspace_causality !== null &&
      !Array.isArray(receiptDetails.delivery_workspace_causality)
    ? receiptDetails.delivery_workspace_causality
    : null;
  const flatCausality = {
    schema_version: receiptDetails.delivery_workspace_causality_schema_version,
    todo_id: receiptDetails.delivery_workspace_causality_todo_id,
    requirement: receiptDetails.delivery_workspace_requirement,
    source: receiptDetails.delivery_workspace_causality_source,
    reason: receiptDetails.delivery_workspace_causality_reason,
  };
  const workspaceCausality: DeliveryWorkspaceCausality | null =
    normalizeDeliveryWorkspaceCausality(nestedCausality, identity.todo_id) ??
    normalizeDeliveryWorkspaceCausality(flatCausality, identity.todo_id);
  const semanticReplanGuard = projectSemanticReplanGuard(receiptDetails);
  const todoBoundReplan = identity.binding_kind === "todo" &&
    semanticReplanGuard.scope === "turn_guard" &&
    semanticReplanGuard.selected_obligation_id !== null;

  const recovery = request.refresh_retry === null ? null : refreshRecovery(
    request.refresh_retry, writebackRun, writeback.failure === null,
    workspaceCausality?.requirement,
    writebackRun !== null && snapshot.runs.slice(
      (snapshot.runPositions.get(writebackRun) ?? -1) + 1,
    ).some((run) =>
      run.goal_id === identity.goal_id && run.agent_id === identity.agent_id &&
      (jsonObject(run.agent_vision) !== null || jsonObject(run.vision_checkpoint)?.required === true)
    ),
  );

  return {
    schema_version: QUOTA_SETTLEMENT_READBACK_RESULT_SCHEMA,
    found: true,
    identity: bundle(identityResult),
    writeback: bundle(writeback),
    spend: bundle(spend),
    delivery: bundle(withWriteback),
    settlement: bundle(settled),
    terminal_closeout: bundle(terminalCloseout),
    terminal_settlement: bundle(terminalSettlement),
    progress: settlementProgress(identityResult, writeback, spend, writebackRun, spendRun,
      receiptDetails.quota_spend_source ?? spendRun?.source, blockedNoSpend),
    workspace_causality: workspaceCausality,
    semantic_replan_guard: semanticReplanGuard,
    writeback_run: writebackRun,
    refresh_recovery: recovery,
    external_delivery: request.refresh_retry === null ? null : refreshExternalDelivery(
      request.refresh_retry.external_delivery ?? null, identity, events,
      recovery?.decision !== "reject" && recovery?.decision !== "repair_receipt",
    ),
    spend_run: spendRun,
    heartbeat_receipt: heartbeatReceipt,
    writeback_event: writebackEvent,
    spend_event: spendEvent,
    completion_event: completionEvent,
    monitor_phase: receiptBoundMonitorPhase({
      poll_present: monitorPoll !== null,
      material_change: isMaterialMonitorPoll(monitorPoll),
      durable_writeback_present: writeback.failure === null,
      quota_spend_present: spend.failure === null,
    }),
    replay_phase: receiptBoundReplayPhase({
      binding_kind: identity.binding_kind,
      writeback_completes_binding: todoBoundReplan || blockedNoSpend,
      completion_receipt_present: completionEvent !== null,
      durable_writeback_present: writeback.failure === null,
      quota_spend_present: spend.failure === null,
      no_spend_closeout_present: blockedNoSpend,
    }),
  };
}

export function readQuotaSettlementFromSnapshot(
  value: unknown,
  snapshot: QuotaSettlementReadbackSnapshot,
): JsonObject {
  return readQuotaSettlementFromRequest(decodeRequest(value), snapshot);
}

export async function readQuotaSettlement(value: unknown): Promise<JsonObject> {
  const request = decodeRequest(value);
  return readQuotaSettlementFromRequest(
    request,
    await readQuotaSettlementSnapshot(request.runtime_root, request.goal_id),
  );
}
