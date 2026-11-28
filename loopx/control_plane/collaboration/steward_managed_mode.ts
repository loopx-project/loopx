/** Source-bound managed Session identity and native host policy.
 *
 * Identity preparation grants no execution. Host policy maps only the original
 * already-admitted Task and existing approval. Goal bootstrap, registration,
 * scheduling and execution retain their owners; a prepared identity never
 * advertises a running monitor or enabled host.
 */
import { createHash } from "node:crypto";
import {posix, win32} from "node:path";
import type { JsonObject } from "../runtime_decode.ts";
import { jsonObject, requireJsonObject, requireNonEmptyString } from "../runtime_decode.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";
import {boundConversationExecutorMatches, goalAgentConversationIdentity, resolveConversationScope} from "./conversation_scope.ts";
import {readTurnSelectionRejection} from "../turn_driver/selection_rejection.ts";
import {EffectiveAction} from "../quota/effective_action.generated.ts";
import {projectTurnBoundary, turnPlanWorkSource} from "../quota/turn_envelope.ts";
import {projectGoalCreationExecutionRequest} from "../goals/creation_execution.ts";

/** A stopped observation may get a new Chat carrier for the same native
 * identity. After a recovery starts, only another proven process restart or a
 * cooldown-bounded pre-poll transient failure can continue it. The host reads
 * the stopped worker lock, journals and native poll receipts. Normal admission
 * still checks ownership, deadline, permissions and user work. */
const WORKER_EXITED_ERROR =
  "The LoopX Chat execution worker exited before this turn finished. Your conversation is saved; execution may have occurred. This turn was not retried automatically.";
const FILE_LOCK_TIMEOUT_ERROR_PATTERN =
  /^file lock acquisition timed out \((?:recorded holder pid [1-9]\d*|lock busy, current holder unknown)\)$/;
const MONITOR_RECOVERY_RETRY_COOLDOWN_MS = 300_000;
const REPORT_VALIDATOR_PREFIX =
  "from pathlib import Path; from loopx.capabilities.periodic_report.private_generation import validate_private_generation; "
  + "validate_private_generation(Path(";

function sameReportValidatorCommand(frozenCmd: unknown, requestCmd: unknown): boolean {
  return Array.isArray(frozenCmd) && Array.isArray(requestCmd)
    && frozenCmd.length === 3 && requestCmd.length === 3
    && frozenCmd.every(part => typeof part === "string" && part.length > 0 && !part.includes("\0"))
    && requestCmd.every(part => typeof part === "string" && part.length > 0 && !part.includes("\0"))
    && frozenCmd[1] === "-c" && requestCmd[1] === "-c"
    && frozenCmd[2] === requestCmd[2]
    && frozenCmd[2].startsWith(REPORT_VALIDATOR_PREFIX)
    && frozenCmd[2].endsWith("))");
}

function sameReportRequestField(key: string, priorValue: unknown, requestValue: unknown): boolean {
  if (key === "validation_command" && sameReportValidatorCommand(priorValue, requestValue)) {
    return true;
  }
  return JSON.stringify(priorValue) === JSON.stringify(requestValue);
}

function sameReportRequest(priorValue: unknown, request: JsonObject): boolean {
  const prior = jsonObject(priorValue);
  if (!prior) return false;
  const keys = Object.keys(request);
  return Object.keys(prior).length === keys.length
    && keys.every(key => Object.hasOwn(prior, key) && sameReportRequestField(key, prior[key], request[key]));
}

function hasSingleManagerContextEvidence(basis: JsonObject, prior: JsonObject, events: unknown): boolean {
  if (requireJsonObject(prior.actor, "original watch actor").schema_version !== "loopx_steward_managed_actor_v0"
      || !Array.isArray(basis.contexts) || basis.contexts.length !== 1
      || !Array.isArray(basis.phases) || basis.phases.length !== 1
      || requireJsonObject(basis.phases[0], "original watch phase").phase !== "manager_context"
      || !Array.isArray(events)
      || events.filter(kind => kind === "agent.phase").length !== 1
      || events.filter(kind => kind === "manager.context").length !== 1) {
    return false;
  }
  return requireJsonObject(basis.contexts[0], "watch context").schema_version === "manager_turn_context_v1";
}

function admitMonitorRestartRecovery(
  params: JsonObject,
  request: JsonObject,
  session: JsonObject,
  legacySessionId?: string,
  queuedTurnCount?: unknown,
): void {
  if (request.monitor_restart_recovery === undefined) {
    if (params.monitor_restart_basis != null) throw new EffectRuntimeRequestError("watch recovery lacks its original request");
    return;
  }
  const recovery = requireJsonObject(request.monitor_restart_recovery, "original watch continuation");
  const basis = requireJsonObject(params.monitor_restart_basis, "original watch observation");
  const original = requireJsonObject(basis.original, "original stopped watch");
  const prior = requireJsonObject(original.loopx_request, "original watch request");
  const keys = Object.keys(request).filter(key => key !== "monitor_restart_recovery");
  const priorKeys = Object.keys(prior).filter(key => key !== "monitor_restart_recovery");
  const predecessor = prior.monitor_restart_recovery === undefined ? null :
    requireJsonObject(prior.monitor_restart_recovery, "previous watch continuation");
  const events = basis.event_kinds;
  // Compatibility with persisted Chat failures: the exact error alone is not
  // proof. Require the original collector's single, empty typed refusal too.
  // Fresh scope/provider checks below still decide the new observation.
  let scopeRefused = false;
  if (original.error_code === "runtime_error" && original.error === "the portfolio observation omitted authorized Goals"
      && requireJsonObject(prior.actor, "original watch actor").schema_version === "loopx_steward_managed_actor_v0"
      && Array.isArray(basis.contexts) && basis.contexts.length === 1) {
    const context = requireJsonObject(basis.contexts[0], "refused watch context");
    const coverage = requireJsonObject(context.coverage, "refused watch coverage");
    scopeRefused = context.schema_version === "manager_turn_context_v1"
      && Array.isArray(context.goals) && context.goals.length === 0
      && Array.isArray(context.warnings) && context.warnings.length === 1 && context.warnings[0] === "external_authorization_changed"
      && coverage.discovered === null && coverage.verified === 0 && coverage.complete === false
      && context.authorization_scope_id === undefined;
  }
  // A provider identity read may fail inside the original read-only context
  // collector, before it publishes any context. Retained legacy errors have no
  // typed failure code: require the collector's exact phase independently of
  // the error, plus the strict no-effect evidence below. Current binding and
  // provider verification still run before a new carrier can be accepted.
  const identityRefused = original.error_code === "runtime_error"
    && original.error === "the selected App identity could not be verified"
    && requireJsonObject(prior.actor, "original watch actor").schema_version === "loopx_steward_managed_actor_v0"
    && Array.isArray(basis.contexts) && basis.contexts.length === 0
    && Array.isArray(basis.phases) && basis.phases.length === 1
    && requireJsonObject(basis.phases[0], "original watch phase").phase === "manager_context"
    && Array.isArray(events) && events.filter(kind => kind === "agent.phase").length === 1;
  // Post-context monitor failures (external signal outage, daemon worker exit,
  // storage I/O or file lock timeout, or runtime unavailability) before any poll
  // receipt or upstream model turn may recover once immediately and retry only
  // after a bounded cooldown when strict single-context and zero-effect journal
  // proofs hold; pre-context unknown worker launches remain closed.
  const postContextWatchRecoverable = hasSingleManagerContextEvidence(basis, prior, events) && (
    original.error_code === "runtime_unavailable"
    || original.error_code === "lock_acquire_timeout"
    || original.error_code === "runtime_error" && (
      original.error === "steward monitor external signal source temporarily unavailable"
      || original.error === "the portfolio scope changed after observation"
      || original.error === "the monitor signal scope changed after observation"
      || original.error === "the monitor repository changed after observation"
      || original.error === "disk I/O error"
      || original.error === WORKER_EXITED_ERROR
      || (typeof original.error === "string" && FILE_LOCK_TIMEOUT_ERROR_PATTERN.test(original.error))
    )
  );
  const completedAtMs = typeof original.completed_at === "string" ? Date.parse(original.completed_at) : NaN;
  const observedAtMs = typeof basis.observed_at === "number" && Number.isFinite(basis.observed_at)
    ? basis.observed_at * 1000 : NaN;
  const chainedPostContextWatchRecoverable = postContextWatchRecoverable
    && Number.isFinite(completedAtMs) && Number.isFinite(observedAtMs)
    && observedAtMs - completedAtMs >= MONITOR_RECOVERY_RETRY_COOLDOWN_MS;
  const effectiveQueuedCount = queuedTurnCount !== undefined ? queuedTurnCount : params.queued_turn_count;
  const originalSessionMatches = original.session_id === session.session_id
    || (legacySessionId !== undefined && original.session_id === legacySessionId);
  if (Object.keys(recovery).join() !== "original_turn_id"
      || recovery.original_turn_id !== original.turn_id || typeof original.turn_id !== "string"
      || !/^[a-f0-9]{32}$/.test(original.turn_id) || !originalSessionMatches
      || original.status !== "failed"
      || !(original.error_code === "server_restarted" || scopeRefused || identityRefused || postContextWatchRecoverable)
      || original.origin !== "native_loopx" || original.loopx_execution !== true
      || original.response !== null || original.upstream_turn_id !== null || original.delta_count !== 0
      || prior.work_kind !== "monitor" || request.work_kind !== "monitor" || request.validation_command !== null
      || (predecessor !== null && (!(original.error_code === "server_restarted" || chainedPostContextWatchRecoverable)
        || Object.keys(predecessor).join() !== "original_turn_id"
        || typeof predecessor.original_turn_id !== "string" || !/^[a-f0-9]{32}$/.test(predecessor.original_turn_id)
        || predecessor.original_turn_id === original.turn_id))
      || basis.old_worker_excluded !== true || basis.poll_absent !== true || basis.lease_available !== true
      || !Array.isArray(events) || !events.includes("turn.failed")
      || events.some(kind => !["turn.queued", "agent.phase", "manager.context", "turn.failed"].includes(String(kind)))
      || priorKeys.length !== keys.length
      || keys.some(key => key !== "monitor_lease_version" && JSON.stringify(request[key]) !== JSON.stringify(prior[key]))
      || Number(effectiveQueuedCount) !== 0) {
    throw new EffectRuntimeRequestError("watch restart requires independent proof before any native observation");
  }
}

/** One new report Turn after a provider-proven untouched quota refusal.
 * This does not reopen the original Chat/kernel Turn or relax its recovery.
 * Native metadata and usable allowance are host observations, never model input.
 */
