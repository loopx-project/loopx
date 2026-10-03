import {
  commitStepPayload,
  requireMatchingEffectId,
  seedCommittedSteps,
  settlementFailed,
  settlementIdentityFromPlan,
  settlementNextAction,
  settlementPure,
  settlementResultPayload,
  type JsonObject,
  type SettlementFailureResult,
  type BoundSettlementIdentity,
  type SettlementResult,
  type SettlementStepKind,
} from "../effect_program.ts";
import {
  optionalNonEmptyString,
  requireBoolean,
  requireJsonObject,
  requireStringArray,
  requireStringLiteral,
} from "../runtime_decode.ts";

import {
  decodeProviderAttemptResult,
  decodeProviderRecord,
  decodePreparedAttempt,
  decodeProviderObservation,
  settlementProviderAction,
  providerPayloadMatchesRef,
  type PreparedEffectAttempt,
  type ProviderObservation,
  type ProviderAttemptResult,
  type ProviderEffect,
  type ProviderEffectContext,
  type ProviderStepKind,
} from "./settlement_provider.ts";

export const TURN_SETTLEMENT_TRANSACTION_SCHEMA_VERSION =
  "loopx_turn_settlement_transaction_v1";
export const TURN_SETTLEMENT_REDUCTION_SCHEMA_VERSION =
  "loopx_turn_settlement_reduction_v1";
export const TURN_SETTLEMENT_OUTCOME_SCHEMA_VERSION =
  "loopx_turn_settlement_outcome_v0";

const BASE_SETTLEMENT_STEPS = [
  "validation",
  "durable_writeback",
  "quota_spend",
] as const satisfies readonly SettlementStepKind[];

/** Compatibility exports; definitions are generated from the shared contract. */
import { TURN_RESULT_KINDS, type TurnResultKind } from "./turn_contract_generated.ts";
export { TURN_RESULT_KINDS, type TurnResultKind } from "./turn_contract_generated.ts";

const FAILED_TURN_RESULT_KINDS = [
  "host_failure",
  "validation_failed",
  "writeback_failed",
  "quota_spend_failed",
  "terminal_closeout_failed",
] as const satisfies readonly TurnResultKind[];

interface TurnSettlementRequest {
  schema_version: typeof TURN_SETTLEMENT_TRANSACTION_SCHEMA_VERSION;
  transaction_plan: JsonObject;
  transaction_phases: readonly string[];
  completed_phases: readonly string[];
  committed_effect_id: string | null;
  writeback_payload: JsonObject | null;
  quota_spend_payload: JsonObject | null;
  terminal_closeout_required: boolean;
  terminal_closeout_payload: JsonObject | null;
  failed_provider_attempt: ProviderAttemptResult | null;
  returned_provider_attempt: ProviderAttemptResult | null;
  effect_attempts: Partial<Record<ProviderStepKind, PreparedEffectAttempt>>;
  provider_observations: Partial<Record<ProviderStepKind, ProviderObservation>>;
  turn_result_kind: TurnResultKind | null;
}

export interface TurnSettlementState {
  completed_phases: readonly string[];
  writeback: JsonObject | null;
  quota_spend: JsonObject | null;
}

interface TurnSettlementExecution {
  schema_version: typeof TURN_SETTLEMENT_REDUCTION_SCHEMA_VERSION;
  decision: "execute";
  provider_effects: readonly ProviderEffect[];
  result: null;
  settlement_result: null;
}

interface TurnSettlementOutcome {
  schema_version: typeof TURN_SETTLEMENT_REDUCTION_SCHEMA_VERSION;
  decision: "complete" | "failed";
  provider_effects: readonly [];
  result: SettlementResult<TurnSettlementState>;
  settlement_result: JsonObject;
}

export type TurnSettlementReduction =
  | TurnSettlementExecution
  | TurnSettlementOutcome;

function optionalObject(value: unknown, label: string): JsonObject | null {
  if (value === null || value === undefined) return null;
  return requireJsonObject(value, label);
}

