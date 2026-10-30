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
  atomicWriteText,
  claimFileMutationLock,
  mutationLockOwner,
  releaseFileMutationLock,
  releaseFileMutationLockClaim,
  withFileMutationLock,
} from "../effect_runtime_io.ts";
import { decideFirstPartyHostRuntime } from "../goals/first_party_host_runtime.ts";
import { parseExactGoalRef } from "../goals/goal_instance_identity.ts";
import { requireNonEmptyString as requiredString, sourceNumberTokens } from "../runtime_decode.ts";
import { preparedAttemptViolation } from "./turn_journal_attempt_contract.ts";
import { readQuotaSettlementForAdmittedOwnerFromSnapshot, readQuotaSettlementSnapshot } from "../quota/settlement_readback.ts";
import { parseQuotaAccountingOwner, requireQuotaOwnerProjection, withQuotaAccountingOwner } from "../quota/source_admission.ts";
import {
  interpretTurnJournalEffect,
  journalPhaseViolation,
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

type SourceNumbers = WeakMap<object, Map<string, string>>;

function rawNumber(token: string): unknown {
  const rawJSON = (JSON as typeof JSON & {rawJSON?: (text: string) => unknown}).rawJSON;
  if (!rawJSON) throw new EffectRuntimeRequestError("Turn journal number preservation requires JSON.rawJSON", "journal_snapshot_invalid");
  return rawJSON(token);
}

function stableValue(value: unknown, numbers?: SourceNumbers): unknown {
  if (typeof value !== "object" || value === null) return value;
  const child = (key: string, item: unknown): unknown => {
    const token = numbers?.get(value)?.get(key);
    return token === undefined ? stableValue(item, numbers) : rawNumber(token);
  };
  if (Array.isArray(value)) return value.map((item, index) => child(String(index), item));
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, child(key, item)]),
  );
}

function operationId(journal: JsonObject, numbers?: SourceNumbers): string {
  return `sha256:${sha256(JSON.stringify(stableValue(journal, numbers)))}`;
}

function journalWithoutRecoveryAudit(journal: JsonObject, numbers?: SourceNumbers): JsonObject {
  const projected = { ...journal };
  const fields = numbers?.get(journal);
  if (fields) numbers?.set(projected, fields);
  delete projected.recovery_audit;
  return projected;
}

