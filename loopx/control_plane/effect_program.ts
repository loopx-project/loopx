import { EffectRuntimeRequestError } from "./effect_runtime_errors.ts";
import { isStringLiteral } from "./runtime_decode.ts";
import { decodeInteractionContract } from "./work_items/interaction_contract.ts";

export {
  RECEIPT_BOUND_MONITOR_PHASES,
  RECEIPT_BOUND_REPLAY_PHASES,
  RECEIPT_BOUND_TERMINAL_PHASES,
  receiptBoundMonitorPhase,
  receiptBoundReplayPhase,
  receiptBoundTerminalPhase,
  type ReceiptBoundMonitorPhase,
  type ReceiptBoundMonitorSettlementState,
  type ReceiptBoundReplayPhase,
  type ReceiptBoundReplaySettlementState,
  type ReceiptBoundTerminalPhase,
  type ReceiptBoundTerminalSettlementState,
} from "./quota/settlement_phase.ts";

export const SETTLEMENT_IDENTITY_SCHEMA_VERSION =
  "quota_settlement_identity_v0";
export const SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION =
  "quota_settlement_identity_v1";
export const SETTLEMENT_PLAN_SCHEMA_VERSION = "quota_settlement_plan_v1";
export const SETTLEMENT_RECEIPT_SCHEMA_VERSION =
  "quota_settlement_receipt_v1";

export type JsonObject = Record<string, unknown>;

export interface EffectRequest<Context> {
  kind: string;
  source: string;
  goal_id: string | null;
  agent_id: string | null;
  capabilities: readonly string[];
  context: Context;
}

export interface EffectInterpretation {
  route: string;
  obligation: string;
  interaction_mode: string;
  capability_action: string | null;
  cadence_class: string | null;
}

export interface EffectObservation<
  Decision extends string,
  Action extends string | null = string,
> {
  decision: Decision;
  should_run: boolean;
  effective_action: Action;
  recommended_action: string;
  action_portfolio: JsonObject | null;
  planning_horizon: JsonObject | null;
  protocol_summary: string | null;
}

export interface EffectNext {
  cli_actions: readonly string[];
  execution_mode: string | null;
  scheduler_action: string | null;
  cadence_class: string | null;
  ack_cli_args: readonly string[];
  failure_cli_args: readonly string[];
}

export interface EffectTurn<
  Context,
  Decision extends string,
  Action extends string | null = string,
> {
  request: EffectRequest<Context>;
  interpretation: EffectInterpretation;
  observation: EffectObservation<Decision, Action>;
  next_effect: EffectNext;
}

export interface EffectStep {
  step_id: string | null;
  kind: string | null;
  command: string | null;
  purpose: string | null;
  raw: JsonObject;
}

export interface EffectProgram {
  steps: readonly EffectStep[];
  execution_mode: string | null;
}

export const SETTLEMENT_STEP_KINDS = [
  "validation",
  "durable_writeback",
  "quota_spend",
  "terminal_closeout",
] as const;
export type SettlementStepKind = (typeof SETTLEMENT_STEP_KINDS)[number];

export const SETTLEMENT_BINDING_KINDS = [
  "todo",
  "autonomous_replan",
  "unbound",
] as const;
export type SettlementBindingKind = (typeof SETTLEMENT_BINDING_KINDS)[number];

export const SETTLEMENT_FAILURE_KINDS = [
  "invalid_identity",
  "receipt_missing",
  "receipt_unbound",
  "identity_mismatch",
  "writeback_missing",
  "writeback_rejected",
  "quota_spend_rejected",
  "terminal_closeout_rejected",
  "cancelled",
  "permission_denied",
  "budget_rejected",
  "effect_outcome_unknown",
] as const;
export type SettlementFailureKind = (typeof SETTLEMENT_FAILURE_KINDS)[number];

export interface SettlementIdentityInput {
  goal_id: string;
  agent_id: string;
  todo_id?: string | null;
  turn_instance_id: string;
  replan_obligation_id?: string | null;
}

