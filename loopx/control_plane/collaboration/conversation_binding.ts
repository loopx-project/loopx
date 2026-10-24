import type {JsonObject} from "../effect_program.ts";
import {isDeepStrictEqual} from "node:util";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireInteger, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {requireAutomationCadenceEligibility} from "../quota/automation_cadence.ts";
import {normalizeProjectContext, normalizeStewardContext, normalizeStewardGoalScope} from "./conversation_scope.ts";
import {bindStewardManagedActor, normalizeStewardManagedActor} from "./steward_managed_mode.ts";

/** Core owns the context and audience grant. A provider supplies verified,
 * opaque identity observations; neither a message nor a model selects them.
 * Ordinary project bindings carry no Goal. A verified personal steward can
 * read the registered owner portfolio; legacy selected scopes stay bounded.
 */
const BINDING_SCHEMA = "loopx_chat_conversation_binding_v0";
const SET_SCHEMA = "loopx_chat_conversation_bindings_v0";

function ref(value: unknown, label: string): string {
  const result = requireNonEmptyString(value, label);
  if (!/^[a-f0-9]{24}$/.test(result)) throw new EffectRuntimeRequestError(`${label} is invalid`);
  return result;
}

function token(value: unknown, label: string): string {
  const result = requireNonEmptyString(value, label);
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(result)) throw new EffectRuntimeRequestError(`${label} is invalid`);
  return result;
}

/** Mirror the canonical Chat store opaque-id boundary, including legacy Goals. */
function sessionIdentity(value: unknown, label: string): string {
  const result = requireNonEmptyString(value, label);
  if (!/^[A-Za-z0-9._-]{1,160}$/.test(result)) throw new EffectRuntimeRequestError(`${label} is invalid`);
  return result;
}

function automationCadenceStatus(value: unknown, recipient: JsonObject): JsonObject {
  const row = requireJsonObject(value, "automation cadence observation");
  if (row.goal_id !== recipient.goal_id || row.agent_id !== recipient.agent_id || row.automation_id !== null) {
    throw new EffectRuntimeRequestError("automation cadence observation belongs to another Agent scope");
  }
  const eligibility = requireAutomationCadenceEligibility(row.eligibility);
  const unavailable = eligibility.state === "unavailable";
  if (unavailable) {
    if (row.configuration_revision !== null || row.min_interval_minutes !== null) {
      throw new EffectRuntimeRequestError("unavailable automation cadence observation cannot claim policy values");
    }
  } else {
    for (const [field, label] of [
      ["configuration_revision", "automation cadence configuration revision"],
      ["min_interval_minutes", "automation cadence minimum interval"],
    ] as const) {
      const number = requireInteger(row[field], label);
      if (!Number.isSafeInteger(number) || number < 0) {
        throw new EffectRuntimeRequestError(`${label} must be a non-negative safe integer`);
      }
    }
    if (Number(row.min_interval_minutes) > 525600) {
      throw new EffectRuntimeRequestError("automation cadence minimum interval exceeds one year");
    }
  }
  return {
    goal_id: recipient.goal_id,
    agent_id: recipient.agent_id,
    automation_id: null,
    configuration_revision: unavailable ? null : Number(row.configuration_revision),
    min_interval_minutes: unavailable ? null : Number(row.min_interval_minutes),
    eligibility,
  };
}

function agentTarget(value: unknown): JsonObject {
  const row = requireJsonObject(value, "attached Agent target grant");
  return {target_ref: ref(row.target_ref, "Agent target reference"), host_ref: ref(row.host_ref, "attached host reference"),
    session_id: sessionIdentity(row.session_id, "attached Session identity"),
    goal_id: sessionIdentity(row.goal_id, "Agent Goal identity"),
    goal_instance_id: row.goal_instance_id === null ? null : sessionIdentity(row.goal_instance_id, "Goal lifetime"),
    agent_id: sessionIdentity(row.agent_id, "registered Agent identity"),
    executor_endpoint_id: token(row.executor_endpoint_id, "Agent executor")};
}

/** A configured target names an existing attached Session, not an executor
 * label. Its host retains claim, completion and permission authority. */
export function resolveConversationAgentTarget(params: JsonObject): JsonObject {
  const selected = binding(params.binding);
  const target = agentTarget(params.target);
  const granted = (selected.agent_targets as JsonObject[] | undefined)?.find(row => row.target_ref === target.target_ref);
  if (!granted || JSON.stringify(granted) !== JSON.stringify(target)) {
    throw new EffectRuntimeRequestError("the exact Agent target grant is unavailable");
  }
  const context = normalizeProjectContext(params.context);
  if (selected.context_kind !== "project" || context.binding_id !== selected.binding_id
      || context.project_ref !== selected.project_ref || context.provider_ref !== selected.provider_ref
      || context.operator_ref !== selected.operator_ref || context.audience !== "bound_owner") {
    throw new EffectRuntimeRequestError("Agent target belongs to another audience");
  }
  validateAgentTargetObservation(target, params.target_observation, context.workspace_path, selected.binding_id);
  return {target, audience: {binding_id: selected.binding_id, source_ref: ref(context.source_ref, "Agent source"),
    provider_ref: selected.provider_ref, operator_ref: selected.operator_ref, project_ref: selected.project_ref}};
}

function validateAgentTargetObservation(target: JsonObject, value: unknown, workspace: unknown, bindingId: unknown): void {
  const observation = requireJsonObject(value, "attached Agent observation");
  const session = requireJsonObject(observation.session, "attached Session observation");
  const goal = requireJsonObject(observation.goal, "registered Agent Goal observation");
  if (session.external_conversation_binding_id != null && session.external_conversation_binding_id !== bindingId) {
    throw new EffectRuntimeRequestError("the attached Session previously belonged to another App audience");
  }
  if (observation.host_binding_verified !== true || session.session_mode !== "attached_host"
      || !["ready", "busy"].includes(String(session.status))
      || observation.host_ref !== target.host_ref
      || !Array.isArray(observation.host_audience_binding_ids)
      || observation.host_audience_binding_ids.some(owner => owner !== bindingId)
      || goal.workspace_path !== workspace || goal.goal_id !== target.goal_id
      || !Array.isArray(goal.registered_agents) || !goal.registered_agents.includes(target.agent_id)) {
    throw new EffectRuntimeRequestError("Agent target is not an available registered host in this workspace");
  }
  for (const field of ["session_id", "goal_id", "agent_id", "executor_endpoint_id"]) {
    if (session[field] !== target[field]) throw new EffectRuntimeRequestError("Agent Session identity changed");
  }
  if ((session.goal_instance_id ?? null) !== target.goal_instance_id) {
    throw new EffectRuntimeRequestError("Agent Goal lifetime changed");
  }
}

