import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject, requireJsonObject } from "../runtime_decode.ts";
import {
  evaluateTodoResumeConditions,
  normalizeTodoResumeWhen,
  resumeConditionHasKnownPendingTarget,
  TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
  TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION,
} from "../todos/resume_condition.ts";

export const BLOCKED_WAIT_REQUEST_SCHEMA = "loopx_quota_blocked_wait_request_v0";
const CAUSAL_WAIT_SCHEMA = "quota_blocked_causal_wait_v0";
export const MONITOR_UNAVAILABLE_SCHEMA = "quota_monitor_unavailable_v0";

function reject(message: string): never {
  throw new EffectRuntimeRequestError(`typed blocked no-spend closeout ${message}`);
}

function causalCondition(waiting: JsonObject, target: JsonObject): JsonObject | null {
  if (!["open", "deferred"].includes(String(waiting.status)) || waiting.role !== "agent" ||
      (waiting.archive_state != null && waiting.archive_state !== "active") ||
      waiting.task_class !== "advancement_task" ||
      waiting.resume_ready !== false || waiting.todo_id === target.todo_id ||
      typeof waiting.resume_when !== "string") return null;
  const kind = waiting.resume_when.split(":", 1)[0];
  if (kind !== "monitor_changed" && kind !== "todo_done") return null;
  if (target.archive_state != null && target.archive_state !== "active") return null;
  if (kind === "monitor_changed" &&
      (typeof target.material_change_generation !== "number" ||
       !Number.isSafeInteger(target.material_change_generation) ||
       target.material_change_generation < 0)) return null;
  const evaluated = evaluateTodoResumeConditions({
    schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [waiting], source_items: [target],
  });
  const rows = evaluated.conditions as JsonObject[];
  const condition = jsonObject(rows[0]?.condition);
  if (kind === "monitor_changed" &&
      condition?.material_change_generation !== waiting.resume_monitor_generation) return null;
  return condition && resumeConditionHasKnownPendingTarget(condition, waiting)
    ? condition : null;
}

/** Frozen canonical dependency facts, not a caller-authored wait string. The
 * durable writeback/guard receipt binds these facts to the exact Turn. Readback
 * must not re-evaluate a historical wait against today's dependency state. */
export function isCausalBlockedWait(value: unknown, todoId: string | null): boolean {
  const wait = jsonObject(value);
  const waiting = jsonObject(wait?.waiting_todo);
  const target = jsonObject(wait?.target_todo);
  if (!wait || wait.schema_version !== CAUSAL_WAIT_SCHEMA || wait.source !== "todo" ||
      !todoId || wait.todo_id !== todoId || waiting?.todo_id !== todoId ||
      !target || wait.resume_when !== waiting.resume_when ||
      timestamp(wait.observed_at) === null) return false;
  try {
    return causalCondition(waiting, target) !== null;
  } catch {
    return false;
  }
}

function timestamp(value: unknown): number | null {
  if (typeof value !== "string") return null;
  try {
    const resume = normalizeTodoResumeWhen({
      schema_version: TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION,
      resume_when: `resume_at:${value}`,
    });
    return resume ? Date.parse(resume.slice("resume_at:".length)) : null;
  } catch {
    return null;
  }
}

function retainedTodo(todo: JsonObject): JsonObject {
  const fields = ["todo_id", "role", "status", "task_class", "archive_state",
    "resume_when", "resume_ready", "resume_monitor_generation", "material_change_generation"];
  return Object.fromEntries(fields.filter((field) => todo[field] !== undefined)
    .map((field) => [field, todo[field]]));
}

/** An unavailable attempt is not an observation or a retry clock. The
 * surrounding settlement owner verifies the exact guard, typed blocker and
 * evidence before committing this frozen, canonical Monitor scope. */
export function isMonitorUnavailableWait(value: unknown, todoId: string | null): boolean {
  const wait = jsonObject(value);
  const monitor = jsonObject(wait?.monitor_todo);
  return !!wait && wait.schema_version === MONITOR_UNAVAILABLE_SCHEMA &&
    wait.source === "turn_settlement" && wait.observation_available === false &&
    !!todoId && wait.todo_id === todoId && monitor?.todo_id === todoId &&
    monitor.role === "agent" && monitor.task_class === "continuous_monitor" &&
    monitor.status === "open" &&
    (monitor.archive_state == null || monitor.archive_state === "active") &&
    timestamp(wait.observed_at) !== null;
}

