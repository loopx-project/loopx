/** Receipt qualification and ordered next-work decisions. Journal recovery is
 * composed, not replaced: neither decision dispatches a host or settles a Turn. */
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { settlementIdentityFromPlan } from "../effect_program.ts";
import { jsonObject, type JsonObject } from "../runtime_decode.ts";
import { turnEnvelopeRoute as typedTurnRoute } from "../quota/turn_envelope.ts";
import { EffectiveAction } from "../quota/effective_action.generated.ts";
import { stripPythonWhitespace } from "../coordination/todo_agents.ts";
import { pythonFieldText } from "../todos/read_codec.ts";
import { interpretTurnJournal } from "./turn_journal.ts";
import { normalizeHostFailure } from "./host_failure.ts";
import { taskValidationFailure } from "./task_validation_failure.ts";
import { TURN_CONTROLLER_CONTRACT, TURN_RESULT_KINDS,
  type LoopXTurnRouteValue, type LoopDispositionValue } from "./turn_contract_generated.ts";

const object = (value: unknown): JsonObject => jsonObject(value) ?? {};
const text = (value: unknown): string => pythonFieldText({value}, "value");
const repr = (value: unknown): string => text([value]).slice(1, -1);
const has = (value: JsonObject, key: string): boolean => Object.hasOwn(value, key);
class ControllerTypeError extends EffectRuntimeRequestError {}
function requireThat(condition: unknown, message: string): asserts condition {
  if (!condition) throw new EffectRuntimeRequestError(message);
}
type Lineage = Record<"goal_id" | "agent_id" | "todo_id", string>;
function lineage(value: unknown): Lineage {
  const raw = object(value);
  return {goal_id: text(raw.goal_id), agent_id: text(raw.agent_id), todo_id: text(raw.todo_id)};
}

// Retain the public TS name while sharing the current driver/envelope owner.
export { typedTurnRoute };

function decisionLineage(decision: JsonObject): Lineage {
  const orchestration = object(decision.task_orchestration_contract);
  const primary = stripPythonWhitespace(text(orchestration.primary_todo_id));
  const todo = orchestration.schema_version === "task_orchestration_contract_v2"
    && orchestration.mode === "adaptive" && primary
    ? primary : text(object(object(decision.action).selected_todo).todo_id);
  return {...lineage(decision), todo_id: todo};
}

function materialEffect(execution: JsonObject, expectedEffect: string | null): string {
  const settlement = object(execution.settlement_result);
  requireThat(settlement.ok === true && settlement.failure == null,
    "material Turn execution requires a successful settlement");
  if (!Array.isArray(settlement.receipts))
    throw new ControllerTypeError("material Turn execution is missing settlement receipts");
  const receipts = settlement.receipts.map(object);
  requireThat(JSON.stringify(receipts.map(item => text(item.step_kind)))
    === JSON.stringify(["validation", "durable_writeback", "quota_spend"]),
  "material Turn execution requires ordered validation/writeback/spend receipts");
  const ids = new Set(receipts.map(item => text(item.effect_id)));
  requireThat(!ids.has("") && ids.size === 1,
    "material Turn settlement receipts must share one effect identity");
  const effect = [...ids][0];
  requireThat(expectedEffect && expectedEffect === effect,
    "material Turn settlement identity does not match the transaction receipt");
  requireThat(receipts.every(item => item.status === "committed"),
    "material Turn settlement receipts must be committed");
  const effects = object(execution.effects);
  const replay = execution.replayed === true && execution.dry_run === false
    && execution.status === "committed"
    // Retain the historical Python numeric equality for this v0 replay marker.
    && (execution.quota_slot_spend_count === 1 || execution.quota_slot_spend_count === true)
    && effects.host_invoked === false && effects.state_written === false && effects.quota_spent === false;
  requireThat(replay || effects.state_written === true && effects.quota_spent === true,
    "material Turn execution is missing durable effect evidence");
  requireThat(object(execution.scheduler).completed === true,
    "material Turn execution is missing completed scheduler handoff");
  return effect;
}

