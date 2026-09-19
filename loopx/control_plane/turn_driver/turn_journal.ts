import transactionContract from "../turn_transaction_contract.json" with {
  type: "json",
};

import {
  SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION,
  SETTLEMENT_IDENTITY_SCHEMA_VERSION,
  SETTLEMENT_PLAN_SCHEMA_VERSION,
  settlementIdentityFromPlan,
  type EffectObservation,
  type EffectTurn,
} from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";

export const TURN_JOURNAL_INSPECTION_SCHEMA_VERSION =
  "loopx_turn_journal_inspection_v1";

type JsonObject = Record<string, unknown>;

export interface TurnJournalInspectionRequest {
  schema_version: "loopx_turn_journal_interpretation_request_v0";
  journal: JsonObject;
  goal_id: string;
  agent_id: string;
  turn_key: string;
  retry_failed?: boolean;
  session_recovery_check?: TurnRecoveryCheck | null;
}

export interface TurnRecoveryCheck {
  kind:
    | "journal_consistency"
    | "host_retry_policy"
    | "host_session_binding"
    | "prepared_effect_readback";
  outcome: "passed" | "failed" | "required";
  reason?: string;
  step_kind?: string;
}

export interface TurnRecoveryDecision {
  schema_version: "loopx_turn_recovery_decision_v0";
  action: "continue" | "return_existing" | "blocked";
  can_continue: boolean;
  resume_from: string | null;
  reinvoke_host: boolean;
  reason: string;
  retry_failed: boolean;
  checks: TurnRecoveryCheck[];
}

export interface TurnRecoveryAudit {
  schema_version: "loopx_turn_recovery_audit_v0";
  planned: TurnRecoveryDecision;
  actual: {
    status: "started" | "finished";
    journal_status: string;
    completed_phases: string[];
    host_invoked: boolean | null;
  };
}

export interface TurnJournalInspection {
  ok: true;
  schema_version: typeof TURN_JOURNAL_INSPECTION_SCHEMA_VERSION;
  decision: "replay_legal" | "replay_blocked";
  journal_status: string;
  replay_legal: boolean;
  goal_matches: boolean;
  owner_matches: boolean;
  turn_key_matches: boolean;
  phases_form_ordered_prefix: boolean;
  completed_phases: string[];
  tombstone_retained: boolean;
  violations: string[];
  journal_consistent: boolean;
  recovery_decision: TurnRecoveryDecision;
  last_recovery: TurnRecoveryAudit | null;
  effects: [];
}

export interface TurnJournalEffectContext {
  replay_legal: boolean;
  goal_matches: boolean;
  owner_matches: boolean;
  turn_key_matches: boolean;
  phases_form_ordered_prefix: boolean;
  journal_status: string;
  tombstone_retained: boolean;
  completed_phases: string[];
  violations: string[];
  journal_consistent: boolean;
  recovery_decision: TurnRecoveryDecision;
  last_recovery: TurnRecoveryAudit | null;
}

// Replay has its own verdict. It is not a quota decision and must not manufacture
// a second action vocabulary in the should-run effective_action slot.
export type TurnJournalEffect = Omit<EffectTurn<
  TurnJournalEffectContext,
  "replay_legal" | "replay_blocked"
>, "observation"> & {
  observation: Omit<EffectObservation<"replay_legal" | "replay_blocked">, "effective_action">;
};

export const transactionPhases = Object.freeze([...transactionContract.phases]);
export const supportedJournalStatuses: ReadonlySet<string> = new Set([
  "in_progress",
  "scheduler_action_required",
  "committed",
  "stopped",
  "failed",
]);
const hostFailureKinds: ReadonlySet<string> = new Set([
  "auth_failed",
  "contract_rejected",
  "executor_timeout",
  "output_budget_exhausted",
  "provider_capacity",
  "provider_overloaded",
  "quota_exhausted",
  "rate_limited",
  "session_missing",
  "transport_lost",
  "unknown",
]);
const hostRetryPolicies: Readonly<Record<string, {
  maxAttempts: number;
  baseBackoffSeconds: number;
}>> = Object.freeze({
  executor_timeout: { maxAttempts: 2, baseBackoffSeconds: 5 },
  provider_capacity: { maxAttempts: 3, baseBackoffSeconds: 30 },
  provider_overloaded: { maxAttempts: 3, baseBackoffSeconds: 30 },
  rate_limited: { maxAttempts: 3, baseBackoffSeconds: 60 },
  transport_lost: { maxAttempts: 3, baseBackoffSeconds: 10 },
});