/** Preflight belongs to the same TS settlement owner as durable readback.
 * Python only transports the complete current Todo facts and observation clock. */
export function prepareBlockedWait(value: unknown): JsonObject {
  const request = requireJsonObject(value, "blocked wait request");
  if (request.schema_version !== BLOCKED_WAIT_REQUEST_SCHEMA || !Array.isArray(request.todos)) {
    reject("requires current Todo facts");
  }
  const todos = request.todos.map((value) => requireJsonObject(value, "blocked wait Todo"));
  const matches = todos.filter((todo) => todo.todo_id === request.todo_id);
  const todo = matches[0];
  if (matches.length !== 1 || !todo || !["open", "deferred"].includes(String(todo.status))) {
    reject("requires the same unfinished Todo");
  }
  const observed = timestamp(request.observed_at);
  if (observed === null) reject("has an invalid timestamp");
  if (todo.task_class === "continuous_monitor") {
    if (todo.role !== "agent" || todo.status !== "open" ||
        (todo.archive_state != null && todo.archive_state !== "active") ||
        request.allow_turn_settlement_retry !== true) {
      reject("requires an admitted open Monitor attempt on canonical authority");
    }
    const fields = ["claimed_by", "last_checked_at", "next_due_at", "result_hash",
      "material_change", "material_change_generation", "cadence", "expires_at", "watch_only"];
    return {schema_version: MONITOR_UNAVAILABLE_SCHEMA, source: "turn_settlement",
      todo_id: request.todo_id, observed_at: request.observed_at, observation_available: false,
      monitor_todo: {...retainedTodo(todo), ...Object.fromEntries(
        fields.filter(field => todo[field] !== undefined).map(field => [field, todo[field]]))}};
  }
  if (todo.task_class !== "advancement_task") reject("requires the same unfinished advancement Todo");
  const resume = todo.resume_when;
  const condition = jsonObject(todo.resume_condition);
  if (typeof resume === "string" && /^(?:monitor_changed|todo_done):/.test(resume)) {
    const targets = todos.filter((target) => target.todo_id === resume.slice(resume.indexOf(":") + 1));
    const target = targets[0];
    const current = targets.length === 1 && target ? causalCondition(todo, target) : null;
    if (!target || !condition || !current ||
        !resumeConditionHasKnownPendingTarget(condition, todo)) {
      reject("requires a registered pending causal target with its captured generation");
    }
    // A projection must describe exactly the same generation as its source.
    if (condition.kind === "monitor_changed" &&
        condition.material_change_generation !== current.material_change_generation) {
      reject("has a stale causal target generation");
    }
    return { schema_version: CAUSAL_WAIT_SCHEMA, source: "todo", todo_id: request.todo_id,
      resume_when: resume, observed_at: request.observed_at,
      waiting_todo: retainedTodo(todo), target_todo: retainedTodo(target) };
  }
  if (typeof resume === "string" && resume.startsWith("pr_merged:")) {
    reject("cannot qualify a PR merge wait from Todo facts alone; use a registered monitor_changed or todo_done dependency with current readback, then retry this same Turn. A PR number is not pending-dependency evidence");
  }
  if (!resume && request.allow_turn_settlement_retry === true && todo.status === "open") {
    const due = new Date(observed + 300_000).toISOString().replace(".000Z", "Z");
    return { schema_version: "quota_blocked_retry_v0", source: "turn_settlement",
      todo_id: request.todo_id, resume_when: `resume_at:${due}`,
      observed_at: request.observed_at, due_at: due };
  }
  if (typeof resume !== "string" || !resume.startsWith("resume_at:") ||
      todo.resume_ready !== false || !condition || condition.kind !== "resume_at" ||
      condition.resume_when !== resume || condition.satisfied !== false) {
    reject("requires the same unfinished Todo to have a pending resume_when=resume_at:<timezone-aware-time> wait or registered causal wait; schedule it with todo update, read it back, then retry this Turn");
  }
  const dueAt = resume.slice("resume_at:".length);
  const due = timestamp(dueAt);
  if (due === null) reject("has an invalid timestamp");
  const delay = (due - observed) / 1000;
  if (delay < 60 || delay > 1800) reject("retry wait must be due in 1–30 minutes; update the Todo resume_at and retry this same Turn");
  return { schema_version: "quota_blocked_retry_v0", source: "todo", todo_id: request.todo_id,
    resume_when: resume, observed_at: request.observed_at, due_at: dueAt };
}