export interface SettlementIdentity extends SettlementIdentityInput {
  todo_id: string | null;
  replan_obligation_id: string | null;
  binding_kind: SettlementBindingKind;
  binding_id: string;
  effect_id: string;
}

export interface SettlementReceipt {
  step_kind: SettlementStepKind;
  status: string;
  effect_id: string;
  source_ref?: string;
}

export interface SettlementFailure {
  kind: SettlementFailureKind;
  step_kind: SettlementStepKind;
  reason: string;
  details?: JsonObject;
}

export interface SettlementSuccess<Value> {
  value: Value;
  receipts: readonly SettlementReceipt[];
  failure: null;
}

export interface SettlementFailureResult {
  value: null;
  receipts: readonly SettlementReceipt[];
  failure: SettlementFailure;
}

export type SettlementResult<Value = unknown> =
  | SettlementSuccess<Value>
  | SettlementFailureResult;

export interface SettlementStep {
  kind: SettlementStepKind;
  owner: string;
  precondition: string;
  idempotency_key_ref: string;
  expected_receipt: string;
  command_template?: string;
  conditional?: true;
}

export interface SettlementPlan {
  identity: SettlementIdentity;
  steps: readonly SettlementStep[];
}

export type SettlementBindGate<Value = unknown> =
  | { execute: true; result: SettlementSuccess<Value> }
  | { execute: false; result: SettlementFailureResult };

export interface SettlementCommitReduction {
  result: SettlementResult<JsonObject>;
  completed_phases: readonly string[] | null;
}

export type SettlementNextAction =
  | {
      decision: "failed";
      step_kind: null;
      result: SettlementFailureResult;
    }
  | {
      decision: "execute";
      step_kind: SettlementStepKind;
      result: SettlementSuccess<JsonObject>;
    }
  | {
      decision: "complete";
      step_kind: null;
      result: SettlementSuccess<JsonObject>;
    };

function asObject(value: unknown): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
}

function pythonString(value: unknown): string {
  if (value === null || value === undefined) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  return String(value);
}

function truthyString(value: unknown): string {
  return value ? pythonString(value) : "";
}

function nullableTruthyString(value: unknown): string | null {
  const rendered = truthyString(value);
  return rendered || null;
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(pythonString).filter((item) => item.trim().length > 0);
}

function requireStepKind(value: unknown): SettlementStepKind {
  const rendered = pythonString(value);
  if (!isStringLiteral(rendered, SETTLEMENT_STEP_KINDS)) {
    throw new EffectRuntimeRequestError("unsupported settlement step kind");
  }
  return rendered;
}

function requireFailureKind(value: unknown): SettlementFailureKind {
  const rendered = pythonString(value);
  if (!isStringLiteral(rendered, SETTLEMENT_FAILURE_KINDS)) {
    throw new EffectRuntimeRequestError("unsupported settlement failure kind");
  }
  return rendered;
}

export function effectProgramFromOrderedSteps(
  orderedSteps: unknown,
  executionMode: unknown = null,
): EffectProgram {
  const steps: EffectStep[] = [];
  if (Array.isArray(orderedSteps)) {
    for (const candidate of orderedSteps) {
      if (
        typeof candidate !== "object" ||
        candidate === null ||
        Array.isArray(candidate)
      ) {
        continue;
      }
      const step = candidate as JsonObject;
      const commandValue =
        step.command || step.command_template || step.prompt || "";
      steps.push({
        step_id: nullableTruthyString(step.id),
        kind: nullableTruthyString(step.kind),
        command: nullableTruthyString(commandValue),
        purpose: nullableTruthyString(step.purpose),
        raw: { ...step },
      });
    }
  }
  return {
    steps,
    execution_mode: nullableTruthyString(executionMode),
  };
}

