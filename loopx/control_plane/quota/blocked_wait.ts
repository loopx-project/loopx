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
const BLOCKED_LIFECYCLE_SCHEMA = "quota_blocked_lifecycle_v0";

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
    // The v0 receipt freezes only a pair and historically validates its direct
    // condition. Do not retroactively apply graph diagnosis to historical
    // receipts. New waits additionally validate the current graph below.
    items: [waiting], source_items: [{...target, resume_when: undefined}],
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

function currentCompletionCondition(todo: JsonObject, todos: JsonObject[]): JsonObject | null {
  if (typeof todo.resume_when !== "string" || !todo.resume_when.trim().toLowerCase().startsWith("todo_done:")) return null;
  const evaluated = evaluateTodoResumeConditions({schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [todo], source_items: todos, kinds: ["todo_done"]});
  return jsonObject((evaluated.conditions as JsonObject[])[0]?.condition);
}

const CYCLE_REPAIR = "The Todo bound to this Turn depends on a completion dependency cycle. Inspect the cycle and current owners; repair only an owner-reviewed obsolete dependency through the authorized Todo lifecycle with fresh owner/lease proof, then re-enter this same Turn. If the needed edit belongs to another owner, retain the binding and report that blocker. Do not qualify the cycle as an ordinary wait, clear valid dependencies, fabricate completion, or rebind the Turn to independent work.";

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

/** Freeze an existing blocked advancement lifecycle, never a retry schedule.
 * The settlement caller separately verifies the exact admission and blocked
 * observation receipts. These facts grant no completion or execution. */
export function isBlockedLifecycleWait(value: unknown, todoId: string | null): boolean {
  const wait = jsonObject(value), todo = jsonObject(wait?.blocked_todo);
  return !!wait && wait.schema_version === BLOCKED_LIFECYCLE_SCHEMA && wait.source === "todo" &&
    !!todoId && wait.todo_id === todoId && todo?.todo_id === todoId &&
    todo.role === "agent" && todo.task_class === "advancement_task" &&
    todo.status === "blocked" && (todo.done === undefined || todo.done === false) &&
    (todo.archive_state == null || todo.archive_state === "active") &&
    wait.resume_when == null && wait.due_at == null && timestamp(wait.observed_at) !== null;
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
  if (matches.length !== 1 || !todo || !["open", "deferred", "blocked"].includes(String(todo.status))) {
    reject("requires the same unfinished Todo");
  }
  const observed = timestamp(request.observed_at);
  if (observed === null) reject("has an invalid timestamp");
  if (todo.status === "blocked") {
    const wait = {schema_version: BLOCKED_LIFECYCLE_SCHEMA, source: "todo",
      todo_id: request.todo_id, observed_at: request.observed_at,
      blocked_todo: {...retainedTodo(todo), ...(todo.done === undefined ? {} : {done: todo.done})}};
    if (!isBlockedLifecycleWait(wait, String(request.todo_id))) {
      reject("requires the same active blocked advancement Todo");
    }
    return wait;
  }
  if (todo.task_class === "continuous_monitor") {
    if (todo.role !== "agent" || todo.status !== "open" ||
        (todo.archive_state != null && todo.archive_state !== "active")) {
      reject("requires an admitted open Monitor attempt");
    }
    // Monitor failure closeout has no retry timer or Todo mutation. The
    // settlement owner verifies its original admission and evidence for both
    // supported Markdown and canonical sources. Keep canonical-only implicit
    // advancement retry scheduling in the separate branch below.
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
    const fresh = currentCompletionCondition(todo, todos);
    if (fresh?.invalid_state === "dependency_cycle") reject("cannot qualify a completion dependency cycle; inspect dependency_cycle_todo_ids and repair the plan through the authorized Todo lifecycle");
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

/** A pending dependency changes executable work, never the Turn's receipts.
 * settlement_progress is the existing settlement owner's exact-Turn readback,
 * transported by the live guard, not an author-declared progress observation.
 * This projection grants no work and cannot settle or debit a Turn. */
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
      (todo.claimed_by && todo.claimed_by !== request.agent_id)) {
    return {status: "none"};
  }
  const progress = jsonObject(request.settlement_progress);
  const writebackRequired = progress?.schema_version === "quota_settlement_progress_v0" &&
      progress.next_step === "durable_writeback" &&
      (progress.state === "writeback_required" || progress.state === "writeback_receipt_required");
  // Ordinary completion is a validation receipt, not Turn settlement. The
  // verified replay phase, rather than terminal Todo status alone, owns this
  // recovery even after the row leaves the active presentation lane.
  const completedSettlement = todo.status === "done" && todo.task_class === "advancement_task" &&
      request.replay_phase === "settlement_pending" &&
      progress?.schema_version === "quota_settlement_progress_v0";
  if (completedSettlement && writebackRequired) {
    const reason = "The original Todo's completion is verified, but its Turn still owes durable writeback and settlement. Record only the validated original result, then spend once for that same identity. Inspect existing effects before retrying; do not repeat completed work, invent a successor, or rebind this Turn.";
    return boundRecovery(request, "durable_writeback", reason);
  }
  if (todo.archive_state !== "active" && !completedSettlement) return {status: "none"};
  if (progress?.schema_version === "quota_settlement_progress_v0" &&
      progress.next_step === "quota_spend" &&
      (progress.state === "spend_required" || progress.state === "spend_receipt_required")) {
    const reason = "The original Turn's durable writeback is verified. Its later dependency wait cannot replace that outcome. Execute only the original settlement spend command; the spend owner revalidates all receipts and acceptance, and a receipt repair cannot debit twice. Leave the Todo wait and successor unchanged.";
    return boundRecovery(request, "quota_spend", reason);
  }
  if (todo.task_class === "continuous_monitor" && todo.status === "blocked" &&
      request.monitor_phase === "poll_due") {
    const reason = "The Monitor bound to this Turn is blocked. Inspect its blocker and existing effects; only after verifying the blocker is resolved, restore the Monitor and re-enter this same Turn. A blocked status is not a poll receipt and does not authorize replan or independent work.";
    return boundRecovery(request, "lifecycle", reason);
  }
  if (todo.task_class === "advancement_task" && todo.status === "blocked") {
    const wait = prepareBlockedWait({...request, schema_version: BLOCKED_WAIT_REQUEST_SCHEMA});
    const reason = "The advancement Todo bound to this Turn is blocked. Inspect its existing effects and evidence, then close only this original attempt with a typed blocked writeback without spending. Keep the Todo blocked and its validator intact; no retry clock or execution is authorized. A fresh host Turn may select independent work.";
    return boundRecovery(request, "blocked_writeback", reason, wait);
  }
  const condition = jsonObject(todo.resume_condition);
  const kind = condition?.kind;
  const resumeWhen = kind !== "monitor_changed" && kind !== "todo_done"
    ? normalizeTodoResumeWhen({
      schema_version: TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION, resume_when: todo.resume_when,
    })
    : null;
  const externalWait = !!resumeWhen && condition?.kind === resumeWhen.split(":", 1)[0] &&
    condition.resume_when === resumeWhen;
  // A later external wait removes executable work, not the original Turn's
  // verified writeback duty. A due wait can become runnable before settlement;
  // it still cannot discard the original identity or authorize new work.
  if (["open", "deferred"].includes(String(todo.status)) &&
      todo.task_class === "advancement_task" &&
      writebackRequired && request.replay_phase === "open" && externalWait) {
    const reason = "The original admitted Turn still owes durable writeback. Its later external wait cannot discard that identity. Inspect existing effects and record only the validated original result, then settle once under the original identity. Keep the wait unchanged; do not repeat business work, select a successor, or invent a retry clock.";
    return boundRecovery(request, "durable_writeback", reason);
  }
  if (!["open", "deferred"].includes(String(todo.status)) ||
      todo.task_class !== "advancement_task" || todo.resume_ready !== false) return {status: "none"};
  // This projection repairs registered Todo dependencies. Other wait kinds
  // retain their existing route: in particular, a valid long timer must not
  // be rejected by the separate 1–30 minute blocked-retry writeback budget.
  if (kind !== "monitor_changed" && kind !== "todo_done") {
    return {status: "none"};
  }
  if (currentCompletionCondition(todo, todos)?.invalid_state === "dependency_cycle") {
    return boundRecovery(request, "dependency", CYCLE_REPAIR);
  }
  // Share the real writeback validation of target existence and generation.
  const wait = prepareBlockedWait({...request, schema_version: BLOCKED_WAIT_REQUEST_SCHEMA,
    allow_turn_settlement_retry: false});
  const reason = "The Todo bound to this Turn now waits on a dependency. Record its verified blocked closeout without spending quota; select independent work on the next host Turn.";
  return boundRecovery(request, "blocked_writeback", reason, wait);
}