export const RECEIPT_BOUND_WAIT_REQUEST_SCHEMA = "loopx_quota_receipt_bound_wait_request_v0";

/** A pending dependency or blocked Monitor changes executable work, never the Turn's binding.
 * This read-only projection cannot settle a Turn. Causal waits need verified
 * refresh-state closeout; an unpolled blocked Monitor needs lifecycle repair. */
export function projectReceiptBoundWait(value: unknown): JsonObject {
  const request = requireJsonObject(value, "receipt-bound wait request");
  if (request.schema_version !== RECEIPT_BOUND_WAIT_REQUEST_SCHEMA ||
      typeof request.turn_instance_id !== "string" || !request.turn_instance_id ||
      typeof request.agent_id !== "string" || !request.agent_id || !Array.isArray(request.todos)) {
    reject("requires a bound Turn and current Todo facts");
  }
  const todos = request.todos.map(row => requireJsonObject(row, "receipt-bound Todo"));
  const matches = todos.filter(row => row.todo_id === request.todo_id);
  const todo = matches[0];
  if (matches.length !== 1 || !todo || todo.role !== "agent" ||
      todo.archive_state !== "active" || (todo.claimed_by && todo.claimed_by !== request.agent_id)) {
    return {status: "none"};
  }
  if (todo.task_class === "continuous_monitor" && todo.status === "blocked" &&
      request.monitor_phase === "poll_due") {
    const reason = "The Monitor bound to this Turn is blocked. Inspect its blocker and existing effects; only after verifying the blocker is resolved, restore the Monitor and re-enter this same Turn. A blocked status is not a poll receipt and does not authorize replan or independent work.";
    return boundRecovery(request, "lifecycle", reason);
  }
  if (!["open", "deferred"].includes(String(todo.status)) ||
      todo.task_class !== "advancement_task" || todo.resume_ready !== false) return {status: "none"};
  // This projection repairs registered Todo dependencies. Other wait kinds
  // retain their existing route: in particular, a valid long timer must not
  // be rejected by the separate 1–30 minute blocked-retry writeback budget.
  const kind = jsonObject(todo.resume_condition)?.kind;
  if (kind !== "monitor_changed" && kind !== "todo_done") return {status: "none"};
  // Share the real writeback validation of target existence and generation.
  const wait = prepareBlockedWait({...request, schema_version: BLOCKED_WAIT_REQUEST_SCHEMA,
    allow_turn_settlement_retry: false});
  const reason = "The Todo bound to this Turn now waits on a dependency. Record its verified blocked closeout without spending quota; select independent work on the next host Turn.";
  return boundRecovery(request, "blocked_writeback", reason, wait);
}

/** Both routes preserve the existing binding and grant only control-plane recovery. */
function boundRecovery(
  request: JsonObject, repair: "lifecycle" | "blocked_writeback", reason: string, wait?: JsonObject,
): JsonObject {
  const obligation = repair === "blocked_writeback"
    ? "close_bound_wait_without_spend" : "repair_bound_monitor_lifecycle";
  return {
    status: "recovery_required",
    recovery: {schema_version: "unsettled_host_turn_recovery_v0", scope: "current_turn",
      turn_instance_id: request.turn_instance_id, binding_kind: "todo",
      binding_id: request.todo_id, repair, ...(wait ? {wait} : {})},
    obligation: {lane: "control_plane_recovery",
      next_lane: repair === "blocked_writeback" ? "advancement_task" : "continuous_monitor",
      obligation, contract: "repair_bound_turn_closeout",
      contract_obligation: obligation, must_attempt_work: true,
      delivery_allowed: false, notify: "DONT_NOTIFY", spend_policy: "no spend for blocked closeout",
      reason_code: repair === "blocked_writeback" ? "receipt_bound_pending_wait" : "unsettled_host_turn",
      reason, recommendation_reason: reason,
      unsettled_reason: reason, recommended_action: reason},
  };
}