export function interpretQuotaShouldRunPacket(
  packetValue: unknown,
  options: {
    goal_id?: string | null;
    agent_id?: string | null;
    capabilities?: readonly string[];
  } = {},
): EffectTurn<JsonObject, string> {
  const packet = asObject(packetValue);
  const interaction = decodeInteractionContract(packet.interaction_contract);
  const lane = asObject(packet.work_lane_contract);
  const scheduler = asObject(packet.scheduler_hint);
  const codexApp = asObject(scheduler.codex_app);
  const ackHint = asObject(codexApp.ack_hint);
  const failureHint = asObject(codexApp.failure_hint);
  const cliChannel = asObject(interaction.cli_channel);
  const gate = asObject(packet.capability_gate);
  const protocol = asObject(packet.protocol_action_packet);
  const actionPortfolio = asObject(packet.action_portfolio);
  const planningHorizon = asObject(packet.planning_horizon);
  return {
    request: {
      kind: "quota_should_run",
      source: "agent_or_host",
      goal_id: options.goal_id ?? null,
      agent_id: options.agent_id ?? null,
      capabilities: [...(options.capabilities ?? [])],
      context: {},
    },
    interpretation: {
      route: truthyString(lane.lane),
      obligation: truthyString(lane.obligation),
      interaction_mode: truthyString(interaction.mode),
      capability_action:
        Object.keys(gate).length > 0 ? pythonString(gate.action) : null,
      cadence_class: nullableTruthyString(scheduler.cadence_class),
    },
    observation: {
      decision: truthyString(packet.decision),
      should_run: Boolean(packet.should_run),
      effective_action: truthyString(packet.effective_action),
      recommended_action: truthyString(packet.recommended_action),
      action_portfolio:
        Object.keys(actionPortfolio).length > 0 ? actionPortfolio : null,
      planning_horizon:
        Object.keys(planningHorizon).length > 0 ? planningHorizon : null,
      protocol_summary: nullableTruthyString(protocol.summary),
    },
    next_effect: {
      cli_actions: stringArray(cliChannel.next_cli_actions),
      execution_mode: nullableTruthyString(
        packet.execution_mode || codexApp.execution_mode || scheduler.execution_mode,
      ),
      scheduler_action: nullableTruthyString(scheduler.action),
      cadence_class: nullableTruthyString(scheduler.cadence_class),
      ack_cli_args: stringArray(ackHint.cli_args),
      failure_cli_args: stringArray(failureHint.cli_args),
    },
  };
}

export function interpretTurnResultPacket(
  packetValue: unknown,
  options: {
    goal_id?: string | null;
    agent_id?: string | null;
    capabilities?: readonly string[];
  } = {},
): EffectTurn<JsonObject, string, null> {
  const packet = asObject(packetValue);
  const scheduler = asObject(packet.scheduler_hint);
  const codexApp = asObject(scheduler.codex_app);
  const ackHint = asObject(codexApp.ack_hint);
  const failureHint = asObject(codexApp.failure_hint);
  const completedPhases = stringArray(packet.completed_phases);
  const failedPhase = nullableTruthyString(packet.failed_phase);
  const resultKind = truthyString(packet.result_kind);
  return {
    request: {
      kind: "turn_result",
      source: "host",
      goal_id: options.goal_id ?? null,
      agent_id: options.agent_id ?? null,
      capabilities: [...(options.capabilities ?? [])],
      context: {
        completed_phases: completedPhases,
        failed_phase: failedPhase,
        classification: packet.classification,
        delivery_outcome: packet.delivery_outcome,
      },
    },
    interpretation: {
      route: "turn_result_settlement",
      obligation: "settle_turn_receipt",
      interaction_mode: "host_result",
      capability_action: null,
      cadence_class: nullableTruthyString(scheduler.cadence_class),
    },
    observation: {
      decision: resultKind,
      should_run: false,
      // A host result carries a verdict, not a new quota decision. Ignore any
      // host-supplied action and keep the no-action wire representation explicit.
      effective_action: null,
      recommended_action:
        truthyString(packet.recommended_action) || "settle the turn receipt",
      action_portfolio: null,
      planning_horizon: null,
      protocol_summary: nullableTruthyString(packet.summary),
    },
    next_effect: {
      cli_actions: stringArray(packet.next_cli_actions),
      execution_mode: nullableTruthyString(
        packet.execution_mode || codexApp.execution_mode || scheduler.execution_mode,
      ),
      scheduler_action: nullableTruthyString(scheduler.action),
      cadence_class: nullableTruthyString(scheduler.cadence_class),
      ack_cli_args: stringArray(ackHint.cli_args),
      failure_cli_args: stringArray(failureHint.cli_args),
    },
  };
}