function asObject(value: unknown): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
}

function isValidIdentity(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function identityState(
  requiredValues: unknown[],
  optionalValues: Array<[boolean, unknown]>,
  expected: unknown,
): [complete: boolean, matches: boolean] {
  const complete =
    requiredValues.every(isValidIdentity) &&
    optionalValues.every(([present, value]) => !present || isValidIdentity(value)) &&
    isValidIdentity(expected);
  const observed = requiredValues.filter(isValidIdentity);
  for (const [present, value] of optionalValues) {
    if (present && isValidIdentity(value)) observed.push(value);
  }
  if (isValidIdentity(expected)) observed.push(expected);
  return [complete, complete && new Set(observed).size === 1];
}

function selectedTurnTodoId(envelope: JsonObject): string | null {
  const orchestration = asObject(envelope.task_orchestration_contract);
  const primaryTodoId = typeof orchestration.primary_todo_id === "string"
    ? orchestration.primary_todo_id.trim()
    : "";
  if (
    orchestration.schema_version === "task_orchestration_contract_v2" &&
    orchestration.mode === "adaptive" &&
    primaryTodoId
  ) {
    return primaryTodoId;
  }
  const action = asObject(envelope.action);
  const selectedTodo = asObject(action.selected_todo);
  return isValidIdentity(selectedTodo.todo_id) ? selectedTodo.todo_id : null;
}

function typedSettlementIdentityState(
  envelope: JsonObject,
  transaction: JsonObject,
  settlement: JsonObject,
  identity: JsonObject,
): [valid: boolean, turnInstanceMatches: boolean, bindingMatches: boolean] {
  if (
    settlement.schema_version !== SETTLEMENT_PLAN_SCHEMA_VERSION ||
    ![
      SETTLEMENT_IDENTITY_SCHEMA_VERSION,
      SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION,
    ].includes(String(identity.schema_version))
  ) {
    return [false, false, false];
  }
  const parsed = settlementIdentityFromPlan(transaction);
  if (parsed.failure !== null || parsed.value === null) {
    return [false, false, false];
  }
  if (
    identity.schema_version === SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION &&
    (
      identity.binding_kind !== parsed.value.binding_kind ||
      identity.binding_id !== parsed.value.binding_id
    )
  ) {
    return [false, false, false];
  }
  const expectedTurnInstance = isValidIdentity(transaction.turn_instance_id)
    ? transaction.turn_instance_id
    : transaction.turn_key;
  const selectedTodoId = selectedTurnTodoId(envelope);
  return [
    true,
    isValidIdentity(expectedTurnInstance) &&
      parsed.value.turn_instance_id === expectedTurnInstance,
    selectedTodoId !== null &&
      parsed.value.binding_kind === "todo" &&
      parsed.value.todo_id === selectedTodoId &&
      parsed.value.binding_id === selectedTodoId,
  ];
}

function stringifyPhase(value: unknown): string {
  if (value === null) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  return String(value);
}

function recoveryResumePhase(
  journal: JsonObject,
  completedPhases: string[],
  journalStatus: string,
): string | null {
  const receipt = asObject(journal.receipt);
  const failedPhase = typeof receipt.failed_phase === "string" ? receipt.failed_phase : "";
  const validationStage = typeof journal.validation_stage === "string"
    ? journal.validation_stage
    : "";
  if (
    journalStatus === "failed" &&
    failedPhase === "validation" &&
    validationStage !== "task_postcondition"
  ) {
    return "host_execute";
  }
  if (journalStatus === "failed" && failedPhase === "terminal_closeout") {
    return "terminal_closeout";
  }
  if (journalStatus === "failed" && transactionPhases.includes(failedPhase)) {
    return failedPhase;
  }
  if (journalStatus === "scheduler_action_required") return "scheduler_apply";
  return transactionPhases[completedPhases.length] ?? null;
}

function preparedEffectCheck(
  journal: JsonObject,
  resumeFrom: string | null,
): TurnRecoveryCheck | null {
  if (!resumeFrom) return null;
  const attempts = asObject(journal.effect_attempts);
  const attempt = asObject(attempts[resumeFrom]);
  if (
    attempt.status !== "prepared" ||
    typeof attempt.effect_ref !== "string" ||
    attempt.effect_ref.length === 0
  ) {
    return null;
  }
  return {
    kind: "prepared_effect_readback",
    outcome: "required",
    step_kind: resumeFrom,
  };
}

function hasExactKeys(value: JsonObject, expected: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === expected.length &&
    expected.every((key) => Object.hasOwn(value, key));
}

function hostRetryPolicyCheck(journal: JsonObject): TurnRecoveryCheck | null {
  if (!Object.hasOwn(journal, "host_failure")) return null;
  const failure = asObject(journal.host_failure);
  const retry = asObject(failure.retry);
  const attempt = failure.attempt;
  const hostAttemptCount = journal.host_attempt_count;
  const retryable = failure.retryable;
  const kind = typeof failure.kind === "string" ? failure.kind : "";
  const policy = hostRetryPolicies[kind];
  const expectedRetryable = policy !== undefined;
  const expectedBackoff = policy && Number.isInteger(attempt)
    ? Math.min(policy.baseBackoffSeconds * (2 ** Math.max(0, Number(attempt) - 1)), 300)
    : null;
  const failureFieldsValid = hasExactKeys(
    failure,
    expectedRetryable
      ? ["schema_version", "kind", "attempt", "retryable", "retry"]
      : ["schema_version", "kind", "attempt", "retryable"],
  );
  const contractValid =
    failureFieldsValid &&
    failure.schema_version === "loopx_turn_host_failure_v0" &&
    hostFailureKinds.has(kind) &&
    Number.isInteger(attempt) &&
    Number(attempt) >= 1 &&
    hostAttemptCount === attempt &&
    typeof retryable === "boolean" &&
    retryable === expectedRetryable &&
    (
      retryable === false
        ? !Object.hasOwn(failure, "retry")
        : hasExactKeys(
          retry,
          ["strategy", "max_attempts", "backoff_seconds"],
        ) &&
          retry.strategy === "same_configuration" &&
          retry.max_attempts === policy?.maxAttempts &&
          retry.backoff_seconds === expectedBackoff
    );
  if (!contractValid) {
    return {
      kind: "host_retry_policy",
      outcome: "failed",
      reason: "host_retry_contract_invalid",
    };
  }
  if (retryable === true && Number(attempt) >= Number(retry.max_attempts)) {
    return {
      kind: "host_retry_policy",
      outcome: "failed",
      reason: "host_retry_budget_exhausted",
    };
  }
  if (retryable === true) {
    return { kind: "host_retry_policy", outcome: "passed" };
  }
  // A max-token terminal has no proved whole-Turn remaining budget or
  // final-response reserve. Unlike legacy explicitly retried terminal errors,
  // repeating it would be a blind rerun of an expensive request.
  return kind === "output_budget_exhausted"
    ? {
        kind: "host_retry_policy",
        outcome: "failed",
        reason: "host_retry_not_available",
      }
    : null;
}

function recoveryDecision(
  request: TurnJournalInspectionRequest,
  journal: JsonObject,
  completedPhases: string[],
  journalStatus: string,
  journalConsistent: boolean,
): TurnRecoveryDecision {
  const retryFailed = request.retry_failed === true;
  const checks: TurnRecoveryCheck[] = [{
    kind: "journal_consistency",
    outcome: journalConsistent ? "passed" : "failed",
  }];
  const base = {
    schema_version: "loopx_turn_recovery_decision_v0" as const,
    retry_failed: retryFailed,
    checks,
  };
  if (!journalConsistent) {
    return {
      ...base,
      action: "blocked",
      can_continue: false,
      resume_from: null,
      reinvoke_host: false,
      reason: "journal_inconsistent",
    };
  }
  if (["committed", "stopped"].includes(journalStatus)) {
    return {
      ...base,
      action: "return_existing",
      can_continue: false,
      resume_from: null,
      reinvoke_host: false,
      reason: "terminal_result_retained",
    };
  }

  const resumeFrom = recoveryResumePhase(journal, completedPhases, journalStatus);
  if (journalStatus === "failed" && !retryFailed) {
    return {
      ...base,
      action: "return_existing",
      can_continue: false,
      resume_from: resumeFrom,
      reinvoke_host: false,
      reason: "failed_retry_not_requested",
    };
  }

  if (journalStatus === "failed") {
    const retryPolicyCheck = hostRetryPolicyCheck(journal);
    if (retryPolicyCheck) {
      checks.push(retryPolicyCheck);
      if (retryPolicyCheck.outcome !== "passed") {
        return {
          ...base,
          action: "blocked",
          can_continue: false,
          resume_from: resumeFrom,
          reinvoke_host: false,
          reason: retryPolicyCheck.reason ?? "host_retry_policy_rejected",
        };
      }
    }
  }

  if (journalStatus === "failed" && Object.hasOwn(journal, "host_recovery")) {
    const sessionCheck = request.session_recovery_check;
    const normalizedCheck: TurnRecoveryCheck = sessionCheck?.kind === "host_session_binding"
      ? {
          kind: "host_session_binding",
          outcome: sessionCheck.outcome,
          ...(sessionCheck.reason ? { reason: sessionCheck.reason } : {}),
        }
      : {
          kind: "host_session_binding",
          outcome: "failed",
          reason: "binding_check_unavailable",
        };
    checks.push(normalizedCheck);
    if (normalizedCheck.outcome !== "passed") {
      return {
        ...base,
        action: "blocked",
        can_continue: false,
        resume_from: resumeFrom,
        reinvoke_host: false,
        reason: normalizedCheck.reason ?? "host_session_binding_rejected",
      };
    }
  }
  if (!resumeFrom) {
    return {
      ...base,
      action: "blocked",
      can_continue: false,
      resume_from: null,
      reinvoke_host: false,
      reason: "recovery_phase_unavailable",
    };
  }
  const preparedCheck = preparedEffectCheck(journal, resumeFrom);
  if (preparedCheck) checks.push(preparedCheck);
  return {
    ...base,
    action: "continue",
    can_continue: true,
    resume_from: resumeFrom,
    reinvoke_host: ["host_execute", "typed_result"].includes(resumeFrom),
    reason: preparedCheck
      ? "resolve_prepared_effect"
      : journalStatus === "scheduler_action_required"
        ? "resume_scheduler"
        : journalStatus === "failed"
          ? "retry_failed_phase"
          : "resume_in_progress",
  };
}

function projectRecoveryDecision(value: unknown): TurnRecoveryDecision | null {
  const decision = asObject(value);
  const action = decision.action;
  const checks = Array.isArray(decision.checks)
    ? decision.checks.map((item) => asObject(item))
    : [];
  if (
    decision.schema_version !== "loopx_turn_recovery_decision_v0" ||
    !["continue", "return_existing", "blocked"].includes(String(action)) ||
    typeof decision.can_continue !== "boolean" ||
    !(typeof decision.resume_from === "string" || decision.resume_from === null) ||
    typeof decision.reinvoke_host !== "boolean" ||
    typeof decision.reason !== "string" ||
    typeof decision.retry_failed !== "boolean"
  ) return null;
  return {
    schema_version: "loopx_turn_recovery_decision_v0",
    action: action as TurnRecoveryDecision["action"],
    can_continue: decision.can_continue,
    resume_from: decision.resume_from as string | null,
    reinvoke_host: decision.reinvoke_host,
    reason: decision.reason,
    retry_failed: decision.retry_failed,
    checks: checks.flatMap((check) => {
      if (
        ![
          "journal_consistency",
          "host_retry_policy",
          "host_session_binding",
          "prepared_effect_readback",
        ].includes(
          String(check.kind),
        ) ||
        !["passed", "failed", "required"].includes(String(check.outcome))
      ) return [];
      return [{
        kind: check.kind as TurnRecoveryCheck["kind"],
        outcome: check.outcome as TurnRecoveryCheck["outcome"],
        ...(typeof check.reason === "string" ? { reason: check.reason } : {}),
        ...(typeof check.step_kind === "string" ? { step_kind: check.step_kind } : {}),
      }];
    }),
  };
}

function projectRecoveryAudit(value: unknown): TurnRecoveryAudit | null {
  const audit = asObject(value);
  const planned = projectRecoveryDecision(audit.planned);
  const actual = asObject(audit.actual);
  if (
    audit.schema_version !== "loopx_turn_recovery_audit_v0" ||
    planned === null ||
    !["started", "finished"].includes(String(actual.status)) ||
    typeof actual.journal_status !== "string" ||
    !Array.isArray(actual.completed_phases) ||
    !actual.completed_phases.every((phase) => typeof phase === "string") ||
    !(typeof actual.host_invoked === "boolean" || actual.host_invoked === null)
  ) return null;
  return {
    schema_version: "loopx_turn_recovery_audit_v0",
    planned,
    actual: {
      status: actual.status as "started" | "finished",
      journal_status: actual.journal_status,
      completed_phases: [...actual.completed_phases],
      host_invoked: actual.host_invoked,
    },
  };
}

export function interpretTurnJournalEffect(
  request: TurnJournalInspectionRequest,
): TurnJournalEffect {
  if (request.schema_version !== "loopx_turn_journal_interpretation_request_v0") {
    throw new EffectRuntimeRequestError("Turn-journal interpretation request schema mismatch");
  }
  const journal = asObject(request.journal);
  const plan = asObject(journal.plan);
  const envelope = asObject(plan.turn_envelope);
  const transaction = asObject(plan.transaction);
  const settlement = asObject(transaction.settlement_plan);
  const identity = asObject(settlement.identity);
  const hostResult = asObject(journal.host_result);
  const receipt = asObject(journal.receipt);

  const [goalComplete, goalMatches] = identityState(
    [journal.goal_id, envelope.goal_id, identity.goal_id],
    [],
    request.goal_id,
  );
  const [ownerComplete, ownerMatches] = identityState(
    [envelope.agent_id, identity.agent_id],
    [],
    request.agent_id,
  );
  const [turnKeyComplete, turnKeyMatches] = identityState(
    [journal.turn_key, transaction.turn_key],
    [
      [Object.hasOwn(hostResult, "turn_key"), hostResult.turn_key],
      [Object.hasOwn(receipt, "turn_key"), receipt.turn_key],
    ],
    request.turn_key,
  );
  const [
    settlementIdentityValid,
    settlementTurnInstanceMatches,
    settlementBindingMatches,
  ] = typedSettlementIdentityState(envelope, transaction, settlement, identity);

  const violations: string[] = [];
  if (!goalComplete) violations.push("goal_identity_missing");
  else if (!goalMatches) violations.push("goal_mismatch");
  if (!ownerComplete) violations.push("owner_identity_missing");
  else if (!ownerMatches) violations.push("owner_mismatch");
  if (!settlementIdentityValid) violations.push("settlement_identity_invalid");
  else if (!settlementTurnInstanceMatches) {
    violations.push("settlement_turn_instance_mismatch");
  }
  if (settlementIdentityValid && !settlementBindingMatches) {
    violations.push("settlement_binding_mismatch");
  }
  if (!turnKeyComplete) violations.push("turn_key_identity_missing");
  else if (!turnKeyMatches) violations.push("turn_key_mismatch");

  let completedPhases: string[] = [];
  let phasesFormOrderedPrefix = false;
  if (Array.isArray(journal.completed_phases)) {
    completedPhases = journal.completed_phases.map(stringifyPhase);
    phasesFormOrderedPrefix = completedPhases.every(
      (phase, index) => transactionPhases[index] === phase,
    );
    if (!phasesFormOrderedPrefix) {
      violations.push("completed_phases_not_ordered_prefix");
    }
  } else {
    violations.push("completed_phases_invalid");
  }

  const journalStatus = journal.status ? String(journal.status) : "";
  const tombstoneRetained = ["committed", "stopped", "failed"].includes(
    journalStatus,
  );
  if (["in_progress", "scheduler_action_required"].includes(journalStatus)) {
    violations.push("journal_not_terminal");
  } else if (!tombstoneRetained) {
    violations.push("journal_status_unsupported");
  }

  const replayLegal = violations.length === 0;
  const journalConsistent =
    goalMatches &&
    ownerMatches &&
    settlementIdentityValid &&
    settlementTurnInstanceMatches &&
    settlementBindingMatches &&
    turnKeyMatches &&
    phasesFormOrderedPrefix &&
    supportedJournalStatuses.has(journalStatus);
  const decision = replayLegal ? "replay_legal" : "replay_blocked";
  const turnRecoveryDecision = recoveryDecision(
    request,
    journal,
    completedPhases,
    journalStatus,
    journalConsistent,
  );
  return {
    request: {
      kind: "turn_journal",
      source: "turn_journal",
      goal_id: request.goal_id,
      agent_id: request.agent_id,
      capabilities: [],
      context: {
        replay_legal: replayLegal,
        goal_matches: goalMatches,
        owner_matches: ownerMatches,
        turn_key_matches: turnKeyMatches,
        phases_form_ordered_prefix: phasesFormOrderedPrefix,
        journal_status: journalStatus,
        tombstone_retained: tombstoneRetained,
        completed_phases: completedPhases,
        violations,
        journal_consistent: journalConsistent,
        recovery_decision: turnRecoveryDecision,
        last_recovery: projectRecoveryAudit(journal.recovery_audit),
      },
    },
    interpretation: {
      route: "turn_journal_replay",
      obligation: "observe_fenced_replay",
      interaction_mode: "read_only",
      capability_action: null,
      cadence_class: null,
    },
    observation: {
      decision,
      should_run: false,
      recommended_action: replayLegal
        ? "Retain the terminal Turn journal tombstone."
        : "Inspect the structured Turn journal violations before replay.",
      action_portfolio: null,
      planning_horizon: null,
      protocol_summary: replayLegal
        ? "Turn journal replay is legal and effect-free."
        : `Turn journal replay is blocked by ${violations.length} structured violation(s).`,
    },
    next_effect: {
      cli_actions: [],
      execution_mode: null,
      scheduler_action: null,
      cadence_class: null,
      ack_cli_args: [],
      failure_cli_args: [],
    },
  };
}

export function projectTurnJournalInspection(
  turn: TurnJournalEffect,
): TurnJournalInspection {
  const context = turn.request.context;
  return {
    ok: true,
    schema_version: TURN_JOURNAL_INSPECTION_SCHEMA_VERSION,
    decision: turn.observation.decision,
    journal_status: context.journal_status,
    replay_legal: context.replay_legal,
    goal_matches: context.goal_matches,
    owner_matches: context.owner_matches,
    turn_key_matches: context.turn_key_matches,
    phases_form_ordered_prefix: context.phases_form_ordered_prefix,
    completed_phases: context.completed_phases,
    tombstone_retained: context.tombstone_retained,
    violations: context.violations,
    journal_consistent: context.journal_consistent,
    recovery_decision: context.recovery_decision,
    last_recovery: context.last_recovery,
    effects: [],
  };
}

export function interpretTurnJournal(
  request: TurnJournalInspectionRequest,
): TurnJournalInspection {
  return projectTurnJournalInspection(interpretTurnJournalEffect(request));
}