function binding(value: unknown): JsonObject {
  const row = requireJsonObject(value, "conversation binding");
  const group = row.audience === "group";
  if (row.audience !== undefined && row.audience !== "group") {
    throw new EffectRuntimeRequestError("unsupported conversation audience");
  }
  if (group && (row.context_kind !== "project" || row.executor_endpoint_id !== "codex")) {
    throw new EffectRuntimeRequestError("group Chat requires a native project executor");
  }
  const groups = row.group_refs;
  if (group ? !Array.isArray(groups) || groups.length === 0 || groups.length > 16
      : groups !== undefined) throw new EffectRuntimeRequestError("invalid group audience grant");
  const groupRefs = group ? [...new Set((groups as unknown[]).map(value => ref(value, "group reference")))].sort() : [];
  if (group && groupRefs.length !== (groups as unknown[]).length) throw new EffectRuntimeRequestError("duplicate group audience grant");
  if (row.goal_scope !== undefined && (row.context_kind !== "steward"
      || !["all_registered", "selected"].includes(String(row.goal_scope)))) {
    throw new EffectRuntimeRequestError("unsupported steward Goal scope");
  }
  if (row.schema_version !== BINDING_SCHEMA || !["project", "steward"].includes(String(row.context_kind))
      || (row.context_kind === "project" ? !["workspace_read", "workspace_write"].includes(String(row.grant)) : row.grant !== "portfolio_read")
      || (row.grant === "workspace_write" && row.executor_endpoint_id !== "codex") || row.enabled !== true) {
    throw new EffectRuntimeRequestError("unsupported conversation binding");
  }
  const targets = row.agent_targets === undefined ? [] : row.agent_targets;
  if (!Array.isArray(targets) || targets.length > 16
      || ((row.context_kind !== "project" || group) && targets.length !== 0)) {
    throw new EffectRuntimeRequestError("invalid Agent target grant set");
  }
  const agents = targets.map(agentTarget);
  for (const field of ["target_ref", "session_id"]) {
    if (new Set(agents.map(row => row[field])).size !== agents.length) {
      throw new EffectRuntimeRequestError("ambiguous Agent target grant");
    }
  }
  if (row.managed_actor !== undefined && (row.context_kind !== "steward" || group || row.executor_endpoint_id !== "codex")) {
    throw new EffectRuntimeRequestError("managed identity requires a private native steward");
  }
  return {
    schema_version: BINDING_SCHEMA, binding_id: ref(row.binding_id, "binding identity"),
    transport_ref: token(row.transport_ref, "transport reference"),
    provider_ref: ref(row.provider_ref, "provider identity"),
    operator_ref: ref(row.operator_ref, "verified operator identity"),
    context_kind: row.context_kind, project_ref: ref(row.project_ref, "workspace reference"),
    executor_endpoint_id: token(row.executor_endpoint_id, "executor endpoint"),
    grant: row.grant, enabled: true,
    ...(group ? {audience: "group", group_refs: groupRefs} : {}),
    ...(row.context_kind === "steward" ? {goal_ids: normalizeStewardGoalScope(row.goal_ids),
      ...(row.goal_scope !== undefined ? {goal_scope: row.goal_scope} : {})} : {}),
    ...(agents.length ? {agent_targets: agents} : {}),
    ...(row.managed_actor !== undefined ? {managed_actor: normalizeStewardManagedActor(row.managed_actor)} : {}),
  };
}

function state(value: unknown): {schema_version: string; revision: number; bindings: JsonObject[]} {
  const row = requireJsonObject(value, "conversation bindings");
  if (row.schema_version !== SET_SCHEMA || !Number.isSafeInteger(row.revision)
      || Number(row.revision) < 0 || !Array.isArray(row.bindings)) {
    throw new EffectRuntimeRequestError("invalid conversation bindings state");
  }
  const bindings = row.bindings.map(binding);
  for (const field of ["binding_id", "transport_ref", "provider_ref"]) {
    if (new Set(bindings.map(row => row[field])).size !== bindings.length) {
      throw new EffectRuntimeRequestError("conversation listener ownership is ambiguous");
    }
  }
  const sessions = bindings.flatMap(row => (row.agent_targets as JsonObject[] | undefined ?? []).map(target => target.session_id));
  const hosts = new Map<unknown, unknown>();
  for (const row of bindings) for (const target of row.agent_targets as JsonObject[] | undefined ?? []) {
    if (hosts.has(target.host_ref) && hosts.get(target.host_ref) !== row.binding_id) {
      throw new EffectRuntimeRequestError("an attached host cannot share two App audiences");
    }
    hosts.set(target.host_ref, row.binding_id);
  }
  if (new Set(sessions).size !== sessions.length) {
    throw new EffectRuntimeRequestError("an attached Session cannot share two App audiences");
  }
  return {schema_version: SET_SCHEMA, revision: Number(row.revision), bindings};
}

function observed(row: JsonObject, value: unknown): void {
  const observation = requireJsonObject(value, "provider identity observation");
  if (observation.verified !== true || observation.transport_ref !== row.transport_ref
      || observation.provider_ref !== row.provider_ref || observation.operator_ref !== row.operator_ref) {
    throw new EffectRuntimeRequestError("provider or operator identity is not independently verified");
  }
}