function decodeRequest(value: unknown): TurnSettlementRequest {
  const request = requireJsonObject(value, "Turn settlement request");
  const schemaVersion = requireStringLiteral(
    request.schema_version,
    [TURN_SETTLEMENT_TRANSACTION_SCHEMA_VERSION] as const,
    "schema_version",
  );
  return {
    schema_version: schemaVersion,
    transaction_plan: requireJsonObject(
      request.transaction_plan,
      "transaction_plan",
    ),
    transaction_phases: requireStringArray(
      request.transaction_phases,
      "transaction_phases",
    ),
    completed_phases: requireStringArray(
      request.completed_phases,
      "completed_phases",
    ),
    committed_effect_id: optionalNonEmptyString(
      request.committed_effect_id,
      "committed_effect_id",
    ),
    writeback_payload: optionalObject(
      request.writeback_payload,
      "writeback_payload",
    ),
    quota_spend_payload: optionalObject(
      request.quota_spend_payload,
      "quota_spend_payload",
    ),
    terminal_closeout_required: requireBoolean(
      request.terminal_closeout_required,
      "terminal_closeout_required",
    ),
    terminal_closeout_payload: optionalObject(
      request.terminal_closeout_payload,
      "terminal_closeout_payload",
    ),
    failed_provider_attempt: decodeProviderAttemptResult(
      request.failed_provider_attempt, "failed_provider_attempt",
    ),
    returned_provider_attempt: decodeProviderAttemptResult(
      request.returned_provider_attempt, "returned_provider_attempt",
    ),
    effect_attempts: decodeProviderRecord(
      request.effect_attempts,
      "effect_attempts",
      decodePreparedAttempt,
    ),
    provider_observations: decodeProviderRecord(
      request.provider_observations,
      "provider_observations",
      decodeProviderObservation,
    ),
    turn_result_kind: request.turn_result_kind === null ||
      request.turn_result_kind === undefined
      ? null
      : requireStringLiteral(
      request.turn_result_kind,
      TURN_RESULT_KINDS,
      "turn_result_kind",
    ),
  };
}

function validateTurnOutcomeKind(
  request: TurnSettlementRequest,
): void {
  const kind = request.turn_result_kind;
  if (kind === null) return;
  if (kind === "iteration_failed") {
    throw new Error(
      "Turn settlement cannot run for iteration stop result_kind iteration_failed",
    );
  }
  if (FAILED_TURN_RESULT_KINDS.includes(kind as (typeof FAILED_TURN_RESULT_KINDS)[number])) {
    throw new Error(
      `Turn settlement cannot complete with failed result_kind ${kind}`,
    );
  }
}

function completionOutcomeError(
  identity: BoundSettlementIdentity,
  payload: JsonObject,
): string | null {
  const completion = payload.completion;
  if (completion === null || completion === undefined) {
    return "completion is missing its durable Todo outcome";
  }
  if (typeof completion !== "object" || Array.isArray(completion)) {
    return "completion has an invalid durable Todo outcome";
  }
  const outcome = completion as JsonObject;
  if (outcome.todo_id !== identity.todo_id) {
    return "completion does not match the selected Todo";
  }
  if (
    outcome.continuation !== "successor" &&
    outcome.continuation !== "active_goal" &&
    outcome.continuation !== "no_followup"
  ) {
    return "completion has an invalid continuation outcome";
  }
  if (
    outcome.continuation === "successor" &&
    (!Array.isArray(outcome.successor_todo_ids) ||
      outcome.successor_todo_ids.length === 0 ||
      !outcome.successor_todo_ids.every(
        (todoId) => typeof todoId === "string" && todoId.length > 0,
      ))
  ) {
    return "successor completion requires successor Todo ids";
  }
  return null;
}

