/**
 * Prior-host-Turn closeout recovery.
 *
 * One must-attempt heartbeat Turn can leave the goal without a legal closeout
 * receipt. Whether that happened, which closeout is accepted, and what the
 * recovery obligation is are domain decisions; they live here so the Python
 * caller only reads its own persisted facts, transports them, and projects the
 * typed result back into the existing public payload.
 *
 * The transaction is two requests around one real Python provider: a
 * fail-closed preflight that reads the persisted receipts and the settlement
 * readback and names the exact prior Turn whose bound facts the caller must
 * read, then one final reduction over those checkpointed facts. A prior Turn
 * whose settlement already validates is answered by the preflight, so the
 * caller reads no bound facts for it.
 */
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  jsonObject,
  requireJsonObject,
  requireNonEmptyString,
} from "../runtime_decode.ts";
import { readGoalHeartbeatReceipts } from "../rollout_receipt_log.ts";
import { isCommittedMonitorPollEffect } from "./settlement_phase.ts";
import {
  heartbeatReceiptBinding,
  heartbeatReceiptFactFromEvent,
  normalizeHeartbeatReplanObligationId,
  normalizeHeartbeatTodoId,
  optionalHeartbeatString,
  selectEffectiveHeartbeatReceipt,
  type HeartbeatReceiptBinding,
  type HeartbeatReceiptFact,
} from "./heartbeat_receipt_identity.ts";
import {
  QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
  readQuotaSettlement,
} from "./settlement_readback.ts";

export const PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA =
  "loopx_prior_host_turn_closeout_preflight_request_v0";
export const PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_RESULT_SCHEMA =
  "loopx_prior_host_turn_closeout_preflight_result_v0";
export const UNSETTLED_HOST_TURN_RECOVERY_REQUEST_SCHEMA =
  "loopx_prior_host_turn_recovery_request_v0";
export const UNSETTLED_HOST_TURN_RECOVERY_RESULT_SCHEMA =
  "loopx_prior_host_turn_recovery_result_v0";
export const UNSETTLED_HOST_TURN_RECOVERY_SCHEMA_VERSION =
  "unsettled_host_turn_recovery_v0";

const MONITOR_TASK_CLASS = "continuous_monitor";
const SETTLED_LIFECYCLE_STATUSES = ["done", "blocked", "deferred"] as const;
const WRITEBACK_RECEIPT = "durable_writeback_receipt";
const SPEND_RECEIPT = "quota_spend_receipt";
const MISSING_RECEIPT_NAMES = [WRITEBACK_RECEIPT, SPEND_RECEIPT] as const;

export const ACCEPTED_CLOSEOUTS = [
  "validated_writeback_and_quota_spend",
  "exact_committed_quota_monitor_poll",
  "typed_external_wait_with_runnable_successor",
  "typed_blocker_or_lifecycle_transition",
] as const;
export type AcceptedCloseout = (typeof ACCEPTED_CLOSEOUTS)[number];

export interface PriorHostTurnCloseoutCandidate extends JsonObject {
  prior_turn_instance_id: string;
  event_id: string | null;
  binding_kind: HeartbeatReceiptBinding["binding_kind"];
  /** The Todo id or autonomous replan obligation id, per `binding_kind`. */
  binding_id: string;
  settlement_effect_id: string | null;
}

interface PreflightRequest {
  runtime_root: string;
  goal_id: string;
  agent_id: string;
  exclude_turn_instance_id: string | null;
}

function decodePreflightRequest(value: unknown): PreflightRequest {
  const request = requireJsonObject(value, "prior host Turn closeout preflight request");
  if (request.schema_version !== PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError(
      "Prior host Turn closeout preflight request schema mismatch",
    );
  }
  return {
    runtime_root: requireNonEmptyString(request.runtime_root, "runtime_root"),
    goal_id: requireNonEmptyString(request.goal_id, "goal_id"),
    agent_id: requireNonEmptyString(request.agent_id, "agent_id"),
    exclude_turn_instance_id: optionalHeartbeatString(
      request.exclude_turn_instance_id,
    ),
  };
}

