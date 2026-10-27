/** Source-bound preparation of one native execution identity for a steward.
 *
 * This owner prepares/binds identity only. Goal bootstrap, Agent registration,
 * scheduling and model execution retain their existing owners. In particular,
 * a prepared identity never advertises a running monitor or an enabled host.
 */
import { createHash } from "node:crypto";
import type { JsonObject } from "../runtime_decode.ts";
import { requireJsonObject, requireNonEmptyString } from "../runtime_decode.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";
import {goalAgentConversationIdentity, resolveConversationScope} from "./conversation_scope.ts";
import {readTurnSelectionRejection} from "../turn_driver/selection_rejection.ts";
import {EffectiveAction} from "../quota/effective_action.generated.ts";

/** A stopped observation may get one new Chat carrier, never a new native
 * observation identity. The host independently reads the old worker lock,
 * complete journals and native poll receipts; normal admission still checks
 * current ownership, deadline, permissions and user work on every reentry. */
function admitMonitorRestartRecovery(params: JsonObject, request: JsonObject, session: JsonObject): void {
  if (request.monitor_restart_recovery === undefined) {
    if (params.monitor_restart_basis != null) throw new EffectRuntimeRequestError("watch recovery lacks its original request");
    return;
  }
  const recovery = requireJsonObject(request.monitor_restart_recovery, "original watch continuation");
  const basis = requireJsonObject(params.monitor_restart_basis, "original watch observation");
  const original = requireJsonObject(basis.original, "original stopped watch");
  const prior = requireJsonObject(original.loopx_request, "original watch request");
  const keys = Object.keys(request).filter(key => key !== "monitor_restart_recovery");
  const events = basis.event_kinds;
  if (Object.keys(recovery).join() !== "original_turn_id"
      || recovery.original_turn_id !== original.turn_id || typeof original.turn_id !== "string"
      || !/^[a-f0-9]{32}$/.test(original.turn_id) || original.session_id !== session.session_id
      || original.status !== "failed" || original.error_code !== "server_restarted"
      || original.origin !== "native_loopx" || original.loopx_execution !== true
      || original.response !== null || original.upstream_turn_id !== null || original.delta_count !== 0
      || prior.work_kind !== "monitor" || request.work_kind !== "monitor" || request.validation_command !== null
      || prior.monitor_restart_recovery !== undefined || basis.old_worker_excluded !== true
      || basis.poll_absent !== true || basis.lease_available !== true || !Array.isArray(events) || !events.includes("turn.failed")
      || events.some(kind => !["turn.queued", "agent.phase", "manager.context", "turn.failed"].includes(String(kind)))
      || Object.keys(prior).length !== keys.length
      || keys.some(key => key !== "monitor_lease_version" && JSON.stringify(request[key]) !== JSON.stringify(prior[key]))
      || Number(params.queued_turn_count) !== 0) {
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
  if (request.work_kind !== "report" || prior.work_kind !== "report" || prior.report_quota_recovery !== undefined
      || request.resume_turn_key !== undefined || prior.resume_turn_key !== undefined
      || Object.keys(recovery).sort().join() !== "native_observation,original_turn_id"
      || typeof original.turn_id !== "string" || !/^[a-f0-9]{32}$/.test(original.turn_id)
      || recovery.original_turn_id !== original.turn_id || original.session_id !== session.session_id
      || original.origin !== "native_loopx" || original.loopx_execution !== true || original.status !== "completed"
      || original.delta_count !== 0 || typeof original.upstream_turn_id !== "string" || !original.upstream_turn_id
      || Object.keys(prior).length !== keys.length
      || keys.some(key => key === "turn_instance_id"
        ? request[key] !== String(prior[key]) + ".quota-recovery"
        : JSON.stringify(request[key]) !== JSON.stringify(prior[key]))
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
      || observation.thread_id !== session.upstream_thread_id || typeof observation.thread_id !== "string" || !observation.thread_id
      || observation.turn_id !== original.upstream_turn_id || observation.only_original_user_item !== true
      || observation.codex_error_info !== "usageLimitExceeded" || observation.quota_available !== true
      || typeof observation.quota_observed_at !== "number" || !Number.isFinite(observation.quota_observed_at)
      || typeof basis.observed_at !== "number" || !Number.isFinite(basis.observed_at)
      || accepting && (basis.generation_present !== false
        || basis.observed_at < observation.quota_observed_at || basis.observed_at - observation.quota_observed_at > 60)) {
    throw new EffectRuntimeRequestError("report continuation requires its original native user-only quota refusal and current usable allowance");
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
      || Object.keys(prior).length !== Object.keys(request).length
      || Object.keys(request).some(key => JSON.stringify(prior[key]) !== JSON.stringify(request[key]))
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
  // The original host returned before calling its model when a fresh guard
  // no longer owed governance. Absence of output alone cannot prove that.
  const probe = requireJsonObject(proof.probe, "original host admission probe");
  const observed = requireJsonObject(probe.turn, "original probe Chat Turn");
  const journal = requireJsonObject(probe.journal, "canonical probe journal");
  const inspection = requireJsonObject(probe.inspection, "canonical probe inspection");
  const recovery = requireJsonObject(inspection.recovery_decision, "probe recovery decision");
  const recorded = requireJsonObject(inspection.recorded_effects, "probe effects");
  const plan = requireJsonObject(journal.plan, "probe plan");
  const route = requireJsonObject(plan.route, "probe route");
  const envelope = requireJsonObject(plan.turn_envelope, "probe envelope");
  const action = requireJsonObject(envelope.action, "probe action");
  const selected = requireJsonObject(action.selected_todo, "probe selected task");
  const identity = requireJsonObject(requireJsonObject(requireJsonObject(plan.transaction, "probe transaction")
    .settlement_plan, "probe settlement").identity, "probe identity");
  const host = requireJsonObject(journal.host, "probe host");
  const stopped = requireJsonObject(journal.host_result, "probe host result");
  const runningHere = params.turn != null && requireJsonObject(params.turn, "current Chat Turn").turn_id === observed.turn_id
    && ["starting", "running"].includes(String(observed.status));
  if (observed.session_id !== sessionId || typeof observed.turn_id !== "string" || !/^[a-f0-9]{32}$/.test(observed.turn_id)
      || observed.turn_id === original.turn_id
      || observed.origin !== "native_loopx" || observed.loopx_execution !== true
      || observed.delta_count !== 0 || observed.upstream_turn_id !== null || observed.error_code != null || observed.error != null
      || JSON.stringify(observed.loopx_request) !== JSON.stringify(request)
      || !(runningHere || observed.status === "completed"
        && JSON.stringify(requireJsonObject(observed.response, "probe response").native_loopx) === JSON.stringify(result))
      || journal.schema_version !== "loopx_turn_journal_v0" || !ENVELOPED_SHA256_PATTERN.test(String(journal.turn_key))
      || journal.status !== "stopped" || journal.goal_id !== envelope.goal_id
      || journal.host_attempt_count !== 1 || journal.result_kind !== "wait"
      || JSON.stringify(journal.completed_phases) !== JSON.stringify(["host_execute", "typed_result", "validation"])
      || host.kind !== "codex-app-server" || host.executable !== "built-in"
      || stopped.schema_version !== "loopx_turn_result_v0" || stopped.turn_key !== journal.turn_key
      || stopped.result_kind !== "wait" || stopped.classification !== "report_replan_not_admitted"
      || inspection.schema_version !== "loopx_turn_journal_inspection_v1" || inspection.ok !== true
      || inspection.goal_matches !== true || inspection.owner_matches !== true || inspection.turn_key_matches !== true
      || inspection.journal_consistent !== true || inspection.journal_status !== "stopped"
      || recovery.action !== "return_existing" || recovery.reinvoke_host !== false
      || recorded.host_invoked !== true || ["state_written", "quota_spent", "scheduler_acknowledged"].some(key => recorded[key] !== false)
      || route.kind !== "ready_for_host" || route.effective_action !== EffectiveAction.NORMAL_RUN
      || envelope.effective_action !== EffectiveAction.NORMAL_RUN || envelope.replan_action_packet != null
      || action.must_attempt !== true || action.delivery_allowed !== true || selected.todo_id !== identity.todo_id
      || identity.goal_id !== envelope.goal_id || identity.agent_id !== envelope.agent_id
      || identity.goal_id !== requireJsonObject(request.actor, "report actor").goal_id
      || identity.agent_id !== requireJsonObject(request.actor, "report actor").agent_id
      || typeof identity.todo_id !== "string" || !/^todo_[A-Za-z0-9_-]{1,100}$/.test(identity.todo_id)
      || identity.turn_instance_id !== request.turn_instance_id + ".replan") {
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
    ...(observationOnly ? ["monitor_deadline", "monitor_lease_version", "monitor_restart_recovery"] : []),
    ...(reportWork ? ["report_window_id", "report_subscription_revision", "report_quota_recovery"] : []),
    ...(scheduledWork ? ["schedule_window_id", "schedule_subscription_revision"] : [])];
  if (Object.keys(request).some(key => !requestFields.includes(key))
      || request.operation !== "native_turn" || request.host !== "codex-app-server" || !["native_turn", "monitor", "report", "scheduled_task"].includes(String(request.work_kind))
      || request.validation_command !== null
      || typeof request.turn_instance_id !== "string" || !/^[A-Za-z0-9._:-]{1,160}$/.test(request.turn_instance_id)
      || request.todo_id !== null && (typeof request.todo_id !== "string" || !/^todo_[A-Za-z0-9_-]{1,100}$/.test(request.todo_id))) {
    throw new EffectRuntimeRequestError("managed work uses the native task's existing validation, without caller commands");
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
  } else if (params.report_settlement_recovery != null) {
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
  if (turn ? turn.session_id !== session.session_id || turn.turn_id !== session.active_turn_id
      || !["starting", "running"].includes(String(turn.status)) || turn.origin !== "native_loopx"
      || turn.loopx_execution !== true || JSON.stringify(turn.loopx_request) !== JSON.stringify(request)
    : session.active_turn_id || params.queued_turn_count !== 0) {
    throw new EffectRuntimeRequestError("the original conversation is busy or no longer owns this Turn; retain its current work");
  }
  admitReportSelectionRecovery(params, request, session.session_id);
  admitReportQuotaRecovery(params, request, session);
  return {actor, workspace_path: goal.workspace_path, host: "codex-app-server", observation_only: observationOnly};
}

/** Both original conversation hosts use the native journal's no-model recovery. */
function reportSettlementOnly(params: JsonObject, request: JsonObject, todo: JsonObject): boolean {
  if (params.report_settlement_recovery == null) return false;
  const recovery = requireJsonObject(params.report_settlement_recovery, "native original report recovery");
  const decision = requireJsonObject(recovery.recovery_decision, "native journal recovery decision");
  const cachedGeneration = todo.status === "open" && recovery.journal_status === "failed"
    && decision.action === "continue" && decision.resume_from === "validation"
    && Array.isArray(recovery.completed_phases) && recovery.completed_phases.length === 2
    && recovery.completed_phases[0] === "host_execute" && recovery.completed_phases[1] === "typed_result";
  const completedGeneration = todo.status === "done" && todo.completion_turn_key === request.turn_instance_id
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
const AGENT_ID = "steward";

function text(value: unknown, name: string, expression?: RegExp): string {
  const result = requireNonEmptyString(value, name);
  if (result.length > 200 || (expression && !expression.test(result))) {
    throw new EffectRuntimeRequestError(`invalid ${name}`);
  }
  return result;
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

export function stewardManagedBasis(params: JsonObject, executing = false) {
  const binding = requireJsonObject(params.binding, "steward binding");
  const session = requireJsonObject(params.session, "original steward Session");
  const context = requireJsonObject(session.steward_context, "original steward authority");
  const observation = requireJsonObject(params.observation, "current provider observation");
  const id = text(binding.binding_id, "binding identity", /^[a-f0-9]{24}$/);
  const source = text(context.source_ref, "steward source", /^[a-f0-9]{24}$/);
  if (binding.context_kind !== "steward" || binding.grant !== "portfolio_read" || binding.enabled !== true
      || binding.audience === "group" || binding.executor_endpoint_id !== "codex"
      || session.goal_id !== "loopx-manager" || session.agent_id !== "codex"
      || session.session_mode === "attached_host" || session.status === "closed"
      || session.channel_id !== `manager.external.native.${id}.${source}`
      || !executing && (session.active_turn_id || params.queued_turn_count !== 0)) {
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
  return {binding, session, id, source, workspace, sessionId, goalId, creation, goal, actor};
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
    "monitor_deadline", "monitor_lease_version", "monitor_restart_recovery", "report_window_id", "report_subscription_revision", "resume_turn_key", "report_quota_recovery"];
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
  admitMonitorRestartRecovery(params, request, current.session);
  if (turn && (turn.session_id !== current.sessionId || turn.turn_id !== current.session.active_turn_id
      || !["starting", "running"].includes(String(turn.status)) || turn.origin !== "native_loopx"
      || turn.loopx_execution !== true || JSON.stringify(turn.loopx_request) !== JSON.stringify(request))) {
    throw new EffectRuntimeRequestError("the original native Chat Turn no longer owns execution");
  }
  const monitor = params.monitor_todo == null ? null : requireJsonObject(params.monitor_todo, "native monitor");
  const observationOnly = request.work_kind === "monitor";
  if (monitor && !observationOnly) {
    throw new EffectRuntimeRequestError("a native watch must retain its no-model observation transport");
  }
  if (observationOnly && (!monitor || !isStewardMonitorTodo(monitor)
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
        const decision = requireJsonObject(journal.recovery_decision, "original native recovery decision");
        const effects = requireJsonObject(journal.recorded_effects, "original native effects");
        const invocation = requireJsonObject(result.effects, "original invocation effects");
        const keys = Object.keys(request).filter(key => key !== "resume_turn_key");
        if (original.session_id !== current.sessionId || original.origin !== "native_loopx"
            || original.loopx_execution !== true || original.status !== "completed"
            || original.delta_count !== 0 || original.upstream_turn_id !== null
            || Object.keys(prior).length !== keys.length
            || keys.some(key => JSON.stringify(prior[key]) !== JSON.stringify(request[key]))
            || result.schema_version !== "loopx_turn_execution_v0" || result.ok !== false
            || result.mode !== "run_once" || result.effects_scope !== "current_invocation"
            || result.error_code !== "stale_goal_authority"
            || result.error !== "first-party Host runtime rejected: stale_goal_authority"
            || result.resume_turn_key !== request.resume_turn_key
            || proof.turn_key !== request.resume_turn_key || Object.values(invocation).some(value => value === true)
            || journal.schema_version !== "loopx_turn_journal_inspection_v1" || journal.ok !== true
            || journal.goal_matches !== true || journal.owner_matches !== true || journal.turn_key_matches !== true
            || journal.journal_consistent !== true || journal.journal_status !== "in_progress"
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
      || request.resume_turn_key !== undefined || params.report_settlement_recovery != null || params.report_launch_recovery != null) {
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
