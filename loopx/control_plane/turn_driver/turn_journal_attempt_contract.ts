/** The journal owner's prepared-intent contract, shared by writes and reads. */
import transactionContract from "../turn_transaction_contract.json" with { type: "json" };
import { SETTLEMENT_STEP_KINDS, type JsonObject } from "../effect_program.ts";

const preparedStepKinds: ReadonlySet<string> = new Set(
  SETTLEMENT_STEP_KINDS.filter((kind) => kind !== "validation"),
);

interface AttemptViolation {
  code: string;
  message: string;
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function preparedAttemptViolation(
  journal: JsonObject,
  state: { status: string; completedPhases: readonly string[]; effectId: string },
): AttemptViolation | null {
  if (journal.effect_attempts === undefined) return null;
  if (!isObject(journal.effect_attempts)) {
    return { code: "prepared_effect_attempts_invalid",
      message: "Turn journal prepared effects must be an object" };
  }
  const entries = Object.entries(journal.effect_attempts);
  // Preserve the writer contract: no pending intent is represented by an
  // absent field, not by an empty map or a set of historical committed intents.
  if (entries.length !== 1) {
    return { code: "prepared_effect_count_invalid",
      message: "Turn journal must carry at most one prepared effect" };
  }
  const [stepKind, attempt] = entries[0];
  if (!preparedStepKinds.has(stepKind)) {
    return { code: "prepared_effect_step_unsupported",
      message: "Turn journal carries an unsupported prepared effect step" };
  }
  if (!isObject(attempt) || attempt.status !== "prepared"
    || attempt.effect_ref !== `${state.effectId}#${stepKind}`) {
    return { code: "prepared_effect_identity_invalid",
      message: "Turn journal prepared effect does not match settlement identity" };
  }
  const phases: readonly string[] = transactionContract.phases;
  const phaseIndex = phases.indexOf(stepKind === "terminal_closeout"
    ? "scheduler_apply" : stepKind);
  if (phaseIndex < 0 || state.completedPhases.length !== phaseIndex
    || !state.completedPhases.every((phase, index) => phase === phases[index])) {
    return { code: "prepared_effect_phase_invalid",
      message: "Turn journal prepared effect is not the next settlement step" };
  }
  if (!["in_progress", "failed"].includes(state.status)) {
    return { code: "prepared_effect_status_invalid",
      message: "Turn journal terminal state cannot retain a prepared effect" };
  }
  return null;
}
