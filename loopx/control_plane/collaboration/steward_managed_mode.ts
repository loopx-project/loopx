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
    "monitor_deadline", "monitor_lease_version", "report_window_id", "report_subscription_revision"];
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
    if (request.validation_command === null || subscription.enabled !== true || subscription.profile_preset !== "portfolio-progress"
        || subscription.route_ref !== `steward-private:${current.id}:${current.source}`
        || subscription.effective_revision !== request.report_subscription_revision
        || window.subscription_revision !== request.report_subscription_revision
        || window.window_id !== request.report_window_id || window.goal_id !== current.actor.goal_id
        || window.agent_id !== current.actor.agent_id || todo.todo_id !== request.todo_id
        || todo.target_key !== window.window_id || todo.claimed_by !== AGENT_ID
        || todo.action_kind !== "generate_periodic_report" || todo.status !== "open") {
      throw new EffectRuntimeRequestError("report execution requires the original live subscription, window and task");
    }
  } else if (request.report_window_id !== undefined || request.report_subscription_revision !== undefined) {
    throw new EffectRuntimeRequestError("report fields belong only to an original calendar report");
  }
  let refreshScope = false;
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
      if (!knownSelfRegistration && !observationOnly) {
        throw new EffectRuntimeRequestError("the original portfolio changed beyond the prepared steward creation");
      }
      // A pure native watch uses only the fresh scoped reader and cannot
      // invoke, reuse or mutate model context. Other changes leave the model's
      // old scope hash untouched so its existing isolation owner still wins.
      refreshScope = Boolean(knownSelfRegistration);
    }
  }
  return {actor: current.actor, workspace_path: current.workspace, host: "codex-app-server",
    refresh_original_scope: refreshScope, observation_only: observationOnly};
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
  const facts = goals.filter(row => row.goal_id !== params.actor_goal_id)
    .sort((a, b) => String(a.goal_id).localeCompare(String(b.goal_id)))
    .map(row => Object.fromEntries(["goal_id", "goal_instance_id", "activation_state", "quality",
      "attention", "progress", "goal_lifecycle", "agents", "deliveries"].map(key => [key, row[key] ?? null])));
  const resultHash = "sha256:" + createHash("sha256").update(JSON.stringify(canonical(facts))).digest("hex");
  return {result_hash: resultHash, material_change: params.previous_hash != null && params.previous_hash !== resultHash,
    coverage: context.coverage ?? null, observed_goal_count: goals.length,
    own_monitor_bookkeeping_excluded: true};
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
