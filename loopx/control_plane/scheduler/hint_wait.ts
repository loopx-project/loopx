import type { JsonObject } from "../runtime_decode.ts";
import { jsonObject } from "../runtime_decode.ts";
import { stripPythonWhitespace } from "../coordination/todo_agents.ts";
import { rruleForMinutes } from "./state_store.ts";
import { evaluateSchedulerStateTransition } from "./state_transition_rules.ts";

export interface HintTimestamp extends JsonObject { iso: string; utc: string; after_seconds: number }
export function hintTimestamp(request: JsonObject, raw: unknown): HintTimestamp | null {
  return (jsonObject(jsonObject(request.timestamps)?.[String(raw)]) as HintTimestamp | null) ?? null;
}

export function hintFrontier(request: JsonObject): JsonObject | null {
  const deadlines = (request.frontier_deadlines as unknown[]).filter(jsonObject) as JsonObject[];
  const candidates = deadlines.map(deadline => ({deadline, time: hintTimestamp(request, deadline.next_due_at)}))
    .filter((entry): entry is {deadline: JsonObject; time: HintTimestamp} => entry.time !== null);
  candidates.sort((left, right) => left.time.after_seconds - right.time.after_seconds);
  if (!candidates.length) return null;
  const selected = candidates[0];
  return {frontier_recheck_after_seconds: Math.max(1, Math.ceil(selected.time.after_seconds)),
    frontier_recheck_source: selected.deadline.source, frontier_recheck_identity: selected.deadline.identity,
    frontier_recheck_candidate_count: candidates.reduce((count, entry) => count + Number(entry.deadline.candidate_count || 0), 0)};
}

export function hintPendingRetry(payload: JsonObject): JsonObject | null {
  const summary = jsonObject(payload.agent_todo_summary) ?? {};
  const deadline = jsonObject(summary.frontier_deadline) ?? {};
  const retry = jsonObject(summary.blocked_retry_wait || (deadline.source === "turn_settlement" ? deadline.blocked_retry : null));
  return retry ? {...retry, execution_authority_granted: false,
    recheck: "rerun quota at or after due_at; current gates and owner cadence still apply"} : null;
}

function monitorIdentity(monitor: JsonObject): string {
  for (const field of ["todo_id", "target_key", "action_kind", "title"]) {
    const identity = stripPythonWhitespace(String(monitor[field] || ""));
    if (identity) return identity;
  }
  return String(monitor.index || "monitor");
}

function monitorPlan(monitor: JsonObject, request: JsonObject): JsonObject | null {
  const expires = hintTimestamp(request, monitor.expires_at);
  const identity = monitorIdentity(monitor);
  if (expires && expires.after_seconds <= 0) return {phase: "expired", selected_monitor_identity: identity};
  const due = hintTimestamp(request, monitor.next_due_at), checked = hintTimestamp(request, monitor.last_checked_at);
  const schedule = evaluateSchedulerStateTransition({schema_version: "loopx_scheduler_state_transition_request_v0",
    operation: "monitor_schedule", generated_at: request.generated_at, cadence: monitor.cadence == null ? null : String(monitor.cadence)});
  const cadence = typeof schedule.cadence_seconds === "number" ? Math.max(1, Math.ceil(schedule.cadence_seconds / 60)) : null;
  const caps: number[] = [];
  let phase: string;
  let resetDue = false;
  if (expires && checked && checked.after_seconds <= 0) {
    phase = "active_window";
    if (cadence !== null) caps.push(cadence);
    if (due && due.after_seconds > 0) caps.push(Math.max(1, Math.ceil(due.after_seconds / 60)));
  } else if (due && due.after_seconds > 0) {
    const horizon = Math.max(1, Math.ceil(due.after_seconds / 60));
    phase = horizon <= 60 ? "near_window" : "far_window";
    caps.push(horizon);
    resetDue = true;
  } else if (cadence !== null) { phase = "cadence_only"; caps.push(cadence); }
  else return null;
  if (!caps.length) return null;
  const cap = Math.min(...caps), floor = Math.min(15, cap);
  const progression = [15, 30, 60].filter(minutes => minutes <= cap).map(minutes => Math.max(floor, minutes));
  return {phase, selected_monitor_identity: identity, selected_todo_id: monitor.todo_id ?? null,
    selected_target_key: monitor.target_key ?? null, host_floor_minutes: floor, cap_minutes: cap, cadence_minutes: cadence,
    next_due_at: due?.iso ?? null, expires_at: expires?.iso ?? null, last_checked_at: checked?.iso ?? null,
    progression_minutes: progression.length ? progression : [floor],
    reset_profile: {monitor_wait_phase: phase, monitor_wait_host_floor_minutes: floor, monitor_wait_selected_identity: identity,
      monitor_wait_cadence_minutes: cadence, monitor_wait_window_start_at: resetDue ? due?.iso ?? null : null,
      monitor_wait_window_end_at: expires?.iso ?? null}};
}

