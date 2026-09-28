import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { isAbsolute } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import { settlementIdentityFromPlan } from "../effect_program.ts";
import {
  EffectRuntimeConflictError,
  EffectRuntimeRequestError,
} from "../effect_runtime_errors.ts";
import {
  atomicWriteJson,
  withFileMutationLock,
} from "../effect_runtime_io.ts";
import { requireNonEmptyString as requiredString } from "../runtime_decode.ts";
import { preparedAttemptViolation } from "./turn_journal_attempt_contract.ts";
import {
  interpretTurnJournalEffect,
  supportedJournalStatuses,
  transactionPhases,
} from "./turn_journal.ts";

const terminalStatuses = new Set(["committed", "stopped"]);
const statusTransitions: Readonly<Record<string, ReadonlySet<string>>> = {
  in_progress: new Set([
    "in_progress",
    "failed",
    "stopped",
    "scheduler_action_required",
    "committed",
  ]),
  failed: new Set(["failed", "in_progress"]),
  scheduler_action_required: new Set(["scheduler_action_required", "committed"]),
};
const legalPhaseAdvances = new Set(["0->2", "2->3", "3->4", "4->5", "5->7"]);

function asObject(value: unknown): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
}

function journalEffectId(journal: JsonObject): string | null {
  const plan = asObject(journal.plan);
  const transaction = asObject(plan.transaction);
  const parsed = settlementIdentityFromPlan(transaction);
  return parsed.failure === null ? parsed.value.effect_id : null;
}

function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (typeof value !== "object" || value === null) return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => [key, stableValue(child)]),
  );
}

function operationId(journal: JsonObject): string {
  return `sha256:${sha256(JSON.stringify(stableValue(journal)))}`;
}

function journalWithoutRecoveryAudit(journal: JsonObject): JsonObject {
  const projected = { ...journal };
  delete projected.recovery_audit;
  return projected;
}

interface JournalState {
  status: string;
  completedPhases: string[];
  effectId: string;
  failedPhase: string | null;
}

function conflict(message: string, code = "journal_transition_conflict"): never {
  throw new EffectRuntimeConflictError(message, code);
}

function samePrefix(left: readonly string[], right: readonly string[]): boolean {
  return left.length <= right.length && left.every((phase, index) => phase === right[index]);
}

function requireJournalState(journal: JsonObject): JournalState {
  if (journal.schema_version !== "loopx_turn_journal_v0") {
    throw new EffectRuntimeRequestError(
      "Turn journal has an unsupported schema",
      "journal_snapshot_invalid",
    );
  }
  const plan = asObject(journal.plan);
  const envelope = asObject(plan.turn_envelope);
  const goalId = typeof journal.goal_id === "string" ? journal.goal_id : "";
  const agentId = typeof envelope.agent_id === "string" ? envelope.agent_id : "";
  const turnKey = typeof journal.turn_key === "string" ? journal.turn_key : "";
  const effect = interpretTurnJournalEffect({
    schema_version: "loopx_turn_journal_interpretation_request_v0",
    journal,
    goal_id: goalId,
    agent_id: agentId,
    turn_key: turnKey,
  });
  const context = effect.request.context;
  const effectId = journalEffectId(journal);
  const attemptViolation = preparedAttemptViolation(journal, {
    status: context.journal_status,
    completedPhases: context.completed_phases,
    effectId: effectId ?? "",
  });
  if (attemptViolation) conflict(attemptViolation.message);
  if (!context.journal_consistent || !effectId) {
    throw new EffectRuntimeRequestError(
      `Turn journal snapshot is inconsistent: ${context.violations.join(", ")}`,
      "journal_snapshot_invalid",
    );
  }
  if (journal.recovery_audit !== undefined && context.last_recovery === null) {
    throw new EffectRuntimeRequestError(
      "Turn journal carries a malformed recovery audit",
      "journal_snapshot_invalid",
    );
  }
  const status = context.journal_status;
  const completedPhases = [...context.completed_phases];
  if (!supportedJournalStatuses.has(status)) {
    throw new EffectRuntimeRequestError(
      "Turn journal status is unsupported",
      "journal_snapshot_invalid",
    );
  }
  const receipt = asObject(journal.receipt);
  const failedPhase = typeof receipt.failed_phase === "string"
    ? receipt.failed_phase
    : null;
  if (status === "committed" && completedPhases.length !== transactionPhases.length) {
    conflict("Committed Turn journal must contain the complete transaction prefix");
  }
  if (status === "stopped" && completedPhases.length !== 3) {
    conflict("Stopped Turn journal must end after validation");
  }
  if (status === "scheduler_action_required" && completedPhases.length !== 5) {
    conflict("Scheduler-pending Turn journal must end after quota spend");
  }
  if (status === "in_progress" && completedPhases.length > 5) {
    conflict("In-progress Turn journal cannot claim scheduler completion");
  }
  if (status === "failed") {
    const nextPhase = transactionPhases[completedPhases.length] ?? null;
    const terminalCloseoutFailure =
      failedPhase === "terminal_closeout" && completedPhases.length === 5;
    if (!failedPhase || (failedPhase !== nextPhase && !terminalCloseoutFailure)) {
      conflict("Failed Turn journal must name the next uncompleted phase");
    }
  }
  const state = { status, completedPhases, effectId, failedPhase };
  return state;
}