function admitReportQuotaRecovery(params: JsonObject, request: JsonObject, session: JsonObject): void {
  if (request.report_quota_recovery === undefined) {
    if (params.report_quota_basis != null) throw new EffectRuntimeRequestError("quota recovery lacks its original report");
    return;
  }
  const recovery = requireJsonObject(request.report_quota_recovery, "report quota continuation");
  const observation = requireJsonObject(recovery.native_observation, "native quota observation");
  const basis = requireJsonObject(params.report_quota_basis, "original quota failure basis");
  const original = requireJsonObject(basis.original, "original failed Chat Turn");
  const prior = requireJsonObject(original.loopx_request, "original report request");
  const result = requireJsonObject(requireJsonObject(original.response, "original response").native_loopx, "original failure");
  const failure = requireJsonObject(result.host_failure, "original host failure");
  const effects = requireJsonObject(result.effects, "original effects");
  const receipt = requireJsonObject(result.receipt, "original native receipt");
  const inspection = requireJsonObject(basis.inspection, "original native inspection");
  const recorded = requireJsonObject(inspection.recorded_effects, "original recorded effects");
  const actor = requireJsonObject(request.actor, "original report actor");
  const lineage = requireJsonObject(receipt.lineage, "original report lineage");
  const keys = Object.keys(request).filter(key => key !== "report_quota_recovery");
  const accepting = params.turn == null && params.report_settlement_recovery == null;
  const history = session.upstream_context_history ?? [];
  if (!Array.isArray(history)) throw new EffectRuntimeRequestError("invalid native context provenance");
  const nativeThreads = [session.upstream_thread_id, ...history.map(value => {
    const item = requireJsonObject(value, "native context provenance");
    if (item.manager_context_version !== null && (!Number.isSafeInteger(item.manager_context_version)
        || Number(item.manager_context_version) < 0)) {
      throw new EffectRuntimeRequestError("invalid native context provenance");
    }
    return requireNonEmptyString(item.thread_id, "original native context thread");
  })];
  if (request.work_kind !== "report" || prior.work_kind !== "report"
      || prior.report_quota_recovery !== undefined || prior.report_timeout_recovery !== undefined
      || request.report_timeout_recovery !== undefined
      || request.resume_turn_key !== undefined || prior.resume_turn_key !== undefined
      || Object.keys(recovery).sort().join() !== "native_observation,original_turn_id"
      || typeof original.turn_id !== "string" || !/^[a-f0-9]{32}$/.test(original.turn_id)
      || recovery.original_turn_id !== original.turn_id || original.session_id !== session.session_id
      || original.origin !== "native_loopx" || original.loopx_execution !== true || original.status !== "completed"
      || original.delta_count !== 0 || typeof original.upstream_turn_id !== "string" || !original.upstream_turn_id
      || Object.keys(prior).length !== keys.length
      || keys.some(key => !Object.hasOwn(prior, key) || (key === "turn_instance_id"
        ? request[key] !== String(prior[key]) + ".quota-recovery"
        : !sameReportRequestField(key, prior[key], request[key])))
      || result.schema_version !== "loopx_turn_execution_v0" || result.ok !== false || result.mode !== "run_once"
      || result.status !== "failed" || result.result_kind !== "host_failure"
      || failure.schema_version !== "loopx_turn_host_failure_v0" || failure.attempt !== 1
      || effects.host_invoked !== true
      || ["state_written", "quota_spent", "scheduler_acknowledged"].some(key => effects[key] !== false || recorded[key] !== false)
      || receipt.ok !== true || receipt.failed_phase !== "host_execute" || receipt.status !== "failed"
      || JSON.stringify(receipt.completed_phases) !== "[]" || JSON.stringify(inspection.completed_phases) !== "[]"
      || lineage.goal_id !== actor.goal_id || lineage.agent_id !== actor.agent_id || lineage.todo_id !== request.todo_id
      || inspection.schema_version !== "loopx_turn_journal_inspection_v1" || inspection.ok !== true
      || ["goal_matches", "owner_matches", "turn_key_matches", "journal_consistent"].some(key => inspection[key] !== true)
      || inspection.journal_status !== "failed"
      || ![null, true].includes(recorded.host_invoked as null | boolean)
      || !nativeThreads.includes(observation.thread_id) || typeof observation.thread_id !== "string" || !observation.thread_id
      || observation.turn_id !== original.upstream_turn_id || observation.only_original_user_item !== true
      || observation.codex_error_info !== "usageLimitExceeded" || observation.quota_available !== true
      || typeof observation.quota_observed_at !== "number" || !Number.isFinite(observation.quota_observed_at)
      || typeof basis.observed_at !== "number" || !Number.isFinite(basis.observed_at)
      || accepting && (basis.generation_present !== false
        || basis.observed_at < observation.quota_observed_at || basis.observed_at - observation.quota_observed_at > 60)) {
    throw new EffectRuntimeRequestError("report continuation requires its original native user-only quota refusal and current usable allowance");
  }
}

/** One new report Turn in an isolated cascade after an untouched Antigravity timeout.
 * Preserves the failed Turn and its immutable artifacts without rewriting or resuming it.
 */
function admitReportTimeoutRecovery(params: JsonObject, request: JsonObject, session: JsonObject): void {
  if (request.report_timeout_recovery === undefined) {
    if (params.report_timeout_basis != null) throw new EffectRuntimeRequestError("timeout recovery lacks its original report");
    return;
  }
  if (request.report_quota_recovery !== undefined) {
    throw new EffectRuntimeRequestError("report continuation cannot combine quota and timeout recovery");
  }
  const recovery = requireJsonObject(request.report_timeout_recovery, "report timeout continuation");
  const observation = requireJsonObject(recovery.native_observation, "native timeout observation");
  const basis = requireJsonObject(params.report_timeout_basis, "original timeout failure basis");
  const original = requireJsonObject(basis.original, "original failed Chat Turn");
  const prior = requireJsonObject(original.loopx_request, "original report request");
  const result = requireJsonObject(requireJsonObject(original.response, "original response").native_loopx, "original failure");
  const failure = requireJsonObject(result.host_failure, "original host failure");
  const effects = requireJsonObject(result.effects, "original effects");
  const receipt = requireJsonObject(result.receipt, "original native receipt");
  const inspection = requireJsonObject(basis.inspection, "original native inspection");
  const recorded = requireJsonObject(inspection.recorded_effects, "original recorded effects");
  const actor = requireJsonObject(request.actor, "original report actor");
  const lineage = requireJsonObject(receipt.lineage, "original report lineage");
  const keys = Object.keys(request).filter(key => key !== "report_timeout_recovery");
  const accepting = params.turn == null && params.report_settlement_recovery == null;
  if (request.work_kind !== "report" || prior.work_kind !== "report"
      || prior.report_timeout_recovery !== undefined || prior.report_quota_recovery !== undefined
      || request.resume_turn_key !== undefined || prior.resume_turn_key !== undefined
      || Object.keys(recovery).sort().join() !== "native_observation,original_turn_id"
      || typeof original.turn_id !== "string" || !/^[a-f0-9]{32}$/.test(original.turn_id)
      || recovery.original_turn_id !== original.turn_id || original.session_id !== session.session_id
      || original.origin !== "native_loopx" || original.loopx_execution !== true || original.status !== "completed"
      || original.delta_count !== 0 || typeof original.upstream_turn_id !== "string" || !original.upstream_turn_id
      || Object.keys(prior).length !== keys.length
      || keys.some(key => !Object.hasOwn(prior, key) || (key === "turn_instance_id"
        ? request[key] !== String(prior[key]) + ".timeout-recovery"
        : !sameReportRequestField(key, prior[key], request[key])))
      || result.schema_version !== "loopx_turn_execution_v0" || result.ok !== false || result.mode !== "run_once"
      || result.status !== "failed" || result.result_kind !== "host_failure"
      || failure.schema_version !== "loopx_turn_host_failure_v0" || failure.attempt !== 1
      || effects.host_invoked !== true
      || ["state_written", "quota_spent", "scheduler_acknowledged"].some(key => effects[key] !== false || recorded[key] !== false)
      || receipt.ok !== true || receipt.failed_phase !== "host_execute" || receipt.status !== "failed"
      || JSON.stringify(receipt.completed_phases) !== "[]" || JSON.stringify(inspection.completed_phases) !== "[]"
      || lineage.goal_id !== actor.goal_id || lineage.agent_id !== actor.agent_id || lineage.todo_id !== request.todo_id
      || inspection.schema_version !== "loopx_turn_journal_inspection_v1" || inspection.ok !== true
      || ["goal_matches", "owner_matches", "turn_key_matches", "journal_consistent"].some(key => inspection[key] !== true)
      || inspection.journal_status !== "failed"
      || ![null, true].includes(recorded.host_invoked as null | boolean)
      || observation.adapter_kind !== "antigravity_ls"
      || typeof observation.thread_id !== "string" || !observation.thread_id
      || observation.turn_id !== original.upstream_turn_id
      || observation.cascade_idle !== true || observation.no_final_response !== true
      || observation.native_timeout !== true || observation.quota_available !== true
      || typeof observation.quota_observed_at !== "number" || !Number.isFinite(observation.quota_observed_at)
      || typeof basis.observed_at !== "number" || !Number.isFinite(basis.observed_at)
      || accepting && (basis.generation_present !== false
        || basis.observed_at < observation.quota_observed_at || basis.observed_at - observation.quota_observed_at > 60)) {
    throw new EffectRuntimeRequestError("report timeout continuation requires its original idle Antigravity timeout observation and current usable allowance");
  }
}