function terminalCompletionError(
  identity: BoundSettlementIdentity,
  payload: JsonObject,
): string | null {
  const error = completionOutcomeError(identity, payload);
  if (error !== null) return error;
  const completion = payload.completion as JsonObject;
  if (completion.continuation !== "no_followup") {
    return "terminal closeout completion must declare no_followup";
  }
  return null;
}

function nonTerminalCompletionError(
  identity: BoundSettlementIdentity,
  payload: JsonObject,
): string | null {
  const error = completionOutcomeError(identity, payload);
  if (error !== null) return error;
  const completion = payload.completion as JsonObject;
  if (completion.continuation === "no_followup") {
    return "non-terminal completion cannot declare no_followup";
  }
  return null;
}

function reductionWithTurnOutcome(
  request: TurnSettlementRequest,
  state: TurnSettlementState,
  receipts: SettlementResult<unknown>["receipts"],
  terminalPayload: JsonObject | null,
  identity: BoundSettlementIdentity,
): TurnSettlementOutcome {
  validateTurnOutcomeKind(request);
  const result = settlementPure(state, receipts);
  const projection = settlementResultPayload(result);
  if (request.turn_result_kind !== null) {
    const outcome: JsonObject = {
      schema_version: TURN_SETTLEMENT_OUTCOME_SCHEMA_VERSION,
      result_kind: request.turn_result_kind,
      completed_phases: [...state.completed_phases],
      failed_phase: null,
    };
    if (request.turn_result_kind === "validated_completion") {
      const completion = terminalPayload?.completion ?? state.writeback?.completion;
      if (completion !== undefined) outcome.completion = completion;
    }
    projection.turn_outcome = outcome;
  }
  return {
    schema_version: TURN_SETTLEMENT_REDUCTION_SCHEMA_VERSION,
    decision: "complete",
    provider_effects: [],
    result,
    settlement_result: projection,
  };
}

function resultKindForFailedStep(stepKind: SettlementStepKind): TurnResultKind {
  switch (stepKind) {
    case "validation":
      return "validation_failed";
    case "durable_writeback":
      return "writeback_failed";
    case "quota_spend":
      return "quota_spend_failed";
    case "terminal_closeout":
      return "terminal_closeout_failed";
    default:
      throw new Error(`Turn settlement cannot project failed step ${stepKind}`);
  }
}

function reductionWithTurnFailure(
  request: TurnSettlementRequest,
  result: SettlementResult<TurnSettlementState>,
): TurnSettlementOutcome {
  if (result.failure === null || request.turn_result_kind === null) {
    return reduction(result);
  }
  const projection = settlementResultPayload(result);
  projection.turn_outcome = {
    schema_version: TURN_SETTLEMENT_OUTCOME_SCHEMA_VERSION,
    result_kind: resultKindForFailedStep(result.failure.step_kind),
    completed_phases: [...request.completed_phases],
    failed_phase: result.failure.step_kind,
  };
  return {
    schema_version: TURN_SETTLEMENT_REDUCTION_SCHEMA_VERSION,
    decision: "failed",
    provider_effects: [],
    result,
    settlement_result: projection,
  };
}

function withTurnOutcome(
  request: TurnSettlementRequest,
  reduced: TurnSettlementReduction,
): TurnSettlementReduction {
  if (reduced.decision !== "failed" || reduced.result.failure === null) {
    return reduced;
  }
  return reductionWithTurnFailure(request, reduced.result);
}

function reduction(
  result: SettlementResult<TurnSettlementState>,
): TurnSettlementOutcome {
  return {
    schema_version: TURN_SETTLEMENT_REDUCTION_SCHEMA_VERSION,
    decision: result.failure === null ? "complete" : "failed",
    provider_effects: [],
    result,
    settlement_result: settlementResultPayload(result),
  };
}

function execution(
  providerEffects: readonly ProviderEffect[],
): TurnSettlementExecution {
  return {
    schema_version: TURN_SETTLEMENT_REDUCTION_SCHEMA_VERSION,
    decision: "execute",
    provider_effects: providerEffects,
    result: null,
    settlement_result: null,
  };
}

