import type { JsonObject } from "../runtime_decode.ts";
import { jsonObject } from "../runtime_decode.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";

const DEFER_ACTIONS = new Set(["backoff_agent_monitor_only", "backoff_until_fresh_evidence",
  "backoff_until_material_transition", "backoff_until_reassigned", "backoff_until_state_change",
  "backoff_waiting_for_user", "repair_interaction_contract_projection"]);

export function goalRuntimeContinuation(hint: JsonObject, frontierSeconds: unknown): JsonObject {
  const action = String(hint.action || "");
  const disposition = action === "run_now" || action === "preserve_current_schedule" ? "continue_now"
    : action === "stop_until_explicit_resume" ? "complete" : DEFER_ACTIONS.has(action) ? "defer" : null;
  if (!disposition) throw new EffectRuntimeRequestError(`unsupported Goal runtime scheduler action: ${action || "missing"}`);
  const continuation: JsonObject = {schema_version: "goal_runtime_continuation_v0", disposition};
  if (disposition !== "defer") return continuation;
  if (frontierSeconds === true || (typeof frontierSeconds === "number" && Number.isInteger(frontierSeconds) && frontierSeconds > 0)) {
    continuation.recheck_after_seconds = frontierSeconds;
    continuation.recheck_source = "frontier_earliest_material_transition";
  } else {
    const host = jsonObject(hint.app_automation) ?? jsonObject(hint.codex_app) ?? {};
    const minutes = host.recommended_interval_minutes;
    if (typeof minutes !== "number" || !Number.isInteger(minutes) || minutes <= 0)
      throw new EffectRuntimeRequestError("deferred Goal runtime continuation requires a positive recheck interval");
    continuation.recheck_after_seconds = minutes * 60;
  }
  continuation.wake_policy = "state_change_or_deadline";
  return continuation;
}

function codexResetAliases(hint: JsonObject): void {
  const reset = jsonObject(hint.reset_policy);
  if (reset) {
    reset.codex_app_initial_interval_minutes = reset.app_automation_initial_interval_minutes;
    reset.codex_app_initial_rrule = reset.app_automation_initial_rrule;
  }
  const detail = jsonObject(jsonObject(hint.cold_path_detail)?.reset_policy_detail);
  if (detail) {
    detail.codex_app_initial_interval_minutes = detail.app_automation_initial_interval_minutes;
    detail.codex_app_initial_rrule = detail.app_automation_initial_rrule;
    detail.codex_app_tool = detail.app_automation_tool;
    detail.codex_app_apply = detail.app_automation_apply;
  }
}

function omitAppResetFields(hint: JsonObject): void {
  const cold = jsonObject(hint.cold_path_detail);
  if (cold) delete cold.stateful_backoff_detail;
  for (const reset of [jsonObject(hint.reset_policy), jsonObject(cold?.reset_policy_detail)]) {
    if (reset) for (const key of Object.keys(reset)) {
      if (key.startsWith("app_automation_") || key.startsWith("codex_app_")) delete reset[key];
    }
  }
}

export function applyHintExecutionContext(hint: JsonObject, context: JsonObject, frontierSeconds?: unknown): JsonObject {
  const app = jsonObject(hint.app_automation) ?? {};
  const applicable = context.app_automation_applicability === "applicable" || context.codex_app_applicability === "applicable";
  if (applicable) {
    app.applicability = "applicable";
    app.host_surface = context.host_surface;
    const backoff = jsonObject(app.stateful_backoff) ?? {};
    const apply = backoff.apply_needed === true || app.host_action_required === true;
    const ack = backoff.ack_needed === true;
    hint.app_automation = app;
    if (context.host_surface === "codex_app") {
      if (!jsonObject(hint.codex_app)) hint.codex_app = structuredClone(app);
      codexResetAliases(hint);
    } else delete hint.codex_app;
    const cold = jsonObject(hint.cold_path_detail);
    if (cold) {
      cold.execution_context = context;
      cold.execution_phase = {schema_version: "scheduler_execution_phase_v0", host_surface: context.host_surface,
        scheduler_owner: context.scheduler_owner, disposition: apply || ack ? "host_action_required" : "not_required",
        completed: !(apply || ack), apply_needed: apply, ack_needed: ack, acknowledged: false};
    }
    return hint;
  }
  const continuation = context.scheduler_owner === "goal_runtime" ? goalRuntimeContinuation(hint, frontierSeconds) : null;
  hint.execution_context = context;
  delete hint.app_automation;
  hint.codex_app = {applicability: "not_applicable", reason_code: `cadence_owned_by_${context.scheduler_owner}`,
    apply: "none", host_action: "none", ack_required: false, no_spend_for_cadence_change: true};
  omitAppResetFields(hint);
  hint.execution_phase = {schema_version: "scheduler_execution_phase_v0", host_surface: context.host_surface,
    scheduler_owner: context.scheduler_owner, disposition: `${context.scheduler_owner}_owned`, completed: true,
    apply_needed: false, ack_needed: false, acknowledged: false,
    completion_reason: "selected scheduler owner requires no Codex App apply or ACK"};
  if (continuation) hint.goal_runtime_continuation = continuation;
  return hint;
}