function monitorObservations(payload: JsonObject): JsonObject[] {
  const summary = jsonObject(payload.agent_todo_summary) ?? {};
  const monitors: JsonObject[] = [];
  const seen = new Set<string>();
  for (const lane of ["current_agent_claimed_monitor_items", "monitor_open_items"]) {
    const candidates = summary[lane];
    if (!Array.isArray(candidates)) continue;
    for (const candidate of candidates) {
      const monitor = jsonObject(candidate);
      if (!monitor) continue;
      const id = String(monitor.todo_id || "");
      if (id && seen.has(id)) continue;
      if (id) seen.add(id);
      monitors.push(monitor);
    }
  }
  return monitors;
}

function compareMonitorIdentity(left: unknown, right: unknown): number {
  const first = [...String(left)], second = [...String(right)];
  for (let index = 0; index < Math.min(first.length, second.length); index++) {
    const difference = first[index].codePointAt(0)! - second[index].codePointAt(0)!;
    if (difference) return difference;
  }
  return first.length - second.length;
}

function waitPlan(payload: JsonObject, request: JsonObject): JsonObject | null {
  const plans = monitorObservations(payload).map(monitor => monitorPlan(monitor, request)).filter(jsonObject) as JsonObject[];
  const expired = plans.filter(plan => plan.phase === "expired").length;
  const live = plans.filter(plan => plan.phase !== "expired");
  const frontier = (request.frontier_deadlines as unknown[])[0];
  const deadline = jsonObject(frontier);
  if (deadline?.source === "continuous_monitor") {
    const projected = monitorPlan({title: deadline.identity, next_due_at: deadline.next_due_at}, request);
    if (projected && !live.some(plan => plan.selected_monitor_identity === projected.selected_monitor_identity && plan.next_due_at === projected.next_due_at)) live.push(projected);
  }
  if (!live.length) return expired ? {phase: "expired", expired_monitor_count: expired,
    host_floor_minutes: 15, base_progression_minutes: [15, 30, 60], progression_minutes: null, reset_profile: null} : null;
  const rank: Record<string, number> = {active_window: 0, near_window: 1, cadence_only: 2, far_window: 3};
  live.sort((a, b) => Number(a.cap_minutes || 1e9) - Number(b.cap_minutes || 1e9)
    || (rank[String(a.phase)] ?? 99) - (rank[String(b.phase)] ?? 99)
    || compareMonitorIdentity(a.selected_monitor_identity, b.selected_monitor_identity));
  return {...live[0], base_progression_minutes: [15, 30, 60], candidate_count: live.length, expired_monitor_count: expired};
}

export interface HintProfile {
  action: string; cadence_class: string; reason: string; interval: number; maximum: number;
  cli_limit: number | null; claude_limit: number | null; advance?: boolean;
  progression?: number[]; reset?: JsonObject; detail?: JsonObject; notification_interval?: number;
}

export function boundedMonitorProfile(profile: HintProfile, payload: JsonObject, request: JsonObject): HintProfile {
  const plan = waitPlan(payload, request);
  const progression = Array.isArray(plan?.progression_minutes) && plan.progression_minutes.length ? plan.progression_minutes as number[] : undefined;
  const initial = progression?.[0] ?? profile.interval;
  const reset = jsonObject(plan?.reset_profile);
  return {...profile, interval: initial, progression,
    reset: reset ? {cadence_class: profile.cadence_class, app_automation_initial_interval_minutes: initial,
      app_automation_initial_rrule: rruleForMinutes(initial), app_automation_max_interval_minutes: profile.maximum,
      unchanged_poll_backoff_multiplier: 2, local_scheduler_unchanged_poll_limit: profile.cli_limit,
      claude_code_loop_unchanged_poll_limit: profile.claude_limit, ...reset} : undefined,
    detail: plan ?? undefined};
}