function failedState(
  failure: SettlementFailureResult,
): TurnSettlementOutcome {
  return reduction(failure);
}

function providerFailure(
  identity: BoundSettlementIdentity,
  request: TurnSettlementRequest,
  stepKind: ProviderStepKind,
  receipts: SettlementResult<unknown>["receipts"],
): TurnSettlementOutcome {
  const attempt = request.failed_provider_attempt;
  if (!attempt || attempt.step_kind !== stepKind) {
    return reduction(
      settlementFailed({
        kind: "receipt_missing",
        step_kind: stepKind,
        reason: `Turn settlement is missing its ${stepKind} provider outcome`,
        receipts,
      }),
    );
  }
  const committed = commitStepPayload({
    identity,
    step_kind: stepKind,
    transaction_phases:
      stepKind === "terminal_closeout"
        ? ["terminal_closeout"]
        : request.transaction_phases,
    payload: attempt.payload,
  });
  if (committed.result.failure === null) {
    throw new Error(
      `failed_provider_attempt for ${stepKind} unexpectedly committed`,
    );
  }
  return reduction({
    value: null,
    receipts: [...receipts, ...committed.result.receipts],
    failure: committed.result.failure,
  });
}

function baseProviderContext(
  request: TurnSettlementRequest,
  identity: BoundSettlementIdentity,
  firstStep: ProviderStepKind,
): ProviderEffectContext {
  const phaseIndex = request.transaction_phases.indexOf(firstStep);
  if (phaseIndex < 0) throw new Error(`transaction phases do not contain ${firstStep}`);
  return {
    step_kind: firstStep,
    effect_ref: `${identity.effect_id}#${firstStep}`,
    completed_phases: request.transaction_phases.slice(0, phaseIndex + 1),
  };
}

function terminalCloseoutRequestFailure(
  request: TurnSettlementRequest,
): SettlementResult<TurnSettlementState> | null {
  if (
    request.terminal_closeout_required &&
    request.turn_result_kind !== null &&
    request.turn_result_kind !== "validated_completion"
  ) {
    return settlementFailed<TurnSettlementState>({
      kind: "terminal_closeout_rejected",
      step_kind: "terminal_closeout",
      reason: "terminal closeout requires a validated completion result",
      receipts: [],
    });
  }
  return null;
}

function reduceBaseProviderAction(
  request: TurnSettlementRequest,
  identity: BoundSettlementIdentity,
  stepKind: ProviderStepKind,
  receipts: SettlementResult<unknown>["receipts"],
): TurnSettlementReduction {
  const effect = baseProviderContext(request, identity, stepKind);
  return request.failed_provider_attempt === null
    ? providerExecution(request, identity, effect, receipts)
    : providerFailure(identity, request, stepKind, receipts);
}

type TerminalCloseoutReduction =
  | { outcome: TurnSettlementReduction; receipts?: never }
  | { outcome?: never; receipts: SettlementResult<unknown>["receipts"] };

function reduceTerminalCloseout(
  request: TurnSettlementRequest,
  identity: BoundSettlementIdentity,
  receipts: SettlementResult<unknown>["receipts"],
): TerminalCloseoutReduction {
  if (!request.terminal_closeout_required) {
    if (request.terminal_closeout_payload !== null) {
      return {
        outcome: reduction(
          settlementFailed({
            kind: "terminal_closeout_rejected",
            step_kind: "terminal_closeout",
            reason: "Turn settlement contains an unrequested terminal closeout",
            receipts,
          }),
        ),
      };
    }
    return { receipts };
  }

  if (request.terminal_closeout_payload === null) {
    const effect: ProviderEffectContext = {
      step_kind: "terminal_closeout",
      effect_ref: `${identity.effect_id}#terminal_closeout`,
      completed_phases: [...request.completed_phases],
    };
    return {
      outcome: request.failed_provider_attempt === null
        ? providerExecution(
            request,
            identity,
            effect,
            receipts,
          )
        : providerFailure(identity, request, "terminal_closeout", receipts),
    };
  }

  const terminal = seedCommittedSteps({
    identity,
    ordered_steps: ["terminal_closeout"],
    committed_payloads: {
      terminal_closeout: request.terminal_closeout_payload,
    },
    completed_phases: ["terminal_closeout"],
    transaction_phases: ["terminal_closeout"],
    require_validation: false,
    source_ref_prefix: "turn_journal",
  });
  if (terminal.failure !== null) return { outcome: failedState(terminal) };

  const completionError = request.turn_result_kind === null
    ? null
    : terminalCompletionError(identity, request.terminal_closeout_payload);
  if (completionError !== null) {
    return {
      outcome: reduction(
        settlementFailed({
          kind: "receipt_missing",
          step_kind: "terminal_closeout",
          reason: completionError,
          receipts,
        }),
      ),
    };
  }
  return { receipts: [...receipts, ...terminal.receipts] };
}