export function settlementIdentity(
  input: SettlementIdentityInput,
): SettlementIdentity {
  const todoId = truthyString(input.todo_id).trim() || null;
  const replanObligationId =
    truthyString(input.replan_obligation_id).trim() || null;
  if (todoId && replanObligationId) {
    throw new EffectRuntimeRequestError(
      "settlement identity cannot bind both todo_id and replan_obligation_id",
    );
  }
  const bindingKind: SettlementBindingKind = todoId
    ? "todo"
    : replanObligationId
      ? "autonomous_replan"
      : "unbound";
  const bindingId = todoId ?? replanObligationId ?? "";
  let effectId: string;
  if (todoId) {
    effectId = `${input.goal_id}:${input.agent_id}:${todoId}:${input.turn_instance_id}`;
  } else if (replanObligationId) {
    effectId = `${input.goal_id}:${input.agent_id}:autonomous_replan:${replanObligationId}:${input.turn_instance_id}`;
  } else {
    effectId = `${input.goal_id}:${input.agent_id}::${input.turn_instance_id}`;
  }
  return {
    goal_id: input.goal_id,
    agent_id: input.agent_id,
    todo_id: todoId,
    turn_instance_id: input.turn_instance_id,
    replan_obligation_id: replanObligationId,
    binding_kind: bindingKind,
    binding_id: bindingId,
    effect_id: effectId,
  };
}

export function settlementIdentityPayload(
  identityValue: SettlementIdentityInput,
): JsonObject {
  const identity = settlementIdentity(identityValue);
  if (identity.todo_id || !identity.replan_obligation_id) {
    return {
      schema_version: SETTLEMENT_IDENTITY_SCHEMA_VERSION,
      effect_id: identity.effect_id,
      goal_id: identity.goal_id,
      agent_id: identity.agent_id,
      todo_id: identity.todo_id ?? "",
      turn_instance_id: identity.turn_instance_id,
    };
  }
  return {
    schema_version: SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION,
    effect_id: identity.effect_id,
    goal_id: identity.goal_id,
    agent_id: identity.agent_id,
    turn_instance_id: identity.turn_instance_id,
    binding_kind: identity.binding_kind,
    binding_id: identity.binding_id,
    replan_obligation_id: identity.replan_obligation_id,
  };
}

export function settlementReceiptPayload(
  receipt: SettlementReceipt,
): JsonObject {
  const payload: JsonObject = {
    schema_version: SETTLEMENT_RECEIPT_SCHEMA_VERSION,
    step_kind: requireStepKind(receipt.step_kind),
    status: receipt.status,
    effect_id: receipt.effect_id,
  };
  if (receipt.source_ref) payload.source_ref = receipt.source_ref;
  return payload;
}

export function settlementFailurePayload(
  failure: SettlementFailure,
): JsonObject {
  const payload: JsonObject = {
    kind: requireFailureKind(failure.kind),
    step_kind: requireStepKind(failure.step_kind),
    reason: failure.reason,
  };
  if (failure.details && Object.keys(failure.details).length > 0) {
    payload.details = { ...failure.details };
  }
  return payload;
}

export function settlementPure<Value>(
  value: Value,
  receipts: readonly SettlementReceipt[] = [],
): SettlementResult<Value> {
  return { value, receipts: [...receipts], failure: null };
}

export function settlementFailed<Value = unknown>(options: {
  kind: SettlementFailureKind;
  step_kind: SettlementStepKind;
  reason: string;
  receipts?: readonly SettlementReceipt[];
  details?: JsonObject | null;
}): SettlementResult<Value> {
  return {
    value: null,
    receipts: [...(options.receipts ?? [])],
    failure: {
      kind: requireFailureKind(options.kind),
      step_kind: requireStepKind(options.step_kind),
      reason: options.reason,
      ...(options.details && Object.keys(options.details).length > 0
        ? { details: { ...options.details } }
        : {}),
    },
  };
}