/** Recovery routes preserve the existing binding and grant no independent work. */
function boundRecovery(
  request: JsonObject, repair: "lifecycle" | "blocked_writeback" | "durable_writeback" | "quota_spend" | "dependency", reason: string, wait?: JsonObject,
): JsonObject {
  const obligation = repair === "blocked_writeback"
    ? "close_bound_wait_without_spend" : repair === "quota_spend" || repair === "durable_writeback"
    ? "settle_bound_turn" : repair === "dependency"
    ? "repair_bound_dependency" : "repair_bound_monitor_lifecycle";
  return {
    status: "recovery_required",
    recovery: {schema_version: "unsettled_host_turn_recovery_v0", scope: "current_turn",
      turn_instance_id: request.turn_instance_id, binding_kind: "todo",
      binding_id: request.todo_id, repair, ...(wait ? {wait} : {})},
    obligation: {lane: "control_plane_recovery",
      next_lane: repair === "lifecycle" ? "continuous_monitor" : "advancement_task",
      obligation, contract: "repair_bound_turn_closeout",
      contract_obligation: obligation, must_attempt_work: true,
      delivery_allowed: false, notify: "DONT_NOTIFY",
      ...(repair === "quota_spend" ? {spend_allowed_now: true} : {}),
      spend_policy: repair === "durable_writeback"
        ? "settle the original admitted Turn after validated writeback; no independent work"
        : repair === "quota_spend"
        ? "settle the original verified writeback once; no independent work or new writeback"
        : "no spend for blocked closeout",
      reason_code: repair === "blocked_writeback" ? "receipt_bound_pending_wait" : "unsettled_host_turn",
      reason, recommendation_reason: reason,
      unsettled_reason: reason, recommended_action: reason},
  };
}