function completionOutcome(value: unknown, expectedTodo: string): JsonObject {
  if (!jsonObject(value)) throw new ControllerTypeError("validated completion is missing its durable Todo outcome");
  const completion = object(value);
  const todo = text(completion.todo_id);
  requireThat(expectedTodo && todo === expectedTodo, "validated completion is missing its durable Todo outcome");
  const continuation = text(completion.continuation);
  requireThat(["successor", "active_goal", "no_followup"].includes(continuation),
    "validated completion has an invalid continuation outcome");
  const outcome: JsonObject = {todo_id: todo, continuation};
  if (continuation === "successor") {
    const successors = completion.successor_todo_ids;
    requireThat(Array.isArray(successors) && successors.length
      && successors.every(item => typeof item === "string" && item),
    "successor completion requires successor Todo ids");
    outcome.successor_todo_ids = [...successors];
  }
  return outcome;
}

export function qualifyExecution(value: unknown, integers: JsonObject = {}, requestIdentity?: unknown): JsonObject {
  const execution = object(value);
  requireThat(execution.schema_version === "loopx_turn_execution_v0",
    "validated turn receipt requires schema_version=loopx_turn_execution_v0");
  const receipt = object(execution.receipt);
  requireThat(receipt.schema_version === "loopx_turn_receipt_validation_v0",
    "validated turn receipt requires schema_version=loopx_turn_receipt_validation_v0");
  requireThat(receipt.ok === true, "validated turn receipt requires ok=true");
  const kind = text(receipt.result_kind);
  requireThat((TURN_RESULT_KINDS as readonly string[]).includes(kind),
    `validated turn receipt has unsupported result_kind ${repr(receipt.result_kind)}`);
  requireThat(execution.result_kind === kind, "execution result_kind does not match its receipt");
  const material = ["validated_completion", "validated_progress"].includes(kind);
  requireThat(!material || receipt.status === "committed" && execution.status === "committed",
    `material result ${kind} requires a fully committed Turn execution and receipt`);
  const identity = object(receipt.lineage);
  if (requestIdentity != null) {
    const decoded = settlementIdentityFromPlan({settlement_plan: {identity: requestIdentity}});
    const expected = decoded.value;
    requireThat(expected?.binding_kind === "request" && Object.keys(identity).length === 4
      && identity.goal_id === expected.goal_id && identity.agent_id === expected.agent_id
      && identity.todo_id === "" && identity.request_binding_id === expected.binding_id
      && receipt.settlement_effect_id === expected.effect_id,
    "validated receipt does not match its original request identity");
  } else {
    requireThat(text(identity.goal_id) && text(identity.agent_id) && text(identity.todo_id),
      "validated turn receipt is missing goal/agent/todo lineage");
  }
  const turnKey = text(receipt.turn_key);
  requireThat(turnKey, "validated turn receipt is missing turn_key");
  let effect = text(receipt.settlement_effect_id) || null;
  let failure: JsonObject | null = null;
  if (has(execution, "host_failure")) {
    requireThat(kind === "host_failure", "only a host_failure receipt may carry host failure metadata");
    failure = normalizeHostFailure(execution.host_failure, integers.failure_attempt as string | null | undefined);
  }
  if (material) effect = materialEffect(execution, effect);
  const completion = kind === "validated_completion"
    ? completionOutcome(object(execution.todo_completion), text(identity.todo_id)) : object(execution.todo_completion);
  const taskFailure = kind === "validation_failed" && has(execution, "validation_stage")
    ? taskValidationFailure({result_kind: kind, status: execution.status, receipt,
      validation: execution.validation, validation_stage: execution.validation_stage}).failure : null;
  return {schema_version: "loop_validated_turn_receipt_v0", result_kind: kind, status: text(receipt.status),
    lineage: {...lineage(identity), ...(text(identity.request_binding_id)
      ? {request_binding_id: identity.request_binding_id} : {})}, turn_key: turnKey, settlement_effect_id: effect,
    ...(failure ? {host_failure: failure} : {}), ...(taskFailure ? {task_failure: taskFailure} : {}),
    ...(Object.keys(completion).length ? {todo_completion: completion} : {})};
}