export function planConversationBinding(params: JsonObject): JsonObject {
  const current = state(params.current);
  if (params.expected_revision !== current.revision) throw new EffectRuntimeRequestError("conversation binding revision changed");
  let rows: JsonObject[];
  if (params.operation === "configure") {
    const candidate = binding(params.binding);
    observed(candidate, params.observation);
    if (candidate.audience === "group") {
      if (!Array.isArray(params.available_group_refs)
          || !(candidate.group_refs as string[]).every(group => (params.available_group_refs as unknown[]).includes(group))) {
        throw new EffectRuntimeRequestError("selected groups are not independently verified under this App");
      }
    }
    const project = Array.isArray(params.available_projects)
      ? params.available_projects.map(normalizeProjectContext).find(row => row.project_ref === candidate.project_ref) : undefined;
    if (!project || (candidate.grant === "workspace_write" && project.grant !== "workspace_write")) {
      throw new EffectRuntimeRequestError("workspace grant is unavailable");
    }
    const previous = current.bindings.find(row => row.transport_ref === candidate.transport_ref);
    // An exact repeat is idempotent. A changed context receives a new binding
    // identity, so an existing Session can never silently move workspaces.
    if (previous && JSON.stringify({...previous, binding_id: null}) === JSON.stringify({...candidate, binding_id: null})) {
      return {changed: false, state: current};
    }
    const scopeOnly = previous?.context_kind === "steward" && candidate.context_kind === "steward"
      && JSON.stringify({...previous, goal_scope: null}) === JSON.stringify({...candidate, goal_scope: null});
    if (previous?.binding_id === candidate.binding_id && !scopeOnly) throw new EffectRuntimeRequestError("changed context requires a new binding identity");
    rows = [...current.bindings.filter(row => row.transport_ref !== candidate.transport_ref), candidate];
  } else if (params.operation === "grant_agent_target" || params.operation === "revoke_agent_target") {
    const id = ref(params.binding_id, "binding identity");
    const previous = current.bindings.find(row => row.binding_id === id);
    if (!previous || previous.context_kind !== "project" || previous.audience === "group") throw new EffectRuntimeRequestError("Agent selection requires a private project binding");
    observed(previous, params.observation);
    const existing = previous.agent_targets as JsonObject[] | undefined ?? [];
    let targets: JsonObject[];
    if (params.operation === "grant_agent_target") {
      const target = agentTarget(params.target);
      if (!Array.isArray(params.available_projects)) throw new EffectRuntimeRequestError("workspace grants unavailable");
      const project = params.available_projects.map(normalizeProjectContext).find(row => row.project_ref === previous.project_ref);
      if (!project) throw new EffectRuntimeRequestError("workspace grant unavailable");
      validateAgentTargetObservation(target, params.target_observation, project.workspace_path, previous.binding_id);
      if (existing.some(row => row.session_id === target.session_id)) throw new EffectRuntimeRequestError("this Agent Session is already granted");
      targets = [...existing, target];
    } else targets = existing.filter(row => row.target_ref !== ref(params.target_ref, "Agent target reference"));
    rows = current.bindings.map(row => row.binding_id === id ? {...row, agent_targets: targets} : row);
  } else if (params.operation === "adopt_created_goal") {
    const id = ref(params.binding_id, "binding identity");
    const previous = current.bindings.find(row => row.binding_id === id);
    const context = normalizeStewardContext(params.context);
    const proposal = requireJsonObject(params.proposal, "creation proposal");
    const principal = requireJsonObject(proposal.context, "creation audience");
    const receipt = requireJsonObject(proposal.receipt, "creation receipt");
    const resources = requireJsonObject(receipt.resource_ids, "creation resources");
    const goal = requireJsonObject(params.goal, "created Goal observation");
    if (!previous || previous.context_kind !== "steward" || previous.project_ref !== context.project_ref
        || proposal.action_kind !== "goal.create" || proposal.status !== "applied"
        || receipt.outcome !== "goal_created" || receipt.projection_verified !== true
        || goal.goal_id !== resources.goal_id || goal.creation_operation_id !== proposal.proposal_id
        || goal.workspace_path !== context.workspace_path) throw new EffectRuntimeRequestError("steward adoption requires its exact creation receipt");
    for (const key of ["binding_id", "source_ref", "provider_ref", "operator_ref"]) {
      if (principal[key] !== context[key] || (key !== "source_ref" && previous[key] !== context[key])) {
        throw new EffectRuntimeRequestError("creation receipt belongs to another audience");
      }
    }
    const goals = normalizeStewardGoalScope([...(previous.goal_ids as string[]), goal.goal_id]);
    if (JSON.stringify(goals) === JSON.stringify(previous.goal_ids)) return {changed: false, state: current};
    rows = current.bindings.map(row => row.binding_id === id ? {...row, goal_ids: goals} : row);
  } else if (params.operation === "bind_managed_actor") {
    const id = ref(params.binding_id, "binding identity");
    const previous = current.bindings.find(row => row.binding_id === id);
    if (!previous) throw new EffectRuntimeRequestError("the original steward binding is unavailable");
    const actor = bindStewardManagedActor({...params, binding: previous});
    if (JSON.stringify(actor) === JSON.stringify(previous.managed_actor)) return {changed: false, state: current};
    if (previous.managed_actor !== undefined) throw new EffectRuntimeRequestError("retain the original managed steward identity");
    rows = current.bindings.map(row => row.binding_id === id ? {...row, managed_actor: actor} : row);
  } else if (params.operation === "disconnect") {
    const id = ref(params.binding_id, "binding identity");
    rows = current.bindings.filter(row => row.binding_id !== id);
    if (rows.length === current.bindings.length) return {changed: false, state: current};
  } else throw new EffectRuntimeRequestError("unsupported conversation binding operation");
  const next = state({...current, revision: current.revision + 1, bindings: rows});
  return {changed: true, state: next};
}

function boundProject(params: JsonObject, row: JsonObject): Record<string, string> {
  if (!Array.isArray(params.available_projects)) throw new EffectRuntimeRequestError("workspace grants unavailable");
  const projects = params.available_projects.map(normalizeProjectContext).filter(project => project.project_ref === row.project_ref);
  if (projects.length !== 1) throw new EffectRuntimeRequestError("workspace grant is unavailable or ambiguous");
  if (row.grant === "workspace_write" && projects[0].grant !== "workspace_write") {
    throw new EffectRuntimeRequestError("workspace write grant is no longer available");
  }
  return projects[0];
}