/** A native refusal before execution can owe governance, never a business replay. */
function admitReportSelectionRecovery(params: JsonObject, request: JsonObject, sessionId: unknown): void {
  if (params.report_selection_recovery == null) return;
  if (request.work_kind !== "report")
    throw new EffectRuntimeRequestError("selection recovery belongs only to its original report");
  const proof = requireJsonObject(params.report_selection_recovery, "original selection refusal");
  const original = requireJsonObject(proof.original, "original refused Chat Turn");
  const prior = requireJsonObject(original.loopx_request, "original refused request");
  const result = requireJsonObject(requireJsonObject(original.response, "original response").native_loopx, "native refusal");
  const refusal = readTurnSelectionRejection(result.selection_rejection, request.todo_id);
  const effects = requireJsonObject(result.effects, "original invocation effects");
  if (original.session_id !== sessionId || original.origin !== "native_loopx"
      || original.loopx_execution !== true || original.status !== "completed"
      || original.delta_count !== 0 || original.upstream_turn_id !== null
      || !sameReportRequest(prior, request)
      || result.schema_version !== "loopx_turn_execution_v0" || result.ok !== false
      || result.mode !== "run_once" || result.effects_scope !== "current_invocation"
      || result.error_code !== "turn_todo_selection_deferred" || result.resume_turn_key != null
      || refusal.state !== "deferred" || refusal.reason_code !== "autonomous_replan"
      || refusal.recovery_action !== "reenter_guard_without_selection"
      || refusal.status_health_ok !== true || refusal.contract_error_count !== 0
      || !(refusal.delivery_preemptions as unknown[]).includes("autonomous_replan")
      || Object.keys(effects).length !== 4
      || ["host_invoked", "state_written", "quota_spent", "scheduler_acknowledged"].some(key => effects[key] !== false)
      || proof.generation_present !== false) {
    throw new EffectRuntimeRequestError("report selection recovery requires the original proven pre-execution replan refusal");
  }
  if (proof.probe == null) return;
  const probe = requireJsonObject(proof.probe, "original host admission probe");
  const observed = requireJsonObject(probe.turn, "original probe Chat Turn");
  const journal = requireJsonObject(probe.journal, "canonical probe journal");
  const inspection = requireJsonObject(probe.inspection, "canonical probe inspection");
  const recovery = requireJsonObject(inspection.recovery_decision, "probe recovery decision");
  const recorded = requireJsonObject(inspection.recorded_effects, "probe effects");
  const plan = requireJsonObject(journal.plan, "probe plan");
  const route = requireJsonObject(plan.route, "probe route");
  const envelope = turnPlanWorkSource(plan, probe.source_number_tokens).work;
  const identity = requireJsonObject(requireJsonObject(requireJsonObject(plan.transaction, "probe transaction")
    .settlement_plan, "probe settlement").identity, "probe identity");
  const host = requireJsonObject(journal.host, "probe host");
  const actor = requireJsonObject(request.actor, "report actor");
  const runningHere = params.turn != null && requireJsonObject(params.turn, "current Chat Turn").turn_id === observed.turn_id
    && ["starting", "running"].includes(String(observed.status));
  const commonInvalid = observed.session_id !== sessionId || typeof observed.turn_id !== "string"
    || !/^[a-f0-9]{32}$/.test(observed.turn_id) || observed.turn_id === original.turn_id
    || observed.origin !== "native_loopx" || observed.loopx_execution !== true
    || observed.error_code != null || observed.error != null
    || !sameReportRequest(observed.loopx_request, request)
    || !(runningHere || observed.status === "completed"
      && JSON.stringify(requireJsonObject(observed.response, "probe response").native_loopx) === JSON.stringify(result))
    || journal.schema_version !== "loopx_turn_journal_v0" || !ENVELOPED_SHA256_PATTERN.test(String(journal.turn_key))
    || journal.goal_id !== envelope.goal_id || journal.host_attempt_count !== 1
    || host.kind !== "codex-app-server" || host.executable !== "built-in"
    || inspection.schema_version !== "loopx_turn_journal_inspection_v1" || inspection.ok !== true
    || inspection.goal_matches !== true || inspection.owner_matches !== true || inspection.turn_key_matches !== true
    || inspection.journal_consistent !== true
    || recovery.action !== "return_existing" || recovery.reinvoke_host !== false
    || recorded.host_invoked !== true
    || ["state_written", "quota_spent", "scheduler_acknowledged"].some(key => recorded[key] !== false)
    || identity.goal_id !== envelope.goal_id || identity.agent_id !== envelope.agent_id
    || identity.goal_id !== actor.goal_id || identity.agent_id !== actor.agent_id
    || identity.turn_instance_id !== request.turn_instance_id + ".replan";
  if (commonInvalid) {
    throw new EffectRuntimeRequestError("report continuation requires its canonical host-only admission stop; retain all model or unknown work");
  }
  if (journal.status === "failed" && journal.result_kind === "validation_failed"
      && (journal.validation_stage === "host_result_contract"
        || journal.validation_stage === "task_postcondition")) {
    // Only once the blocking autonomous replan obligation is cleared elsewhere
    // may a report whose prior governance probe failed pre-commit validation
    // resume cleanly via one bounded .admission-resume carrier.
    const receipt = requireJsonObject(journal.receipt, "probe receipt");
    const eligibility = requireJsonObject(receipt.commit_eligibility, "probe commit eligibility");
    const guard = requireJsonObject(probe.current_guard, "current guard observation");
    if (inspection.journal_status !== "failed"
        || JSON.stringify(journal.completed_phases) !== JSON.stringify(["host_execute", "typed_result"])
        || receipt.ok !== true || receipt.failed_phase !== "validation" || receipt.status !== "failed"
        || ["writeback", "quota_spend", "scheduler_ack"].some(key => eligibility[key] !== false)
        || route.kind !== "replan_required"
        || route.effective_action !== EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED
        || envelope.effective_action !== EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED
        || identity.binding_kind !== "autonomous_replan"
        || guard.effective_action !== EffectiveAction.NORMAL_RUN
        || guard.replan_action_packet != null) {
      throw new EffectRuntimeRequestError("report continuation after validation failure requires a zero-write governance refusal and cleared replan");
    }
    return;
  }
  // The original host returned before calling its model when a fresh guard
  // no longer owed governance. Absence of output alone cannot prove that.
  const action = requireJsonObject(envelope.action, "probe action");
  const selected = requireJsonObject(action.selected_todo, "probe selected task");
  const stopped = requireJsonObject(journal.host_result, "probe host result");
  if (observed.delta_count !== 0 || observed.upstream_turn_id !== null
      || journal.status !== "stopped" || journal.result_kind !== "wait"
      || JSON.stringify(journal.completed_phases) !== JSON.stringify(["host_execute", "typed_result", "validation"])
      || stopped.schema_version !== "loopx_turn_result_v0" || stopped.turn_key !== journal.turn_key
      || stopped.result_kind !== "wait" || stopped.classification !== "report_replan_not_admitted"
      || inspection.journal_status !== "stopped"
      || route.kind !== "ready_for_host" || route.effective_action !== EffectiveAction.NORMAL_RUN
      || envelope.effective_action !== EffectiveAction.NORMAL_RUN || envelope.replan_action_packet != null
      || action.must_attempt !== true || action.delivery_allowed !== true || selected.todo_id !== identity.todo_id
      || typeof identity.todo_id !== "string" || !/^todo_[A-Za-z0-9_-]{1,100}$/.test(identity.todo_id)) {
    throw new EffectRuntimeRequestError("report continuation requires its canonical host-only admission stop; retain all model or unknown work");
  }
}

/** Registration and current Goal lifetime are observed by the host, never by
 * a message or the selected executor/model. This creates no scheduling grant. */
export function admitManagedSessionIdentity(params: JsonObject): JsonObject {
  const goal = requireJsonObject(params.goal, "original registered Goal");
  let identity: JsonObject;
  try {
    identity = goalAgentConversationIdentity({goal_id: goal.goal_id, managed_agent_id: params.managed_agent_id});
  } catch {
    throw new EffectRuntimeRequestError("select an exact registered Goal and Agent");
  }
  if (params.goal_active !== true || !Array.isArray(goal.registered_agents)
      || !goal.registered_agents.includes(identity.managed_agent_id)
      || goal.goal_instance_id != null && (typeof goal.goal_instance_id !== "string"
        || !/^[A-Za-z0-9._-]{1,160}$/.test(goal.goal_instance_id))
      || params.workspace_path !== goal.workspace_path) {
    throw new EffectRuntimeRequestError("the managed Agent must belong to the active local Goal and workspace");
  }
  if (params.session != null) {
    const session = requireJsonObject(params.session, "original managed Session");
    if (resolveConversationScope(session).kind !== "owner_goal"
        || session.project_context != null || session.steward_context != null
        || session.session_mode !== "managed_runtime" || session.agent_id !== "codex" || session.executor_endpoint_id !== "codex"
        || session.adapter_kind !== "codex_app_server" || session.upstream_mode === "goal"
        || session.status === "closed" || session.goal_id !== goal.goal_id
        || (session.goal_instance_id ?? null) !== goal.goal_instance_id
        || session.managed_agent_id !== identity.managed_agent_id
        || session.managed_workspace_path !== goal.workspace_path || session.channel_id !== identity.channel_id) {
      throw new EffectRuntimeRequestError("the original managed Agent Session identity is unavailable");
    }
  }
  return {...identity, goal_instance_id: goal.goal_instance_id ?? null};
}

/** Local owner Sessions reuse the same governed Turn host. Registration is a
 * selection, not a grant: the native Turn owner still admits the actual work.
 * A bound steward keeps its stricter provider/source admission below.
 */
export function admitManagedSessionTurn(params: JsonObject): JsonObject {
  const session = requireJsonObject(params.session, "managed Session");
  const goal = requireJsonObject(params.goal, "original Goal");
  const request = requireJsonObject(params.request, "native Turn request");
  const actor = requireJsonObject(request.actor, "managed Session actor");
  const turn = params.turn == null ? null : requireJsonObject(params.turn, "current Chat Turn");
  admitManagedSessionIdentity({...params, managed_agent_id: actor.agent_id, workspace_path: goal.workspace_path});
  const fields = ["schema_version", "goal_id", "goal_instance_id", "agent_id", "session_id", "workspace_path"];
  if (Object.keys(actor).some(key => !fields.includes(key))
      || actor.schema_version !== "loopx_managed_session_actor_v0"
      || actor.goal_id !== goal.goal_id || actor.goal_instance_id !== goal.goal_instance_id
      || actor.workspace_path !== goal.workspace_path
      || actor.session_id !== session.session_id
      || typeof actor.agent_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(actor.agent_id)
      || typeof actor.session_id !== "string" || !/^[a-f0-9]{32}$/.test(actor.session_id)) {
    throw new EffectRuntimeRequestError("the native Turn must retain its original managed Session and Goal lifetime");
  }
  const observationOnly = request.work_kind === "monitor";
  const reportWork = request.work_kind === "report";
  const scheduledWork = request.work_kind === "scheduled_task";
  const requestFields = ["operation", "host", "actor", "turn_instance_id", "todo_id", "validation_command", "work_kind",
    ...(request.creation_request === undefined ? [] : ["creation_request"]),
    ...(observationOnly ? ["monitor_deadline", "monitor_lease_version", "monitor_restart_recovery"] : []),
    ...(reportWork ? ["report_window_id", "report_subscription_revision", "report_quota_recovery", "report_timeout_recovery"] : []),
    ...(scheduledWork ? ["schedule_window_id", "schedule_subscription_revision"] : [])];
  if (Object.keys(request).some(key => !requestFields.includes(key))
      || request.operation !== "native_turn" || request.host !== "codex-app-server" || !["native_turn", "monitor", "report", "scheduled_task"].includes(String(request.work_kind))
      || request.validation_command !== null
      || typeof request.turn_instance_id !== "string" || !/^[A-Za-z0-9._:-]{1,160}$/.test(request.turn_instance_id)
      || request.todo_id !== null && (typeof request.todo_id !== "string" || !/^todo_[A-Za-z0-9_-]{1,100}$/.test(request.todo_id))) {
    throw new EffectRuntimeRequestError("managed work uses the native task's existing validation, without caller commands");
  }
  if (request.creation_request !== undefined) {
    const original = projectGoalCreationExecutionRequest({proposal: params.creation_proposal,
      session, goal, current_original_task: params.current_original_task});
    const retained = requireJsonObject(request.creation_request, "original creation execution request");
    if (request.work_kind !== "native_turn" || original === null
        || Object.keys(retained).length !== Object.keys(original).length
        || Object.entries(original).some(([key, value]) => JSON.stringify(retained[key]) !== JSON.stringify(value))
        || request.todo_id !== original.todo_id
        || request.turn_instance_id !== `managed-session.${original.operation_id}`) {
      throw new EffectRuntimeRequestError("native execution must retain its exact confirmed original creation request");
    }
  }
  if (observationOnly) {
    const monitor = requireJsonObject(params.monitor_todo, "original managed Session monitor");
    if (monitor.target_key !== `managed-session-watch.${session.session_id}`
        || monitor.task_class !== "continuous_monitor" || monitor.action_kind !== "local_runtime_observe"
        || monitor.claimed_by !== actor.agent_id || ![true, "true"].includes(monitor.watch_only as boolean | string)
        || monitor.status !== "open" || monitor.todo_id !== request.todo_id
        || typeof request.monitor_deadline !== "string" || request.monitor_deadline !== monitor.next_due_at
        || !Number.isSafeInteger(request.monitor_lease_version) || Number(request.monitor_lease_version) < 0) {
      throw new EffectRuntimeRequestError("a local watch must retain its original Agent, Session, Todo and deadline");
    }
  }
  admitMonitorRestartRecovery(params, request, session);
  if (reportWork) {
    const window = requireJsonObject(params.report_window, "original report window");
    const subscription = requireJsonObject(params.report_subscription, "current report subscription");
    const todo = requireJsonObject(params.report_todo, "original report Todo");
    const recovery = params.report_settlement_recovery == null ? null
      : requireJsonObject(params.report_settlement_recovery, "native original report recovery");
    const settlementOnly = reportSettlementOnly(params, request, todo);
    if (subscription.enabled !== true || subscription.profile_preset !== "weekly-progress"
        || subscription.route_ref !== `managed-session:${session.session_id}`
        || subscription.effective_revision !== request.report_subscription_revision
        || window.subscription_revision !== request.report_subscription_revision
        || window.window_id !== request.report_window_id || window.goal_id !== actor.goal_id
        || window.agent_id !== actor.agent_id || todo.todo_id !== request.todo_id
        || todo.target_key !== window.window_id || todo.claimed_by !== actor.agent_id
        || todo.action_kind !== "generate_periodic_report" || !(todo.status === "open" && !recovery || settlementOnly)) {
      throw new EffectRuntimeRequestError("report execution requires the original Session subscription, window and task");
    }
  } else if (params.report_settlement_recovery != null || params.report_frozen_contract_recovery != null) {
    throw new EffectRuntimeRequestError("report recovery belongs only to its original calendar report");
  }
  if (scheduledWork) {
    const basis = requireJsonObject(params.scheduled_task, "original scheduled task");
    const subscription = requireJsonObject(basis.subscription, "current task subscription");
    const window = requireJsonObject(basis.window, "original calendar window");
    const todo = requireJsonObject(basis.todo, "original finite task");
    const frozen = requireJsonObject(basis.definition, "original task definition");
    const originalActor = requireJsonObject(frozen.actor, "original task actor");
    const task = requireJsonObject(subscription.task, "configured task");
    const frozenTask = requireJsonObject(frozen.task, "frozen task");
    const sourceTask = requireJsonObject(basis.source_task, "current completed source Task");
    const originalSource = requireJsonObject(frozen.source_task, "original source Task");
    const inherited = ["action_kind", "task_domain", "task_repository", "capability_binding_ref", "priority",
      "required_write_scopes", "required_capabilities", "target_capabilities", "required_decision_scopes", "excluded_agents"];
    const originalFields = [...inherited, "todo_id", "text", "claimed_by", "status", "task_class",
      "completion_validation_sha256", "completion_validation_revision", "completion_turn_key"];
    if (subscription.enabled !== true || subscription.source !== "goal_override"
        || subscription.profile_preset !== "session-task" || subscription.route_ref !== `managed-session:${session.session_id}`
        || subscription.effective_revision !== request.schedule_subscription_revision
        || window.subscription_revision !== request.schedule_subscription_revision
        || window.window_id !== request.schedule_window_id || window.goal_id !== actor.goal_id || window.agent_id !== actor.agent_id
        || fields.some(key => originalActor[key] !== actor[key])
        || frozenTask.source_todo_id !== task.source_todo_id || task.source_todo_id !== sourceTask.todo_id
        || originalFields.some(key => JSON.stringify(sourceTask[key] ?? null) !== JSON.stringify(originalSource[key] ?? null))
        || sourceTask.claimed_by !== actor.agent_id || sourceTask.status !== "done"
        || sourceTask.task_class !== "advancement_task" || sourceTask.completion_validation_required !== true
        || sourceTask.action_kind === "generate_periodic_report" || /^cadence_[a-f0-9]{64}$/.test(String(sourceTask.target_key ?? ""))
        || todo.todo_id !== request.todo_id || todo.target_key !== window.window_id
        || todo.claimed_by !== actor.agent_id || todo.task_class !== "advancement_task"
        || todo.status !== "open" || todo.text !== frozen.todo_text
        || ["action_kind", "task_domain", "task_repository", "capability_binding_ref", "priority"].some(key => todo[key] !== sourceTask[key])
        || ["required_write_scopes", "required_capabilities", "target_capabilities", "required_decision_scopes", "excluded_agents"]
          .some(key => JSON.stringify(todo[key] ?? []) !== JSON.stringify(sourceTask[key] ?? []))
        || todo.completion_validation_required !== true) {
      throw new EffectRuntimeRequestError("scheduled work requires its original explicit task, validation, Session and calendar window");
    }
  }
  const creation = jsonObject(request.creation_request);
  const queuedCreation = turn !== null && creation !== null && turn.status === "queued"
    && turn.started_at === null && turn.upstream_turn_id === null && turn.delta_count === 0
    && !Object.hasOwn(turn, "_acceptance")
    && turn.client_turn_id === `managed.${creation.operation_id}`;
  if (turn ? turn.session_id !== session.session_id || turn.turn_id !== session.active_turn_id
      || !(["starting", "running"].includes(String(turn.status)) || queuedCreation) || turn.origin !== "native_loopx"
      || turn.loopx_execution !== true || JSON.stringify(turn.loopx_request) !== JSON.stringify(request)
    : session.active_turn_id || params.queued_turn_count !== 0) {
    throw new EffectRuntimeRequestError("the original conversation is busy or no longer owns this Turn; retain its current work");
  }
  admitReportSelectionRecovery(params, request, session.session_id);
  admitReportQuotaRecovery(params, request, session);
  admitReportTimeoutRecovery(params, request, session);
  return {actor, workspace_path: goal.workspace_path, host: "codex-app-server", observation_only: observationOnly};
}