export function settlementResultPayload(
  result: SettlementResult,
): JsonObject {
  return {
    ok: result.failure === null,
    receipts: result.receipts.map(settlementReceiptPayload),
    failure: result.failure ? settlementFailurePayload(result.failure) : null,
  };
}

export function settlementBindGate<Value>(
  current: SettlementResult<Value>,
): SettlementBindGate<Value> {
  if (current.failure) {
    return {
      execute: false,
      result: {
        value: null,
        receipts: [...current.receipts],
        failure: { ...current.failure },
      },
    };
  }
  return { execute: true, result: current };
}

export function settlementBindReduce<Current, Next>(
  current: SettlementResult<Current>,
  next: SettlementResult<Next>,
): SettlementResult<Next> {
  if (current.failure) {
    return {
      value: null,
      receipts: [...current.receipts],
      failure: { ...current.failure },
    };
  }
  if (next.failure) {
    return {
      value: null,
      receipts: [...current.receipts, ...next.receipts],
      failure: { ...next.failure },
    };
  }
  return {
    value: next.value,
    receipts: [...current.receipts, ...next.receipts],
    failure: null,
  };
}

export function settlementStepPayload(step: SettlementStep): JsonObject {
  const payload: JsonObject = {
    kind: requireStepKind(step.kind),
    owner: step.owner,
    precondition: step.precondition,
    idempotency_key_ref: step.idempotency_key_ref,
    expected_receipt: step.expected_receipt,
  };
  if (step.command_template) payload.command_template = step.command_template;
  if (step.conditional) payload.conditional = true;
  return payload;
}

export function settlementPlanPayload(plan: SettlementPlan): JsonObject {
  return {
    schema_version: SETTLEMENT_PLAN_SCHEMA_VERSION,
    identity: settlementIdentityPayload(plan.identity),
    ordered_steps: plan.steps.map(settlementStepPayload),
    host_handoff: {
      owner: "host",
      kind: "scheduler_handoff",
      inside_agent_settlement: false,
    },
  };
}

export function effectIdsMatch(
  committedEffectId: string | null | undefined,
  expectedEffectId: string,
): boolean {
  return !committedEffectId || committedEffectId === expectedEffectId;
}

export function settlementReceipt(
  identityValue: SettlementIdentityInput,
  stepKind: SettlementStepKind,
  sourceRef: string | null = null,
): SettlementReceipt {
  const identity = settlementIdentity(identityValue);
  return {
    step_kind: requireStepKind(stepKind),
    status: "committed",
    effect_id: identity.effect_id,
    ...(sourceRef ? { source_ref: sourceRef } : {}),
  };
}

export function settlementIdentityFromPlan(
  transactionPlanValue: unknown,
): SettlementResult<SettlementIdentity> {
  const transactionPlan = asObject(transactionPlanValue);
  const settlementPlanValue = transactionPlan.settlement_plan;
  if (
    typeof settlementPlanValue !== "object" ||
    settlementPlanValue === null ||
    Array.isArray(settlementPlanValue)
  ) {
    return settlementFailed({
      kind: "receipt_missing",
      step_kind: "validation",
      reason: "Turn transaction has no typed settlement plan",
    });
  }
  const identityValue = (settlementPlanValue as JsonObject).identity;
  if (
    typeof identityValue !== "object" ||
    identityValue === null ||
    Array.isArray(identityValue)
  ) {
    return settlementFailed({
      kind: "invalid_identity",
      step_kind: "validation",
      reason: "Turn settlement plan has no identity",
    });
  }
  const identityObject = identityValue as JsonObject;
  if (
    ["goal_id", "agent_id", "turn_instance_id"].some(
      (field) => truthyString(identityObject[field]).trim().length === 0,
    )
  ) {
    return settlementFailed({
      kind: "invalid_identity",
      step_kind: "validation",
      reason: "Turn settlement plan has an incomplete identity",
    });
  }
  const todoId = truthyString(identityObject.todo_id).trim();
  const replanObligationId = truthyString(
    identityObject.replan_obligation_id,
  ).trim();
  if (Boolean(todoId) === Boolean(replanObligationId)) {
    return settlementFailed({
      kind: "invalid_identity",
      step_kind: "validation",
      reason:
        "Turn settlement plan requires exactly one Todo or autonomous replan obligation binding",
    });
  }
  let built: SettlementIdentity;
  try {
    built = settlementIdentity({
      goal_id: pythonString(identityObject.goal_id),
      agent_id: pythonString(identityObject.agent_id),
      todo_id: todoId || null,
      turn_instance_id: pythonString(identityObject.turn_instance_id),
      replan_obligation_id: replanObligationId || null,
    });
  } catch (error) {
    return settlementFailed({
      kind: "invalid_identity",
      step_kind: "validation",
      reason: error instanceof Error ? error.message : pythonString(error),
    });
  }
  const effectId = truthyString(identityObject.effect_id).trim();
  if (!effectId) {
    return settlementFailed({
      kind: "invalid_identity",
      step_kind: "validation",
      reason: "Turn settlement plan has no effect id",
    });
  }
  if (effectId !== built.effect_id) {
    return settlementFailed({
      kind: "invalid_identity",
      step_kind: "validation",
      reason: "Turn settlement plan effect id does not match its identity",
    });
  }
  return settlementPure(built);
}