export function resolveBoundConversation(params: JsonObject): JsonObject {
  const current = state(params.current);
  const id = ref(params.binding_id, "binding identity");
  const row = current.bindings.find(row => row.binding_id === id);
  if (!row) throw new EffectRuntimeRequestError("conversation binding is no longer authorized");
  observed(row, params.observation);
  const source = ref(params.source_ref, "source conversation identity");
  const group = row.audience === "group";
  // The local operator may continue an existing private Session. This is a
  // distinct host-observed origin, never a fabricated provider human event.
  const local = params.origin === "web";
  if (params.origin !== undefined && !local) throw new EffectRuntimeRequestError("unsupported bound input origin");
  if (local && (group || params.session_context === undefined || params.private_human_message !== false)) {
    throw new EffectRuntimeRequestError("local input requires an existing private bound Session");
  }
  let topic: JsonObject = {};
  if (group) {
    const groupRef = ref(params.group_ref, "group reference"), topicRef = ref(params.topic_ref, "topic reference");
    ref(params.sender_ref, "group sender identity");
    if (params.group_human_message !== true || params.private_human_message !== false || topicRef !== source
        || !(row.group_refs as string[]).includes(groupRef)) {
      throw new EffectRuntimeRequestError("message audience is outside the binding grant");
    }
    topic = {group_ref: groupRef, topic_ref: topicRef, filesystem_scope: "workspace_only"};
  } else if (params.sender_ref !== row.operator_ref || params.private_human_message !== !local
      || params.group_ref !== undefined || params.topic_ref !== undefined || params.group_human_message === true) {
    throw new EffectRuntimeRequestError("message audience is outside the binding grant");
  }
  const project = boundProject(params, row);
  const goals = row.context_kind === "steward" && row.goal_scope === "all_registered"
    ? normalizeStewardGoalScope(params.available_goal_ids) : row.goal_ids;
  const rawContext = {...project, ...topic, audience: group ? "bound_group" : "bound_owner", binding_id: id, source_ref: source,
    provider_ref: row.provider_ref, operator_ref: row.operator_ref, grant: row.grant,
    ...(row.context_kind === "steward" ? {kind: "bound_steward", grant: "portfolio_read", goal_ids: goals} : {})};
  const context = group ? normalizeProjectContext(rawContext) : rawContext;
  if (params.session_context !== undefined) {
    const saved = requireJsonObject(params.session_context, "bound Session context");
    // New commissions may extend the same owner's scope. Workspace, role and
    // audience remain frozen; all evidence reads use the fresh binding scope.
    // JSON object member order is not identity. Preserve every value and field
    // check while accepting equivalent contexts from another JSON writer.
    const matches = row.context_kind === "steward"
      ? isDeepStrictEqual({...saved, goal_ids: []}, {...context, goal_ids: []})
        && (row.goal_scope === "all_registered" || normalizeStewardGoalScope(saved.goal_ids).every(g => (row.goal_ids as string[]).includes(g)))
      : isDeepStrictEqual(saved, context);
    if (!matches) throw new EffectRuntimeRequestError("bound Session context changed");
  }
  const channel = row.context_kind === "steward"
    ? `manager.external.native.${id}.${source}` : `project.external.${id}.${source}`;
  if (params.session !== undefined) {
    const session = requireJsonObject(params.session, "canonical bound Session");
    const steward = row.context_kind === "steward";
    sessionIdentity(session.session_id, "bound Session identity");
    if (session.status === "closed" || session.channel_id !== channel
        || session.goal_id !== (steward ? "loopx-manager" : null)
        || session.agent_id !== row.executor_endpoint_id || session.executor_endpoint_id !== row.executor_endpoint_id
        || session.session_mode !== "managed_runtime" || (steward ? session.project_context : session.steward_context) != null
        || params.session_context === undefined
        || !isDeepStrictEqual(steward ? session.steward_context : session.project_context, params.session_context)) {
      throw new EffectRuntimeRequestError("canonical bound Session identity changed");
    }
  }
  // This is an audience proof, not a host-tool grant. Only the independently
  // verified owner in this private source may inherit an explicit machine
  // owner grant. Portfolio scope and project write grants do not imply it.
  return {binding: row, context, owner_manager_audience: row.context_kind === "steward"
      && project.filesystem_scope !== "workspace_only",
    channel_id: channel};
}

/** Discover existing private targets from canonical metadata. This is a read
 * projection: the next input still needs fresh source and execution admission.
 */
export function listLocalBoundSessions(params: JsonObject): JsonObject {
  const current = state(params.current);
  const id = ref(params.binding_id, "binding identity");
  const row = current.bindings.find(item => item.binding_id === id);
  if (!row) throw new EffectRuntimeRequestError("conversation binding is no longer authorized");
  observed(row, params.observation);
  if (row.audience === "group") throw new EffectRuntimeRequestError("local discovery requires a private binding");
  boundProject(params, row);
  if (!Array.isArray(params.sessions)) throw new EffectRuntimeRequestError("canonical Session observations unavailable");
  const ids: string[] = [];
  for (const value of params.sessions) {
    const session = requireJsonObject(value, "canonical Session");
    const contexts = [session.project_context, session.steward_context];
    if (!contexts.some(value => value && typeof value === "object" && !Array.isArray(value)
        && (value as JsonObject).binding_id === id) || session.status === "closed") continue;
    const steward = row.context_kind === "steward";
    const saved = requireJsonObject(steward ? session.steward_context : session.project_context, "bound Session context");
    resolveBoundConversation({...params, source_ref: saved.source_ref,
      session,
      sender_ref: row.operator_ref, private_human_message: false, origin: "web", session_context: saved});
    ids.push(sessionIdentity(session.session_id, "bound Session identity"));
  }
  if (new Set(ids).size !== ids.length) throw new EffectRuntimeRequestError("canonical bound Session identity is ambiguous");
  return {session_ids: ids};
}

/** Shared native command projection. Provider grammar carries the explicit
 * command; Core binds its exact target before IO. Replays retain that target.
 */