/** Both original conversation hosts use the native journal's no-model recovery. */
function reportSettlementOnly(params: JsonObject, request: JsonObject, todo: JsonObject): boolean {
  if (params.report_settlement_recovery == null) {
    if (params.report_frozen_contract_recovery != null) {
      throw new EffectRuntimeRequestError("original report recovery requires validated remaining effects without a model");
    }
    return false;
  }
  const recovery = requireJsonObject(params.report_settlement_recovery, "native original report recovery");
  const decision = requireJsonObject(recovery.recovery_decision, "native journal recovery decision");
  if (params.report_frozen_contract_recovery != null) {
    const frozen = requireJsonObject(params.report_frozen_contract_recovery, "frozen report contract recovery");
    const effects = requireJsonObject(recovery.recorded_effects, "native recorded effects");
    const commonProof = recovery.schema_version === "loopx_turn_journal_inspection_v1" && recovery.ok === true
      && recovery.goal_matches === true && recovery.owner_matches === true && recovery.turn_key_matches === true
      && recovery.journal_consistent === true && todo.status === "open"
      && effects.host_invoked === true && effects.state_written === false
      && effects.quota_spent === false && effects.scheduler_acknowledged === false
      && typeof frozen.turn_key === "string" && ENVELOPED_SHA256_PATTERN.test(frozen.turn_key)
      && frozen.host_attempt_count === 1
      && frozen.generation_verified === true && frozen.saved_candidate_verified === true;
    const acceptedFrozen = commonProof && recovery.journal_status === "failed"
      && Array.isArray(recovery.completed_phases) && recovery.completed_phases.length === 2
      && recovery.completed_phases[0] === "host_execute" && recovery.completed_phases[1] === "typed_result"
      && decision.action === "continue" && decision.resume_from === "host_execute" && decision.reinvoke_host === true
      && frozen.result_kind === "validation_failed" && frozen.validation_stage === "host_result_contract";
    const acceptedStoppedCandidate = commonProof && recovery.journal_status === "stopped"
      && Array.isArray(recovery.completed_phases) && recovery.completed_phases.length === 3
      && recovery.completed_phases[0] === "host_execute" && recovery.completed_phases[1] === "typed_result"
      && recovery.completed_phases[2] === "validation"
      && decision.action === "return_existing" && decision.resume_from === null && decision.reinvoke_host === false
      && frozen.result_kind === "wait" && frozen.classification === "no_typed_host_result"
      && !Object.hasOwn(frozen, "validation_stage");
    if ((!acceptedFrozen && !acceptedStoppedCandidate) || params.preparing_model_context === true) {
      throw new EffectRuntimeRequestError("original report recovery requires validated remaining effects without a model");
    }
    return true;
  }
  const cachedGeneration = todo.status === "open" && recovery.journal_status === "failed"
    && decision.action === "continue" && decision.resume_from === "validation"
    && Array.isArray(recovery.completed_phases) && recovery.completed_phases.length === 2
    && recovery.completed_phases[0] === "host_execute" && recovery.completed_phases[1] === "typed_result";
  const completedGeneration = todo.status === "done"
    && [request.turn_instance_id, `${String(request.turn_instance_id)}.saved-candidate`].includes(String(todo.completion_turn_key))
    && (decision.action === "continue" && ["durable_writeback", "quota_spend", "scheduler_apply", "scheduler_ack"].includes(String(decision.resume_from))
      || decision.action === "return_existing" && recovery.journal_status === "committed");
  const accepted = recovery.schema_version === "loopx_turn_journal_inspection_v1" && recovery.ok === true
    && recovery.goal_matches === true && recovery.owner_matches === true && recovery.turn_key_matches === true
    && recovery.journal_consistent === true && decision.reinvoke_host === false
    && (cachedGeneration || completedGeneration);
  if (!accepted || params.preparing_model_context === true) {
    throw new EffectRuntimeRequestError("original report recovery requires validated remaining effects without a model");
  }
  return true;
}

export const STEWARD_MANAGED_ACTOR_SCHEMA = "loopx_steward_managed_actor_v0";
export const STEWARD_BACKGROUND_SESSION_PREFIX = "steward.background.";
export const STEWARD_BACKGROUND_CHANNEL_PREFIX = "manager.external.steward.background.";
const AGENT_ID = "steward";

function text(value: unknown, name: string, expression?: RegExp): string {
  const result = requireNonEmptyString(value, name);
  if (result.length > 200 || (expression && !expression.test(result))) {
    throw new EffectRuntimeRequestError(`invalid ${name}`);
  }
  return result;
}

export function stewardBackgroundSessionId(bindingId: string): string {
  const id = text(bindingId, "binding identity", /^[a-f0-9]{24}$/);
  return `${STEWARD_BACKGROUND_SESSION_PREFIX}${id}`;
}

function validateStewardBackgroundCarrier(carrierRaw: unknown, bindingId: string, parentContext: JsonObject): JsonObject {
  const carrier = requireJsonObject(carrierRaw, "steward background carrier Session");
  const bgContext = requireJsonObject(carrier.steward_background_context, "steward background carrier authority");
  if (carrier.session_id !== stewardBackgroundSessionId(bindingId)
      || carrier.channel_id !== `${STEWARD_BACKGROUND_CHANNEL_PREFIX}${bindingId}`
      || carrier.goal_id !== "loopx-manager"
      || carrier.session_mode !== "managed_runtime"
      || carrier.status === "closed"
      || carrier.project_context != null
      || carrier.steward_context != null
      || carrier.managed_agent_id != null
      || bgContext.kind !== "bound_steward"
      || bgContext.audience !== "bound_owner"
      || bgContext.grant !== "portfolio_read"
      || (parentContext.kind !== undefined && bgContext.kind !== parentContext.kind)
      || (parentContext.audience !== undefined && bgContext.audience !== parentContext.audience)
      || (parentContext.grant !== undefined && bgContext.grant !== parentContext.grant)
      || ["binding_id", "source_ref", "provider_ref", "operator_ref", "project_ref"].some(
        key => bgContext[key] !== parentContext[key])) {
    throw new EffectRuntimeRequestError("the steward background watch carrier Session is invalid");
  }
  return carrier;
}

export function normalizeStewardManagedActor(value: unknown): JsonObject {
  const row = requireJsonObject(value, "managed steward identity");
  const fields = ["schema_version", "goal_id", "goal_instance_id", "agent_id", "session_id", "source_ref", "creation_operation_id"];
  if (Object.keys(row).some(key => !fields.includes(key)) || row.schema_version !== STEWARD_MANAGED_ACTOR_SCHEMA
      || row.agent_id !== AGENT_ID) throw new EffectRuntimeRequestError("invalid managed steward identity");
  return {schema_version: STEWARD_MANAGED_ACTOR_SCHEMA,
    goal_id: text(row.goal_id, "managed Goal", /^[a-z0-9][a-z0-9._-]{0,119}$/),
    // Ordinary registries have no source-session lifetime token. Preserve
    // null, as the existing native Goal/Agent binding contract does; never
    // fabricate a token. Source-session registries retain their exact token.
    goal_instance_id: row.goal_instance_id === null ? null : text(row.goal_instance_id, "managed Goal lifetime"), agent_id: AGENT_ID,
    session_id: text(row.session_id, "original steward Session", /^[a-f0-9]{32}$/),
    source_ref: text(row.source_ref, "original steward source", /^[a-f0-9]{24}$/),
    creation_operation_id: text(row.creation_operation_id, "managed Goal creation")};
}

/** Supported native host metadata, never authorization or a request to switch hosts. */
export function observeStewardNativeExecutor(params: JsonObject): JsonObject {
  const binding = requireJsonObject(params.binding, "steward binding");
  const session = requireJsonObject(params.session, "original steward Session");
  const supported = (session.agent_id === "codex" && (binding.executor_endpoint_id === "codex"
    || boundConversationExecutorMatches(binding, session)))
    || (binding.executor_endpoint_id === "codex" && session.agent_id === "antigravity"
      && boundConversationExecutorMatches(binding, session));
  return {execution_available: supported, ...(!supported ? {execution_error_code: "unsupported_executor"} : {})};
}