function requireJournalTransition(
  existing: JsonObject,
  incoming: JsonObject,
  previous: JournalState,
  next: JournalState,
): void {
  if (operationId(asObject(existing.plan)) !== operationId(asObject(incoming.plan))) {
    conflict("Turn journal transaction plan is immutable", "journal_plan_conflict");
  }
  if (terminalStatuses.has(previous.status)) {
    const auditOnly =
      previous.status === next.status &&
      operationId(journalWithoutRecoveryAudit(existing)) ===
        operationId(journalWithoutRecoveryAudit(incoming));
    if (auditOnly) return;
    conflict("Terminal Turn journal tombstones are immutable");
  }
  if (!statusTransitions[previous.status]?.has(next.status)) {
    conflict(`Illegal Turn journal status transition ${previous.status}->${next.status}`);
  }
  if (
    previous.status === next.status &&
    ["failed", "scheduler_action_required"].includes(previous.status) &&
    operationId(journalWithoutRecoveryAudit(existing)) !==
      operationId(journalWithoutRecoveryAudit(incoming))
  ) {
    conflict("Settled Turn journal state only permits recovery-audit completion");
  }
  const validationRetryRewind =
    previous.status === "failed" &&
    next.status === "in_progress" &&
    previous.failedPhase === "validation" &&
    samePrefix(next.completedPhases, previous.completedPhases);
  if (
    !samePrefix(previous.completedPhases, next.completedPhases) &&
    !validationRetryRewind
  ) {
    conflict("Turn journal completed phases cannot regress or fork");
  }
  const phaseAdvance = `${previous.completedPhases.length}->${next.completedPhases.length}`;
  if (
    next.completedPhases.length > previous.completedPhases.length &&
    !legalPhaseAdvances.has(phaseAdvance)
  ) {
    conflict("Turn journal transition cannot skip transaction checkpoints");
  }
}

export async function commitTurnJournal(
  params: JsonObject,
): Promise<JsonObject> {
  const path = requiredString(params.path, "path");
  if (!isAbsolute(path)) {
    throw new EffectRuntimeRequestError("Turn journal path must be absolute");
  }
  const journal = asObject(params.journal);
  const incomingState = requireJournalState(journal);
  const expectedEffectId = typeof params.expected_effect_id === "string"
    ? params.expected_effect_id.trim()
    : "";
  const incomingEffectId = incomingState.effectId;
  if (expectedEffectId && incomingEffectId !== expectedEffectId) {
    throw new EffectRuntimeRequestError(
      "Turn journal does not carry the expected settlement effect",
    );
  }
  const incomingOperationId = operationId(journal);
  return await withFileMutationLock(path, async () => {
    let existing: JsonObject | null = null;
    try {
      const encoded = await readFile(path, "utf8");
      existing = asObject(JSON.parse(encoded));
      const existingState = requireJournalState(existing);
      const existingEffectId = existingState.effectId;
      if (
        existingEffectId &&
        existingEffectId !== incomingEffectId
      ) {
        throw new EffectRuntimeConflictError(
          "Turn journal belongs to another settlement effect",
          "journal_effect_conflict",
        );
      }
      if (operationId(existing) === incomingOperationId) {
        return {
          ok: true,
          appended: false,
          replayed: true,
          effect_id: incomingEffectId,
          operation_id: incomingOperationId,
        };
      }
      requireJournalTransition(existing, journal, existingState, incomingState);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    if (existing === null && (
      incomingState.status !== "in_progress" ||
      incomingState.completedPhases.length !== 0
    )) {
      conflict("A new Turn journal must begin in progress with no completed phases");
    }
    await atomicWriteJson(path, journal);
    return {
      ok: true,
      appended: true,
      replayed: false,
      effect_id: incomingEffectId,
      operation_id: incomingOperationId,
    };
  });
}