export function planBoundConversationRequest(params: JsonObject): JsonObject {
  const row = requireJsonObject(params.request, "external request");
  const request = ref(row.request_ref, "external request identity");
  const command = row.command;
  const imageCount = params.attachment_count ?? 0;
  if (!Number.isSafeInteger(imageCount) || Number(imageCount) < 0 || Number(imageCount) > 4) {
    throw new EffectRuntimeRequestError("invalid external image attachment count");
  }
  if (![null, "agents", "select_agent", "select_project", "status", "help", "new", "stop", "unsupported", "commission", "confirm_commission", "cancel_commission", "stop_commission", "resume_commission"].includes(command as null | string)) {
    throw new EffectRuntimeRequestError("unsupported external conversation command");
  }
  const current = params.current_session === null ? null : requireJsonObject(params.current_session, "current Session");
  if (params.context_recovery !== undefined) {
    return planContextRecovery(params, row, current);
  }
  const target = row.target_recorded === true ? row : current;
  const session = target?.session_id ?? null;
  const turn = row.target_recorded === true ? row.turn_id ?? null : current?.active_turn_id ?? null;
  if (Number(imageCount) > 0 && (command !== null || params.agent_target != null)) {
    // Preserve the existing attached-host capability boundary and never drop
    // images while executing a control command or handing off to that host.
    return {operation: "reply", session_id: session, turn_id: null, response_code: "unsupported_attachment"};
  }
  if (["agents", "select_agent", "select_project"].includes(String(command))) {
    if (requireJsonObject(params.binding, "conversation binding").audience === "group") {
      return {operation: "reply", session_id: session, turn_id: null, response_code: "group_recipient_unavailable"};
    }
    return {operation: "select_recipient", session_id: null, turn_id: null};
  }
  if (params.agent_target !== undefined && params.agent_target !== null) {
    const target = agentTarget(params.agent_target);
    if (!current || current.session_id !== target.session_id || current.goal_id !== target.goal_id
        || current.agent_id !== target.agent_id || current.session_mode !== "attached_host") {
      throw new EffectRuntimeRequestError("the selected Agent Session is unavailable");
    }
    if (command === "new" || command === "stop") {
      return {operation: "reply", session_id: current.session_id, turn_id: null, response_code: "attached_control_unavailable"};
    }
  }
  if (["commission", "confirm_commission", "cancel_commission", "stop_commission", "resume_commission"].includes(String(command))) {
    const selected = requireJsonObject(params.binding, "steward binding");
    if (selected.context_kind !== "steward") throw new EffectRuntimeRequestError("explicit commissions require a selected steward");
    return {operation: "steward_action", session_id: session, turn_id: null};
  }
  if (command === "status" || command === "help") {
    return {operation: "reply", session_id: session, turn_id: null,
      response_code: command === "help" ? "conversation_help" : !current ? "no_session"
        : current.active_turn_id ? "active_session" : "ready_session",
      status_snapshot: boundConversationStatus(params, current)};
  }
  if (command === "unsupported") {
    return {operation: "reply", session_id: session, turn_id: null,
      response_code: "unsupported_attachment"};
  }
  if (command === "stop" || command === "new") {
    if (command === "stop" && params.recovering_request === true && row.target_recorded !== true) {
      // Older prepared journals may predate freezing before inventory. Their
      // original scope is unknown; current work cannot reconstruct that intent.
      throw new EffectRuntimeRequestError("the original stop scope was not recorded");
    }
    const rawBackground = command === "stop"
      ? (row.target_recorded === true ? row.background_turn_ids : params.background_turn_ids) ?? [] : [];
    if (!Array.isArray(rawBackground)) throw new EffectRuntimeRequestError("invalid background Turn observation");
    const background = rawBackground.map(value => sessionIdentity(value, "completed background Turn"));
    if (new Set(background).size !== background.length || (background.length && (!session || params.agent_target))) {
      throw new EffectRuntimeRequestError("background Turns require the original ordinary Session");
    }
    return {operation: command, session_id: session, turn_id: turn,
      ...(background.length ? {background_turn_ids: background} : {}),
      response_code: command === "new" ? "new_session" : turn || background.length ? "stop_requested" : "no_active_turn"};
  }
  return {operation: "admit_turn", client_turn_id: `external-${request}`, session_id: session, turn_id: null};
}

/** A new, correlated continuation after an observed pre-execution failure.
 * A delivered failure notice is not a completed business request. This bounded
 * migration path never reopens the failed Turn or retries unknown effects.
 */