export function stewardManagedBasis(params: JsonObject, executing = false) {
  const binding = requireJsonObject(params.binding, "steward binding");
  const session = requireJsonObject(params.session, "original steward Session");
  const context = requireJsonObject(session.steward_context, "original steward authority");
  const observation = requireJsonObject(params.observation, "current provider observation");
  const id = text(binding.binding_id, "binding identity", /^[a-f0-9]{24}$/);
  const source = text(context.source_ref, "steward source", /^[a-f0-9]{24}$/);
  const hasCarrier = params.carrier_session != null;
  if (hasCarrier && jsonObject(params.request)?.work_kind !== "monitor") {
    throw new EffectRuntimeRequestError("steward background carrier is reserved for observation-only monitor work");
  }
  const carrier = hasCarrier ? validateStewardBackgroundCarrier(params.carrier_session, id, context) : null;
  if (binding.context_kind !== "steward" || binding.grant !== "portfolio_read" || binding.enabled !== true
      || binding.audience === "group" || observeStewardNativeExecutor(params).execution_available !== true
      || session.goal_id !== "loopx-manager"
      || session.session_mode === "attached_host" || session.status === "closed"
      || session.channel_id !== `manager.external.native.${id}.${source}`
      || !executing && !hasCarrier && (session.active_turn_id || params.queued_turn_count !== 0)) {
    throw new EffectRuntimeRequestError("prepare the original idle private steward, retaining its current work");
  }
  for (const key of ["binding_id", "provider_ref", "operator_ref", "project_ref"]) {
    if (context[key] !== binding[key]) throw new EffectRuntimeRequestError("steward authority changed");
  }
  if (observation.verified !== true || observation.transport_ref !== binding.transport_ref
      || observation.provider_ref !== binding.provider_ref || observation.operator_ref !== binding.operator_ref) {
    throw new EffectRuntimeRequestError("the original provider and owner must be verified");
  }
  const workspace = requireNonEmptyString(params.workspace_path, "private steward workspace");
  const sessionId = text(session.session_id, "original steward Session", /^[a-f0-9]{32}$/);
  const goalId = `steward-agent-${id}`;
  const creation = `steward-managed:${id}:${source}`;
  const goal = params.goal == null ? null : requireJsonObject(params.goal, "native steward Goal");
  if (goal && (goal.goal_id !== goalId || goal.workspace_path !== workspace || goal.creation_operation_id !== creation)) {
    throw new EffectRuntimeRequestError("the target Goal belongs to another creation or workspace");
  }
  const actor = binding.managed_actor == null ? null : normalizeStewardManagedActor(binding.managed_actor);
  if (actor && (!goal || actor.goal_id !== goalId || actor.session_id !== sessionId || actor.source_ref !== source
      || actor.creation_operation_id !== creation || actor.goal_instance_id !== goal.goal_instance_id
      || !Array.isArray(goal.registered_agents) || !goal.registered_agents.includes(AGENT_ID))) {
    throw new EffectRuntimeRequestError("the prepared steward identity or its original Goal lifetime is unavailable");
  }
  return {binding, session, id, source, workspace, sessionId, goalId, creation, goal, actor, carrier};
}

/** Bind an internal host dispatch to the prepared actor and original Core Turn.
 * This is conversation admission only; the native quota/Turn owner still
 * decides whether this exact work can run and commits its result.
 */
export function admitStewardManagedTurn(params: JsonObject): JsonObject {
  const turn = params.turn == null ? null : requireJsonObject(params.turn, "current Chat Turn");
  const current = stewardManagedBasis(params, turn !== null);
  if (!current.actor || !current.goal || params.goal_active !== true) {
    throw new EffectRuntimeRequestError("the original prepared steward Goal must be active");
  }
  const request = requireJsonObject(params.request, "native steward Turn request");
  const fields = ["operation", "host", "actor", "turn_instance_id", "todo_id", "validation_command", "work_kind",
    "monitor_deadline", "monitor_lease_version", "monitor_restart_recovery", "report_window_id", "report_subscription_revision", "resume_turn_key", "report_quota_recovery", "report_timeout_recovery"];
  if (Object.keys(request).some(key => !fields.includes(key))
      || request.operation !== "native_turn" || request.host !== "codex-app-server"
      || ![undefined, "native_turn", "monitor", "report"].includes(request.work_kind as string | undefined)
      || JSON.stringify(normalizeStewardManagedActor(request.actor)) !== JSON.stringify(current.actor)
      || typeof request.turn_instance_id !== "string" || !/^[A-Za-z0-9._:-]{1,160}$/.test(request.turn_instance_id)
      || request.todo_id !== null && (typeof request.todo_id !== "string" || !/^todo_[A-Za-z0-9_-]{1,100}$/.test(request.todo_id))) {
    throw new EffectRuntimeRequestError("native work must retain the exact prepared steward identity");
  }
  if (request.validation_command !== null && (!Array.isArray(request.validation_command)
      || request.validation_command.length < 1 || request.validation_command.length > 100
      || request.validation_command.some(value => typeof value !== "string" || !value || value.includes("\0") || value.length > 10000))) {
    throw new EffectRuntimeRequestError("the host's native validator must be a bounded argv");
  }
  admitReportSelectionRecovery(params, request, current.sessionId);
  admitReportQuotaRecovery(params, request, current.session);
  admitReportTimeoutRecovery(params, request, current.session);
  if (current.carrier !== null) {
    const rawCarrierQueued = params.carrier_queued_turn_count ?? 0;
    if (!Number.isSafeInteger(rawCarrierQueued) || Number(rawCarrierQueued) < 0) {
      throw new EffectRuntimeRequestError("invalid steward background carrier queue count");
    }
    const carrierQueuedCount = Number(rawCarrierQueued);
    admitMonitorRestartRecovery(params, request, current.carrier, current.sessionId, carrierQueuedCount);
    if (turn === null) {
      if (current.carrier.active_turn_id || carrierQueuedCount !== 0) {
        throw new EffectRuntimeRequestError("the steward background watch carrier is busy; retain its current observation");
      }
    } else if (turn.session_id !== current.carrier.session_id || turn.turn_id !== current.carrier.active_turn_id
        || !["starting", "running"].includes(String(turn.status)) || turn.origin !== "native_loopx"
        || turn.loopx_execution !== true || JSON.stringify(turn.loopx_request) !== JSON.stringify(request)) {
      throw new EffectRuntimeRequestError("the original native Chat Turn no longer owns execution");
    }
  } else {
    admitMonitorRestartRecovery(params, request, current.session);
    if (turn && (turn.session_id !== current.sessionId || turn.turn_id !== current.session.active_turn_id
        || !["starting", "running"].includes(String(turn.status)) || turn.origin !== "native_loopx"
        || turn.loopx_execution !== true || JSON.stringify(turn.loopx_request) !== JSON.stringify(request))) {
      throw new EffectRuntimeRequestError("the original native Chat Turn no longer owns execution");
    }
  }
  const monitor = params.monitor_todo == null ? null : requireJsonObject(params.monitor_todo, "native monitor");
  const observationOnly = request.work_kind === "monitor";
  if (monitor && !observationOnly) {
    throw new EffectRuntimeRequestError("a native watch must retain its no-model observation transport");
  }
  if (observationOnly && (!monitor || !isStewardNativeMonitorTodo(monitor)
      || monitor.todo_id !== request.todo_id || monitor.status !== "open"
      || typeof request.monitor_deadline !== "string" || request.monitor_deadline !== monitor.next_due_at
      || !Number.isSafeInteger(request.monitor_lease_version) || Number(request.monitor_lease_version) < 0)) {
    throw new EffectRuntimeRequestError("observation-only execution requires the original native watch Todo");
  }
  if (request.work_kind === "report") {
    const window = requireJsonObject(params.report_window, "original report window");
    const subscription = requireJsonObject(params.report_subscription, "current report subscription");
    const todo = requireJsonObject(params.report_todo, "original report Todo");
    const recovery = params.report_settlement_recovery == null ? null
      : requireJsonObject(params.report_settlement_recovery, "native original report recovery");
    const settlementOnly = reportSettlementOnly(params, request, todo);
    if (request.validation_command === null || subscription.enabled !== true || subscription.profile_preset !== "portfolio-progress"
        || subscription.route_ref !== `steward-private:${current.id}:${current.source}`
        || subscription.effective_revision !== request.report_subscription_revision
        || window.subscription_revision !== request.report_subscription_revision
        || window.window_id !== request.report_window_id || window.goal_id !== current.actor.goal_id
        || window.agent_id !== current.actor.agent_id || todo.todo_id !== request.todo_id
        || todo.target_key !== window.window_id || todo.claimed_by !== AGENT_ID
        || todo.action_kind !== "generate_periodic_report" || !(todo.status === "open" && !recovery || settlementOnly)) {
      throw new EffectRuntimeRequestError("report execution requires the original live subscription, window and task");
    }
    if (request.resume_turn_key !== undefined) {
      if (typeof request.resume_turn_key !== "string" || !ENVELOPED_SHA256_PATTERN.test(request.resume_turn_key)) {
        throw new EffectRuntimeRequestError("report recovery requires the original native Turn key");
      }
      // An already accepted worker retains its ordinary active-Turn fence.
      // Before acceptance, qualify one exact transport refusal against native
      // history. This code is emitted only before turn/start publication by
      // the first-party launch owner; absent events or a timeout do not qualify.
      if (!turn) {
        const proof = requireJsonObject(params.report_launch_recovery, "original launch rejection");
        const original = requireJsonObject(proof.original, "original rejected Chat Turn");
        const prior = requireJsonObject(original.loopx_request, "original rejected request");
        const result = requireJsonObject(requireJsonObject(original.response, "original response").native_loopx, "native rejection");
        const journal = requireJsonObject(proof.inspection, "canonical original Turn inspection");
        const retained = requireJsonObject(proof.journal, "canonical rejected native Turn");
        const failure = requireJsonObject(retained.host_failure, "recorded pre-publication rejection");
        const receipt = requireJsonObject(retained.receipt, "recorded host failure receipt");
        const decision = requireJsonObject(journal.recovery_decision, "original native recovery decision");
        const effects = requireJsonObject(journal.recorded_effects, "original native effects");
        const invocation = requireJsonObject(result.effects, "original invocation effects");
        const keys = Object.keys(request).filter(key => key !== "resume_turn_key");
        if (original.session_id !== current.sessionId || original.origin !== "native_loopx"
            || original.loopx_execution !== true || original.status !== "completed"
            || original.delta_count !== 0 || original.upstream_turn_id !== null
            || Object.keys(prior).length !== keys.length
            || keys.some(key => !Object.hasOwn(prior, key) || !sameReportRequestField(key, prior[key], request[key]))
            || result.schema_version !== "loopx_turn_execution_v0" || result.ok !== false
            || result.mode !== "run_once" || result.status !== "failed" || result.result_kind !== "host_failure"
            || result.reason !== "first-party Host runtime rejected: stale_goal_authority"
            || result.resume_turn_key !== request.resume_turn_key
            || proof.turn_key !== request.resume_turn_key || retained.turn_key !== request.resume_turn_key
            || retained.status !== "failed" || retained.result_kind !== "host_failure"
            || retained.reason !== result.reason || retained.host_result != null
            || retained.host_attempt_count !== 1 || failure.kind !== "contract_rejected"
            || failure.schema_version !== "loopx_turn_host_failure_v0" || failure.attempt !== 1 || failure.retryable !== false
            || JSON.stringify(result.host_failure) !== JSON.stringify(retained.host_failure)
            || receipt.failed_phase !== "host_execute"
            || !Array.isArray(retained.completed_phases) || retained.completed_phases.length !== 0
            || invocation.host_invoked !== true || invocation.state_written !== false
            || invocation.quota_spent !== false || invocation.scheduler_acknowledged !== false
            || journal.schema_version !== "loopx_turn_journal_inspection_v1" || journal.ok !== true
            || journal.goal_matches !== true || journal.owner_matches !== true || journal.turn_key_matches !== true
            || journal.journal_consistent !== true || journal.journal_status !== "failed"
            || !Array.isArray(journal.completed_phases) || journal.completed_phases.length !== 0
            || proof.host_attempt_count !== 1 || proof.generation_present !== false
            || effects.host_invoked !== null || effects.state_written !== false
            || effects.quota_spent !== false || effects.scheduler_acknowledged !== false
            || decision.action !== "continue" || decision.reinvoke_host !== true || decision.resume_from !== "host_execute") {
          throw new EffectRuntimeRequestError("report launch recovery is not proven safe by the original native rejection");
        }
      }
    } else if (params.report_launch_recovery != null) {
      throw new EffectRuntimeRequestError("launch recovery must retain the original native Turn key");
    }
  } else if (request.report_window_id !== undefined || request.report_subscription_revision !== undefined
      || request.resume_turn_key !== undefined || params.report_settlement_recovery != null
      || params.report_frozen_contract_recovery != null || params.report_launch_recovery != null) {
    throw new EffectRuntimeRequestError("report fields belong only to an original calendar report");
  }
  let refreshScope = false;
  let rebindContext = false;
  if (turn) {
    const scope = requireJsonObject(params.scope, "original steward scope observation");
    if (typeof scope.current !== "string" || !scope.current) {
      throw new EffectRuntimeRequestError("the original portfolio scope is unavailable");
    }
    if (current.session.manager_authorization_scope_id !== scope.current) {
      // Preparing this actor added exactly its own registered Goal. Admit that
      // known creation without forking the original native conversation. Any
      // other inventory/lifetime or selected-scope change retains the existing
      // external-conversation isolation boundary.
      const knownSelfRegistration = current.binding.goal_scope === "all_registered"
        && scope.without_actor && current.session.manager_authorization_scope_id === scope.without_actor;
      // A due report may enter only the existing context-preparation owner.
      // It must discard old model context before strict execution admission;
      // this phase never updates the saved scope hash or permits a model call.
      rebindContext = !knownSelfRegistration && current.binding.goal_scope === "all_registered"
        && request.work_kind === "report" && params.preparing_model_context === true;
      if (!knownSelfRegistration && !observationOnly && !rebindContext) {
        throw new EffectRuntimeRequestError("the original portfolio changed beyond the prepared steward creation");
      }
      // A pure native watch uses only the fresh scoped reader and cannot
      // invoke, reuse or mutate model context. Other changes leave the model's
      // old scope hash untouched so its existing isolation owner still wins.
      refreshScope = Boolean(knownSelfRegistration);
    }
  }
  return {actor: current.actor, workspace_path: current.workspace, host: "codex-app-server",
    refresh_original_scope: refreshScope, observation_only: observationOnly,
    ...(rebindContext ? {context_rebind_required: true} : {})};
}