function candidate(
  binding: HeartbeatReceiptBinding,
  turnInstanceId: string,
  fact: HeartbeatReceiptFact,
): PriorHostTurnCloseoutCandidate {
  return {
    prior_turn_instance_id: turnInstanceId,
    event_id: fact.event_id,
    binding_kind: binding.binding_kind,
    binding_id: binding.binding_id,
    settlement_effect_id: binding.settlement_effect_id,
  };
}

/**
 * Select the prior must-attempt Turns that still require a host closeout.
 *
 * Every prior Turn is identity-validated, so a conflicting receipt fails the
 * read instead of being hidden behind a newer Turn. Results are newest first
 * and carry at most one effective receipt per Turn, which is the same single
 * binding the caller's recovery decision may act on.
 */
function selectCloseoutCandidates(
  receipts: readonly JsonObject[],
  goalId: string,
  agentId: string,
  excludeTurnInstanceId: string | null,
): { candidates: PriorHostTurnCloseoutCandidate[]; turnsValidated: number } {
  const perTurn = new Map<string, HeartbeatReceiptFact[]>();
  const newestFirst: string[] = [];
  for (const event of receipts) {
    const fact = heartbeatReceiptFactFromEvent(event);
    const turnInstanceId = fact.run_id;
    if (!turnInstanceId || turnInstanceId === excludeTurnInstanceId) {
      continue;
    }
    const existing = perTurn.get(turnInstanceId);
    if (existing) existing.push(fact);
    else perTurn.set(turnInstanceId, [fact]);
    const position = newestFirst.indexOf(turnInstanceId);
    if (position !== -1) newestFirst.splice(position, 1);
    newestFirst.push(turnInstanceId);
  }

  const candidates: PriorHostTurnCloseoutCandidate[] = [];
  let validated = 0;
  for (const turnInstanceId of [...newestFirst].reverse()) {
    const entries = (perTurn.get(turnInstanceId) ?? []).map((fact) => ({
      fact,
      value: fact,
    }));
    const effective = selectEffectiveHeartbeatReceipt(goalId, agentId, entries);
    validated += 1;
    if (effective === null) continue;
    // Closeout is opt-in on the persisted receipt, so older receipts from
    // before this contract cannot become new recovery obligations.
    if (!effective.closeout_required) continue;
    const binding = heartbeatReceiptBinding(goalId, agentId, effective);
    if (binding === null) continue;
    candidates.push(candidate(binding, turnInstanceId, effective));
  }
  return {candidates, turnsValidated: validated};
}

function settlementReadbackRequest(
  request: PreflightRequest,
  candidate: PriorHostTurnCloseoutCandidate,
): JsonObject {
  return {
    schema_version: QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
    runtime_root: request.runtime_root,
    goal_id: request.goal_id,
    agent_id: request.agent_id,
    todo_id: candidate.binding_kind === "todo" ? candidate.binding_id : null,
    turn_instance_id: candidate.prior_turn_instance_id,
    replan_obligation_id: candidate.binding_kind === "autonomous_replan"
      ? candidate.binding_id
      : null,
    infer_turn_instance_id: false,
    allow_unbound_binding: false,
  };
}

function bundleFailed(readback: JsonObject, step: string): boolean {
  const bundle = jsonObject(readback[step]);
  const payload = bundle === null ? null : jsonObject(bundle.payload);
  if (payload === null || typeof payload.ok !== "boolean") {
    throw new EffectRuntimeRequestError(
      `quota settlement readback has no ${step} result`,
      "malformed_settlement_readback",
    );
  }
  return payload.ok !== true;
}

/**
 * Fail-closed preflight for the prior-host-Turn closeout transaction.
 *
 * It reads the persisted guards itself, so the caller ships a runtime path and
 * an identity instead of a whole rollout log, and it validates the selected
 * Turn's settlement so a Turn that already settled never makes the caller read
 * bound facts.
 */