function planContextRecovery(params: JsonObject, row: JsonObject, current: JsonObject | null): JsonObject {
  const recovery = requireJsonObject(params.context_recovery, "context recovery observation");
  const version = requireInteger(recovery.version, "context representation version");
  if (version < 1) throw new EffectRuntimeRequestError("invalid context representation version");
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  if (!current || row.command !== null || row.status !== "accepted" || row.delivery_verified !== true
      || row.agent_target != null || current.session_id !== row.session_id || current.agent_id !== "codex"
      || current.upstream_mode !== "chat" || current.status === "closed"
      || current.session_mode === "attached_host") return no;
  const selected = binding(params.binding);
  if (selected.audience === "group") return no;
  const steward = selected.context_kind === "steward";
  if (selected.executor_endpoint_id !== "codex" || current.goal_id !== (steward ? "loopx-manager" : null)) return no;
  const saved = steward ? normalizeStewardContext(current.steward_context)
    : normalizeProjectContext(current.project_context);
  const source = requireJsonObject(row.source, "original conversation source");
  if (row.binding_id !== selected.binding_id || saved.binding_id !== selected.binding_id
      || saved.source_ref !== source.source_ref || saved.provider_ref !== selected.provider_ref
      || saved.operator_ref !== selected.operator_ref || source.sender_ref !== selected.operator_ref
      || source.private_human_message !== true
      || saved.project_ref !== selected.project_ref || (!steward && saved.grant !== selected.grant)
      || current.channel_id !== `${steward ? "manager.external.native" : "project.external"}.${selected.binding_id}.${saved.source_ref}`) return no;
  const failed = requireJsonObject(recovery.failed_turn, "original failed Turn");
  const previous = row.context_recovery == null ? null : requireJsonObject(row.context_recovery, "reserved context recovery");
  // A failed review cannot fall through to routing/quota business retries.
  if ((previous?.read_only_review === true && previous.failed_turn_id !== row.turn_id)
      || (Array.isArray(row.context_recovery_history) && row.context_recovery_history.some(value =>
        requireJsonObject(value, "prior recovery").read_only_review === true))) return no;
  if (!steward && failed.error_code === "server_restarted") {
    return planResultReview(recovery, row, current, failed, previous, version);
  }
  if (failed.session_id !== current.session_id || failed.turn_id !== row.turn_id
      || failed.status !== "failed" || failed.delta_count !== 0 || failed.response !== null
      || recovery.steering_observed !== false
      || !["context_window_exceeded", "host_gate", "upstream_transport_failure", "server_overloaded", "usage_limit_exceeded"].includes(String(failed.error_code))
      || typeof failed.upstream_turn_id !== "string" || !failed.upstream_turn_id) return no;
  const retryKind = failed.error_code === "usage_limit_exceeded" ? "quota" : "routing";
  const attemptKey = `${retryKind}_attempt`;
  if (recovery.admitted_turn != null && previous !== null && previous.failed_turn_id === failed.turn_id
      && Number.isSafeInteger(previous[attemptKey]) && Number(previous[attemptKey]) >= 1
      && Number(previous[attemptKey]) <= 3 && previous.version === version) {
    // A crash after queue admission must adopt that exact canonical Turn,
    // even if it has already executed. This branch never creates model work.
    const admitted = requireJsonObject(recovery.admitted_turn, "admitted recovery Turn");
    const client = `external-${ref(row.request_ref, "original request")}-recovery-${version}-${retryKind}-${previous[attemptKey]}`;
    if (previous.client_turn_id !== client || admitted.client_turn_id !== client
        || admitted.session_id !== current.session_id || admitted.turn_id === failed.turn_id
        || typeof row.message !== "string" || typeof row.origin !== "string"
        || admitted.message !== row.message || admitted.origin !== row.origin
        || !Array.isArray(admitted.attachments ?? []) || (admitted.attachments as unknown[] | undefined)?.length) return no;
    return {operation: "admit_turn", recovery_candidate: true,
      recovery_eligible: recovery.source_verified === true, client_turn_id: client,
      admitted_turn_id: sessionIdentity(admitted.turn_id, "admitted recovery Turn"),
      session_id: current.session_id, native_thread_ids: []};
  }
  const routing = planRoutingRecovery(recovery, row, current, failed, previous, version);
  if (routing !== null) return routing;
  if (!steward) return no;
  const client = `external-${ref(row.request_ref, "original request")}-recovery-${version}`;
  if (previous && (previous.version !== version || previous.failed_turn_id !== row.turn_id
      || previous.client_turn_id !== client)) return no;
  const history = current.upstream_context_history ?? [];
  if (!Array.isArray(history)) throw new EffectRuntimeRequestError("invalid native context provenance");
  const oldThreads = history.map(value => requireJsonObject(value, "native context provenance"))
    .filter(item => Number.isSafeInteger(item.manager_context_version) && Number(item.manager_context_version) < version)
    .map(item => sessionIdentity(item.thread_id, "original native thread"));
  if (Number.isSafeInteger(current.manager_context_version) && Number(current.manager_context_version) < version) {
    oldThreads.push(sessionIdentity(current.upstream_thread_id, "original native thread"));
  }
  const currentHostFailure = current.manager_context_version === version && failed.error_code === "host_gate";
  const nativeThreads = currentHostFailure
    ? [sessionIdentity(current.upstream_thread_id, "current native thread"), ...oldThreads] : oldThreads;
  if (!nativeThreads.length) return no;
  const candidate = {...no, recovery_candidate: true, client_turn_id: client,
    session_id: current.session_id, failed_turn_id: failed.turn_id, native_thread_ids: [...new Set(nativeThreads)]};
  if (recovery.native_observation == null) return candidate;
  const observed = requireJsonObject(recovery.native_observation, "native failure readback");
  if (!nativeThreads.includes(String(observed.thread_id)) || observed.turn_id !== failed.upstream_turn_id
      || observed.only_original_user_item !== true || recovery.source_verified !== true) return candidate;
  const info = observed.codex_error_info;
  let transient = (oldThreads.includes(String(observed.thread_id))
    && ["contextWindowExceeded", "serverOverloaded", "internalServerError"].includes(String(info)))
    || (currentHostFailure && observed.thread_id === current.upstream_thread_id && info === "other");
  if (info && typeof info === "object" && !Array.isArray(info)) {
    const entries = Object.entries(info);
    if (entries.length === 1 && ["httpConnectionFailed", "responseStreamConnectionFailed", "responseStreamDisconnected", "responseTooManyFailedAttempts"].includes(entries[0][0])) {
      const detail = requireJsonObject(entries[0][1], "native connection readback");
      transient = Object.hasOwn(detail, "httpStatusCode") && (detail.httpStatusCode === null
        || Number.isSafeInteger(detail.httpStatusCode) && Number(detail.httpStatusCode) >= 500 && Number(detail.httpStatusCode) <= 599);
    }
  }
  return {...candidate, recovery_eligible: transient};
}

/** Interrupted work may have effects. Never retry it. The original worker may
 * reserve one read-only review of existing results, with its own presentation.
 * The adapter enforces the existing workspace-only read policy, without tools
 * which could send, write, delegate or repeat the original business operation.
 */