export const STEWARD_MONITOR_TARGET = "steward-portfolio-watch";

function isStewardMonitorTodo(row: JsonObject): boolean {
  return row.target_key === STEWARD_MONITOR_TARGET && row.task_class === "continuous_monitor"
    && row.claimed_by === AGENT_ID && (row.watch_only === true || row.watch_only === "true");
}

function isStewardNativeMonitorTodo(row: JsonObject): boolean {
  if (row.target_key === STEWARD_MONITOR_TARGET) return isStewardMonitorTodo(row);
  return row.task_class === "continuous_monitor" && row.claimed_by === AGENT_ID
    && typeof row.target_key === "string"
    && !row.target_key.startsWith("managed-session-watch.")
    && !row.target_key.startsWith("steward-monitor-followup:")
    && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/.test(row.target_key);
}

/** The monitor Todo is the configuration authority; the binding stores no toggle. */
export function planStewardMonitor(params: JsonObject): JsonObject {
  const current = stewardManagedBasis(params, true);
  if (!current.actor || !current.goal || params.operation === "enable" && params.goal_active !== true) {
    throw new EffectRuntimeRequestError("prepare the original active steward first");
  }
  if (!["enable", "pause"].includes(String(params.operation))
      || !Number.isSafeInteger(params.cadence_minutes)
      || Number(params.cadence_minutes) < 30 || Number(params.cadence_minutes) > 1440
      || !Array.isArray(params.todos)) {
    throw new EffectRuntimeRequestError("select a monitor operation and a cadence of 30..1440 minutes");
  }
  const matches = params.todos.map(value => requireJsonObject(value, "native Todo"))
    .filter(row => row.target_key === STEWARD_MONITOR_TARGET);
  if (matches.length > 1 || matches.some(row => !isStewardMonitorTodo(row) || !["open", "blocked"].includes(String(row.status)))) {
    throw new EffectRuntimeRequestError("the native steward monitor is ambiguous or belongs to another owner");
  }
  const todo = matches[0] ?? null;
  const planId = createHash("sha256").update(JSON.stringify([
    current.actor, params.operation, params.cadence_minutes, todo,
  ])).digest("hex");
  return {schema_version: "loopx_steward_monitor_plan_v0", plan_id: planId,
    actor: current.actor, operation: params.operation, cadence_minutes: params.cadence_minutes,
    target_key: STEWARD_MONITOR_TARGET, todo, model_invoked: false};
}

/** Hash observed material fields, not collection clocks or the monitor's own bookkeeping. */
export function observeStewardPortfolio(params: JsonObject): JsonObject {
  const context = requireJsonObject(params.context, "current portfolio observation");
  if (!Array.isArray(params.goal_ids) || !Array.isArray(context.goals)) {
    throw new EffectRuntimeRequestError("the complete authorized portfolio is unavailable");
  }
  const expected = [...params.goal_ids].map(String).sort();
  const goals = context.goals.map(value => requireJsonObject(value, "observed Goal"));
  if (JSON.stringify(goals.map(row => String(row.goal_id)).sort()) !== JSON.stringify(expected)) {
    throw new EffectRuntimeRequestError("the portfolio observation omitted authorized Goals");
  }
  const facts = goals.filter(row => row.goal_id !== params.actor_goal_id)
    .sort((a, b) => String(a.goal_id).localeCompare(String(b.goal_id)))
    .map(row => Object.fromEntries(["goal_id", "goal_instance_id", "activation_state", "quality",
      "attention", "progress", "goal_lifecycle", "agents", "deliveries"].map(key => [key, row[key] ?? null])));
  return {...monitorObservation(facts, params.previous_hash),
    coverage: context.coverage ?? null, observed_goal_count: goals.length,
    own_monitor_bookkeeping_excluded: true};
}

function monitorObservation(facts: unknown, previousHash: unknown): JsonObject {
  // These are diagnostic projection clocks, not source event timestamps.
  const clocks = new Set(["snapshot_id", "portfolio_snapshot_id", "collected_at",
    "collection_completed_at", "generated_at", "evaluated_at", "age_seconds"]);
  function canonical(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value)
      .filter(([key]) => !clocks.has(key)).sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => [key, canonical(item)]));
    return value;
  }
  const resultHash = "sha256:" + createHash("sha256").update(JSON.stringify(canonical(facts))).digest("hex");
  return {result_hash: resultHash, material_change: previousHash != null && previousHash !== resultHash};
}

const PR_SIGNAL_VERDICTS = new Set(["APPROVE", "REQUEST_CHANGES", "NONE"]);
const PR_AUTHOR_MODES = new Set(["include", "exclude"]);
const SIGNAL_SCOPE_SCHEMA = "steward_signal_monitor_scope_v0";

export function normalizeStewardSignalMonitorScope(value: unknown): JsonObject | null {
  if (value === undefined || value === null) return null;
  const scope = requireJsonObject(value, "steward monitor signal scope");
  const allowedKeys = new Set(["schema_version", "include_portfolio", "include_inbox", "pr_filter"]);
  for (const key of Object.keys(scope)) {
    if (!allowedKeys.has(key)) {
      throw new EffectRuntimeRequestError(`unsupported signal_scope field: ${key}`);
    }
  }
  if (scope.schema_version !== SIGNAL_SCOPE_SCHEMA) {
    throw new EffectRuntimeRequestError(`signal_scope requires schema_version=${SIGNAL_SCOPE_SCHEMA}`);
  }
  const includePortfolio = scope.include_portfolio === undefined ? true : scope.include_portfolio;
  const includeInbox = scope.include_inbox === undefined ? true : scope.include_inbox;
  if (typeof includePortfolio !== "boolean" || typeof includeInbox !== "boolean") {
    throw new EffectRuntimeRequestError("signal_scope include_portfolio and include_inbox must be booleans");
  }
  let normalizedFilter: JsonObject | null = null;
  if (scope.pr_filter !== undefined && scope.pr_filter !== null) {
    const rawFilter = requireJsonObject(scope.pr_filter, "PR signal filter");
    const allowedFilterKeys = new Set([
      "exclude_drafts",
      "exclude_conflicting",
      "effective_verdicts",
      "required_approver_declarations",
      "author_mode",
      "authors",
      "min_stalled_hours",
    ]);
    for (const key of Object.keys(rawFilter)) {
      if (!allowedFilterKeys.has(key)) {
        throw new EffectRuntimeRequestError(`unsupported pr_filter field: ${key}`);
      }
    }
    const nextFilter: JsonObject = {};
    for (const boolKey of ["exclude_drafts", "exclude_conflicting"] as const) {
      if (rawFilter[boolKey] !== undefined) {
        if (typeof rawFilter[boolKey] !== "boolean") {
          throw new EffectRuntimeRequestError(`pr_filter.${boolKey} must be a boolean`);
        }
        nextFilter[boolKey] = rawFilter[boolKey];
      }
    }
    if (rawFilter.required_approver_declarations !== undefined) {
      if (!Array.isArray(rawFilter.required_approver_declarations) || rawFilter.required_approver_declarations.length === 0) {
        throw new EffectRuntimeRequestError("pr_filter.required_approver_declarations must be a non-empty array");
      }
      const tokens = rawFilter.required_approver_declarations.map((entry) =>
        requireNonEmptyString(entry, "pr_filter.required_approver_declarations[]").toLowerCase(),
      );
      nextFilter.required_approver_declarations = [...new Set(tokens)].sort();
    }
    if (rawFilter.effective_verdicts !== undefined) {
      if (!Array.isArray(rawFilter.effective_verdicts) || rawFilter.effective_verdicts.length === 0) {
        throw new EffectRuntimeRequestError("pr_filter.effective_verdicts must be a non-empty array");
      }
      const verdicts = rawFilter.effective_verdicts.map((item) => {
        if (typeof item !== "string" || !PR_SIGNAL_VERDICTS.has(item)) {
          throw new EffectRuntimeRequestError(`invalid pr_filter.effective_verdicts entry: ${String(item)}`);
        }
        return item;
      });
      nextFilter.effective_verdicts = [...new Set(verdicts)];
    }
    if (rawFilter.author_mode !== undefined || rawFilter.authors !== undefined) {
      if (typeof rawFilter.author_mode !== "string" || !PR_AUTHOR_MODES.has(rawFilter.author_mode)) {
        throw new EffectRuntimeRequestError("pr_filter.author_mode must be include or exclude");
      }
      if (!Array.isArray(rawFilter.authors) || rawFilter.authors.length === 0) {
        throw new EffectRuntimeRequestError("pr_filter.authors must be a non-empty array when author_mode is set");
      }
      const authors = rawFilter.authors.map((entry) => requireNonEmptyString(entry, "pr_filter.authors[]").toLowerCase());
      nextFilter.author_mode = rawFilter.author_mode;
      nextFilter.authors = [...new Set(authors)].sort();
    }
    if (rawFilter.min_stalled_hours !== undefined) {
      if (
        typeof rawFilter.min_stalled_hours !== "number"
        || !Number.isFinite(rawFilter.min_stalled_hours)
        || rawFilter.min_stalled_hours <= 0
      ) {
        throw new EffectRuntimeRequestError("pr_filter.min_stalled_hours must be a positive finite number");
      }
      nextFilter.min_stalled_hours = rawFilter.min_stalled_hours;
    }
    normalizedFilter = nextFilter;
  }
  return {
    schema_version: SIGNAL_SCOPE_SCHEMA,
    include_portfolio: includePortfolio,
    include_inbox: includeInbox,
    ...(normalizedFilter !== null ? { pr_filter: normalizedFilter } : {}),
  };
}