interface Rule {id: string; when: Record<string, readonly (string | boolean)[]>;
  check?: string; disposition?: string; reason?: string | Record<string, string>; lineage?: string; extra?: string}
type Budget = {lineage: Lineage; max_turns: bigint; completed_turns: bigint};
function budget(value: unknown): Budget | null {
  if (value == null) return null;
  const raw = object(value);
  // Python's public value object admits arbitrary integers. Decimal transport
  // preserves their exact comparison instead of rounding at the JSON bridge.
  const integer = (v: unknown): bigint => {
    requireThat(typeof v === "string" && /^-?\d+$/.test(v), "invalid bounded turn budget transport");
    return BigInt(v);
  };
  const maximum = integer(raw.max_turns);
  const completed = integer(raw.completed_turns);
  requireThat(maximum > 0n, "bounded turn budget max_turns must be > 0");
  requireThat(completed >= 0n, "bounded turn budget completed_turns must be >= 0");
  requireThat(completed <= maximum, "bounded turn budget completed_turns must be <= max_turns");
  const identity = lineage(raw.lineage);
  requireThat(identity.goal_id && identity.agent_id && identity.todo_id,
    "bounded turn budget is missing goal/agent/todo lineage");
  return {lineage: identity, max_turns: maximum, completed_turns: completed};
}

/** Lazy rows retain validation precedence. In particular capability/user/terminal
 * returns never require a later progress budget or host retry policy. */