function planResultReview(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, previous: JsonObject | null, version: number): JsonObject {
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  if (failed.session_id !== current.session_id || failed.turn_id !== row.turn_id
      || failed.status !== "failed" || failed.response !== null
      || !Number.isSafeInteger(failed.delta_count) || Number(failed.delta_count) <= 0
      || recovery.steering_observed !== false || recovery.stop_observed !== false
      || recovery.attachments_present !== false
      || typeof failed.upstream_turn_id !== "string" || !failed.upstream_turn_id) return no;
  const client = `external-${ref(row.request_ref, "original request")}-recovery-${version}-review-1`;
  if (previous?.read_only_review === true && (previous.failed_turn_id !== failed.turn_id
      || previous.version !== version || previous.client_turn_id !== client)) return no;
  if (recovery.admitted_turn != null && previous?.read_only_review === true) {
    const admitted = requireJsonObject(recovery.admitted_turn, "admitted result review");
    if (admitted.client_turn_id !== client || admitted.session_id !== current.session_id
        || admitted.turn_id === failed.turn_id || admitted.message !== row.message
        || admitted.origin !== row.origin || (admitted.attachments != null
          && (!Array.isArray(admitted.attachments) || admitted.attachments.length))) return no;
    return {...no, recovery_candidate: true, recovery_eligible: recovery.source_verified === true,
      read_only_review: true, client_turn_id: client, session_id: current.session_id,
      admitted_turn_id: sessionIdentity(admitted.turn_id, "admitted result review"), native_thread_ids: []};
  }
  if (current.active_turn_id != null || current.status !== "ready") return no;
  const now = Date.parse(String(recovery.observed_at)), completed = Date.parse(String(failed.completed_at));
  if (!Number.isFinite(now) || !Number.isFinite(completed) || now < completed + 60_000) return no;
  const native = sessionIdentity(current.upstream_thread_id, "original native thread");
  const candidate = {...no, recovery_candidate: true, read_only_review: true,
    client_turn_id: client, session_id: current.session_id, failed_turn_id: failed.turn_id,
    native_thread_ids: [native]};
  if (recovery.native_observation == null) return candidate;
  const observed = requireJsonObject(recovery.native_observation, "interrupted result inventory");
  return {...candidate, recovery_eligible: recovery.source_verified === true
    && observed.thread_id === native && observed.turn_id === failed.upstream_turn_id
    && observed.read_only_review === true && observed.no_final_answer === true
    && observed.effects_observed === true};
}

/** Routing outages outlive one worker or context migration. Reserve at most
 * three separately correlated continuations, with a persisted failure clock.
 * Provider IO must prove a latest, terminal, pre-execution request every time.
 */
function planRoutingRecovery(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, previous: JsonObject | null, version: number): JsonObject | null {
  const quota = failed.error_code === "usage_limit_exceeded";
  const kind = quota ? "quota" : "routing";
  const attemptKey = `${kind}_attempt`;
  if (!quota && !["host_gate", "upstream_transport_failure"].includes(String(failed.error_code))) return null;
  // An unfinished older reservation must finish under its original identity.
  if (previous !== null && previous.failed_turn_id === failed.turn_id && previous[attemptKey] === undefined) return null;
  const observed = recovery.native_observation == null ? null
    : requireJsonObject(recovery.native_observation, "native failure readback");
  if (observed && !quota && observed.pre_execution_routing_timeout !== true) return null;
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  const reserved = previous?.failed_turn_id === failed.turn_id;
  const priorAttempt = previous?.[attemptKey] ?? 0;
  if (!Number.isSafeInteger(priorAttempt) || Number(priorAttempt) < 0) return no;
  const attempt = Number(priorAttempt) + (reserved ? 0 : 1);
  if (attempt < 1 || attempt > 3 || typeof current.upstream_thread_id !== "string"
      || !current.upstream_thread_id || current.active_turn_id != null || current.status !== "ready") return no;
  const now = typeof recovery.observed_at === "string" ? Date.parse(recovery.observed_at) : NaN;
  const completed = typeof failed.completed_at === "string" ? Date.parse(failed.completed_at) : NaN;
  const notBefore = completed + 60_000 * 2 ** (attempt - 1);
  if (!Number.isFinite(now) || !Number.isFinite(completed) || now < notBefore) {
    // Preserve the existing context-migration path when no routing diagnostic
    // has been established. A proven routing timeout always obeys backoff.
    return observed ? no : null;
  }
  const client = `external-${ref(row.request_ref, "original request")}-recovery-${version}-${kind}-${attempt}`;
  if (reserved && (previous?.client_turn_id !== client || previous.version !== version)) return no;
  const candidate = {...no, recovery_candidate: true, client_turn_id: client,
    [attemptKey]: attempt, not_before: new Date(notBefore).toISOString(),
    session_id: current.session_id, failed_turn_id: failed.turn_id,
    native_thread_ids: [sessionIdentity(current.upstream_thread_id, "current native thread")]};
  if (!observed) return candidate;
  return {...candidate, recovery_eligible: recovery.source_verified === true
    && observed.thread_id === current.upstream_thread_id && observed.turn_id === failed.upstream_turn_id
    && (quota ? observed.codex_error_info === "usageLimitExceeded" && observed.only_original_user_item === true
      && observed.quota_available === true && typeof observed.quota_observed_at === "number"
      && now / 1000 >= observed.quota_observed_at && now / 1000 - observed.quota_observed_at <= 120
      : observed.codex_error_info === "other" && observed.only_pre_execution_items === true)};
}

/** A labelled observation of the same authorized context and canonical queue.
 * It neither creates a Session nor certifies execution or result delivery.
 * The external request persists this snapshot so duplicate delivery cannot
 * silently substitute a later Session or another binding's current state.
 */