export async function preflightPriorHostTurnCloseout(
  value: unknown,
): Promise<JsonObject> {
  const request = decodePreflightRequest(value);
  const receipts = await readGoalHeartbeatReceipts(
    request.runtime_root,
    request.goal_id,
    request.agent_id,
  );
  const { candidates, turnsValidated } = selectCloseoutCandidates(
    receipts ?? [],
    request.goal_id,
    request.agent_id,
    request.exclude_turn_instance_id,
  );
  if (candidates.length === 0) {
    return {
      schema_version: PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_RESULT_SCHEMA,
      status: "none",
      reason: "no_prior_turn_requires_closeout",
      turns_validated: turnsValidated,
    };
  }
  let newestSettledTurn: string | null = null;
  for (const selected of candidates) {
    const readback = await readQuotaSettlement(
      settlementReadbackRequest(request, selected),
    );
    if (readback.found === true && !bundleFailed(readback, "settlement")) {
      // A newer settled Turn cannot hide an older missing closeout. Keep
      // scanning in persisted newest-first order until the first unsettled
      // candidate is found.
      newestSettledTurn ??= selected.prior_turn_instance_id;
      continue;
    }
    const missingReceipts: string[] = [];
    if (readback.found !== true || bundleFailed(readback, "writeback")) {
      missingReceipts.push(WRITEBACK_RECEIPT);
    }
    if (readback.found !== true || bundleFailed(readback, "spend")) {
      missingReceipts.push(SPEND_RECEIPT);
    }
    return {
      schema_version: PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_RESULT_SCHEMA,
      status: "candidate",
      turns_validated: turnsValidated,
      candidate: selected,
      missing_receipts: missingReceipts,
    };
  }
  return {
    schema_version: PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_RESULT_SCHEMA,
    status: "none",
    reason: "prior_turn_settlement_validated",
    turns_validated: turnsValidated,
    prior_turn_instance_id: newestSettledTurn,
    accepted_closeout: "validated_writeback_and_quota_spend",
  };
}

function decodeCandidate(value: unknown): PriorHostTurnCloseoutCandidate {
  const raw = requireJsonObject(value, "prior host Turn recovery candidate");
  const bindingKind = raw.binding_kind;
  if (bindingKind !== "todo" && bindingKind !== "autonomous_replan") {
    throw new EffectRuntimeRequestError(
      "prior host Turn recovery candidate binding_kind is unsupported",
    );
  }
  const bindingId = requireNonEmptyString(raw.binding_id, "candidate.binding_id");
  if (bindingKind === "todo" && normalizeHeartbeatTodoId(bindingId) !== bindingId) {
    throw new EffectRuntimeRequestError(
      "prior host Turn recovery candidate Todo binding is not a legal Todo id",
    );
  }
  if (
    bindingKind === "autonomous_replan" &&
    normalizeHeartbeatReplanObligationId(bindingId) !== bindingId
  ) {
    throw new EffectRuntimeRequestError(
      "prior host Turn recovery candidate autonomous replan binding is malformed",
    );
  }
  return {
    prior_turn_instance_id: requireNonEmptyString(
      raw.prior_turn_instance_id,
      "candidate.prior_turn_instance_id",
    ),
    event_id: optionalHeartbeatString(raw.event_id),
    binding_kind: bindingKind,
    binding_id: bindingId,
    settlement_effect_id: optionalHeartbeatString(raw.settlement_effect_id),
  };
}

interface CandidateTodoFacts {
  task_class: string;
  status: string;
  has_resume_when: boolean;
  has_successor_todo_ids: boolean;
  target_key: string | null;
  cadence: string | null;
}

function decodeTodoFacts(value: unknown): CandidateTodoFacts | null {
  if (value === null || value === undefined) return null;
  const raw = requireJsonObject(value, "prior host Turn recovery bound Todo");
  if (
    typeof raw.has_resume_when !== "boolean" ||
    typeof raw.has_successor_todo_ids !== "boolean"
  ) {
    throw new EffectRuntimeRequestError(
      "prior host Turn recovery Todo must declare its retained wait shape",
    );
  }
  return {
    task_class: typeof raw.task_class === "string" ? raw.task_class : "",
    // The lifecycle verdict reads the persisted status verbatim; trimming here
    // would accept a value the legacy projection never accepted.
    status: typeof raw.status === "string" ? raw.status : "",
    has_resume_when: raw.has_resume_when,
    has_successor_todo_ids: raw.has_successor_todo_ids,
    target_key: optionalHeartbeatString(raw.target_key),
    cadence: optionalHeartbeatString(raw.cadence),
  };
}