export function decideLoopDisposition(params: JsonObject): JsonObject {
  const receipt = params.receipt == null ? null : object(params.receipt);
  const decision = object(params.decision);
  let route: LoopXTurnRouteValue = "contract_error";
  let chosen: Lineage = lineage({});
  let effective = {...chosen};
  let retryState = "not_applicable";
  let bounded: Budget | null = null;
  const kind = receipt ? text(receipt.result_kind) : "absent";
  const receiptLineage = lineage(receipt?.lineage);
  let failure = object(receipt?.host_failure);
  const completion = object(receipt?.todo_completion);
  const projectRoute = (): LoopDispositionValue => {
    const projected = TURN_CONTROLLER_CONTRACT.route_projection[route];
    requireThat(projected !== null, "quota decision failed the shared envelope contract (schema or signature hashes)");
    return projected;
  };
  const fact = (name: string): string | boolean => {
    switch (name) {
      case "receipt_present": return receipt !== null;
      case "receipt_kind": return kind;
      case "route": return route;
      case "validation_recovery": return text(object(receipt?.task_failure).recovery_kind) || "absent";
      case "terminal_action": return decision.effective_action === EffectiveAction.TERMINAL_NO_FOLLOWUP;
      case "completion": return kind === "validated_completion" ? text(completion.continuation) : "not_applicable";
      case "host_failure_present": return Object.keys(failure).length > 0;
      case "retry_state": return retryState;
      case "budget_state": return kind !== "validated_progress" ? "not_applicable"
        : bounded === null ? "absent" : bounded.completed_turns >= bounded.max_turns ? "exhausted" : "available";
      default: throw new EffectRuntimeRequestError(`unsupported controller partition: ${name}`);
    }
  };
  const check = (name: string): void => {
    switch (name) {
      case "envelope":
        route = typedTurnRoute(decision, jsonObject(params.historical_route_text) ?? undefined);
        requireThat(route !== "contract_error", "quota decision failed the shared envelope contract (schema or signature hashes)");
        if (object(decision.user).action_required === true) route = "user_action_required";
        break;
      case "decision_actor":
        chosen = decisionLineage(decision);
        requireThat(chosen.goal_id && chosen.agent_id, "fresh quota decision is missing goal/agent lineage");
        effective = {...chosen}; break;
      case "receipt_binding":
        requireThat(text(params.predecessor_turn_key), "stale_receipt: continuation is missing predecessor_turn_key; cannot prove causal succession from the receipt");
        requireThat(text(params.predecessor_turn_key) === receipt?.turn_key,
          `stale_receipt: fresh decision predecessor_turn_key ('${text(params.predecessor_turn_key)}') does not match the receipt turn_key ('${text(receipt?.turn_key)}')`);
        for (const key of ["goal_id", "agent_id"] as const)
          requireThat(receiptLineage[key] === chosen[key],
            `stale_receipt: receipt lineage does not match the fresh decision on ${key} (receipt='${receiptLineage[key]}', decision='${chosen[key]}')`);
        break;
      case "initial_terminal":
        requireThat(decision.state === EffectiveAction.TERMINAL_NO_FOLLOWUP, "terminal no-follow-up requires fresh Goal frontier state"); break;
      case "initial_todo":
        requireThat(chosen.todo_id || ["wait", "user_action_required"].includes(projectRoute()),
          "executable quota decision is missing selected Todo lineage"); break;
      case "completion_terminal":
        requireThat(decision.effective_action === EffectiveAction.TERMINAL_NO_FOLLOWUP && decision.state === EffectiveAction.TERMINAL_NO_FOLLOWUP,
          "no_followup completion requires fresh terminal Goal frontier evidence"); break;
      case "completion_todo":
        requireThat(chosen.todo_id, "Todo completion continuation requires a fresh selected Todo");
        if (fact("completion") === "successor")
          requireThat(Array.isArray(completion.successor_todo_ids) && completion.successor_todo_ids.includes(chosen.todo_id),
            "stale_receipt: fresh decision is not a declared completion successor");
        else if (fact("completion") === "active_goal") requireThat(chosen.todo_id !== receiptLineage.todo_id,
          "stale_receipt: active Goal continuation reselected the completed Todo");
        break;
      case "receipt_todo":
        if (chosen.todo_id) requireThat(chosen.todo_id === receiptLineage.todo_id,
          `stale_receipt: receipt Todo does not match the fresh decision (receipt='${receiptLineage.todo_id}', decision='${chosen.todo_id}')`);
        else {
          requireThat(kind !== "validated_progress" || ["user_action_required", "wait"].includes(route),
            "receipt-backed executable decision is missing selected Todo lineage");
          effective.todo_id = receiptLineage.todo_id;
        }
        break;
      case "progress_budget":
        requireThat(params.budget != null, "validated progress cannot continue without a proven bounded turn budget");
        bounded = budget(params.budget);
        for (const key of ["goal_id", "agent_id", "todo_id"] as const)
          requireThat(bounded?.lineage[key] === effective[key],
            `stale_budget: budget lineage does not match the fresh decision on ${key} (budget='${bounded?.lineage[key]}', decision='${effective[key]}')`);
        break;
      case "host_retry": {
        const normalized = normalizeHostFailure(failure,
          object(params.integer_facts).failure_attempt as string | null | undefined);
        failure = normalized;
        retryState = normalized.retryable === true
          ? Number(normalized.attempt) < Number(object(normalized.retry).max_attempts) ? "available" : "exhausted"
          : "not_retryable";
        break;
      }
      default: throw new EffectRuntimeRequestError(`unsupported controller check: ${name}`);
    }
  };
  for (const rule of TURN_CONTROLLER_CONTRACT.rules as readonly Rule[]) {
    if (!Object.entries(rule.when).every(([name, values]) => values.includes(fact(name)))) continue;
    if (rule.check) { check(rule.check); continue; }
    const disposition = rule.disposition === "project_route" ? projectRoute() : rule.disposition;
    const reasons = rule.reason;
    const reason = typeof reasons === "string" ? reasons : reasons?.[String(disposition)] ?? reasons?.default;
    requireThat(typeof reason === "string", `controller rule ${rule.id} has no reason for ${disposition}`);
    const result: JsonObject = {schema_version: "loop_turn_loop_disposition_v0", disposition,
      reason: reason.replaceAll("{failure_kind}", text(failure.kind) || "None").replaceAll("{receipt_kind}", kind),
      spends_quota: false, launches_host: false, writes_state: false,
      lineage: rule.lineage === "receipt" ? receiptLineage : rule.lineage === "decision" ? chosen : effective};
    if (disposition === "replan") result.replan_continuation = {
      requires_bounded_delta: true, delta_kinds: ["todo_delta", "vision_delta"],
      stale_todo_rerun_allowed: false, fresh_envelope_required: true};
    else if (rule.extra === "capability") result.capability_action = object(object(decision.action).capability_intent);
    else if (rule.extra === "iteration_stop") Object.assign(result,
      {stop_scope: "iteration", goal_terminal: false, continuation_required: false});
    else if (rule.extra === "retry") {
      const retry = object(failure.retry);
      result.retry_continuation = {same_turn: true, retry_failed_turn: true, strategy: retry.strategy,
        retry_after_seconds: retry.backoff_seconds, attempt: failure.attempt, max_attempts: retry.max_attempts,
        fresh_envelope_required: true, model_fallback_allowed: false};
    }
    return result;
  }
  throw new EffectRuntimeRequestError("controller contract has no disposition for the qualified inputs");
}