export function requireMatchingEffectId(
  committedEffectId: string | null | undefined,
  expectedEffectId: string,
): SettlementResult<string> {
  if (!effectIdsMatch(committedEffectId, expectedEffectId)) {
    return settlementFailed({
      kind: "identity_mismatch",
      step_kind: "validation",
      reason:
        `Turn journal belongs to another settlement effect: journal effect is ${committedEffectId} ` +
        `but plan effect is ${expectedEffectId}`,
    });
  }
  return settlementPure(expectedEffectId);
}

export function isCommittedPayload(value: unknown): value is JsonObject {
  const payload = asObject(value);
  return payload.ok === true && payload.appended === true;
}

const DEFAULT_MISSING_FAILURE_KINDS: Partial<
  Record<SettlementStepKind, SettlementFailureKind>
> = { durable_writeback: "receipt_missing" };

const MISSING_PAYLOAD_REASONS: Partial<Record<SettlementStepKind, string>> = {
  durable_writeback: "Turn journal is missing its committed writeback payload",
  quota_spend: "Turn journal is missing its committed quota spend payload",
};

export function seedCommittedSteps(options: {
  identity: SettlementIdentityInput;
  ordered_steps: readonly SettlementStepKind[];
  committed_payloads: Partial<Record<SettlementStepKind, unknown>>;
  completed_phases?: readonly string[] | null;
  transaction_phases?: readonly string[] | null;
  require_validation?: boolean;
  missing_failure_kinds?: Partial<
    Record<SettlementStepKind, SettlementFailureKind>
  >;
  source_ref_prefix?: string;
}): SettlementResult<JsonObject> {
  const identity = settlementIdentity(options.identity);
  const requireValidation = options.require_validation ?? true;
  const completedPhases = options.completed_phases ?? null;
  const transactionPhases = options.transaction_phases ?? null;
  if (completedPhases && transactionPhases) {
    const prefix = transactionPhases.slice(0, completedPhases.length);
    if (
      completedPhases.length !== prefix.length ||
      completedPhases.some((phase, index) => phase !== prefix[index])
    ) {
      return settlementFailed({
        kind: "receipt_missing",
        step_kind: "validation",
        reason: "Turn journal phases are not an ordered transaction prefix",
      });
    }
    if (requireValidation && !completedPhases.includes("validation")) {
      return settlementFailed({
        kind: "receipt_missing",
        step_kind: "validation",
        reason: "Turn settlement requires a committed validation receipt",
      });
    }
  }
  const missingKinds = {
    ...DEFAULT_MISSING_FAILURE_KINDS,
    ...(options.missing_failure_kinds ?? {}),
  };
  const receipts: SettlementReceipt[] = [];
  const committed: JsonObject = {};
  const sourcePrefix = options.source_ref_prefix ?? "turn_journal";
  for (const rawStepKind of options.ordered_steps) {
    const stepKind = requireStepKind(rawStepKind);
    if (stepKind === "validation" && requireValidation) {
      if (completedPhases && !completedPhases.includes("validation")) {
        return settlementFailed({
          kind: "receipt_missing",
          step_kind: stepKind,
          reason: "Turn settlement requires a committed validation receipt",
          receipts,
        });
      }
      receipts.push(
        settlementReceipt(
          identity,
          stepKind,
          `${sourcePrefix}:${identity.effect_id}#${stepKind}`,
        ),
      );
      committed[stepKind] = {};
      continue;
    }
    const committedFlag = completedPhases
      ? completedPhases.includes(stepKind)
      : true;
    if (!committedFlag) continue;
    const payload = options.committed_payloads[stepKind];
    if (!isCommittedPayload(payload)) {
      return settlementFailed({
        kind: missingKinds[stepKind] ?? "receipt_missing",
        step_kind: stepKind,
        reason:
          MISSING_PAYLOAD_REASONS[stepKind] ??
          `Turn journal is missing its committed ${stepKind} payload`,
        receipts,
      });
    }
    receipts.push(
      settlementReceipt(
        identity,
        stepKind,
        `${sourcePrefix}:${identity.effect_id}#${stepKind}`,
      ),
    );
    committed[stepKind] = payload;
  }
  return settlementPure(committed, receipts);
}