interface BindingFacts {
  read: boolean;
  todo: CandidateTodoFacts | null;
  committedMonitorPoll: JsonObject | null;
}

function decodeBindingFacts(value: unknown): BindingFacts {
  const raw = requireJsonObject(value, "prior host Turn recovery binding facts");
  if (raw.status === "not_read") {
    return { read: false, todo: null, committedMonitorPoll: null };
  }
  if (raw.status !== "read") {
    throw new EffectRuntimeRequestError(
      "prior host Turn recovery binding facts status is unsupported",
    );
  }
  return {
    read: true,
    todo: decodeTodoFacts(raw.todo ?? null),
    committedMonitorPoll: jsonObject(raw.committed_monitor_poll) ?? null,
  };
}

/**
 * Decode the checkpointed missing-receipt list the preflight produced.
 *
 * The names are the closeout policy's own vocabulary, so an unknown or
 * reordered list is a caller error rather than something to normalize silently.
 */
function decodeMissingReceipts(value: unknown): string[] {
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError("missing_receipts must be an array");
  }
  const expected = MISSING_RECEIPT_NAMES.filter((name) => value.includes(name));
  if (
    expected.length === 0 ||
    expected.length !== value.length ||
    expected.some((name, index) => name !== value[index])
  ) {
    throw new EffectRuntimeRequestError(
      "missing_receipts must be the closeout receipts this transaction names, in order",
    );
  }
  return [...expected];
}

function acceptedLifecycleCloseout(todo: CandidateTodoFacts | null): AcceptedCloseout | null {
  if (todo === null) return null;
  if (
    todo.status === "open" &&
    todo.has_resume_when &&
    todo.has_successor_todo_ids
  ) {
    return "typed_external_wait_with_runnable_successor";
  }
  const settled = (SETTLED_LIFECYCLE_STATUSES as readonly string[]).includes(
    todo.status,
  );
  return settled ? "typed_blocker_or_lifecycle_transition" : null;
}

type RecoveryRepair = "monitor_poll" | "resume_prior_turn" | "lifecycle";

function recoveryRepair(
  bindingKind: HeartbeatReceiptBinding["binding_kind"],
  todo: CandidateTodoFacts | null,
): RecoveryRepair {
  if (bindingKind !== "todo" || todo === null) return "lifecycle";
  if (todo.task_class === MONITOR_TASK_CLASS) return "monitor_poll";
  // Missing receipts do not prove an external dependency. Re-enter
  // the original guard to recheck eligibility and retain its settlement id.
  // This is a recovery route, never an accepted closeout or delivery grant.
  return todo.task_class === "advancement_task" && todo.status === "open" &&
      !todo.has_resume_when
    ? "resume_prior_turn"
    : "lifecycle";
}

function recoveryObligation(
  repair: RecoveryRepair,
  bindingId: string,
): JsonObject {
  const obligation = repair === "resume_prior_turn"
    ? "reenter_original_guard_then_settle"
    : "author_typed_closeout_then_continue_successor";
  return {
    lane: "control_plane_recovery",
    next_lane: "advancement_task",
    obligation,
    contract: "repair_prior_turn_closeout",
    contract_obligation: obligation,
    must_attempt_work: true,
    delivery_allowed: false,
    notify: "DONT_NOTIFY",
    spend_policy: "no spend for the recovery transition",
    reason_code: "unsettled_host_turn",
    reason: "a prior must-attempt heartbeat has no legal closeout receipt",
    recommendation_reason: "prior must-attempt host Turn is missing a legal closeout",
    unsettled_reason: "prior must-attempt host Turn remains unsettled",
    recommended_action: repair === "resume_prior_turn"
      ? `Recover prior unsettled host Turn for ${bindingId}; re-enter its original ` +
        "guard, inspect existing effects, then resume and settle verified work under " +
        "that identity before rerunning the current Turn; do not invent an external wait"
      : `Recover prior unsettled host Turn for ${bindingId}; use a typed ` +
        "lifecycle observation, then rerun quota and continue eligible work",
    repair,
  };
}

