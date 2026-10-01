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
  if (matches.length !== 1 || !todo || !["open", "deferred"].includes(String(todo.status)) ||
      todo.task_class !== "advancement_task") reject("requires the same unfinished advancement Todo");
  const observed = timestamp(request.observed_at);
  if (observed === null) reject("has an invalid timestamp");
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

/** A pending dependency changes executable work, never the Turn's binding.
 * This is a read-only recovery projection. Only refresh-state can validate and
 * commit the existing blocked closeout; observing a wait does not settle it. */
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
  if (matches.length !== 1 || !todo || todo.role !== "agent" || !["open", "deferred"].includes(String(todo.status)) ||
      todo.archive_state !== "active" || todo.task_class !== "advancement_task" ||
      todo.resume_ready !== false || (todo.claimed_by && todo.claimed_by !== request.agent_id)) {
    return {status: "none"};
  }
  // This projection repairs registered Todo dependencies. Other wait kinds
  // retain their existing route: in particular, a valid long timer must not
  // be rejected by the separate 1–30 minute blocked-retry writeback budget.
  const kind = jsonObject(todo.resume_condition)?.kind;
  if (kind !== "monitor_changed" && kind !== "todo_done") return {status: "none"};
  // Share the real writeback validation of target existence and generation.
  const wait = prepareBlockedWait({...request, schema_version: BLOCKED_WAIT_REQUEST_SCHEMA,
    allow_turn_settlement_retry: false});
  const reason = "The Todo bound to this Turn now waits on a dependency. Record its verified blocked closeout without spending quota; select independent work on the next host Turn.";
  return {
    status: "recovery_required",
    recovery: {schema_version: "unsettled_host_turn_recovery_v0", scope: "current_turn",
      turn_instance_id: request.turn_instance_id, binding_kind: "todo",
      binding_id: request.todo_id, repair: "blocked_writeback", wait},
    obligation: {lane: "control_plane_recovery", next_lane: "advancement_task",
      obligation: "close_bound_wait_without_spend", contract: "repair_bound_turn_closeout",
      contract_obligation: "close_bound_wait_without_spend", must_attempt_work: true,
      delivery_allowed: false, notify: "DONT_NOTIFY", spend_policy: "no spend for blocked closeout",
      reason_code: "receipt_bound_pending_wait", reason, recommendation_reason: reason,
      unsettled_reason: reason, recommended_action: reason},
  };
}
