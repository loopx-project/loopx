import {EffectRuntimeConflictError, EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, requireNonEmptyString, requireStringLiteral, type JsonObject} from "../runtime_decode.ts";
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";

const RUN_IDENTITY_FIELDS = ["benchmark_id", "study_id", "case_id", "run_id", "arm_id", "arm_role", "attempt"];

interface ActiveRun {
  run_id: string;
  case_id: string;
  arm_role: string;
  admitted_at: string;
}

interface TerminalFollowthroughPlan {
  row: JsonObject;
  active_run: ActiveRun | null;
  already_released: boolean;
}

function objects(value: unknown, label: string): JsonObject[] {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an array`);
  return value.map(item => requireJsonObject(item, label));
}

function timestamp(value: unknown, label: string): {text: string; micros: bigint} {
  const text = requireNonEmptyString(value, label);
  const micros = parseTodoTimestampMicros(text, true);
  if (micros === null) throw new EffectRuntimeRequestError(`${label} must be an ISO timestamp with a timezone`);
  return {text, micros};
}

/** The adapter binds provider facts and holds the board and admission locks through readback and release. */
export function prepareBenchmarkTerminalFollowthrough(value: unknown): TerminalFollowthroughPlan {
  const request = requireJsonObject(value, "benchmark terminal followthrough");
  const goalId = requireNonEmptyString(request.goal_id, "goal_id");
  const identity = requireJsonObject(request.identity, "identity");
  for (const field of RUN_IDENTITY_FIELDS) {
    if (field !== "attempt") requireNonEmptyString(identity[field], `identity.${field}`);
  }
  if (typeof identity.attempt !== "number" || !Number.isSafeInteger(identity.attempt) || identity.attempt < 0) {
    throw new EffectRuntimeRequestError("identity.attempt must be a non-negative safe integer");
  }

  const admission = requireJsonObject(request.admission_receipt, "admission_receipt");
  if (admission.ok !== true || admission.admitted !== true || admission.dry_run !== false ||
      admission.write_performed !== true || admission.idempotent !== false || admission.goal_id !== goalId) {
    throw new EffectRuntimeRequestError("terminal followthrough requires the original executed admission for this goal");
  }
  const run = requireJsonObject(admission.run, "admission_receipt.run");
  const admittedAt = timestamp(run.admitted_at, "admission_receipt.run.admitted_at");
  const expectedRun: ActiveRun = {
    run_id: requireNonEmptyString(run.run_id, "admission_receipt.run.run_id"),
    case_id: requireNonEmptyString(run.case_id, "admission_receipt.run.case_id"),
    arm_role: requireNonEmptyString(run.arm_role, "admission_receipt.run.arm_role"),
    admitted_at: admittedAt.text,
  };
  if (expectedRun.run_id !== identity.run_id || expectedRun.case_id !== identity.case_id ||
      expectedRun.arm_role !== identity.arm_role) {
    throw new EffectRuntimeRequestError("admission receipt does not match the run identity");
  }

  const terminal = requireJsonObject(request.terminal, "terminal");
  if (!RUN_IDENTITY_FIELDS.every(field => terminal[field] === identity[field])) {
    throw new EffectRuntimeRequestError("terminal evidence does not match the run identity");
  }
  const status = requireStringLiteral(terminal.status, ["completed", "runner_invalid", "cancelled"] as const,
    "terminal.status");
  const observedAt = timestamp(terminal.observed_at, "terminal.observed_at");
  if (observedAt.micros < admittedAt.micros) {
    throw new EffectRuntimeRequestError("terminal observation predates admission");
  }
  const reconciledAt = timestamp(request.reconciled_at, "reconciled_at");
  if (observedAt.micros > reconciledAt.micros) {
    throw new EffectRuntimeRequestError("terminal observation follows host reconciliation time");
  }
  if (terminal.solver_terminal !== true || terminal.grading_drained !== true) {
    throw new EffectRuntimeRequestError("terminal followthrough requires a terminal solver and drained grading");
  }
  // A goal's concurrency slot has no study or benchmark namespace.
  const rows = objects(request.rows, "rows").filter(row => row.run_id === expectedRun.run_id);
  if (rows.length !== 1 || rows[0].schema_version !== "benchmark_experiment_board_row_v0" ||
      !RUN_IDENTITY_FIELDS.every(field => rows[0][field] === identity[field])) {
    throw new EffectRuntimeConflictError("terminal followthrough requires one existing exact board row");
  }
  const [row] = rows;
  const sameTerminal = row.status === status;
  if (!sameTerminal && row.status !== "planned" && row.status !== "running") {
    throw new EffectRuntimeConflictError("existing board status conflicts with the terminal observation");
  }
  const slots = objects(request.active_runs, "active_runs").filter(slot => slot.run_id === expectedRun.run_id);
  if (slots.length > 1 || (slots.length === 1 &&
      !Object.entries(expectedRun).every(([field, expected]) => slots[0][field] === expected))) {
    throw new EffectRuntimeConflictError("active slot does not match the executed admission");
  }
  const active = slots.length === 1;
  if (!active && !sameTerminal) {
    throw new EffectRuntimeConflictError("terminal board transition requires the admitted slot");
  }
  const observation = requireJsonObject(request.runtime_observation, "runtime_observation");
  if (observation.schema_version !== "benchmark_runtime_observation_v0" ||
      observation.admission_active !== active || observation.job_receipt_state !== "resolved" ||
      observation.terminal_result_present !== true || observation.reconciliation_required !== active ||
      observation.classification !== (active ? "terminal_pending_reconcile" : "not_admitted") ||
      observation.recommended_transition !== (active ? "write_terminal_then_release" : "none")) {
    throw new EffectRuntimeRequestError("terminal followthrough requires resolved terminal runtime evidence");
  }
  if (!active) return {row, active_run: null, already_released: true};
  // A replay may arrive after score and insight updates on the terminal row.
  if (sameTerminal) return {row, active_run: expectedRun, already_released: false};
  // Reconciliation observes a distinct board transition even if provider evidence arrived late.
  if (reconciledAt.micros <= timestamp(row.observed_at, "row.observed_at").micros) {
    throw new EffectRuntimeConflictError("host reconciliation time must follow the existing board observation");
  }
  return {row: {...row, status, observed_at: reconciledAt.text}, active_run: expectedRun, already_released: false};
}