function providerExecution(
  request: TurnSettlementRequest,
  identity: BoundSettlementIdentity,
  effect: ProviderEffectContext,
  receipts: SettlementResult<unknown>["receipts"],
): TurnSettlementReduction {
  const firstStep = effect.step_kind;
  const next = settlementProviderAction(
    effect,
    request.effect_attempts,
    request.provider_observations,
    request.returned_provider_attempt,
  );
  if (next.failure !== null) {
    return reduction(settlementFailed({ ...next.failure, receipts }));
  }
  // Admission precedes checkpoint: an invalid committed payload must leave the
  // prepared operation recoverable, never fabricate a committed journal prefix.
  if (next.effect.action === "checkpoint") {
    const validateCompletion = firstStep === "terminal_closeout" &&
        request.turn_result_kind !== null
      ? terminalCompletionError
      : firstStep === "durable_writeback" &&
          request.turn_result_kind === "validated_completion" &&
          !request.terminal_closeout_required
      ? nonTerminalCompletionError
      : null;
    if (validateCompletion !== null) {
      const error = validateCompletion(identity, next.effect.payload);
      if (error !== null) {
        return reduction(settlementFailed({
          kind: "receipt_missing", step_kind: firstStep, reason: error, receipts,
        }));
      }
      // Preserve the lifecycle adapter's compact public completion projection.
      const completion = next.effect.payload.completion as JsonObject;
      return execution([{
        ...next.effect,
        payload: {
          ...next.effect.payload,
          completion: {
            todo_id: completion.todo_id,
            continuation: completion.continuation,
            ...(completion.continuation === "successor"
              ? { successor_todo_ids: [...completion.successor_todo_ids as string[]] }
              : {}),
          },
        },
      }]);
    }
  }
  return execution([next.effect]);
}

/**
 * Reduce one complete Turn settlement snapshot.
 *
 * The first reduction validates identity, replay, and the committed journal
 * prefix before authorizing still-Python provider effects. Each provider return
 * or readback is admitted before a checkpoint is
 * authorized; only its persisted acknowledgement advances to the next provider.
 * A replay that needs no provider effect completes in one reduction.
 */
