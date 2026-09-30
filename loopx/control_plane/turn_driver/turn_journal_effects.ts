import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import { settlementIdentityFromPlan } from "../effect_program.ts";
import {
  EffectRuntimeConflictError,
  EffectRuntimeRequestError,
} from "../effect_runtime_errors.ts";
import {
  atomicWriteJson,
  claimFileMutationLock,
  mutationLockOwner,
  releaseFileMutationLock,
  releaseFileMutationLockClaim,
  withFileMutationLock,
} from "../effect_runtime_io.ts";
import { decideFirstPartyHostRuntime } from "../goals/first_party_host_runtime.ts";
import { parseExactGoalRef } from "../goals/goal_instance_identity.ts";
import { requireNonEmptyString as requiredString } from "../runtime_decode.ts";
import { preparedAttemptViolation } from "./turn_journal_attempt_contract.ts";
import {
  interpretTurnJournalEffect,
  parseTurnJournalGoalBinding,
  supportedJournalStatuses,
  transactionPhases,
  type TurnJournalGoalBinding,
} from "./turn_journal.ts";

const SOURCE_ADMISSION_SCHEMA_VERSION =
  "loopx_turn_journal_source_admission_v0";
const SOURCE_SESSION_PROFILE_ID = "source_session_v1";
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

interface SourceAdmission {
  registryPath: string;
  plannedGoalRef: {
    goal_id: string;
    goal_instance_id: string;
  };
  authority: unknown;
  lock: {
    target: string;
    pid: number;
    token: string;
  };
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

function sameGoalRef(
  left: SourceAdmission["plannedGoalRef"],
  right: SourceAdmission["plannedGoalRef"],
): boolean {
  return left.goal_id === right.goal_id
    && left.goal_instance_id === right.goal_instance_id;
}

function sourceGuardTarget(registryPath: string, goalId: string): string {
  return join(
    dirname(registryPath),
    ".loopx",
    "lifecycle",
    "goal-instance",
    "guards",
    `${sha256(goalId)}.guard`,
  );
}

function requireSourceAdmission(
  value: unknown,
  binding: Extract<TurnJournalGoalBinding, { kind: "exact" }>,
): SourceAdmission {
  const admission = asObject(value);
  if (
    admission.schema_version !== SOURCE_ADMISSION_SCHEMA_VERSION
    || admission.profile_id !== SOURCE_SESSION_PROFILE_ID
  ) {
    throw new EffectRuntimeRequestError(
      "Turn journal source admission is malformed",
      "journal_source_admission_invalid",
    );
  }
  const registryPath = requiredString(
    admission.registry_path,
    "source admission registry_path",
  );
  if (!isAbsolute(registryPath) || resolve(registryPath) !== registryPath) {
    throw new EffectRuntimeRequestError(
      "Turn journal source admission registry path must be absolute and normalized",
      "journal_source_admission_invalid",
    );
  }
  const planned = parseExactGoalRef(admission.planned_goal_ref);
  if (planned.kind === "invalid") {
    throw new EffectRuntimeRequestError(
      "Turn journal source admission has an invalid planned GoalRef",
      "journal_source_admission_invalid",
    );
  }
  const plannedGoalRef = {
    goal_id: planned.value.goalId.value,
    goal_instance_id: planned.value.goalInstanceId.value,
  };
  if (!sameGoalRef(plannedGoalRef, binding.goal_ref)) {
    throw new EffectRuntimeRequestError(
      "Turn journal source admission does not match the journal GoalRef",
      "journal_source_admission_invalid",
    );
  }
  const lock = asObject(admission.lock);
  const target = requiredString(lock.target, "source admission lock target");
  const expectedTarget = sourceGuardTarget(registryPath, plannedGoalRef.goal_id);
  if (
    !isAbsolute(target)
    || resolve(target) !== target
    || target !== expectedTarget
  ) {
    throw new EffectRuntimeRequestError(
      "Turn journal source admission lock target mismatch",
      "journal_source_admission_invalid",
    );
  }
  if (
    typeof lock.pid !== "number"
    || !Number.isSafeInteger(lock.pid)
    || lock.pid <= 0
  ) {
    throw new EffectRuntimeRequestError(
      "Turn journal source admission lock owner is invalid",
      "journal_source_admission_invalid",
    );
  }
  return {
    registryPath,
    plannedGoalRef,
    authority: admission.authority,
    lock: {
      target,
      pid: lock.pid,
      token: requiredString(lock.token, "source admission lock token"),
    },
  };
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
  const goalBinding = parseTurnJournalGoalBinding(journal);
  if (goalBinding.kind === "invalid") {
    throw new EffectRuntimeRequestError(
      `Turn journal GoalRef binding is invalid: ${goalBinding.violation}`,
      "journal_snapshot_invalid",
    );
  }
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
  const commit = async (): Promise<JsonObject> =>
    await withFileMutationLock(path, async () => {
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
  if (goalBinding.kind === "legacy") {
    if (params.source_admission !== undefined) {
      throw new EffectRuntimeRequestError(
        "Legacy Turn journals cannot carry source admission",
        "journal_source_admission_invalid",
      );
    }
    return await commit();
  }
  if (params.source_admission === undefined) {
    throw new EffectRuntimeRequestError(
      "Exact GoalRef Turn journals require source admission",
      "journal_source_admission_required",
    );
  }
  const admission = requireSourceAdmission(
    params.source_admission,
    goalBinding,
  );
  const claim = await claimFileMutationLock(
    admission.lock.target,
    admission.lock.token,
  );
  if (!claim) {
    conflict(
      "Turn journal source admission lock handoff expired",
      "journal_source_admission_expired",
    );
  }
  let adopted = false;
  try {
    const owner = await mutationLockOwner(admission.lock.target);
    if (
      owner?.pid !== admission.lock.pid
      || owner.token !== admission.lock.token
    ) {
      conflict(
        "Turn journal source admission lock owner changed",
        "journal_source_admission_expired",
      );
    }
    adopted = true;
    const decision = decideFirstPartyHostRuntime({
      profile_id: SOURCE_SESSION_PROFILE_ID,
      operation: "require_current",
      planned_goal_ref: admission.plannedGoalRef,
      authority: admission.authority,
    });
    if (decision.kind === "reject") {
      conflict(
        `Turn journal source admission rejected: ${decision.code}`,
        decision.code,
      );
    }
    if (decision.kind !== "resume") {
      throw new EffectRuntimeRequestError(
        "Turn journal source admission did not resume the exact GoalRef",
        "journal_source_admission_invalid",
      );
    }
    return await commit();
  } finally {
    if (adopted) {
      await releaseFileMutationLock(
        admission.lock.target,
        admission.lock.token,
        claim,
        true,
      );
    } else {
      await releaseFileMutationLockClaim(claim);
    }
  }
}