function boundConversationStatus(params: JsonObject, current: JsonObject | null): JsonObject {
  const selected = binding(params.binding);
  const steward = selected.context_kind === "steward";
  const context = steward ? normalizeStewardContext(params.context) : normalizeProjectContext(params.context);
  for (const key of ["binding_id", "project_ref", "provider_ref", "operator_ref"]) {
    if (context[key] !== selected[key]) throw new EffectRuntimeRequestError("status context belongs to another binding");
  }
  const source = ref(context.source_ref, "status source identity");
  const channel = steward ? `manager.external.native.${selected.binding_id}.${source}`
    : `project.external.${selected.binding_id}.${source}`;
  const recipient = params.agent_target ? agentTarget(params.agent_target) : null;
  if (recipient) {
    if (!current || current.session_id !== recipient.session_id || current.goal_id !== recipient.goal_id
        || current.agent_id !== recipient.agent_id || current.session_mode !== "attached_host") {
      throw new EffectRuntimeRequestError("status Agent Session changed");
    }
  } else if (current) {
    const saved = steward ? normalizeStewardContext(current.steward_context) : normalizeProjectContext(current.project_context);
    if (current.channel_id !== channel || current.goal_id !== (steward ? "loopx-manager" : null)
        || JSON.stringify({...saved, goal_ids: []}) !== JSON.stringify({...context, goal_ids: []})) {
      throw new EffectRuntimeRequestError("status Session context changed");
    }
  }
  const statusCommand = requireJsonObject(params.request, "external request").command === "status";
  const cadenceExpected = statusCommand && recipient !== null;
  if ((params.automation_cadence !== undefined) !== cadenceExpected) {
    throw new EffectRuntimeRequestError("automation cadence observation requires an exact selected Agent status");
  }
  const cadence = cadenceExpected
    ? automationCadenceStatus(params.automation_cadence, recipient as JsonObject)
    : null;
  if (!Number.isSafeInteger(params.queued_count) || Number(params.queued_count) < 0
      || (!current && params.queued_count !== 0)) throw new EffectRuntimeRequestError("invalid canonical queue observation");
  const instant = requireNonEmptyString(params.observed_at, "status observation time");
  if (instant.length > 64 || !Number.isFinite(Date.parse(instant))) throw new EffectRuntimeRequestError("invalid status observation time");
  const turn = params.active_turn === null ? null : requireJsonObject(params.active_turn, "observed active Turn");
  if (turn && (!current || turn.session_id !== current.session_id || turn.turn_id !== current.active_turn_id)) {
    throw new EffectRuntimeRequestError("status Turn belongs to another Session");
  }
  for (const item of [current, turn]) {
    if (item && (typeof item.status !== "string" || !/^[a-z][a-z0-9_]{0,63}$/.test(item.status))) {
      throw new EffectRuntimeRequestError("invalid canonical status observation");
    }
  }
  return {schema_version: "loopx_chat_bound_status_v0", observed_at: instant,
    context_kind: selected.context_kind,
    workspace_path: selected.audience === "group" ? String(context.workspace_path).split(/[\\/]/).filter(Boolean).at(-1) : context.workspace_path,
    ...(selected.audience === "group" ? {audience: "group"} : {}),
    executor_endpoint_id: recipient?.executor_endpoint_id ?? selected.executor_endpoint_id, grant: selected.grant,
    ...(recipient ? {recipient_agent_id: recipient.agent_id, recipient_goal_id: recipient.goal_id, recipient_mode: "attached_host"} : {}),
    ...(cadence ? {automation_cadence: cadence} : {}),
    authorized_commission_count: steward ? (selected.goal_ids as string[]).length : 0,
    session_status: current?.status ?? null, active_turn_status: turn?.status ?? null,
    active_turn_observation_available: !current?.active_turn_id || turn !== null,
    queued_count: params.queued_count};
}

/** Explicit text grammar selects one existing typed Goal operation. Normal
 * conversation and model prose never grant creation or scheduling authority. */
export function stewardCommand(params: JsonObject): JsonObject {
  const command = params.command;
  const message = requireNonEmptyString(params.message, "steward command").trim();
  const patterns: Record<string, RegExp> = {commission: /^\/(?:delegate|委托)\s+--tokens\s+([1-9][0-9]{0,8})\s+([\s\S]+)$/,
    confirm_commission: /^\/confirm\s+(proposal-[a-f0-9]{32})$/,
    cancel_commission: /^\/cancel\s+(proposal-[a-f0-9]{32})$/,
    stop_commission: /^\/stop-commission\s+(proposal-[a-f0-9]{32})$/,
    resume_commission: /^\/resume-commission\s+(proposal-[a-f0-9]{32})\s+--tokens\s+([1-9][0-9]{0,8})$/};
  const match = patterns[String(command)]?.exec(message);
  const argument = command === "commission" ? match?.[2] : match?.[1];
  if (!match || !argument?.trim() || Array.from(argument).length > 1000) {
    throw new EffectRuntimeRequestError("invalid explicit steward command");
  }
  return {argument: argument.trim(), ...(command === "commission" ? {native_token_budget: Number(match[1])}
    : command === "resume_commission" ? {native_token_budget: Number(match[2])} : {})};
}

export function authorizeStewardCreation(params: JsonObject): JsonObject {
  const selected = normalizeStewardContext(params.context);
  const proposal = requireJsonObject(params.proposal, "creation proposal");
  const audience = requireJsonObject(proposal.context, "creation audience");
  const todoCreation = proposal.action_kind === "todo.create";
  if ((!todoCreation && proposal.action_kind !== "goal.create") || audience.kind !== "manager"
      || audience.goal_id !== "loopx-manager") throw new EffectRuntimeRequestError("not a steward creation preview");
  for (const field of ["binding_id", "source_ref", "provider_ref", "operator_ref", "project_ref"]) {
    if (audience[field] !== selected[field]) throw new EffectRuntimeRequestError("confirmation audience changed");
  }
  if (todoCreation) {
    const parameters = requireJsonObject(proposal.normalized_parameters, "Todo creation parameters");
    if (!(selected.goal_ids as string[]).includes(String(parameters.goal_id))
        || parameters.start_execution === true || parameters.agent_id != null
        || ["stop", "resume"].includes(String(params.operation))) {
      throw new EffectRuntimeRequestError("Todo confirmation requires its currently granted Goal and cannot start execution");
    }
  }
  if (["stop", "resume"].includes(String(params.operation))) {
    const receipt = requireJsonObject(proposal.receipt, "commission receipt");
    const resources = requireJsonObject(receipt.resource_ids, "commission resources");
    if (proposal.status !== "applied" || !(selected.goal_ids as string[]).includes(String(resources.goal_id))) {
      throw new EffectRuntimeRequestError("execution control requires the exact adopted commission");
    }
    return {authorized: true};
  }
  const expiry = Date.parse(String(audience.expires_at));
  const now = Date.parse(String(params.now));
  if (!Number.isFinite(expiry) || !Number.isFinite(now) || now > expiry
      || now < Date.parse(String(proposal.created_at)) - 300_000) {
    throw new EffectRuntimeRequestError("steward confirmation expired or timestamp invalid");
  }
  return {authorized: true};
}