function prAuthorLogin(item: JsonObject): string {
  const authorObj = jsonObject(item.author);
  const raw = authorObj ? authorObj.login : item.author;
  return typeof raw === "string" ? raw.trim().toLowerCase() : "";
}

function hasMatchingApprovedReviewerDeclaration(item: JsonObject, requiredTokens: string[]): boolean {
  const reviewSummary = jsonObject(item.review_summary);
  if (!reviewSummary || reviewSummary.effective_verdict !== "APPROVE" || reviewSummary.has_review_conflict === true) {
    return false;
  }
  const exactReviews = Array.isArray(reviewSummary.exact_head_reviews) ? reviewSummary.exact_head_reviews : [];
  const latestByActor = new Map<string, { verdict: string; declaration: string }>();
  for (const entry of exactReviews) {
    const review = jsonObject(entry);
    if (!review) continue;
    const actorKey = `${String(review.author_login ?? "").trim().toLowerCase()}\0${String(review.reviewer_declaration ?? "").trim().toLowerCase()}`;
    if (review.state === "DISMISSED") {
      latestByActor.delete(actorKey);
    } else if (
      review.valid_verdict_review === true
      && (review.verdict === "APPROVE" || review.verdict === "REQUEST_CHANGES")
    ) {
      latestByActor.set(actorKey, {
        verdict: review.verdict,
        declaration: String(review.reviewer_declaration ?? "").toLowerCase(),
      });
    }
  }
  return Array.from(latestByActor.values()).some(
    (entry) => entry.verdict === "APPROVE" && requiredTokens.some((tok) => entry.declaration.includes(tok)),
  );
}

function classifyStalledBucket(stalledAgeMs: number, authorReplyBypass: boolean): string {
  if (authorReplyBypass) return "awaiting_maintainer_reply";
  if (stalledAgeMs >= 168 * 3_600_000) return "7d_plus";
  if (stalledAgeMs >= 72 * 3_600_000) return "72h_plus";
  if (stalledAgeMs >= 24 * 3_600_000) return "24h_to_72h";
  return "under_24h";
}

type RepositoryItem = JsonObject & { number: number };

function hasReadableRepositoryItems(items: unknown[]): items is RepositoryItem[] {
  const seenNumbers = new Set<number>();
  for (const item of items) {
    const number = jsonObject(item)?.number;
    if (typeof number !== "number" || !Number.isSafeInteger(number) || number <= 0 || seenNumbers.has(number)) {
      return false;
    }
    seenNumbers.add(number);
  }
  return true;
}

type RepositoryBackfillPlan = {
  source: "comment" | "review";
  add: (item: RepositoryItem) => void;
};

function filterScopedRepositoryItems(
  items: RepositoryItem[],
  filter: JsonObject | null,
  observedAtValue: unknown,
  plan?: RepositoryBackfillPlan,
): { items: RepositoryItem[]; observationIncomplete: boolean } {
  const minStalledHours = typeof filter?.min_stalled_hours === "number" ? filter.min_stalled_hours : null;
  let observedAtMs = NaN;
  if (minStalledHours !== null && plan?.source !== "review") {
    observedAtMs = typeof observedAtValue === "string" ? Date.parse(observedAtValue) : NaN;
    if (!Number.isFinite(observedAtMs)) {
      throw new EffectRuntimeRequestError("scoped monitor min_stalled_hours requires a valid observed_at timestamp");
    }
  }
  const effectiveVerdicts = Array.isArray(filter?.effective_verdicts)
    ? new Set(filter.effective_verdicts.map(String))
    : null;
  const requiredApprovers = Array.isArray(filter?.required_approver_declarations)
    ? filter.required_approver_declarations.map(String)
    : null;
  const authorMode = typeof filter?.author_mode === "string" ? filter.author_mode : null;
  const authors = Array.isArray(filter?.authors) ? new Set(filter.authors.map(String)) : null;
  const canBypassAge = authorMode === "exclude";
  const excludeDrafts = filter?.exclude_drafts === true;
  const excludeConflicting = filter?.exclude_conflicting === true;
  const usesReviewVerdicts = effectiveVerdicts !== null || requiredApprovers !== null || excludeConflicting;
  const requiresCompleteReviews = minStalledHours !== null || usesReviewVerdicts;

  const matched: RepositoryItem[] = [];
  for (const item of items) {
    if (excludeDrafts && item.isDraft === true) continue;
    const mergeable = typeof item.mergeable === "string" ? item.mergeable.trim().toUpperCase() : null;
    if (excludeConflicting && mergeable === "CONFLICTING") continue;
    // Native readbacks carry null when unavailable. Older observations can omit these fields.
    let membershipUncertain = (excludeDrafts && item.isDraft !== undefined && typeof item.isDraft !== "boolean")
      || (excludeConflicting && item.mergeable !== undefined && mergeable !== "MERGEABLE");
    const login = prAuthorLogin(item);
    if (authorMode !== null && authors !== null) {
      membershipUncertain ||= !login;
      if (login && authorMode === "include" && !authors.has(login)) continue;
      if (login && authorMode === "exclude" && authors.has(login)) continue;
    }
    const reviewSummary = jsonObject(item.review_summary);
    const exactReviews = reviewSummary?.exact_head_reviews;
    const reviewAuthorUnknown = usesReviewVerdicts && item.author !== undefined && !login && Array.isArray(exactReviews)
      && exactReviews.some((entry) => {
        const state = jsonObject(entry)?.state;
        return state === "APPROVED" || state === "CHANGES_REQUESTED" || state === "COMMENTED";
      });
    const reviewReadbackIncomplete = item.review_readback_incomplete === true;
    if (reviewReadbackIncomplete || reviewAuthorUnknown || (requiresCompleteReviews && item.review_coverage_incomplete === true)) {
      if (plan === undefined) return { items: [], observationIncomplete: true };
      if (plan.source === "review" && !reviewReadbackIncomplete && !reviewAuthorUnknown) plan.add(item);
      continue;
    }
    if (plan?.source === "review") continue;
    if (excludeConflicting && reviewSummary?.has_review_conflict === true) continue;
    if (effectiveVerdicts !== null) {
      const verdict = String(reviewSummary?.effective_verdict ?? "NONE");
      if (!effectiveVerdicts.has(verdict)) continue;
    }
    if (requiredApprovers !== null && !hasMatchingApprovedReviewerDeclaration(item, requiredApprovers)) {
      continue;
    }
    let scopedItem = item;
    if (minStalledHours !== null) {
      const authorReplied = item.author_replied_after_request_changes === true
        || reviewSummary?.author_replied_after_request_changes === true;
      const hasExplicitStalledSince = Object.prototype.hasOwnProperty.call(item, "stalled_since")
        || (reviewSummary !== null && Object.prototype.hasOwnProperty.call(reviewSummary, "stalled_since"));
      const rawStalledSince = typeof item.stalled_since === "string"
        ? item.stalled_since
        : (reviewSummary?.stalled_since ?? item.stalled_since);
      const stalledSinceUnknown = item.stalled_since_unknown === true;
      if (
        !stalledSinceUnknown
        && hasExplicitStalledSince
        && rawStalledSince == null
        && effectiveVerdicts !== null
        && effectiveVerdicts.size === 1
        && effectiveVerdicts.has("REQUEST_CHANGES")
      ) {
        continue;
      }
      const basisIso = stalledSinceUnknown ? null : (rawStalledSince ?? item.updatedAt);
      const basisMs = typeof basisIso === "string" ? Date.parse(basisIso) : NaN;
      const basisKnown = Number.isFinite(basisMs);
      const thresholdMet = basisKnown && observedAtMs - basisMs >= minStalledHours * 3_600_000;
      if (!canBypassAge && basisKnown && !thresholdMet) continue;

      // Fail closed when comment pagination did not reach the stalled anchor or a newer
      // author/maintainer reply, so evicted replies cannot drop awaiting_maintainer_reply.
      const commentsCoverageIncomplete = item.comments_coverage_incomplete === true
        || reviewSummary?.comments_coverage_incomplete === true;
      if (commentsCoverageIncomplete && (basisKnown || canBypassAge)) {
        if (plan === undefined) return { items: [], observationIncomplete: true };
        // Backfill can exclude under-age items even when another scope field is uncertain.
        if (!membershipUncertain || (canBypassAge && basisKnown && !thresholdMet)) {
          plan.add(item);
        }
        continue;
      }
      const authorReplyBypass = authorReplied && canBypassAge;
      if (!authorReplyBypass && !thresholdMet) {
        if (!basisKnown && plan === undefined) return { items: [], observationIncomplete: true };
        continue;
      }
      const stalledAgeMs = basisKnown ? Math.max(0, observedAtMs - basisMs) : 0;
      scopedItem = {
        ...item,
        stalled_threshold_met: thresholdMet,
        stalled_bucket: classifyStalledBucket(stalledAgeMs, authorReplyBypass),
      };
    }
    if (membershipUncertain) {
      if (plan === undefined) return { items: [], observationIncomplete: true };
      continue;
    }
    matched.push(scopedItem);
  }
  return { items: matched, observationIncomplete: false };
}

function planStewardSignalBackfill(params: JsonObject, source: RepositoryBackfillPlan["source"]): JsonObject {
  const scope = normalizeStewardSignalMonitorScope(params.signal_scope);
  const filter = jsonObject(scope?.pr_filter);
  const items = params.repository_items;
  if (!Array.isArray(items)) {
    throw new EffectRuntimeRequestError(`${source} backfill requires repository items`);
  }
  if (!hasReadableRepositoryItems(items)) {
    throw new EffectRuntimeRequestError(`${source} backfill requires distinct valid PR numbers`);
  }
  const prNumbers: number[] = [];
  // The planner's cutoff includes the time still available for provider reads.
  filterScopedRepositoryItems(items, filter, params.observed_at, {
    source,
    add: (item) => prNumbers.push(item.number),
  });
  return { pr_numbers: prNumbers };
}

/** Read-only selection at the same coverage gate used by scoped observation. */
export function planStewardSignalCommentBackfill(params: JsonObject): JsonObject {
  return planStewardSignalBackfill(params, "comment");
}

export function planStewardSignalReviewBackfill(params: JsonObject): JsonObject {
  return planStewardSignalBackfill(params, "review");
}

const AGE_FILTER_ITEM_FIELDS = [
  "stalled_since",
  "stalled_since_unknown",
  "author_replied_after_request_changes",
  "comments_coverage_incomplete",
  "stalled_threshold_met",
  "stalled_bucket",
] as const;

const AGE_FILTER_REVIEW_SUMMARY_FIELDS = [
  "latest_request_changes_review_id",
  "stalled_since",
  "author_replied_after_request_changes",
  "comments_coverage_incomplete",
] as const;

function omitKeys(obj: JsonObject, keysToOmit: readonly string[]): JsonObject {
  const omit = new Set(keysToOmit);
  return Object.fromEntries(Object.entries(obj).filter(([key]) => !omit.has(key)));
}