function positive(value: unknown, field: string, exact?: unknown): number | string {
  if (exact === undefined) {
    requireThat(typeof value === "number" && Number.isInteger(value) && value >= 1, `${field} must be a positive integer`);
    return value;
  }
  requireThat(typeof exact === "string" && /^\d+$/.test(exact) && BigInt(exact) >= 1n
    && Number(exact) === value, `${field} must be a positive integer`);
  return BigInt(exact) > BigInt(Number.MAX_SAFE_INTEGER) ? exact : Number(exact);
}

export function decideManagedStep(params: JsonObject): JsonObject {
  const journal = object(params.journal);
  requireThat(journal.status === "failed", "managed step requires a failed Turn journal");
  requireThat(text(journal.result_kind) === "host_failure", "managed step requires a typed host failure Turn");
  const integers = object(params.integer_facts);
  const attempt = positive(journal.host_attempt_count, "Turn journal host_attempt_count", integers.host_attempt_count);
  const failure = normalizeHostFailure(journal.host_failure, integers.failure_attempt as string | null | undefined);
  const retry = object(failure.retry);
  const maxAttempts = failure.retryable === true ? positive(retry.max_attempts, "Turn journal retry max_attempts") : null;
  for (const [name, authority, message] of [
    ["observed_attempt", attempt, "observed_attempt disagrees with the Turn journal attempt authority"],
    ["observed_max_attempts", maxAttempts, "observed_max_attempts disagrees with the Turn journal retry policy"],
  ] as const) {
    if (params[name] != null) {
      const observation = positive(params[name], name, integers[name]);
      requireThat(authority === null || observation === authority, message);
    }
  }
  requireThat(text(journal.turn_key) === params.turn_key, "Turn journal turn_key does not match the requested Turn");
  requireThat(failure.retryable === true, `host failure ${failure.kind} is not retryable; repair instead`);
  // normalizeHostFailure already proved the exact integer; the decimal form is
  // retained above the JSON safe range for equality and Python readback.
  requireThat(attempt === failure.attempt, "Turn journal host_failure attempt disagrees with host_attempt_count");
  if (!jsonObject(journal.plan)) throw new ControllerTypeError("Turn journal has no stored plan to reconcile");
  const transaction = object(object(journal.plan).transaction);
  requireThat(text(journal.turn_key) && text(transaction.turn_key) === text(journal.turn_key),
    "Turn journal turn_key does not match its stored transaction");
  if (!jsonObject(journal.receipt)) throw new ControllerTypeError("Turn journal has no validated receipt to reconcile");
  const receipt = object(journal.receipt);
  requireThat(receipt.schema_version === "loopx_turn_receipt_validation_v0", "Turn journal receipt has an unsupported schema");
  requireThat(receipt.ok === true, "Turn journal receipt is not a validated receipt");
  // Qualify persisted receipt fields directly. No execution-shaped object or
  // caller-invented effects stand in for the original journal.
  requireThat((TURN_RESULT_KINDS as readonly unknown[]).includes(receipt.result_kind),
    `validated turn receipt has unsupported result_kind ${repr(receipt.result_kind)}`);
  requireThat(!["validated_progress", "validated_completion"].includes(text(receipt.result_kind)),
    `material result ${text(receipt.result_kind)} requires a fully committed Turn execution and receipt`);
  requireThat(receipt.result_kind === "host_failure", "only a host_failure receipt may carry host failure metadata");
  const identity = lineage(receipt.lineage);
  requireThat(identity.goal_id && identity.agent_id && identity.todo_id,
    "validated turn receipt is missing goal/agent/todo lineage");
  requireThat(text(receipt.turn_key), "validated turn receipt is missing turn_key");
  requireThat(identity.goal_id === params.goal_id && identity.agent_id === params.agent_id,
    "Turn journal lineage does not match the requested goal/agent");
  const inspection = interpretTurnJournal({schema_version: "loopx_turn_journal_interpretation_request_v0",
    journal, goal_id: text(params.goal_id), agent_id: text(params.agent_id), turn_key: text(params.turn_key)});
  requireThat(inspection.journal_consistent, `Turn journal replay is blocked: ${inspection.violations.join(", ")}`);
  const decision = object(params.decision);
  for (const key of ["goal_id", "agent_id"])
    requireThat(!text(decision[key]) || text(decision[key]) === params[key], `fresh decision ${key} does not match the managed step`);
  const disposition = decideLoopDisposition({receipt: {...receipt, lineage: identity, host_failure: journal.host_failure}, decision,
    integer_facts: integers, historical_route_text: params.historical_route_text,
    predecessor_turn_key: params.predecessor_turn_key == null ? params.turn_key : params.predecessor_turn_key});
  const result: JsonObject = {schema_version: "loopx_turn_managed_step_v0", disposition: disposition.disposition,
    reason: disposition.reason, turn_key: params.turn_key, attempt};
  for (const key of ["lineage", "stop_scope", "goal_terminal", "continuation_required", "successor_todo_ids",
    "capability_action", "capability_action_required", "user_action", "retry_continuation"])
    if (has(disposition, key)) result[key] = disposition[key];
  if (jsonObject(disposition.retry_continuation)) result.max_attempts = object(disposition.retry_continuation).max_attempts;
  return result;
}

/** One request carries all facts for the chosen public boundary. Preserve Python
 * input-error classes/messages; transport/internal failures remain runtime errors. */
export function controllerRequest(params: JsonObject): JsonObject {
  try {
    switch (params.operation) {
      case "receipt": return {ok: true, value: qualifyExecution(params.execution, object(params.integer_facts), params.request_identity)};
      case "route": return {ok: true, value: {route: typedTurnRoute(object(params.decision),
        jsonObject(params.historical_route_text) ?? undefined)}};
      case "disposition": return {ok: true, value: decideLoopDisposition(params)};
      case "managed": return {ok: true, value: decideManagedStep(params)};
      default: throw new EffectRuntimeRequestError("unsupported Turn controller operation");
    }
  } catch (error) {
    if (!(error instanceof EffectRuntimeRequestError)) throw error;
    return {ok: false, error_type: error instanceof ControllerTypeError ? "TypeError" : "ValueError", message: error.message};
  }
}
