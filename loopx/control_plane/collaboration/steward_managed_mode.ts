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

function basis(params: JsonObject) {
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
      || session.active_turn_id || params.queued_turn_count !== 0) {
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

export function planStewardManagedMode(params: JsonObject): JsonObject {
  const current = basis(params);
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
  const current = basis(params);
  const goal = current.goal;
  if (!goal || !Array.isArray(goal.registered_agents) || !goal.registered_agents.includes(AGENT_ID)) {
    throw new EffectRuntimeRequestError("native Goal and Agent registration must be read back before binding");
  }
  return normalizeStewardManagedActor({schema_version: STEWARD_MANAGED_ACTOR_SCHEMA,
    goal_id: current.goalId, goal_instance_id: goal.goal_instance_id, agent_id: AGENT_ID,
    session_id: current.sessionId, source_ref: current.source, creation_operation_id: current.creation});
}