function projectRepositoryItemForMaterialHash(item: RepositoryItem, usesStalledAgeFilter: boolean): JsonObject {
  const reviewSummary = jsonObject(item.review_summary);
  if (usesStalledAgeFilter) {
    const hasStalledSinceClock = typeof item.stalled_since === "string"
      || typeof reviewSummary?.stalled_since === "string";
    const omittedItemFields = hasStalledSinceClock
      ? ["comments_coverage_incomplete", "stalled_since_unknown", "updatedAt"]
      : ["comments_coverage_incomplete", "stalled_since_unknown"];
    const projectedItem = omitKeys(item, omittedItemFields);
    if (reviewSummary !== null) {
      projectedItem.review_summary = omitKeys(reviewSummary, ["comments_coverage_incomplete"]);
    }
    return projectedItem;
  }
  const projectedItem = omitKeys(item, [...AGE_FILTER_ITEM_FIELDS, "review_coverage_incomplete"]);
  if (reviewSummary !== null) {
    projectedItem.review_summary = omitKeys(reviewSummary, AGE_FILTER_REVIEW_SUMMARY_FIELDS);
  }
  return projectedItem;
}

/** Combine authorized local portfolio, inbox and optional repository signal
 * snapshots into one deterministic change gate for custom Steward monitors.
 * Incomplete repository reads can only wake the monitor on independent changes. */
export function observeStewardSignalMonitor(params: JsonObject): JsonObject {
  const previousHash = typeof params.previous_hash === "string" && params.previous_hash
    ? params.previous_hash : null;
  const incompleteObservation: JsonObject = {
    result_hash: previousHash,
    material_change: false,
    observation_incomplete: true,
    own_monitor_bookkeeping_excluded: true,
  };
  const signalScope = normalizeStewardSignalMonitorScope(params.signal_scope);
  const repo = params.repository_snapshot == null ? null : requireJsonObject(params.repository_snapshot, "repository signal");
  const rawItems = repo?.items;
  const repoItems = repo?.status !== "unavailable" && Array.isArray(rawItems)
    && hasReadableRepositoryItems(rawItems) ? rawItems : null;
  const prFilter = signalScope === null ? null : jsonObject(signalScope.pr_filter);
  const rawMinStalledHours = prFilter?.min_stalled_hours;
  const usesStalledAgeFilter = typeof rawMinStalledHours === "number"
    && Number.isFinite(rawMinStalledHours)
    && rawMinStalledHours > 0;
  const scopedResult = signalScope !== null && repoItems !== null
    ? filterScopedRepositoryItems(
      repoItems,
      prFilter,
      params.observed_at ?? repo?.observed_at,
    )
    : null;
  const scopedRepoItems = scopedResult === null ? null : scopedResult.items;
  const currentRepoItems = scopedRepoItems ?? repoItems;
  const repoIncomplete = repo !== null && (repoItems === null || scopedResult?.observationIncomplete === true
    || currentRepoItems?.some((item) => item.checks_coverage_incomplete === true
      || item.review_readback_incomplete === true
      || item.merge_state_coverage_incomplete === true
      || item.pr_metadata_coverage_incomplete === true) === true);
  const includePortfolio = signalScope === null ? true : signalScope.include_portfolio === true;
  const includeInbox = signalScope === null ? true : signalScope.include_inbox === true;
  if (repoIncomplete && !includePortfolio && !includeInbox) return incompleteObservation;
  const portfolio = params.portfolio_observation == null ? null
    : requireJsonObject(params.portfolio_observation, "portfolio observation");
  const inboxItems = Array.isArray(params.inbox_items) ? params.inbox_items : null;
  const repositoryCoverage = repoIncomplete ? {repository_observation_incomplete: true} : {};
  const independentSignalsReadable = (!includePortfolio || portfolio !== null) && (!includeInbox || inboxItems !== null);
  if (repoIncomplete && !independentSignalsReadable) {
    return {...incompleteObservation, ...repositoryCoverage};
  }
  // Saved items already passed the scope filter at their last verified observation time.
  const previousRepo = jsonObject(params.previous_repository_snapshot);
  const previousItems = previousRepo?.status !== "unavailable" ? previousRepo?.items : null;
  const baseRepoItems = repoIncomplete
    ? (Array.isArray(previousItems) && hasReadableRepositoryItems(previousItems) ? previousItems : null)
    : currentRepoItems;
  const hashedRepoItems = baseRepoItems?.map((item) => projectRepositoryItemForMaterialHash(item, usesStalledAgeFilter)) ?? null;
  const facts = signalScope === null
    ? {
      portfolio_hash: portfolio?.result_hash ?? null,
      inbox_items: inboxItems ?? [],
      repository_items: hashedRepoItems,
    }
    : {
      signal_scope: signalScope,
      portfolio_hash: includePortfolio ? (portfolio?.result_hash ?? null) : null,
      inbox_items: includeInbox ? (inboxItems ?? []) : [],
      repository_items: hashedRepoItems,
    };
  const clocks = new Set(["snapshot_id", "portfolio_snapshot_id", "collected_at",
    "collection_completed_at", "generated_at", "evaluated_at", "age_seconds", "observed_at"]);
  function canonical(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value)
      .filter(([key]) => !clocks.has(key)).sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => [key, canonical(item)]));
    return value;
  }
  const resultHash = "sha256:" + createHash("sha256").update(JSON.stringify(canonical(facts))).digest("hex");
  if (repoIncomplete && previousHash === resultHash) {
    return {...incompleteObservation, ...repositoryCoverage};
  }
  return {
    result_hash: resultHash,
    material_change: previousHash !== resultHash,
    observation_incomplete: false,
    own_monitor_bookkeeping_excluded: true,
    ...repositoryCoverage,
    ...(!repoIncomplete && scopedRepoItems !== null ? { scoped_repository_items: scopedRepoItems } : {}),
  };
}

/** The local Agent watches its native work, never the private steward portfolio.
 * Complete canonical Todo rows are supplied by the existing native reader.
 * Other watches' clocks/receipts cannot manufacture a work transition. */
export function observeManagedWork(params: JsonObject): JsonObject {
  const goal = requireJsonObject(params.goal, "original Goal");
  const actor = requireJsonObject(params.actor, "original managed Agent");
  if (goal.goal_id !== actor.goal_id || goal.goal_instance_id !== actor.goal_instance_id || !Array.isArray(params.todos)) {
    throw new EffectRuntimeRequestError("the complete original Goal work source is unavailable");
  }
  const tasks = params.todos.map(value => requireJsonObject(value, "native Todo"))
    .filter(row => row.task_class !== "continuous_monitor")
    .sort((a, b) => String(a.todo_id).localeCompare(String(b.todo_id)))
    .map(row => Object.fromEntries(["todo_id", "status", "priority", "task_class", "action_kind", "claimed_by",
      "title", "note", "evidence", "reason", "completion_receipt_id"].map(key => [key, row[key] ?? null])));
  return {...monitorObservation({goal_id: goal.goal_id, goal_instance_id: goal.goal_instance_id, tasks}, params.previous_hash),
    observed_task_count: tasks.length, own_monitor_bookkeeping_excluded: true};
}

export function planStewardManagedMode(params: JsonObject): JsonObject {
  const current = stewardManagedBasis(params);
  if (!Number.isSafeInteger(params.binding_revision) || Number(params.binding_revision) < 0) {
    throw new EffectRuntimeRequestError("current conversation revision is required");
  }
  // A creation by this exact operation can be recovered after bootstrap or
  // registration loses its response. Mutable Goal state is checked above,
  // not included in the immutable pre-creation consent basis.
  const fingerprint = [current.id, current.binding.provider_ref, current.binding.operator_ref,
    current.binding.project_ref, current.source, current.sessionId, current.session.upstream_thread_id ?? null,
    current.workspace, params.binding_revision, params.runtime_profile ?? null];
  const planId = createHash("sha256").update(JSON.stringify(fingerprint)).digest("hex");
  return {schema_version: "loopx_steward_managed_mode_plan_v0", plan_id: planId,
    binding_id: current.id, session_id: current.sessionId, goal_id: current.goalId,
    agent_id: AGENT_ID, creation_operation_id: current.creation, workspace_path: current.workspace,
    already_prepared: current.actor !== null, model_invoked: false, scheduling_enabled: false,
    execution_authority_granted: false};
}

export function bindStewardManagedActor(params: JsonObject): JsonObject {
  const current = stewardManagedBasis(params);
  const goal = current.goal;
  if (!goal || !Array.isArray(goal.registered_agents) || !goal.registered_agents.includes(AGENT_ID)) {
    throw new EffectRuntimeRequestError("native Goal and Agent registration must be read back before binding");
  }
  return normalizeStewardManagedActor({schema_version: STEWARD_MANAGED_ACTOR_SCHEMA,
    goal_id: current.goalId, goal_instance_id: goal.goal_instance_id, agent_id: AGENT_ID,
    session_id: current.sessionId, source_ref: current.source, creation_operation_id: current.creation});
}

/** Adapt a current, already admitted native Task to the host's existing
 * sandbox vocabulary. A Task selection or check confirmation grants no writes.
 * Only the existing whole-workspace approval is expressible here. The current
 * Task lease cannot enlarge it. Narrower scopes remain read-only; the native
 * transport pins the same workspace roots in the guarded Task-start frame.
 */
export function canonicalNativeTaskSandboxPolicy(params: JsonObject): JsonObject {
  const actor = requireJsonObject(params.actor, "original native Task actor");
  const authority = requireJsonObject(params.authority, "admitted Task authority");
  const validation = requireJsonObject(params.validation, "current Task validation plan");
  const identity = requireJsonObject(authority._work_identity, "checked original work identity");
  const preparation = requireJsonObject(authority.execution_preparation, "original Task execution preparation");
  if (!(params.host === "codex-app-server" && actor.schema_version === "loopx_managed_session_actor_v0"
    && typeof actor.workspace_path === "string" && actor.workspace_path.length > 0
    && identity.goal_id === actor.goal_id && identity.agent_id === actor.agent_id
    && identity.todo_id === validation.todo_id && preparation.todo_id === validation.todo_id
    && preparation.agent_id === actor.agent_id && preparation.state === "ready"
    && preparation.delivery_allowed === true && authority.should_run === true
    && authority.normal_delivery_allowed === true && validation.state === "ready")) {
    throw new EffectRuntimeRequestError("native host policy requires the original admitted Task, validation and execution lease");
  }
  const approval = jsonObject(projectTurnBoundary(authority).checkpointed_boundary_authority);
  const readonly = {type: "readOnly", networkAccess: false};
  if (approval?.schema_version !== "checkpointed_boundary_authority_v0"
      || !Array.isArray(approval.active_write_scope) || approval.active_write_scope.length === 0) return readonly;
  const lease = requireJsonObject(params.task_lease, "current original Task write lease");
  if (lease.schema_version !== "task_lease_v0" || lease.status !== "active"
      || lease.goal_id !== actor.goal_id || lease.todo_id !== validation.todo_id || lease.owner !== actor.agent_id
      || !Number.isSafeInteger(lease.version) || Number(lease.version) < 1
      || !Array.isArray(lease.write_scopes)) {
    throw new EffectRuntimeRequestError("native Task writes require the same current execution lease");
  }
  const paths = actor.workspace_path.startsWith("/") ? posix : win32;
  if (!paths.isAbsolute(actor.workspace_path)) {
    throw new EffectRuntimeRequestError("native filesystem policy requires the original absolute workspace");
  }
  const workspace = paths.resolve(actor.workspace_path);
  // Empty legacy inventories retain their already explicit Goal-wide grant.
  // Fresh writable creations declare the reviewed scope on their original Task.
  if (!approval.active_write_scope.includes("**")
      || lease.write_scopes.length > 0 && !lease.write_scopes.includes("**")) return readonly;
  return {type: "workspaceWrite", writableRoots: [workspace], networkAccess: false,
    excludeTmpdirEnvVar: true, excludeSlashTmp: true};
}