/**
 * Reduce the selected prior Turn to its closeout verdict.
 *
 * The accepted closeout order is policy: a validated settlement wins, then an
 * exactly committed monitor-poll closeout, then a typed lifecycle transition.
 * Anything else is a recovery obligation with the exact missing receipts named.
 */
export function reduceUnsettledHostTurnRecovery(value: unknown): JsonObject {
  const request = requireJsonObject(value, "prior host Turn recovery request");
  if (request.schema_version !== UNSETTLED_HOST_TURN_RECOVERY_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError(
      "Prior host Turn recovery request schema mismatch",
    );
  }
  const goalId = requireNonEmptyString(request.goal_id, "goal_id");
  const agentId = requireNonEmptyString(request.agent_id, "agent_id");
  const selected = decodeCandidate(request.candidate);
  const missingReceipts = decodeMissingReceipts(request.missing_receipts);

  const bindingFacts = decodeBindingFacts(request.binding_facts);
  if (!bindingFacts.read) {
    // A caller that skipped the bound-Todo read cannot be given a verdict: the
    // monitor-poll and lifecycle closeouts are exactly what that read proves.
    throw new EffectRuntimeRequestError(
      "prior host Turn recovery requires its bound Todo facts once the preflight named it as a closeout candidate",
    );
  }
  const todo = bindingFacts.todo;
  const committedEffectId = bindingFacts.committedMonitorPoll === null
    ? null
    : optionalHeartbeatString(bindingFacts.committedMonitorPoll.effect_id);
  if (
    selected.binding_kind === "todo" &&
    todo?.task_class === MONITOR_TASK_CLASS &&
    committedEffectId !== null &&
    isCommittedMonitorPollEffect(committedEffectId, {
      goal_id: goalId,
      agent_id: agentId,
      turn_instance_id: selected.prior_turn_instance_id,
      todo_id: selected.binding_id,
    })
  ) {
    return {
      schema_version: UNSETTLED_HOST_TURN_RECOVERY_RESULT_SCHEMA,
      status: "none",
      prior_turn_instance_id: selected.prior_turn_instance_id,
      accepted_closeout: "exact_committed_quota_monitor_poll",
    };
  }

  const lifecycleCloseout = acceptedLifecycleCloseout(todo);
  if (lifecycleCloseout !== null) {
    return {
      schema_version: UNSETTLED_HOST_TURN_RECOVERY_RESULT_SCHEMA,
      status: "none",
      prior_turn_instance_id: selected.prior_turn_instance_id,
      accepted_closeout: lifecycleCloseout,
    };
  }

  const recovery: JsonObject = {
    schema_version: UNSETTLED_HOST_TURN_RECOVERY_SCHEMA_VERSION,
    state: "recovery_required",
    reason_code: "required_closeout_receipt_missing",
    prior_turn_instance_id: selected.prior_turn_instance_id,
    prior_event_id: selected.event_id,
    binding_kind: selected.binding_kind,
    binding_id: selected.binding_id,
    settlement_effect_id: selected.settlement_effect_id,
    missing_receipts: missingReceipts,
    accepted_closeouts: [...ACCEPTED_CLOSEOUTS],
    external_state_policy: "typed_host_observation_only",
    quota_policy: "no_spend_for_recovery_transition",
  };
  if (todo !== null) {
    recovery.binding_task_class = todo.task_class;
    recovery.binding_target_key = todo.target_key;
    recovery.binding_cadence = todo.cadence;
  }
  const repair = recoveryRepair(selected.binding_kind, todo);
  // The typed repair lane lets the CLI renderer project its command list
  // without re-deriving which closeout family the Turn belongs to.
  recovery.repair = repair;
  return {
    schema_version: UNSETTLED_HOST_TURN_RECOVERY_RESULT_SCHEMA,
    status: "recovery_required",
    prior_turn_instance_id: selected.prior_turn_instance_id,
    recovery,
    obligation: recoveryObligation(repair, selected.binding_id),
  };
}