function reduceTurnSettlementRequest(
  request: TurnSettlementRequest,
): TurnSettlementReduction {
  const identityResult = settlementIdentityFromPlan(request.transaction_plan);
  if (identityResult.failure !== null) return failedState(identityResult);

  const identity = identityResult.value;
  const matching = requireMatchingEffectId(
    request.committed_effect_id,
    identity.effect_id,
  );
  if (matching.failure !== null) return failedState(matching);

  for (const [step, payload] of [
    ["durable_writeback", request.writeback_payload],
    ["quota_spend", request.quota_spend_payload],
    ["terminal_closeout", request.terminal_closeout_payload],
  ] as const) {
    if (!providerPayloadMatchesRef(payload, `${identity.effect_id}#${step}`)) {
      return reduction(settlementFailed({
        kind: "identity_mismatch", step_kind: step,
        reason: "Journal provider payload does not match the current operation", receipts: [],
      }));
    }
  }
  if (request.failed_provider_attempt !== null && (
    Object.keys(request.effect_attempts).length || Object.keys(request.provider_observations).length ||
    request.returned_provider_attempt !== null
  )) {
    return reduction(settlementFailed({
      kind: "receipt_missing", step_kind: request.failed_provider_attempt.step_kind,
      reason: "Rejected provider attempt has not been durably aborted", receipts: [],
    }));
  }

  const terminalRequestFailure = terminalCloseoutRequestFailure(request);
  if (terminalRequestFailure !== null) return reduction(terminalRequestFailure);

  const base = settlementNextAction({
    identity,
    ordered_steps: BASE_SETTLEMENT_STEPS,
    committed_payloads: {
      validation: {},
      durable_writeback: request.writeback_payload,
      quota_spend: request.quota_spend_payload,
    },
    completed_phases: request.completed_phases,
    transaction_phases: request.transaction_phases,
    require_validation: true,
    source_ref_prefix: "turn_journal",
  });
  if (base.decision === "failed") return failedState(base.result);
  if (base.decision === "execute") {
    if (base.step_kind === "validation" || base.step_kind === "terminal_closeout") {
      throw new Error(`unsupported base settlement step ${base.step_kind}`);
    }
    if (
      base.step_kind === "quota_spend" &&
      request.turn_result_kind === "validated_completion" &&
      !request.terminal_closeout_required &&
      request.writeback_payload !== null
    ) {
      const completionError = nonTerminalCompletionError(
        identity,
        request.writeback_payload,
      );
      if (completionError !== null) {
        return reduction(
          settlementFailed({
            kind: "receipt_missing",
            step_kind: "durable_writeback",
            reason: completionError,
            receipts: base.result.receipts,
          }),
        );
      }
    }
    return reduceBaseProviderAction(
      request,
      identity,
      base.step_kind,
      base.result.receipts,
    );
  }

  let receipts: SettlementResult<unknown>["receipts"] = [...base.result.receipts];
  if (
    request.turn_result_kind === "validated_completion" &&
    !request.terminal_closeout_required &&
    request.writeback_payload !== null
  ) {
    const completionError = nonTerminalCompletionError(
      identity,
      request.writeback_payload,
    );
    if (completionError !== null) {
      return reduction(
        settlementFailed({
          kind: "receipt_missing",
          step_kind: "durable_writeback",
          reason: completionError,
          receipts,
        }),
      );
    }
  }
  const terminal = reduceTerminalCloseout(request, identity, receipts);
  if (terminal.outcome !== undefined) return terminal.outcome;
  receipts = terminal.receipts;

  const danglingAttempt = Object.keys(request.effect_attempts)[0] as
    | ProviderStepKind
    | undefined;
  if (danglingAttempt) {
    return reduction(
      settlementFailed({
        kind: "receipt_missing",
        step_kind: danglingAttempt,
        reason: "Turn settlement has a prepared effect after its provider phase",
        receipts,
      }),
    );
  }

  if (Object.keys(request.provider_observations).length || request.returned_provider_attempt !== null) {
    return reduction(settlementFailed({
      kind: "receipt_missing", step_kind: "quota_spend",
      reason: "Turn settlement has provider evidence after its provider phase", receipts,
    }));
  }

  if (request.failed_provider_attempt !== null) {
    throw new Error(
      "failed_provider_attempt remains after all required settlement effects committed",
    );
  }
  return reductionWithTurnOutcome(
    request,
    {
      completed_phases: [...request.completed_phases],
      writeback: request.writeback_payload,
      quota_spend: request.quota_spend_payload,
    },
    receipts,
    request.terminal_closeout_payload,
    identity,
  );
}

export function reduceTurnSettlementTransaction(
  value: unknown,
): TurnSettlementReduction {
  const request = decodeRequest(value);
  return withTurnOutcome(request, reduceTurnSettlementRequest(request));
}
