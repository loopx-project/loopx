import type { JsonObject } from "../runtime_decode.ts";
import { jsonObject } from "../runtime_decode.ts";
import { stripPythonWhitespace } from "../coordination/todo_agents.ts";
import { normalizeSchedulerRrule, APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY as APP_STATE, CODEX_APP_STATEFUL_BACKOFF_STATE_KEY as CODEX_STATE } from "./state_store.ts";

const DEFAULT_CAPABILITIES = new Set(["shell", "filesystem_read", "filesystem_write"]);

function text(raw: unknown): string {
  if (!raw) return "";
  return stripPythonWhitespace(typeof raw === "boolean" ? "True" : String(raw));
}

function capabilities(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return [...new Set(raw.map(text).filter(capability => capability && !DEFAULT_CAPABILITIES.has(capability)))];
}

function followupArgs(request: JsonObject, command: string): unknown[] {
  const surface = text(request.surface === undefined ? "codex_app" : request.surface);
  const stateKey = text(request.state_key === undefined ? APP_STATE : request.state_key);
  const args: unknown[] = ["quota", command, "--goal-id", text(request.goal_id),
    "--agent-id", text(request.agent_id), surface === "trae_app" ? "--trae_app" : "-A"];
  for (const capability of capabilities(request.available_capabilities)) args.push("--available-capability", capability);
  if (jsonObject(request.scheduler_host_facts)) {
    // The Python adapter performs only the persisted zlib/base64 wire codec.
    args.push({kind: "scheduler_host_followup", host_facts: request.scheduler_host_facts,
      use_current_hint: command === "scheduler-ack-current"});
  }
  if (surface !== "codex_app") args.push("--surface", surface);
  if (stateKey !== CODEX_STATE) args.push("--state-key", stateKey);
  return args;
}

export function schedulerAckHint(request: JsonObject): JsonObject {
  const appliedRrule = normalizeSchedulerRrule(request.applied_rrule);
  const args = followupArgs(request, "scheduler-ack-current");
  args.push("--applied-rrule", appliedRrule);
  if (request.host_match_observed) args.push("--host-match-observed", "--reset-token", text(request.reset_token),
    "--identity-signature", text(request.identity_signature));
  args.push("--execute");
  const structuredArgs: JsonObject = {goal_id: text(request.goal_id), agent_id: text(request.agent_id),
    surface: text(request.surface === undefined ? "codex_app" : request.surface), state_key: text(request.state_key === undefined ? APP_STATE : request.state_key),
    applied_rrule: appliedRrule, reset_token: text(request.reset_token), identity_signature: text(request.identity_signature)};
  const available = capabilities(request.available_capabilities);
  if (available.length) structuredArgs.available_capabilities = available;
  if (request.host_match_observed) structuredArgs.host_match_observed = true;
  return {schema_version: "app_automation_scheduler_ack_hint_v0",
    after: text(request.after || "automation_update_rrule_success"), command: "quota scheduler-ack-current", execute: true,
    cli_args: args, args: structuredArgs, uses_current_hint: true, no_spend: true};
}

export function schedulerFailureHint(request: JsonObject): JsonObject {
  const args = followupArgs(request, "scheduler-fail-current");
  args.push("--failed-rrule", normalizeSchedulerRrule(request.failed_rrule));
  const observed = normalizeSchedulerRrule(request.observed_host_rrule);
  if (observed) args.push("--app-automation-current-rrule", observed);
  args.push("--execute");
  return {schema_version: "app_automation_scheduler_failure_hint_v0", cli_args: args};
}

export function schedulerFallbackHint(request: JsonObject): JsonObject {
  const goal = text(request.goal_id), agent = text(request.agent_id);
  let automation = text(request.automation_id);
  const turn = text(request.turn_instance_id) || "${LOOPX_TURN:?}";
  const projected = !/^[A-Za-z0-9._:-]{1,128}$/.test(automation);
  if (projected) automation = (agent ? `loopx-${goal}-${agent}` : `loopx-${goal}`)
    .replace(/[^A-Za-z0-9._:-]/g, "-").replace(/-+/g, "-").replace(/^-+|-+$/g, "").slice(0, 128) || "loopx-fallback";
  return {schema_version: "codex_app_scheduler_fallback_hint_v0", available: true, command: "loopx-apply-rrule",
    after: "automation_update_unavailable_or_failed", cli_args: ["loopx-apply-rrule", "--goal-id", goal, "--agent-id", agent,
      "--automation-id", automation, "--turn-instance-id", turn],
    args: {goal_id: goal, agent_id: agent, automation_id: automation, turn_instance_id: turn}, no_spend: true,
    reason: "fallback only when automation_update is unavailable or failed and apply_needed=true; loopx-apply-rrule directly updates the Codex App SQLite automation store (codex-dev.db) and TOML, which bypasses the app API - use only as a bounded fallback, never as the routine path; run the bound ack_hint after it succeeds"
      + (projected ? "; automation_id_projected: no installed heartbeat automation matched this goal/agent, so loopx-apply-rrule will create it before applying the rrule" : ""),
    ...(projected ? {automation_id_projected: true, action: "create_then_apply_rrule_via_fallback"} : {})};
}


export function legacySchedulerAckHint(request: JsonObject): JsonObject {
  return {...schedulerAckHint({...request, state_key: request.state_key === undefined ? CODEX_STATE : request.state_key}),
    schema_version: "codex_app_scheduler_ack_hint_v0"};
}

export function legacySchedulerFailureHint(request: JsonObject): JsonObject {
  const hint = schedulerFailureHint({...request, state_key: request.state_key === undefined ? CODEX_STATE : request.state_key});
  return {...hint, schema_version: "codex_app_scheduler_failure_hint_v0", cli_args: (hint.cli_args as unknown[]).map(
    argument => argument === "--app-automation-current-rrule" ? "--codex-app-current-rrule" : argument)};
}