export function settlementNextAction(options: {
  identity: SettlementIdentityInput;
  ordered_steps: readonly SettlementStepKind[];
  committed_payloads: Partial<Record<SettlementStepKind, unknown>>;
  completed_phases?: readonly string[] | null;
  transaction_phases?: readonly string[] | null;
  require_validation?: boolean;
  missing_failure_kinds?: Partial<
    Record<SettlementStepKind, SettlementFailureKind>
  >;
  source_ref_prefix?: string;
}): SettlementNextAction {
  const seeded = seedCommittedSteps(options);
  if (seeded.failure !== null) {
    return { decision: "failed", step_kind: null, result: seeded };
  }
  const completed = new Set(options.completed_phases ?? []);
  const next = options.ordered_steps.find(
    (step) => step !== "validation" && !completed.has(step),
  );
  if (next) {
    return { decision: "execute", step_kind: next, result: seeded };
  }
  return { decision: "complete", step_kind: null, result: seeded };
}

export function callbackFailureKind(
  stepKind: SettlementStepKind,
  reason: string,
): SettlementFailureKind {
  if (stepKind === "durable_writeback") return "writeback_rejected";
  if (stepKind === "terminal_closeout") return "terminal_closeout_rejected";
  if (reason.toLowerCase().includes("budget")) return "budget_rejected";
  return "quota_spend_rejected";
}

export function commitStepPayload(options: {
  identity: SettlementIdentityInput;
  step_kind: SettlementStepKind;
  transaction_phases: readonly string[];
  payload: unknown;
  source_ref_prefix?: string;
}): SettlementCommitReduction {
  const identity = settlementIdentity(options.identity);
  const stepKind = requireStepKind(options.step_kind);
  const payload = asObject(options.payload);
  if (!isCommittedPayload(options.payload)) {
    const reason = truthyString(payload.error || payload.reason) ||
      `${stepKind} callback rejected the settlement`;
    return {
      result: settlementFailed({
        kind: callbackFailureKind(stepKind, reason),
        step_kind: stepKind,
        reason,
      }),
      completed_phases: null,
    };
  }
  const phaseIndex = options.transaction_phases.indexOf(stepKind);
  if (phaseIndex < 0) {
    throw new EffectRuntimeRequestError(`transaction phases do not contain ${stepKind}`);
  }
  const completedPhases = options.transaction_phases.slice(0, phaseIndex + 1);
  const sourcePrefix = options.source_ref_prefix ?? "turn_journal";
  return {
    result: settlementPure(payload, [
      settlementReceipt(
        identity,
        stepKind,
        `${sourcePrefix}:${identity.effect_id}#${stepKind}`,
      ),
    ]),
    completed_phases: completedPhases,
  };
}