function parseJournal(encoded: string): {journal: JsonObject; numbers: SourceNumbers} {
  const numbers: SourceNumbers = new WeakMap();
  const journal = asObject(JSON.parse(encoded, function (key, value: unknown, context?: {source?: string}) {
    if (typeof value === "number" && Number.isInteger(value) &&
        context?.source !== undefined && context.source !== JSON.stringify(value)) {
      const fields = numbers.get(this) ?? new Map<string, string>();
      fields.set(key, context.source);
      numbers.set(this, fields);
    }
    return value;
  }));
  return {journal, numbers};
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

function resultValidationInvalidated(existing: JsonObject, incoming: JsonObject,
  previous: JournalState, next: JournalState): boolean {
  if (previous.status !== "in_progress" || next.status !== "failed" ||
      previous.completedPhases.length !== 3 || next.completedPhases.length !== 2 ||
      next.failedPhase !== "validation" || incoming.validation_stage !== "result_freshness") return false;
  // Only a rejected, uncommitted result may invalidate its validation checkpoint.
  // Prepared effects must already have been aborted by the settlement owner.
  if ([existing, incoming].some(journal => ["writeback", "quota_spend", "terminal_closeout",
      "effect_attempts", "source_effect_hold"].some(key => journal[key] !== undefined))) return false;
  const context = asObject(existing.result_work_context);
  const retry = asObject(incoming.result_revalidation);
  const rejection = asObject(retry.rejection);
  return rejection.ok === false &&
    ["checkpoint_read_context_stale", "delivery_validation_binding_mismatch"].includes(String(rejection.error_code)) &&
    typeof context.read_context_id === "string" && context.read_context_id.length > 0 &&
    typeof existing.task_validation === "object" && existing.task_validation !== null &&
    typeof existing.host_result === "object" && existing.host_result !== null &&
    operationId(context) === operationId(asObject(incoming.result_work_context)) &&
    operationId(context) === operationId(asObject(retry.original_context)) &&
    operationId(asObject(existing.host_result)) === operationId(asObject(retry.candidate)) &&
    incoming.task_validation === undefined && incoming.result_work_binding === undefined;
}

function requireTerminalResultRevalidation(existing: JsonObject, incoming: JsonObject,
  previous: JournalState, next: JournalState): void {
  const prior = asObject(existing.result_revalidation);
  const retry = asObject(incoming.result_revalidation);
  if (prior.phase !== "terminal_closeout" && retry.phase !== "terminal_closeout") return;
  if (previous.completedPhases.length !== 5 || next.completedPhases.length !== 5 ||
      [existing, incoming].some(journal => journal.terminal_closeout !== undefined || journal.effect_attempts !== undefined) ||
      ["writeback", "quota_spend"].some(key => existing[key] == null || incoming[key] == null ||
        operationId(asObject(existing[key])) !== operationId(asObject(incoming[key])))) {
    conflict("Terminal result revalidation must retain the original settled effects");
  }
  if (retry.phase === "terminal_closeout") {
    const rejection = asObject(retry.rejection);
    if (rejection.ok !== false || !["checkpoint_read_context_stale", "delivery_validation_binding_mismatch"]
      .includes(String(rejection.error_code))) conflict("Terminal result revalidation requires a known result rejection");
    const original = prior.phase === "terminal_closeout" ? prior : {
      candidate: existing.host_result, original_context: existing.result_work_context};
    if (["candidate", "original_context"].some(key =>
      operationId(asObject(retry[key])) !== operationId(asObject(original[key])))) {
      conflict("Terminal result revalidation must retain its original candidate and basis");
    }
    if (typeof asObject(retry.original_context).read_context_id !== "string" ||
        !String(asObject(retry.original_context).read_context_id)) {
      conflict("Terminal result revalidation requires its original work key");
    }
    const view = asObject(retry.validation);
    if (Object.keys(view).length) {
      const viewPhases = view.completed_phases;
      if (!Array.isArray(viewPhases) || viewPhases.length > 3 ||
          typeof asObject(view.work_context).read_context_id !== "string" ||
          !viewPhases.every((phase, index) => phase === transactionPhases[index]) ||
          !["in_progress", "failed"].includes(String(view.status)) ||
          ["writeback", "quota_spend", "terminal_closeout", "effect_attempts"].some(key => view[key] !== undefined) ||
          journalPhaseViolation(String(view.status), viewPhases as string[],
            typeof asObject(view.receipt).failed_phase === "string" ? String(asObject(view.receipt).failed_phase) : null)) {
        conflict("Terminal result revalidation may execute only Host and validation stages");
      }
    }
    if (["host_result", "task_validation"].some(key =>
      operationId(asObject(existing[key])) !== operationId(asObject(incoming[key]))) &&
        (!Array.isArray(view.completed_phases) || view.completed_phases.length !== 3 ||
          asObject(view.task_validation).ok !== true ||
          ["host_result", "task_validation"].some(key =>
            operationId(asObject(view[key])) !== operationId(asObject(incoming[key]))))) {
      // Clearing old validation when recording a rejection is allowed; publishing
      // a replacement candidate requires the completed validation view.
      if (incoming.task_validation !== undefined ||
          operationId(asObject(existing.host_result)) !== operationId(asObject(incoming.host_result))) {
        conflict("Terminal result revalidation cannot publish an unvalidated replacement");
      }
    }
  } else {
    const view = asObject(prior.validation);
    if (!Array.isArray(view.completed_phases) || view.completed_phases.length !== 3 ||
        asObject(view.task_validation).ok !== true || incoming.result_work_binding == null ||
        operationId(asObject(view.work_context)) !== operationId(asObject(incoming.result_work_context)) ||
        operationId(asObject(view.host_result)) !== operationId(asObject(incoming.host_result)) ||
        operationId(asObject(view.task_validation)) !== operationId(asObject(incoming.task_validation))) {
      conflict("Terminal result revalidation requires the newly validated candidate binding");
    }
  }
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
  const receipt = asObject(journal.receipt);
  const failedPhase = typeof receipt.failed_phase === "string" ? receipt.failed_phase : null;
  const statusViolation = journalPhaseViolation(context.journal_status, context.completed_phases, failedPhase);
  if (statusViolation !== null) conflict(statusViolation);

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
  previousNumbers?: SourceNumbers,
  nextNumbers?: SourceNumbers,
): void {
  if (operationId(asObject(existing.plan), previousNumbers) !== operationId(asObject(incoming.plan), nextNumbers)) {
    conflict("Turn journal transaction plan is immutable", "journal_plan_conflict");
  }
  requireTerminalResultRevalidation(existing, incoming, previous, next);
  for (const key of ["completion_intent", "checkpoint_host_binding"]) {
    if (existing[key] !== undefined && operationId(asObject(existing[key])) !==
      operationId(asObject(incoming[key]))) {
      conflict(`Original Turn ${key} is immutable`);
    }
  }
  if (terminalStatuses.has(previous.status)) {
    const auditOnly =
      previous.status === next.status &&
      operationId(journalWithoutRecoveryAudit(existing, previousNumbers), previousNumbers) ===
        operationId(journalWithoutRecoveryAudit(incoming, nextNumbers), nextNumbers);
    if (auditOnly) return;
    conflict("Terminal Turn journal tombstones are immutable");
  }
  if (!statusTransitions[previous.status]?.has(next.status)) {
    conflict(`Illegal Turn journal status transition ${previous.status}->${next.status}`);
  }
  if (
    previous.status === next.status &&
    ["failed", "scheduler_action_required"].includes(previous.status) &&
    operationId(journalWithoutRecoveryAudit(existing, previousNumbers), previousNumbers) !==
      operationId(journalWithoutRecoveryAudit(incoming, nextNumbers), nextNumbers)
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
    !validationRetryRewind && !resultValidationInvalidated(existing, incoming, previous, next)
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
  const source = asObject(params.journal);
  const tokens = sourceNumberTokens(source, params.source_number_tokens);
  let originalJson: string | null = null;
  if (tokens.length) {
    const numbers = new WeakMap<object, Map<string, string>>();
    for (const {path, token} of tokens) {
      let parent: Record<string | number, unknown> = source;
      for (const key of path.slice(0, -1)) parent = parent[key] as typeof parent;
      const fields = numbers.get(parent) ?? new Map<string, string>();
      fields.set(String(path.at(-1)), token);
      numbers.set(parent, fields);
    }
    originalJson = JSON.stringify(source, function (key, value: unknown) {
      const token = numbers.get(this)?.get(key);
      return token === undefined ? value : rawNumber(token);
    }, 2);
  }
  // Validate and publish the same isolated snapshot when source spellings are
  // supplied. Native objects without metadata retain their historical path.
  const {journal, numbers} = originalJson === null
    ? {journal: source, numbers: undefined} : parseJournal(originalJson);
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
  const incomingOperationId = operationId(journal, numbers);
  const commit = async (): Promise<JsonObject> =>
    await withFileMutationLock(path, async () => {
      let existing: JsonObject | null = null;
      try {
        const encoded = await readFile(path, "utf8");
        const previous = parseJournal(encoded);
        existing = previous.journal;
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
        if (operationId(existing, previous.numbers) === incomingOperationId) {
          return {
            ok: true,
            appended: false,
            replayed: true,
            effect_id: incomingEffectId,
            operation_id: incomingOperationId,
          };
        }
        requireJournalTransition(existing, journal, existingState, incomingState, previous.numbers, numbers);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      }
      if (existing === null && (
        incomingState.status !== "in_progress" ||
        incomingState.completedPhases.length !== 0
      )) {
        conflict("A new Turn journal must begin in progress with no completed phases");
      }
      if (originalJson === null) await atomicWriteJson(path, journal);
      else await atomicWriteText(path, `${originalJson}\n`);
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

/** Reflect provider-verified settlement; never authorize another Host or debit. */
export function planSettledTurnJournal(existing: JsonObject, readback: JsonObject): JsonObject[] {
  const state = requireJournalState(existing);
  const plan = asObject(existing.plan);
  const parsed = settlementIdentityFromPlan(asObject(plan.transaction));
  if (parsed.failure !== null) conflict("Original journal settlement identity is unavailable");
  const expected = parsed.value;
  const identity = asObject(asObject(readback.identity).result).value;
  const decoded = settlementIdentityFromPlan({ settlement_plan: { identity } });
  if (readback.found !== true || decoded.failure !== null ||
      (["goal_id", "agent_id", "binding_kind", "binding_id", "turn_instance_id", "effect_id"] as const)
        .some(key => decoded.value[key] !== expected[key])) {
    conflict("Provider settlement does not match the original journal identity");
  }
  const terminal = asObject(existing.completion_intent).continuation === "no_followup";
  const settled = asObject(asObject(readback[terminal ? "terminal_settlement" : "settlement"]).payload);
  const required = ["validation", "durable_writeback", "quota_spend", ...(terminal ? ["terminal_closeout"] : [])];
  const receipts = settled.receipts;
  if (settled.ok !== true || !Array.isArray(receipts) || receipts.length !== required.length ||
      receipts.some((value, index) => {
        const receipt = asObject(value);
        return receipt.schema_version !== "quota_settlement_receipt_v1" || receipt.step_kind !== required[index] ||
          receipt.status !== "committed" || receipt.effect_id !== expected.effect_id ||
          typeof receipt.source_ref !== "string" || !receipt.source_ref.trim();
      }) || !readback.writeback_run || !readback.spend_run) {
    conflict("Original journal requires complete verified provider settlement receipts");
  }
  if (state.status === "committed") return [];
  const committedReceipts = receipts as JsonObject[];
  if (!["failed", "in_progress", "scheduler_action_required"].includes(state.status) ||
      state.completedPhases.length < 3 || state.completedPhases.length > 5 ||
      asObject(existing.task_validation).ok !== true ||
      !["validated_completion", "validated_progress"].includes(String(asObject(existing.host_result).result_kind)) ||
      existing.effect_attempts !== undefined || existing.source_effect_hold !== undefined ||
      asObject(plan.host).scheduler_owner !== "outer_controller") {
    conflict("Original journal is not eligible for receipt-only settlement reconciliation");
  }
  const steps: JsonObject[] = [];
  let current = structuredClone(existing);
  const audit = existing.settlement_reconciliation ?? {
    previous_receipt: existing.receipt, previous_failure: existing.settlement_result,
    previous_reason: existing.reason, receipts: committedReceipts,
  };
  function advance(count: number, status: string): void {
    const phases = transactionPhases.slice(0, count);
    const resultKind = asObject(existing.host_result).result_kind;
    const next: JsonObject = { ...current, status, completed_phases: phases, result_kind: resultKind,
      settlement_reconciliation: audit,
      receipt: { ...asObject(current.receipt), ok: true, status: count === 7 ? "committed" : "validated",
        result_kind: resultKind, completed_phases: phases, failed_phase: null,
        next_phase: transactionPhases[count] ?? null, settlement_effect_id: expected.effect_id,
        commit_eligibility: { writeback: true, quota_spend: count >= 4, scheduler_ack: count === 7 }, errors: [] } };
    if (count >= 4) next.writeback = readback.writeback_run;
    if (count >= 5) next.quota_spend = readback.spend_run;
    if (terminal && count >= 5) next.terminal_closeout = asObject(readback.terminal_closeout).payload ?? committedReceipts.at(-1);
    if (count === 7) {
      next.scheduler = { completed: true, acknowledged: false, disposition: "outer_controller" };
      next.settlement_result = settled;
      delete next.reason;
    }
    requireJournalTransition(current, next, requireJournalState(current), requireJournalState(next));
    current = next; steps.push(next);
  }
  if (state.status === "failed") advance(state.completedPhases.length, "in_progress");
  if (state.completedPhases.length < 4) advance(4, "in_progress");
  if (state.completedPhases.length < 5) advance(5, "in_progress");
  advance(7, "committed");
  return steps;
}

export async function reconcileTurnJournalSettlement(params: JsonObject): Promise<JsonObject> {
  const path = requiredString(params.path, "path");
  const runtimeRoot = requiredString(params.runtime_root, "runtime_root");
  if (!isAbsolute(path) || !isAbsolute(runtimeRoot)) throw new EffectRuntimeRequestError("Journal reconciliation paths must be absolute");
  const parsed = settlementIdentityFromPlan({ settlement_plan: { identity: params.settlement_identity } });
  if (parsed.failure !== null || parsed.value.binding_kind !== "todo") throw new EffectRuntimeRequestError("Journal reconciliation requires a typed Todo identity");
  const identity = parsed.value;
  const owner = parseQuotaAccountingOwner({ runtimeRoot, goalId: identity.goal_id,
    goalRefValue: params.goal_ref, sourceAdmissionValue: params.source_admission });
  return withQuotaAccountingOwner(owner, async () => withFileMutationLock(path, async () => {
    const journal = asObject(JSON.parse(await readFile(path, "utf8")));
    const original = settlementIdentityFromPlan(asObject(asObject(journal.plan).transaction));
    if (original.failure !== null || (["goal_id", "agent_id", "binding_kind", "binding_id", "turn_instance_id", "effect_id"] as const)
      .some(key => original.value[key] !== identity[key]) ||
      path !== join(runtimeRoot, "goals", identity.goal_id, "turns", `${String(journal.turn_key).slice(7)}.json`)) {
      conflict("Journal reconciliation address differs from its original identity");
    }
    const binding = parseTurnJournalGoalBinding(journal);
    if (binding.kind === "invalid") conflict("Journal reconciliation has invalid GoalRef binding");
    requireQuotaOwnerProjection(owner, binding.kind === "exact" ? binding.goal_ref : undefined, "Turn journal");
    const readback = readQuotaSettlementForAdmittedOwnerFromSnapshot({
      schema_version: "loopx_quota_settlement_readback_request_v0", runtime_root: runtimeRoot,
      goal_id: identity.goal_id, agent_id: identity.agent_id, todo_id: identity.todo_id,
      turn_instance_id: identity.turn_instance_id, replan_obligation_id: null,
      infer_turn_instance_id: false, allow_unbound_binding: false,
    }, owner, await readQuotaSettlementSnapshot(runtimeRoot, identity.goal_id));
    const steps = planSettledTurnJournal(journal, readback);
    for (const snapshot of steps) await atomicWriteJson(path, snapshot);
    return { ok: true, appended: steps.length > 0, replayed: steps.length === 0,
      effect_id: identity.effect_id, turn_key: journal.turn_key, status: "committed" };
  }));
}
