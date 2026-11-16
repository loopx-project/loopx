import type {JsonObject} from "../effect_program.ts";
import {isDeepStrictEqual} from "node:util";
import {createHash} from "node:crypto";
import {BARE_SHA256_PATTERN, ENVELOPED_SHA256_PATTERN} from "../content_digest.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireInteger, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {requireAutomationCadenceEligibility} from "../quota/automation_cadence.ts";
import {boundConversationExecutorMatches, normalizeProjectContext, normalizeStewardContext, normalizeStewardGoalScope} from "./conversation_scope.ts";
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
  // The prepared identity survives executor changes. Native execution still
  // requires the Codex binding at stewardManagedBasis, not at storage read.
  if (row.managed_actor !== undefined && (row.context_kind !== "steward" || group)) {
    throw new EffectRuntimeRequestError("managed identity requires a private steward");
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

function state(value: unknown): {schema_version: string; revision: number; bindings: JsonObject[]; app_steward_binding_id?: string | null} {
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
  // This is an explicit local entry preference, not an audience grant. Retain a
  // removed identity so disconnect/replacement cannot silently select a peer.
  const preferred = row.app_steward_binding_id;
  return {schema_version: SET_SCHEMA, revision: Number(row.revision), bindings,
    ...(preferred !== undefined ? {app_steward_binding_id: preferred === null ? null : ref(preferred, "App steward binding")} : {})};
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
  if (params.operation === "select_app_steward") {
    const id = params.binding_id === null ? null : ref(params.binding_id, "App steward binding");
    if (id !== null) {
      const selected = current.bindings.find(row => row.binding_id === id);
      if (!selected || selected.context_kind !== "steward" || selected.audience === "group") {
        throw new EffectRuntimeRequestError("select an existing private owner steward");
      }
      observed(selected, params.observation);
      boundProject(params, selected);
    }
    if ((current.app_steward_binding_id ?? null) === id) return {changed: false, state: current};
    return {changed: true, state: state({...current, revision: current.revision + 1, app_steward_binding_id: id})};
  } else if (params.operation === "reconcile_steward_executor") {
    const id = ref(params.binding_id, "steward binding");
    const previous = current.bindings.find(row => row.binding_id === id);
    const session = requireJsonObject(params.session, "original steward Session");
    const allocation = requireJsonObject(session.manager_executor_allocation, "steward executor allocation");
    const defaults = requireJsonObject(params.machine_defaults, "steward machine defaults");
    const endpoint = defaults.executor_endpoint;
    if (!previous || previous.context_kind !== "steward" || previous.audience === "group"
        || !["antigravity", "codex"].includes(String(previous.executor_endpoint_id))
        || !["antigravity", "codex"].includes(String(endpoint))
        || defaults.status !== "ready" || defaults.source !== "machine_configuration"
        || !["preferred", "pinned"].includes(String(defaults.selection_policy))
        || typeof defaults.configuration_revision !== "string"
        || !ENVELOPED_SHA256_PATTERN.test(defaults.configuration_revision)
        || allocation.schema_version !== "manager_executor_allocation_v0"
        || allocation.configuration_revision !== defaults.configuration_revision
        || allocation.selection_policy !== defaults.selection_policy || allocation.available !== true
        || allocation.configured_endpoint !== endpoint || allocation.executor_endpoint !== endpoint
        || allocation.model !== session.agent_model_id || allocation.reasoning_effort !== session.agent_reasoning_effort
        || (defaults.executor_model != null && defaults.executor_model !== allocation.model)
        || (defaults.executor_reasoning_effort != null && defaults.executor_reasoning_effort !== allocation.reasoning_effort)
        || session.agent_id !== endpoint || session.executor_endpoint_id !== endpoint
        || session.status !== "ready" || session.active_turn_id !== null || params.queue_empty !== true) {
      throw new EffectRuntimeRequestError("reconciliation requires the idle original steward and its adopted machine executor");
    }
    const saved = requireJsonObject(session.steward_context, "original steward context");
    rows = current.bindings.map(row => row === previous ? {...row, executor_endpoint_id: endpoint} : row);
    resolveBoundConversation({current: {...current, bindings: rows}, binding_id: id,
      observation: params.observation, available_projects: params.available_projects,
      available_goal_ids: params.available_goal_ids, source_ref: saved.source_ref,
      sender_ref: previous.operator_ref, private_human_message: false, origin: "web",
      session_context: saved, session});
    if (previous.executor_endpoint_id === endpoint) return {changed: false, state: current};
  } else if (params.operation === "configure") {
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

function matchesStewardContext(saved: JsonObject, context: JsonObject, row: JsonObject): boolean {
  return isDeepStrictEqual({...saved, goal_ids: []}, {...context, goal_ids: []})
    && (row.goal_scope === "all_registered"
      || normalizeStewardGoalScope(saved.goal_ids).every(goal => (row.goal_ids as string[]).includes(goal)));
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
      ? matchesStewardContext(saved, context, row) : isDeepStrictEqual(saved, context);
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
        || !boundConversationExecutorMatches(row, session)
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
  if (params.app_steward_entry === true
      && (current.app_steward_binding_id !== id || row.context_kind !== "steward" || row.audience === "group")) {
    throw new EffectRuntimeRequestError("the selected original steward is unavailable");
  }
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
  if (params.pending_observations !== undefined) {
    const observations = params.pending_observations;
    if (Object.keys(params).length !== 1 || !Array.isArray(observations) || observations.length > 64) {
      throw new EffectRuntimeRequestError("select at most 64 read-only pending observations");
    }
    return {pending_flags: observations.map(value => {
      const item = requireJsonObject(value, "pending observation input");
      if (Object.keys(item).some(key => !["request", "current_session", "pending_observation"].includes(key))
          || item.pending_observation === undefined) {
        throw new EffectRuntimeRequestError("pending batches cannot admit or recover work");
      }
      return planBoundConversationRequest(item).pending;
    })};
  }
  const row = requireJsonObject(params.request, "external request");
  const request = ref(row.request_ref, "external request identity");
  const command = row.command;
  const imageCount = params.attachment_count ?? 0;
  if (!Number.isSafeInteger(imageCount) || Number(imageCount) < 0 || Number(imageCount) > 4) {
    throw new EffectRuntimeRequestError("invalid external image attachment count");
  }
  if (params.history_answer_review !== undefined) {
    const observed = requireJsonObject(params.history_answer_review, "historical answer observation");
    const binding = requireJsonObject(params.binding, "conversation binding");
    const current = params.current_session == null ? null : requireJsonObject(params.current_session, "current Session");
    const source = requireJsonObject(row.source, "original owner source");
    const context = current?.steward_context as JsonObject | undefined;
    const identityAvailable = binding.context_kind === "steward" && binding.grant === "portfolio_read"
      && binding.enabled === true && (binding.audience ?? "owner") === "owner"
      && row.binding_id === binding.binding_id && source.sender_ref === binding.operator_ref
      && source.private_human_message === true && context?.binding_id === binding.binding_id
      && context?.source_ref === source.source_ref && context?.provider_ref === binding.provider_ref
      && context?.operator_ref === binding.operator_ref && current?.agent_id === "codex" && current.steward_context != null;
    const reserved = row.context_recovery as JsonObject | undefined;
    const previous = observed.previous_turn == null ? null : requireJsonObject(observed.previous_turn, "previous read-only answer");
    const response = previous?.response as JsonObject | undefined;
    const retained = row.context_recovery_history as JsonObject[] | undefined;
    const originalReview = reserved?.context_correction === true ? (Array.isArray(retained) ? retained.at(-1) : undefined) : reserved;
    const correctionVersion = Number(originalReview?.version) + 1;
    // One compatibility correction for a delivered, explicitly partial review
    // that predates original-context delivery. This never retries business or
    // a failed/unknown native Turn. Keep the old terminal answer immutable.
    const correction = row.status === "accepted" && row.capture_origin === "provider_history"
      && (row.delivery_verified === true || reserved?.previous_delivery_verified === true)
      && originalReview?.history_review === true && originalReview.read_only_review === true
      && originalReview.answer_review === true && originalReview.context_version == null
      && Number.isSafeInteger(originalReview.version) && Number(originalReview.version) > 0
      && Number.isSafeInteger(correctionVersion)
      && originalReview.failed_turn_id == null && originalReview.native_observation == null
      && row.session_id === current?.session_id
      && previous?.status === "completed" && previous.session_id === current?.session_id
      && typeof previous.upstream_turn_id === "string" && previous.upstream_turn_id.length > 0
      && previous.turn_id === row.turn_id && previous.message === row.message
      && previous.client_turn_id === originalReview.client_turn_id
      && originalReview.client_turn_id === `external-${request}-recovery-${originalReview.version}-review-1`
      && response?.gate != null && Array.isArray(response.proposals) && response.proposals.length === 0
      && !response.context_handoff && !response.protected_action && !response.goal_draft && !response.unfinished_work
      && (reserved === originalReview || reserved?.context_version === 1
        && reserved.context_correction === true && reserved.previous_answer_turn_id === previous.turn_id
        && reserved.version === correctionVersion
        && reserved.client_turn_id === `external-${request}-recovery-${correctionVersion}-review-1`);
    if (observed.discovery_only === true) return {pending: correction && identityAvailable && row.command === null
      && (reserved?.context_correction === true || current?.status === "ready" && current.active_turn_id == null)};
    const created = Date.parse(String(observed.source_created_at));
    const now = Date.parse(String(observed.observed_at));
    if (row.command !== null || (row.status !== "prepared" && !correction) || Number(imageCount) !== 0
        || !identityAvailable || !current || current.status !== "ready"
        || current.active_turn_id != null || current.steward_context == null
        || observed.source_verified !== true || observed.queue_empty !== true || observed.adapter_replaceable !== true
        || observed.stop_observed !== false || !Number.isSafeInteger(observed.version) || Number(observed.version) < 1
        || !Number.isFinite(created) || !Number.isFinite(now) || !Number.isFinite(Date.parse(String(current.created_at)))
        || created < Date.parse(String(current.created_at)) || created > now - 5_000
        || typeof row.message !== "string" || !row.message.trim() || row.message.trim().startsWith("/")) {
      return {operation: "hold_history_answer", reason: observed.stop_observed === true ? "source_stopped"
        : observed.adapter_replaceable !== true ? "owned_background_unavailable"
        : observed.queue_empty !== true || current?.active_turn_id != null ? "session_busy"
        : current?.agent_id !== "codex" ? "current_executor_unavailable"
        : created < Date.parse(String(current?.created_at)) ? "source_predates_session"
        : "source_scope_unavailable"};
    }
    // This creates a new, tools-restricted answer, never an execution of the
    // historical request or a fabricated failed native Turn.
    return {operation: "admit_history_answer", session_id: current.session_id,
      context_correction: correction, context_version: 1, version: correction ? correctionVersion : observed.version,
      ...(correction ? {previous_answer_turn_id: previous!.turn_id} : {}),
      // Preserve the existing read-only suffix for older answer-capable
      // readers. Advancing this request's reserved version is not a global
      // manager-context upgrade and cannot turn the review into business.
      client_turn_id: `external-${request}-recovery-${correction ? correctionVersion : observed.version}-review-1`};
  }
  if (![null, "agents", "select_agent", "select_project", "status", "help", "new", "stop", "unsupported", "commission", "confirm_commission", "cancel_commission", "stop_commission", "resume_commission"].includes(command as null | string)) {
    throw new EffectRuntimeRequestError("unsupported external conversation command");
  }
  const current = params.current_session === null ? null : requireJsonObject(params.current_session, "current Session");
  if (params.pending_observation !== undefined) {
    const observation = requireJsonObject(params.pending_observation, "pending work observation");
    const turn = observation.turn == null ? null : requireJsonObject(observation.turn, "observed Turn");
    // Pending work is not current retry eligibility: backoff, disabled bindings,
    // exhausted attempts and unavailable providers do not retire the request.
    // A delivered transport failure is not a business result. Its effects can
    // remain unknown, so visibility must not grant another execution.
    // A delivered partial answer is not completion either. Keep explicit
    // unfinished work and a correlated quota continuation's unresolved gate
    // visible, without changing recovery eligibility or admitting execution.
    const response = turn?.response && typeof turn.response === "object" && !Array.isArray(turn.response)
      ? turn.response as JsonObject : null;
    const reservation = row.context_recovery && typeof row.context_recovery === "object"
      && !Array.isArray(row.context_recovery) ? row.context_recovery as JsonObject : null;
    const gate = response?.gate;
    const continuationAttempt = reservation?.quota_attempt ?? reservation?.preparation_attempt;
    const continuationGate = reservation !== null && Number.isSafeInteger(continuationAttempt)
      && Number(continuationAttempt) >= 1 && Number(continuationAttempt) <= 3
      && typeof reservation.failed_turn_id === "string" && reservation.failed_turn_id !== row.turn_id
      && typeof reservation.client_turn_id === "string" && reservation.client_turn_id.length > 0
      && turn?.client_turn_id === reservation.client_turn_id
      && gate !== null && typeof gate === "object" && !Array.isArray(gate)
      && typeof (gate as JsonObject).kind === "string" && typeof (gate as JsonObject).summary === "string";
    const invalidMetadata = gate !== null && typeof gate === "object" && !Array.isArray(gate)
      && (gate as JsonObject).kind === "response_metadata_invalid" && typeof (gate as JsonObject).summary === "string";
    const unfinished = turn?.status === "completed" && response !== null
      && (unfinishedProjectWork(response) !== null || continuationGate || invalidMetadata);
    const metadataReview = reservation?.metadata_review === true
      && turn?.client_turn_id === reservation.client_turn_id;
    const metadataReceipts = response?.context_handoff_receipts;
    const metadataOwed = metadataReview && (turn?.status !== "completed"
      || !Array.isArray(metadataReceipts) || !Array.isArray(reservation.metadata_handoffs)
      || metadataReceipts.length !== reservation.metadata_handoffs.length
      || !Array.isArray(response?.context_handoff_failures) || response.context_handoff_failures.length > 0);
    const answerReviewOwed = reservation?.answer_review === true
      && turn?.client_turn_id === reservation.client_turn_id
      && (turn?.status !== "completed" || response?.gate != null);
    // A restricted result review can supply an answer without settling the
    // original business. Retain the exact declaration independently of whatever
    // the reviewer says; neither a delivered review nor its failure is replay authority.
    const originalResult = reservation?.original_turn as JsonObject | undefined;
    const originalResponse = originalResult?.response as JsonObject | undefined;
    const retainedWork = reservation != null && reservation.answer_review !== true
      && (reservation.read_only_review === true || reservation.work_attempt === 1
        && typeof reservation.completed_review_turn_id === "string" && !pureResultText(response))
      && turn?.client_turn_id === reservation.client_turn_id && originalResult?.status === "completed"
      && originalResult.session_id === row.session_id && originalResult.turn_id === reservation.failed_turn_id
      && originalResult.turn_id !== row.turn_id && originalResponse != null
      && unfinishedProjectWork(originalResponse) !== null
      && isDeepStrictEqual(originalResponse.unfinished_work, reservation.unfinished_work);
    const recovered = row.native_result_recovery == null ? null
      : requireJsonObject(row.native_result_recovery, "recovered native result");
    const native = recovered?.native_observation as JsonObject | undefined;
    const result = native?.response as JsonObject | undefined;
    const returned = recovered?.delivery_verified === true && recovered.turn_id === row.turn_id
      && recovered.message_id === `native.${row.turn_id}.recovered`
      && typeof recovered.message_text === "string" && recovered.message_text.length > 0
      && result != null && !["gate", "context_handoff", "protected_action", "goal_draft", "unfinished_work"].some(key => result[key])
      && Array.isArray(result.proposals) && result.proposals.length === 0;
    const expandedResult = turn?.status === "completed" && response !== null && result != null
      && recovered !== null && recovered.turn_id === row.turn_id && recovered.message_id === `native.${row.turn_id}.recovered`
      && typeof recovered.message_text === "string" && recovered.message_text.length > 0
      && extendsOriginalText(response, result);
    return {pending: !returned && row.command === null && row.status === "accepted"
      && turn?.session_id === row.session_id && turn?.turn_id === row.turn_id
      && (expandedResult || metadataOwed || answerReviewOwed || retainedWork || unfinished
        || turn?.status === "completed" && response?.message === "" && pureResultText(response, true)
        || turn?.status === "failed"
        && ["usage_limit_exceeded", "response_timeout", "upstream_transport_failure", "context_window_exceeded",
          "antigravity_rpc_error", "antigravity_transport_error", "antigravity_step_error",
          "manager_authorization_unavailable", "runtime_error", "server_restarted"].includes(String(turn.error_code)))
      && !(current?.session_id === row.session_id && current?.status === "closed")
      && observation.stop_observed !== true};
  }
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
  const adoptionHold = planStewardBindingAdoptionHold(params, row);
  return adoptionHold ?? {operation: "admit_turn", client_turn_id: `external-${request}`, session_id: session, turn_id: null};
}

function planStewardBindingAdoptionHold(params: JsonObject, request: JsonObject): JsonObject | null {
  if (params.steward_adoption_session == null || request.status !== "prepared"
      || request.command !== null || params.agent_target != null) return null;
  const original = requireJsonObject(params.steward_adoption_session, "original steward Session");
  const row = binding(params.binding);
  const allocation = original.manager_executor_allocation as JsonObject | undefined;
  const endpoint = original.executor_endpoint_id;
  if (row.context_kind !== "steward" || row.audience === "group"
      || !["antigravity", "codex"].includes(String(row.executor_endpoint_id))
      || !["antigravity", "codex"].includes(String(endpoint))
      || original.agent_id !== endpoint || endpoint === row.executor_endpoint_id
      || !["preferred", "pinned"].includes(String(allocation?.selection_policy))
      || !["ready", "busy", "stale", "resuming"].includes(String(original.status))) return null;
  const source = requireJsonObject(request.source, "original owner source");
  const context = requireJsonObject(params.context, "verified steward context");
  const saved = requireJsonObject(original.steward_context, "saved steward context");
  if (request.binding_id !== row.binding_id || source.private_human_message !== true
      || source.sender_ref !== row.operator_ref || context.operator_ref !== row.operator_ref
      || context.binding_id !== row.binding_id || context.provider_ref !== row.provider_ref
      || context.project_ref !== row.project_ref || context.source_ref !== source.source_ref
      || context.audience !== "bound_owner" || context.grant !== "portfolio_read"
      || original.goal_id !== "loopx-manager" || original.session_mode !== "managed_runtime"
      || original.project_context != null
      || original.channel_id !== `manager.external.native.${row.binding_id}.${source.source_ref}`
      || !matchesStewardContext(saved, context, row)) {
    throw new EffectRuntimeRequestError("the original steward Session belongs to another source");
  }
  return {operation: "hold_steward_binding", session_id: sessionIdentity(original.session_id, "original steward Session")};
}

function pureResultText(response: JsonObject | null, allowEmpty = false): boolean {
  return response?.schema_version === "loopx_chat_agent_response_v0"
    && typeof response.message === "string" && (allowEmpty || response.message.length > 0)
    && Array.isArray(response.proposals) && response.proposals.length === 0
    && ["gate", "context_handoff", "protected_action", "goal_draft", "unfinished_work"].every(key => response[key] == null);
}

function unsubmittedMetadata(response: JsonObject | null): boolean {
  const gate = response?.gate as JsonObject | undefined;
  return response?.schema_version === "loopx_chat_agent_response_v0"
    && typeof response.message === "string" && response.message.length > 0
    && gate?.kind === "response_metadata_invalid" && typeof gate.summary === "string"
    && Array.isArray(response.proposals) && response.proposals.length === 0
    && ["context_handoff", "protected_action", "goal_draft", "unfinished_work"].every(key => response[key] == null);
}

function extendsOriginalText(original: JsonObject, result: JsonObject): boolean {
  const metadata = (value: JsonObject) => Object.fromEntries(Object.entries(value)
    .filter(([key, value]) => key !== "message" && value != null));
  return pureResultText(original, true) && pureResultText(result)
    && (result.message as string).length > (original.message as string).length
    && (result.message as string).startsWith(original.message as string)
    && isDeepStrictEqual(metadata(original), metadata(result));
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
  const pendingTurn = recovery.failed_turn as JsonObject | undefined;
  const pendingResponse = pendingTurn?.response as JsonObject | undefined;
  // A committed empty answer has no delivered failure to acknowledge first.
  // It may only adopt its verified native result, never dispatch recovery work.
  const pendingEmpty = current?.agent_id === "antigravity" && pendingTurn?.status === "completed"
    && pendingResponse?.message === "" && pureResultText(pendingResponse, true);
  if (!current || row.command !== null || row.status !== "accepted" || row.delivery_verified !== true && !pendingEmpty
      || row.agent_target != null || current.session_id !== row.session_id
      || !["codex", "antigravity"].includes(String(current.agent_id))
      || current.upstream_mode !== (current.agent_id === "antigravity" ? "default" : "chat") || current.status === "closed"
      || current.session_mode === "attached_host") return no;
  const selected = binding(params.binding);
  if (selected.audience === "group") return no;
  const steward = selected.context_kind === "steward";
  if (!boundConversationExecutorMatches(selected, {...current, executor_endpoint_id: current.executor_endpoint_id ?? current.agent_id}) || current.goal_id !== (steward ? "loopx-manager" : null)) return no;
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
  if (recovery.retained_result_review != null) {
    if ((row.context_recovery as JsonObject | undefined)?.legacy_result_review === true) {
      return planRelatedResultReview(recovery, row, current, failed,
        row.context_recovery as JsonObject, selected, version);
    }
    return planWorkAfterResultReview(recovery, row, current, failed, selected, version);
  }
  const runtimePreparation = planRuntimePreparationRecovery(recovery, row, current, failed, version);
  if (runtimePreparation !== null) return runtimePreparation;
  const preparation = steward ? planPreparationRecovery(recovery, row, current, failed, version) : null;
  if (preparation !== null) return preparation;
  // Once preparation has dispatched, a different terminal category must not
  // switch recovery families and start a fresh business retry budget.
  if ((row.context_recovery != null
      && requireJsonObject(row.context_recovery, "preparation reservation").preparation_attempt != null)
      || (Array.isArray(row.context_recovery_history) && row.context_recovery_history.some(value =>
        requireJsonObject(value, "prior preparation recovery").preparation_attempt != null))) return no;
  if (current.agent_id === "antigravity") {
    if (failed.error_code === "runtime_error") {
      // Parsing/presentation can fail after native work. This route admits
      // only a restricted review of independently read original evidence;
      // it never retries the failed business Turn or adopts an unverified final.
      return planInterruptedStewardAnswer(recovery, row, current, failed, version);
    }
    if (failed.error_code === "antigravity_step_error") {
      const quota = planNativeQuotaRecovery(recovery, row, current, failed, version);
      const observed = recovery.native_observation as JsonObject | null | undefined;
      return quota.recovery_candidate === true && observed?.read_only_review !== true ? quota
        : planInterruptedStewardAnswer(recovery, row, current, failed, version);
    }
    const originalResponse = failed.response && typeof failed.response === "object" && !Array.isArray(failed.response)
      ? failed.response as JsonObject : null;
    const anchor = recovery.native_input && typeof recovery.native_input === "object" && !Array.isArray(recovery.native_input)
      ? recovery.native_input as JsonObject : null;
    // The lexical hint only bounds read-only probes. It confers no result,
    // source or execution authority; an anchored native final is still needed.
    const completedText = failed.status === "completed" && pureResultText(originalResponse, true)
      && (recovery.response_literal_open === true || originalResponse!.message === "")
      && recovery.queued_input_present === false
      && anchor !== null && anchor.thread_id === current.upstream_thread_id
      && Number.isSafeInteger(anchor.start_step_index) && Number(anchor.start_step_index) >= 0
      && typeof anchor.input_sha256 === "string" && BARE_SHA256_PATTERN.test(anchor.input_sha256);
    const completedMetadata = failed.status === "completed" && unsubmittedMetadata(originalResponse)
      && anchor !== null && anchor.thread_id === current.upstream_thread_id
      && Number.isSafeInteger(anchor.start_step_index) && Number(anchor.start_step_index) >= 0
      && typeof anchor.input_sha256 === "string" && BARE_SHA256_PATTERN.test(anchor.input_sha256);
    // Result adoption has no admission/dispatch authority. Keep the failed
    // Turn and original failure delivery, even when native tools ran.
    if (!steward || failed.session_id !== current.session_id || failed.turn_id !== row.turn_id
        || failed.client_turn_id !== `external-${row.request_ref}` || failed.message !== row.message
        || (!completedText && !completedMetadata && (failed.status !== "failed" || failed.response != null
          || !["response_timeout", "antigravity_rpc_error", "antigravity_transport_error",
            "upstream_transport_failure", "server_restarted"].includes(String(failed.error_code))))
        || recovery.attachments_present !== false || recovery.steering_observed !== false
        || recovery.stop_observed !== false) return no;
    const adopted = row.native_result_recovery == null ? null
      : requireJsonObject(row.native_result_recovery, "adopted native result");
    if (completedMetadata) {
      // The original prose was already delivered. Review only its unsubmitted
      // metadata: no duplicate prose adoption, Turn rewrite or business replay.
      return planNativeMetadataReview(recovery, row, current, failed, null, version);
    }
    if (adopted?.delivery_verified === true) {
      if (completedText) return no; // A repaired answer never starts a format review or business continuation.
      return planNativeMetadataReview(recovery, row, current, failed, adopted, version);
    }
    // A fresh native result requires an idle, stable trajectory. Do not spend
    // shared reply workers probing older results while this Session is busy.
    if (current.active_turn_id != null || current.status !== "ready") return no;
    if (row.context_recovery != null) return no;
    const candidate = {...no, operation: "adopt_native_result", recovery_candidate: true,
      recover_native_result: true, failed_turn_id: failed.turn_id, session_id: current.session_id};
    const proof = recovery.native_observation == null ? null
      : requireJsonObject(recovery.native_observation, "native terminal readback");
    if (proof === null) return candidate;
    const created = Date.parse(String(proof.input_created_at));
    const start = Date.parse(String(failed.started_at)), end = Date.parse(String(failed.completed_at));
    const response = proof.response == null ? null : requireJsonObject(proof.response, "native result response");
    // Returning an already generated historical answer is not replay. Bind its
    // exact original input and closed native segment; later ordinary questions
    // must not strand that answer. Legacy unanchored results stay latest-only.
    const anchoredHistorical = anchor !== null && anchor.thread_id === current.upstream_thread_id
      && Number.isSafeInteger(anchor.start_step_index) && Number(anchor.start_step_index) >= 0
      && typeof anchor.input_sha256 === "string" && BARE_SHA256_PATTERN.test(anchor.input_sha256)
      && proof.start_step_index === anchor.start_step_index && proof.input_sha256 === anchor.input_sha256
      && proof.latest_input_verified === false && Number.isSafeInteger(proof.end_step_index)
      && Number(proof.end_step_index) > Number(proof.start_step_index) + 1
      && Number(proof.end_step_index) < Number(proof.total_steps);
    return {...candidate, recovery_eligible: recovery.source_verified === true
      && proof.thread_id === current.upstream_thread_id && proof.request_ref === row.request_ref
      && typeof proof.execution_id === "string" && proof.execution_id.length > 0
      && (completedText
        ? proof.start_step_index === anchor!.start_step_index && proof.input_sha256 === anchor!.input_sha256
          && (proof.latest_input_verified === true || proof.latest_input_verified === false
            && Number.isSafeInteger(proof.end_step_index)
            && Number(proof.end_step_index) > Number(proof.start_step_index) + 1
            && Number(proof.end_step_index) < Number(proof.total_steps))
          && response !== null && extendsOriginalText(originalResponse!, response)
        : proof.latest_input_verified === true || anchoredHistorical)
      && proof.normal_terminal_verified === true
      && Number.isFinite(created) && created >= start && created <= end
      && Number.isSafeInteger(proof.start_step_index) && Number(proof.start_step_index) >= 0
      && Number.isSafeInteger(proof.total_steps) && Number(proof.total_steps) > Number(proof.start_step_index) + 1
      && [proof.input_sha256, proof.answer_sha256].every(value => typeof value === "string" && BARE_SHA256_PATTERN.test(value))
      && response?.schema_version === "loopx_chat_agent_response_v0"
      && typeof response.message === "string" && response.message.trim().length > 0
      && (adopted === null || adopted.turn_id === failed.turn_id
        && adopted.message_id === `native.${failed.turn_id}.recovered`
        && typeof adopted.message_text === "string" && adopted.message_text.length > 0
        && isDeepStrictEqual(adopted.native_observation, proof))};
  }
  const previous = row.context_recovery == null ? null : requireJsonObject(row.context_recovery, "reserved context recovery");
  // One completed-work continuation is terminal for this request, including
  // when it later fails. It cannot fall through to routing/quota retries.
  if ((previous?.work_attempt != null && previous.failed_turn_id !== row.turn_id)
      || (Array.isArray(row.context_recovery_history) && row.context_recovery_history.some(value =>
        requireJsonObject(value, "prior recovery").work_attempt != null))) return no;
  // A failed review cannot fall through to routing/quota business retries.
  if ((previous?.read_only_review === true && previous.failed_turn_id !== row.turn_id)
      || (Array.isArray(row.context_recovery_history) && row.context_recovery_history.some(value =>
        requireJsonObject(value, "prior recovery").read_only_review === true))) return no;
  if (failed.error_code === "server_restarted") {
    return planResultReview(recovery, row, current, failed, previous, version);
  }
  const exhaustedAnswer = steward && current.agent_id === "codex"
    ? planExhaustedStewardAnswer(recovery, row, current, failed, previous, version) : null;
  if (exhaustedAnswer !== null) return exhaustedAnswer;
  if (failed.status === "completed" && !steward) {
    if (recovery.related_result_review != null) {
      return planRelatedResultReview(recovery, row, current, failed, previous, selected, version);
    }
    return planUnfinishedWork(recovery, row, current, failed, previous, version);
  }
  if (failed.session_id !== current.session_id || failed.turn_id !== row.turn_id
      || failed.status !== "failed" || failed.delta_count !== 0 || failed.response !== null
      || recovery.steering_observed !== false
      || !["context_window_exceeded", "host_gate", "upstream_transport_failure", "server_overloaded", "usage_limit_exceeded"].includes(String(failed.error_code))
      || typeof failed.upstream_turn_id !== "string" || !failed.upstream_turn_id) return no;
  const retryKind = failed.error_code === "usage_limit_exceeded" ? "quota" : "routing";
  const attemptKey = `${retryKind}_attempt`;
  const client = `external-${ref(row.request_ref, "original request")}-recovery-${version}`;
  const attempt = previous?.[attemptKey];
  const reservedClient = Number.isSafeInteger(attempt) && Number(attempt) >= 1 && Number(attempt) <= 3
    ? `${client}-${retryKind}-${attempt}`
    : attempt === undefined && steward && previous?.client_turn_id === client ? client : null;
  if (recovery.admitted_turn != null && previous !== null && previous.failed_turn_id === failed.turn_id
      && previous.version === version && reservedClient !== null) {
    // A crash after queue admission must adopt that exact canonical Turn,
    // even if it has already executed. This branch never creates model work.
    const admitted = requireJsonObject(recovery.admitted_turn, "admitted recovery Turn");
    const origin = reservedClient === client && row.origin === undefined ? "lark" : row.origin;
    if (previous.client_turn_id !== reservedClient || admitted.client_turn_id !== reservedClient
        || admitted.session_id !== current.session_id || admitted.turn_id === failed.turn_id
        || typeof row.message !== "string" || typeof origin !== "string"
        || admitted.message !== row.message || admitted.origin !== origin
        || !Array.isArray(admitted.attachments ?? []) || (admitted.attachments as unknown[] | undefined)?.length) return no;
    return {operation: "admit_turn", recovery_candidate: true,
      recovery_eligible: recovery.source_verified === true, client_turn_id: reservedClient,
      admitted_turn_id: sessionIdentity(admitted.turn_id, "admitted recovery Turn"),
      session_id: current.session_id, native_thread_ids: []};
  }
  // A source stop or a newer admitted equivalent vetoes new execution.
  // Correlation repair above remains read-only and keeps its exact identity.
  if (recovery.stop_observed === true || recovery.later_equivalent_input_present === true) return no;
  const routing = planRoutingRecovery(recovery, row, current, failed, previous, version);
  if (routing !== null) return routing;
  if (!steward) return no;
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

/** A text-only format review of already returned native prose. This is never
 * another business attempt, and any failed/unknown review exhausts the route.
 */
function textualBrief(value: unknown, depth = 0): boolean {
  if (depth > 8) return false;
  if (typeof value === "string") return true;
  if (Array.isArray(value)) return value.every(item => textualBrief(item, depth + 1));
  return value !== null && typeof value === "object"
    && Object.values(value).every(item => textualBrief(item, depth + 1));
}

function planNativeMetadataReview(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, adopted: JsonObject | null, version: number): JsonObject {
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  const client = `external-${ref(row.request_ref, "original request")}-recovery-${version}-metadata-1`;
  const previous = row.context_recovery == null ? null : requireJsonObject(row.context_recovery, "metadata review reservation");
  if (previous !== null && (previous.metadata_review !== true || previous.version !== version
      || previous.failed_turn_id !== failed.turn_id || previous.client_turn_id !== client)) return no;
  const candidate = {...no, recovery_candidate: true, metadata_review: true, client_turn_id: client,
    session_id: current.session_id, failed_turn_id: failed.turn_id};
  if (recovery.admitted_turn == null && (current.status !== "ready" || current.active_turn_id != null
      || recovery.queued_input_present !== false)) return no;
  const supplied = adopted?.native_observation ?? previous?.native_observation ?? recovery.native_observation;
  if (supplied == null) return adopted === null ? candidate : no;
  const {raw_response: _raw, ...frozen} = requireJsonObject(supplied, "original native observation");
  const response = requireJsonObject(frozen.response, "original native response");
  if (!unsubmittedMetadata(response) || frozen.thread_id !== current.upstream_thread_id
      || frozen.request_ref !== row.request_ref || frozen.normal_terminal_verified !== true) return no;
  if (adopted !== null) {
    if (adopted.turn_id !== failed.turn_id || adopted.message_id !== `native.${failed.turn_id}.recovered`
        || typeof adopted.message_text !== "string" || !adopted.message_text
        || frozen.latest_input_verified !== true) return no;
  } else {
    const anchor = requireJsonObject(recovery.native_input, "original completed input");
    const created = Date.parse(String(frozen.input_created_at));
    if (!isDeepStrictEqual(failed.response, response) || frozen.start_step_index !== anchor.start_step_index
        || typeof frozen.execution_id !== "string" || !frozen.execution_id
        || frozen.input_sha256 !== anchor.input_sha256 || !Number.isFinite(created)
        || created < Date.parse(String(failed.started_at)) || created > Date.parse(String(failed.completed_at))
        || !Number.isSafeInteger(frozen.total_steps) || Number(frozen.total_steps) <= Number(frozen.start_step_index) + 1
        || ![frozen.input_sha256, frozen.answer_sha256].every(v => typeof v === "string" && BARE_SHA256_PATTERN.test(v))
        || !(frozen.latest_input_verified === true || frozen.latest_input_verified === false
          && Number.isSafeInteger(frozen.end_step_index) && Number(frozen.end_step_index) > Number(frozen.start_step_index) + 1
          && Number(frozen.end_step_index) < Number(frozen.total_steps))) return no;
  }
  if (previous !== null && recovery.admitted_turn != null) {
    const admitted = requireJsonObject(recovery.admitted_turn, "admitted metadata review");
    if (admitted.client_turn_id !== client || admitted.session_id !== current.session_id
        || admitted.turn_id === failed.turn_id || admitted.message !== row.message
        || admitted.origin !== row.origin || !Array.isArray(admitted.attachments ?? [])
        || (admitted.attachments as unknown[] | undefined)?.length) return no;
    return {...candidate, recovery_eligible: recovery.source_verified === true,
      admitted_turn_id: sessionIdentity(admitted.turn_id, "admitted metadata review"),
      ...(previous.metadata_executor === "codex" ? {metadata_executor: "codex"} : {}),
      metadata_handoffs: previous.metadata_handoffs};
  }
  if (current.status !== "ready" || current.active_turn_id != null || recovery.queued_input_present !== false) return no;
  if (recovery.native_observation == null) return candidate;
  const proof = requireJsonObject(recovery.native_observation, "fresh original native result");
  const {raw_response: raw, ...observed} = proof;
  if (!isDeepStrictEqual(observed, frozen) || typeof raw !== "string" || raw.length > 100000
      || createHash("sha256").update(raw).digest("hex") !== frozen.answer_sha256) return candidate;
  // The parser retained prose but admitted none of the malformed action batch.
  // Transcribe bounded JSON briefs as exact evidence strings; this does not
  // interpret their fields or grant actions. The normal owner must still
  // validate the formatter's canonical brief and each current recipient.
  let payload: JsonObject;
  try {
    const start = raw.lastIndexOf("<loopx-review-json>"), end = raw.lastIndexOf("</loopx-review-json>");
    if (start < 0 || end <= start) return candidate;
    payload = requireJsonObject(JSON.parse(raw.slice(start + "<loopx-review-json>".length, end)), "native action envelope");
  } catch { return candidate; }
  if (Object.keys(payload).some(key => !["schema_version", "message", "context_handoff", "proposals", "gate",
        "goal_draft", "protected_action", "unfinished_work"].includes(key))
      || [payload.goal_draft, payload.protected_action, payload.gate, payload.unfinished_work].some(v => v != null)
      || !Array.isArray(payload.proposals ?? []) || (payload.proposals as unknown[] | undefined)?.length
      ) return candidate;
  const handoffs = Array.isArray(payload.context_handoff) ? payload.context_handoff : [payload.context_handoff];
  if (!handoffs.length || handoffs.length > 32) return candidate;
  const targets = new Set<string>();
  const transcribed: JsonObject[] = [];
  for (const value of handoffs) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return candidate;
    const item = value as JsonObject;
    const keys = Object.keys(item).filter(key => key !== "schema_version").sort().join(",");
    if (keys !== "agent_id,brief,goal_id" || item.schema_version != null && item.schema_version !== "context_handoff_v0"
        || [item.goal_id, item.agent_id].some(v => typeof v !== "string" || !/^[A-Za-z0-9._-]{1,160}$/.test(v))
        || !(typeof item.brief === "string" || item.brief && typeof item.brief === "object" && !Array.isArray(item.brief))
        || !textualBrief(item.brief)) return candidate;
    const brief = typeof item.brief === "string" ? item.brief : JSON.stringify(item.brief);
    if (!brief.trim() || brief.length > 6000) return candidate;
    const target = JSON.stringify([item.goal_id, item.agent_id]);
    if (targets.has(target)) return candidate;
    targets.add(target);
    transcribed.push({goal_id: item.goal_id, agent_id: item.agent_id, brief});
  }
  return nativeRecoveryCapacity(recovery, row, current, {...candidate, metadata_handoffs: transcribed,
    ...(adopted === null && recovery.source_verified === true ? {retain_metadata_result: true} : {})});
}

/** A quota error alone never permits replay. One exact native input/error pair,
 * no later source input, fresh capacity and the original source permit a new
 * queue Turn. Effects, ambiguous terminal state and missing anchors stay held.
 */
function planNativeQuotaRecovery(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, version: number): JsonObject {
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  const originalClient = `external-${ref(row.request_ref, "original request")}`;
  if (failed.session_id !== current.session_id || failed.turn_id !== row.turn_id
      || failed.client_turn_id !== originalClient || failed.message !== row.message || failed.origin !== row.origin
      || failed.status !== "failed" || failed.response !== null || failed.delta_count !== 0
      || typeof failed.upstream_turn_id !== "string" || !failed.upstream_turn_id
      || recovery.attachments_present !== false || recovery.steering_observed !== false) return no;
  const client = `${originalClient}-recovery-${version}-quota-1`;
  const previous = row.context_recovery == null ? null : requireJsonObject(row.context_recovery, "quota reservation");
  if (row.context_recovery_history != null
      && (!Array.isArray(row.context_recovery_history) || row.context_recovery_history.length)
      || previous !== null && (previous.version !== version || previous.quota_attempt !== 1
        || previous.failed_turn_id !== failed.turn_id || previous.client_turn_id !== client
        || ["routing_attempt", "preparation_attempt", "read_only_review", "metadata_review", "work_attempt"]
          .some(key => previous[key] != null))) return no;
  if (recovery.admitted_turn != null && previous !== null) {
    const admitted = requireJsonObject(recovery.admitted_turn, "admitted quota Turn");
    if (admitted.client_turn_id !== client || admitted.session_id !== current.session_id
        || admitted.turn_id === failed.turn_id || admitted.message !== row.message || admitted.origin !== row.origin
        || !Array.isArray(admitted.attachments ?? []) || (admitted.attachments as unknown[] | undefined)?.length) return no;
    return {...no, recovery_candidate: true, recovery_eligible: recovery.source_verified === true,
      client_turn_id: client, admitted_turn_id: sessionIdentity(admitted.turn_id, "admitted quota Turn"),
      session_id: current.session_id, native_thread_ids: []};
  }
  const now = Date.parse(String(recovery.observed_at));
  const notBefore = Date.parse(String(failed.completed_at)) + 60_000;
  const anchor = recovery.native_input as JsonObject | null | undefined;
  if (recovery.stop_observed !== false || recovery.later_source_input_present !== false
      || current.status !== "ready" || current.active_turn_id != null || recovery.queued_input_present !== false
      || !Number.isFinite(now) || !Number.isFinite(notBefore) || now < notBefore || !anchor
      || anchor.thread_id !== current.upstream_thread_id || !Number.isSafeInteger(anchor.start_step_index)
      || Number(anchor.start_step_index) < 0 || typeof anchor.input_sha256 !== "string"
      || !BARE_SHA256_PATTERN.test(anchor.input_sha256)) return no;
  const candidate = {...no, recovery_candidate: true, quota_attempt: 1, client_turn_id: client,
    not_before: new Date(notBefore).toISOString(), failed_turn_id: failed.turn_id, session_id: current.session_id,
    native_thread_ids: [current.upstream_thread_id]};
  const proof = recovery.native_observation as JsonObject | null | undefined;
  if (!proof) return candidate;
  const created = Date.parse(String(proof.input_created_at));
  const started = Date.parse(String(failed.started_at)), completed = Date.parse(String(failed.completed_at));
  if (proof.thread_id !== anchor.thread_id || proof.request_ref !== row.request_ref
      || proof.start_step_index !== anchor.start_step_index || proof.input_sha256 !== anchor.input_sha256
      || proof.total_steps !== Number(anchor.start_step_index) + 2 || proof.latest_input_verified !== true
      || proof.pre_execution_quota_failure !== true || proof.only_original_user_item !== true
      || proof.model_id !== (current.agent_model_id ?? "gemini-4-argon-high")
      || typeof proof.execution_id !== "string" || !proof.execution_id
      || typeof proof.observed_at !== "number" || !Number.isFinite(proof.observed_at)
      || now / 1000 < proof.observed_at || now / 1000 - proof.observed_at > 120
      || ![created, started, completed].every(Number.isFinite) || created < started || created > completed) return candidate;
  return nativeRecoveryCapacity(recovery, row, current, candidate);
}

/** A model list/positive remainder is not proof a known quota failure cleared.
 * Keep the existing request pending without reserving its one format attempt.
 */
function nativeRecoveryCapacity(recovery: JsonObject, row: JsonObject, current: JsonObject,
  candidate: JsonObject): JsonObject {
  if (recovery.source_verified !== true) return candidate;
  const capacity = recovery.metadata_capacity as JsonObject | null | undefined;
  const now = Date.parse(String(recovery.observed_at)) / 1000;
  const alternate = capacity?.alternate_executor as JsonObject | undefined;
  const allocation = alternate?.allocation as JsonObject | undefined;
  const configuration = recovery.executor_configuration as JsonObject | undefined;
  if (alternate && typeof alternate.observed_at === "number" && Number.isFinite(alternate.observed_at)
      && alternate.observed_at <= now && now - alternate.observed_at <= 120
      && allocation?.selection_policy === "flexible" && allocation.configured_endpoint === "antigravity"
      && allocation.executor_endpoint === "codex" && allocation.available === true
      && Array.isArray(allocation.eligible_endpoints) && allocation.eligible_endpoints.length === 2
      && allocation.eligible_endpoints.includes("antigravity") && allocation.eligible_endpoints.includes("codex")
      && allocation.schema_version === "manager_executor_allocation_v0"
      && allocation.allocation_reason === "flexible_availability_fallback"
      && configuration?.status === "ready" && configuration.selection_policy === "flexible"
      && configuration.executor_endpoint === "antigravity"
      && Array.isArray(configuration.eligible_endpoints) && configuration.eligible_endpoints.length === 2
      && configuration.eligible_endpoints.includes("antigravity") && configuration.eligible_endpoints.includes("codex")
      && typeof allocation.configuration_revision === "string" && allocation.configuration_revision.length > 0
      && allocation.configuration_revision === configuration.configuration_revision) {
    return {...candidate, recovery_eligible: true,
      ...(candidate.metadata_review === true ? {metadata_executor: "codex"}
        : candidate.answer_review === true ? {answer_executor: "codex"} : {quota_executor: "codex"})};
  }
  if (!capacity || capacity.model_id !== (current.agent_model_id ?? "gemini-4-argon-high")
      || typeof capacity.account_ref !== "string" || !BARE_SHA256_PATTERN.test(capacity.account_ref)
      || typeof capacity.remaining_fraction !== "number" || !Number.isFinite(capacity.remaining_fraction)
      || capacity.remaining_fraction < 0 || capacity.remaining_fraction > 1
      || typeof capacity.observed_at !== "number" || !Number.isFinite(capacity.observed_at)
      || !Number.isFinite(now) || now < capacity.observed_at || now - capacity.observed_at > 120
      || typeof capacity.quota_exhausted !== "boolean" || typeof capacity.normal_completion !== "boolean"
      || typeof capacity.reset_time !== "string"
      || !Number.isFinite(Date.parse(String(capacity.reset_time)))) return candidate;
  const waiting = row.metadata_quota_wait as JsonObject | null | undefined;
  if (waiting != null && (typeof waiting.model_id !== "string" || typeof waiting.remaining_fraction !== "number"
      || typeof waiting.account_ref !== "string" || !BARE_SHA256_PATTERN.test(waiting.account_ref)
      || !Number.isFinite(waiting.remaining_fraction) || waiting.remaining_fraction < 0 || waiting.remaining_fraction > 1
      || typeof waiting.reset_time !== "string" || !Number.isFinite(Date.parse(waiting.reset_time)))) return candidate;
  const sameEpoch = waiting?.model_id === capacity.model_id && waiting.account_ref === capacity.account_ref
    && Date.parse(String(waiting.reset_time)) === Date.parse(capacity.reset_time);
  // Fractions can fluctuate across refreshes. Only a quota/account epoch
  // change or the known reset boundary can clear this recorded hard failure.
  const restored = waiting != null && (!sameEpoch || now >= Date.parse(String(waiting.reset_time)) / 1000);
  if (capacity.remaining_fraction <= 0 || waiting != null && !restored
      || waiting == null && capacity.quota_exhausted === true) {
    return {...candidate, metadata_quota_wait: waiting ?? capacity};
  }
  return {...candidate, recovery_eligible: true};
}

/** A completed model Turn can explicitly report unfinished business. The
 * proposal alone grants nothing: only the original worker may reserve one
 * continuation after stable correlated-native/source/grant/stop checks.
 */
function unfinishedProjectWork(result: JsonObject): JsonObject | null {
  const raw = result.unfinished_work;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const work = raw as JsonObject;
  if (Object.keys(work).sort().join(",") !== "dependency,effects_settled,remaining"
      || work.effects_settled !== true
      || [work.remaining, work.dependency].some(value => typeof value !== "string"
        || !value.trim() || value.length > 1000)
      || [result.gate, result.protected_action, result.context_handoff, result.goal_draft].some(value => value != null)
      || !Array.isArray(result.proposals) || result.proposals.length) return null;
  return work;
}

function planWorkAfterResultReview(recovery: JsonObject, row: JsonObject, current: JsonObject,
  original: JsonObject, selected: JsonObject, version: number): JsonObject {
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  const retained = requireJsonObject(recovery.retained_result_review, "retained result review");
  const reservation = requireJsonObject(retained.reservation, "original review reservation");
  const reviewed = requireJsonObject(retained.turn, "completed result review");
  const previous = requireJsonObject(row.context_recovery, "current recovery reservation");
  const history = Array.isArray(row.context_recovery_history) ? row.context_recovery_history as JsonObject[] : [];
  const continuing = previous.work_attempt === 1;
  const expected = continuing ? history.at(-1) : previous;
  const work = original.response != null && typeof original.response === "object" && !Array.isArray(original.response)
    ? unfinishedProjectWork(original.response as JsonObject) : null;
  // A delivered read-only report retains intent. Only a fresh observation by
  // the original writable owner can admit the already bounded remaining work.
  if (selected.context_kind !== "project" || current.agent_id !== "codex" || work === null
      || !isDeepStrictEqual(expected, reservation) || reservation.read_only_review !== true
      || reservation.answer_review === true || reservation.history_review === true || reservation.work_attempt != null
      || reservation.version !== version || !isDeepStrictEqual(reservation.binding_snapshot, selected)
      || reservation.client_turn_id !== `external-${row.request_ref}-recovery-${version}-review-1`
      || !isDeepStrictEqual(reservation.original_turn, original) || !isDeepStrictEqual(reservation.unfinished_work, work)
      || original.status !== "completed" || original.session_id !== current.session_id
      || original.turn_id !== reservation.failed_turn_id || original.client_turn_id !== `external-${row.request_ref}`
      || original.message !== row.message || original.origin !== row.origin
      || reviewed.status !== "completed" || reviewed.session_id !== current.session_id
      || reviewed.turn_id !== row.turn_id || reviewed.turn_id === original.turn_id
      || reviewed.client_turn_id !== reservation.client_turn_id || reviewed.message !== row.message
      || reviewed.origin !== row.origin || typeof reviewed.upstream_turn_id !== "string" || !reviewed.upstream_turn_id
      || !pureResultText(reviewed.response as JsonObject | null)
      || history.some(item => item.work_attempt != null)
      || continuing && (previous.failed_turn_id !== original.turn_id
        || previous.completed_review_turn_id !== reviewed.turn_id)) return no;
  const admitted = continuing ? recovery.admitted_turn : null;
  if (admitted == null && (recovery.live_work_owner !== true || recovery.later_equivalent_input_present !== false)) return no;
  const proof = recovery.native_observation as JsonObject | null | undefined;
  if (proof != null && (proof.completed_work !== true || proof.all_effects_settled !== true)) return no;
  const plan = planUnfinishedWork({...recovery, admitted_turn: admitted}, {...row, turn_id: original.turn_id},
    current, original, continuing ? previous : null, version);
  return plan.work_attempt === 1 ? {...plan, completed_review_turn_id: reviewed.turn_id} : no;
}

function planUnfinishedWork(recovery: JsonObject, row: JsonObject, current: JsonObject,
  completed: JsonObject, previous: JsonObject | null, version: number): JsonObject {
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  const response = completed.response;
  if (!response || typeof response !== "object" || Array.isArray(response)) return no;
  const work = unfinishedProjectWork(response as JsonObject);
  if (work === null) return no;
  const matchesWork = (value: unknown) => value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join(",") === "dependency,effects_settled,remaining"
    && ["remaining", "dependency", "effects_settled"].every(key => (value as JsonObject)[key] === work[key]);
  if (completed.session_id !== current.session_id || completed.turn_id !== row.turn_id
      || recovery.steering_observed !== false || recovery.stop_observed !== false
      || recovery.attachments_present !== false
      || typeof completed.upstream_turn_id !== "string" || !completed.upstream_turn_id) return no;
  const observedResult = recovery.native_observation as JsonObject | null | undefined;
  // An admitted continuation may already have appended the original input.
  // Recover its existing receipt before considering a fresh result review.
  const existingWorkTurn = previous?.work_attempt === 1 && recovery.admitted_turn != null;
  if (previous?.read_only_review === true || (!existingWorkTurn
      && (recovery.later_equivalent_input_present === true || observedResult?.completed_result_review === true))) {
    return planCompletedResultReview(recovery, row, current, completed, previous, version, work);
  }
  const client = `external-${ref(row.request_ref, "original request")}-recovery-${version}-work-1`;
  if (previous?.work_attempt != null && (previous.work_attempt !== 1
      || previous.failed_turn_id !== completed.turn_id || previous.version !== version
      || previous.client_turn_id !== client || !matchesWork(previous.unfinished_work))) return no;
  const candidate = {...no, recovery_candidate: true, work_attempt: 1, unfinished_work: work,
    client_turn_id: client, session_id: current.session_id, failed_turn_id: completed.turn_id};
  if (previous?.work_attempt === 1 && recovery.admitted_turn != null) {
    const admitted = requireJsonObject(recovery.admitted_turn, "admitted unfinished work");
    if (admitted.client_turn_id !== client || admitted.session_id !== current.session_id
        || admitted.turn_id === completed.turn_id || admitted.message !== row.message
        || admitted.origin !== row.origin || !Array.isArray(admitted.attachments ?? [])
        || (admitted.attachments as unknown[] | undefined)?.length) return no;
    return {...candidate, recovery_eligible: recovery.source_verified === true,
      admitted_turn_id: sessionIdentity(admitted.turn_id, "admitted unfinished work"), native_thread_ids: [],
      not_before: previous.not_before};
  }
  if (current.active_turn_id != null || current.status !== "ready"
      || recovery.queued_input_present !== false || recovery.later_equivalent_input_present !== false) return no;
  const now = Date.parse(String(recovery.observed_at)), ended = Date.parse(String(completed.completed_at));
  if (!Number.isFinite(now) || !Number.isFinite(ended) || now < ended + 60_000) return no;
  const native = sessionIdentity(current.upstream_thread_id, "original native thread");
  const planned = {...candidate, native_thread_ids: [native], not_before: new Date(ended + 60_000).toISOString()};
  if (recovery.native_observation == null) return planned;
  const observed = requireJsonObject(recovery.native_observation, "completed unfinished-work inventory");
  return {...planned, recovery_eligible: recovery.source_verified === true
    && observed.thread_id === native && observed.turn_id === completed.upstream_turn_id
    && observed.completed_work === true && observed.all_effects_settled === true
    && typeof observed.native_inventory_sha256 === "string" && BARE_SHA256_PATTERN.test(observed.native_inventory_sha256)
    && matchesWork(observed.unfinished_work)};
}

/** A separately delivered review can justify inspecting an older same-question
 * source, never treating two message IDs as one completed business request. */
function planRelatedResultReview(recovery: JsonObject, row: JsonObject, current: JsonObject,
  completed: JsonObject, previous: JsonObject | null, selected: JsonObject, version: number): JsonObject {
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  const baseClient = `external-${ref(row.request_ref, "original request")}-recovery-${version}-review-1`;
  const preparationReserved = previous?.review_preparation_attempt === 1;
  const history = (row.context_recovery_history ?? []) as JsonObject[];
  if (selected.context_kind !== "project" || current.agent_id !== "codex"
      || !pureResultText(completed.response as JsonObject | null)
      || completed.session_id !== current.session_id || completed.status !== "completed"
      || recovery.attachments_present !== false || recovery.steering_observed !== false
      || recovery.stop_observed !== false || typeof completed.upstream_turn_id !== "string"
      || !completed.upstream_turn_id || previous !== null && previous.legacy_result_review !== true
      || previous?.review_preparation_attempt != null && !preparationReserved
      || !Array.isArray(history) || history.length && !(preparationReserved && history.length === 1
        && history[0].legacy_result_review === true && history[0].read_only_review === true
        && history[0].review_preparation_attempt == null && history[0].client_turn_id === baseClient
        && history[0].failed_turn_id === completed.turn_id)) return no;
  const related = recovery.related_result_review as JsonObject | null | undefined;
  const sibling = related?.request as JsonObject | undefined;
  const reviewed = related?.turn as JsonObject | undefined;
  const reserved = sibling?.context_recovery as JsonObject | undefined;
  const original = reserved?.original_turn as JsonObject | undefined;
  const work = original?.response ? unfinishedProjectWork(original.response as JsonObject) : null;
  if (!related || related.steering_observed !== false || !sibling || !reviewed || !reserved || !original || !work
      || sibling.request_ref === row.request_ref || sibling.binding_id !== row.binding_id
      || sibling.session_id !== row.session_id || !isDeepStrictEqual(sibling.source, row.source)
      || sibling.message !== row.message || sibling.origin !== row.origin || sibling.command !== null
      || sibling.status !== "accepted" || sibling.delivery_verified !== true || sibling.turn_id !== reviewed.turn_id
      || reserved.read_only_review !== true || reserved.legacy_result_review === true
      || reserved.answer_review === true || reserved.history_review === true || reserved.work_attempt != null
      || !isDeepStrictEqual(reserved.binding_snapshot, selected) || !isDeepStrictEqual(reserved.unfinished_work, work)
      || original.session_id !== current.session_id || original.turn_id !== reserved.failed_turn_id
      || original.client_turn_id !== `external-${sibling.request_ref}` || original.status !== "completed"
      || original.message !== row.message || original.origin !== row.origin
      || reviewed.session_id !== current.session_id || reviewed.status !== "completed"
      || reviewed.client_turn_id !== reserved.client_turn_id || reviewed.message !== row.message
      || reviewed.origin !== row.origin || !pureResultText(reviewed.response as JsonObject | null)
      || !Number.isFinite(Date.parse(String(original.completed_at)))
      || !Number.isFinite(Date.parse(String(completed.created_at)))
      || !Number.isFinite(Date.parse(String(reviewed.completed_at)))
      || !Number.isFinite(Date.parse(String(completed.completed_at)))
      || Date.parse(String(original.completed_at)) > Date.parse(String(completed.created_at))
      || Date.parse(String(reviewed.completed_at)) <= Date.parse(String(completed.completed_at))) return no;
  // A local preparation failure may leave the single model invocation unused.
  // Require the exact queue/host-gate terminal record, no native input/delta or
  // response, fresh closed native inventory, and retain this failed reservation.
  // Never recover a review that actually reached the model or spend twice.
  const preparation = recovery.result_review_preparation as JsonObject | undefined;
  const failed = preparation?.turn as JsonObject | undefined;
  const events = preparation?.events;
  const preparationOnly = previous !== null && failed?.status === "failed"
    && failed.session_id === current.session_id && failed.client_turn_id === baseClient
    && row.turn_id === failed.turn_id && failed.message === row.message && failed.origin === row.origin
    && failed.error_code === "host_gate" && failed.upstream_turn_id == null && failed.response == null
    && failed.delta_count === 0 && Array.isArray(events) && events.length === 2
    && (events[0] as JsonObject).kind === "turn.queued" && (events[1] as JsonObject).kind === "turn.failed"
    && ((events[1] as JsonObject).payload as JsonObject | undefined)?.error_code === "host_gate"
    && Number.isFinite(Date.parse(String(failed.completed_at)))
    && Date.parse(String(recovery.observed_at)) >= Date.parse(String(failed.completed_at)) + 60_000;
  if (previous !== null && row.turn_id !== completed.turn_id && row.delivery_verified === true && !preparationOnly) return no;
  const observation = preparationOnly && !preparationReserved ? {...recovery, admitted_turn: null} : recovery;
  const plan = planCompletedResultReview(observation, row, current, completed, previous, version, null);
  return preparationOnly && plan.recovery_candidate === true ? {...plan,
    client_turn_id: `external-${row.request_ref}-recovery-${version}-preparation-1`, review_preparation_attempt: 1,
    preparation_failed_turn_id: failed!.turn_id, original_result_turn_id: completed.turn_id} : plan;
}

/** A result-only fallback never consumes or restores the writable retry budget. */
function planCompletedResultReview(recovery: JsonObject, row: JsonObject, current: JsonObject,
  completed: JsonObject, previous: JsonObject | null, version: number, work: JsonObject | null): JsonObject {
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  // An inconclusive effect observation must not replace the original writable
  // owner with a result reader. Keep that owner for the next fresh work proof.
  // Already reserved reviews retain their own adoption and execution checks.
  if (previous === null && recovery.live_work_owner !== false) return no;
  // Old readers recognize preparation-1 and reject its read-only policy, so a
  // rollback holds this receipt instead of treating an unknown key as ordinary input.
  const client = `external-${ref(row.request_ref, "original request")}-recovery-${version}-`
    + (previous?.review_preparation_attempt === 1 ? "preparation-1" : "review-1");
  if (work !== null && previous?.review_preparation_attempt != null) return no;
  if (current.steward_context != null || completed.client_turn_id !== `external-${row.request_ref}`
      || completed.message !== row.message || completed.origin !== row.origin
      || previous !== null && (previous.read_only_review !== true || previous.failed_turn_id !== completed.turn_id
        || previous.client_turn_id !== client || previous.version !== version || previous.work_attempt != null
        || previous.answer_review === true || !isDeepStrictEqual(previous.original_turn, completed)
        || (work === null ? previous.legacy_result_review !== true || previous.unfinished_work != null
          || !isDeepStrictEqual(previous.related_result_review, recovery.related_result_review)
          : !isDeepStrictEqual(previous.unfinished_work, work)))) return no;
  const intent = work === null
    ? {legacy_result_review: true, related_result_review: recovery.related_result_review}
    : {unfinished_work: work};
  const candidate = {...no, recovery_candidate: true, read_only_review: true, ...intent,
    failed_turn_id: completed.turn_id, session_id: current.session_id, client_turn_id: client};
  if (previous !== null && recovery.admitted_turn != null) {
    const admitted = requireJsonObject(recovery.admitted_turn, "admitted result review");
    if (admitted.client_turn_id !== client || admitted.session_id !== current.session_id
        || admitted.turn_id === completed.turn_id || admitted.message !== row.message || admitted.origin !== row.origin
        || !Array.isArray(admitted.attachments ?? []) || (admitted.attachments as unknown[] | undefined)?.length) return no;
    return {...candidate, recovery_eligible: recovery.source_verified === true,
      admitted_turn_id: sessionIdentity(admitted.turn_id, "admitted result review"), native_thread_ids: []};
  }
  const now = Date.parse(String(recovery.observed_at)), ended = Date.parse(String(completed.completed_at));
  if (current.status !== "ready" || current.active_turn_id != null || recovery.queued_input_present !== false
      || !Number.isFinite(now) || !Number.isFinite(ended) || now < ended + 60_000) return no;
  const native = sessionIdentity(current.upstream_thread_id, "original native thread");
  const planned = {...candidate, native_thread_ids: [native]};
  if (recovery.native_observation == null) return planned;
  const proof = requireJsonObject(recovery.native_observation, "completed result review inventory");
  return {...planned, recovery_eligible: recovery.source_verified === true
    && proof.thread_id === native && proof.turn_id === completed.upstream_turn_id
    && proof.read_only_review === true && proof.completed_result_review === true
    && proof.completed_work !== true && proof.all_effects_settled !== true
    && typeof proof.visible_response === "string" && proof.visible_response.trim().length > 0
    && typeof proof.native_inventory_sha256 === "string" && BARE_SHA256_PATTERN.test(proof.native_inventory_sha256)
    && isDeepStrictEqual(proof.unfinished_work, work)
    && (work !== null || proof.untyped_result_review === true && isDeepStrictEqual(proof.response, completed.response))};
}

function interruptedVisibleResult(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const result = value as JsonObject;
  if (Object.keys(result).join(",") === "visible_response") {
    return typeof result.visible_response === "string" && result.visible_response.trim().length > 0;
  }
  if (Object.keys(result).sort().join(",") === "content,file_uri") {
    if (typeof result.file_uri !== "string" || typeof result.content !== "string") return false;
    try {
      const uri = new URL(result.file_uri);
      return uri.protocol === "file:" && uri.host === "" && uri.pathname.startsWith("/")
        && uri.search === "" && uri.hash === "";
    } catch { return false; }
  }
  return typeof result.commandLine === "string" && result.commandLine.length > 0
    && Number.isSafeInteger(result.exitCode);
}

/** Exhausting business retries must not erase the original question. One
 * separately sandboxed answer review can inspect existing results, never
 * reopen the business budget. Its failed attempt is terminal too.
 */
function planExhaustedStewardAnswer(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, previous: JsonObject | null, version: number): JsonObject | null {
  if (previous === null) return null;
  const client = `external-${ref(row.request_ref, "original request")}-recovery-${version}-review-1`;
  const reserved = previous?.answer_review === true && previous.read_only_review === true
    && previous.failed_turn_id === failed.turn_id && previous.client_turn_id === client;
  if (!reserved && previous?.quota_attempt !== 3) return null;
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  const budget = reserved && Array.isArray(row.context_recovery_history)
    ? row.context_recovery_history.at(-1) as JsonObject | undefined : previous;
  if (current.steward_context == null || failed.session_id !== current.session_id || failed.turn_id !== row.turn_id
      || failed.message !== row.message || failed.origin !== row.origin
      || failed.status !== "failed" || failed.error_code !== "usage_limit_exceeded"
      || failed.delta_count !== 0 || failed.response !== null || typeof failed.upstream_turn_id !== "string"
      || !failed.upstream_turn_id || previous?.version !== version
      || typeof current.upstream_thread_id !== "string" || !current.upstream_thread_id
      || recovery.attachments_present !== false || recovery.steering_observed !== false
      || recovery.stop_observed !== false
      || budget?.quota_attempt !== 3 || budget.version !== version || budget.failed_turn_id === failed.turn_id
      || budget.client_turn_id !== failed.client_turn_id
      || failed.client_turn_id !== `external-${row.request_ref}-recovery-${version}-quota-3`
      || ["metadata_review", "preparation_attempt", "routing_attempt", "work_attempt"].some(key => previous[key] != null)) return no;
  const history = current.upstream_context_history ?? [];
  if (!Array.isArray(history)) return no;
  const threads = [current.upstream_thread_id, ...history.map(value => requireJsonObject(value, "native context provenance"))
    .filter(item => (item.agent_id == null || item.agent_id === "codex")
      && Number.isSafeInteger(item.manager_context_version) && Number(item.manager_context_version) >= 1
      && Number(item.manager_context_version) <= version)
    .map(item => sessionIdentity(item.thread_id, "original native thread"))];
  const priorNative = budget.native_observation as JsonObject | undefined;
  const priorThreadId = priorNative?.thread_id;
  const nativeThreads = [...new Set([...(typeof priorThreadId === "string" && threads.includes(priorThreadId) ? [priorThreadId] : []), ...threads])];
  const candidate = {...no, recovery_candidate: true, answer_review: true, read_only_review: true,
    client_turn_id: client, session_id: current.session_id, failed_turn_id: failed.turn_id,
    native_thread_ids: nativeThreads};
  if (reserved && recovery.admitted_turn != null) {
    const admitted = requireJsonObject(recovery.admitted_turn, "admitted answer review");
    if (admitted.client_turn_id !== client || admitted.session_id !== current.session_id
        || admitted.turn_id === failed.turn_id || admitted.message !== row.message || admitted.origin !== row.origin
        || !Array.isArray(admitted.attachments ?? []) || (admitted.attachments as unknown[] | undefined)?.length) return no;
    return {...candidate, recovery_eligible: recovery.source_verified === true, answer_executor: "codex",
      admitted_turn_id: sessionIdentity(admitted.turn_id, "answer review"), native_thread_ids: []};
  }
  const now = Date.parse(String(recovery.observed_at)), ended = Date.parse(String(failed.completed_at));
  // Let a newer admitted resend answer the question. The branch above still
  // repairs the receipt of a review already accepted under its own identity.
  if (current.status !== "ready" || current.active_turn_id != null || recovery.queued_input_present !== false
      || recovery.later_equivalent_input_present === true
      || !Number.isFinite(now) || !Number.isFinite(ended) || now < ended + 60_000) return no;
  if (recovery.native_observation == null) return candidate;
  const proof = requireJsonObject(recovery.native_observation, "exhausted original request inventory");
  if (!nativeThreads.some(thread => thread === proof.thread_id) || proof.turn_id !== failed.upstream_turn_id
      || proof.only_original_user_item !== true || proof.codex_error_info !== "usageLimitExceeded") return candidate;
  const capacity = nativeRecoveryCapacity(recovery, row, current, candidate);
  return {...capacity, recovery_eligible: capacity.recovery_eligible === true && capacity.answer_executor === "codex"};
}

/** Interrupted work may have effects. Never retry it. The original worker may
 * reserve one read-only review of existing results, with its own presentation.
 * The adapter retains only the original runtime's authorized reads, with
 * filesystem writes, network effects and new delegated actions disabled.
 */
function planInterruptedStewardAnswer(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, version: number): JsonObject {
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  const anchor = recovery.native_input as JsonObject | null | undefined;
  const previous = row.context_recovery as JsonObject | null | undefined;
  const client = `external-${ref(row.request_ref, "original request")}-recovery-${version}-review-1`;
  if (current.steward_context == null || failed.session_id !== current.session_id || failed.turn_id !== row.turn_id
      || failed.client_turn_id !== `external-${row.request_ref}` || failed.message !== row.message || failed.origin !== row.origin
      || failed.status !== "failed" || failed.response !== null || typeof failed.upstream_turn_id !== "string"
      || recovery.attachments_present !== false || recovery.steering_observed !== false || recovery.stop_observed !== false
      || !anchor || anchor.thread_id !== current.upstream_thread_id || !Number.isSafeInteger(anchor.start_step_index)
      || Number(anchor.start_step_index) < 0 || typeof anchor.input_sha256 !== "string"
      || !BARE_SHA256_PATTERN.test(anchor.input_sha256)
      || (Array.isArray(row.context_recovery_history) && row.context_recovery_history.length)
      || previous != null && (previous.answer_review !== true || previous.read_only_review !== true
        || ["metadata_review", "quota_attempt", "routing_attempt", "preparation_attempt", "work_attempt"].some(key => previous[key] != null)
        || previous.failed_turn_id !== failed.turn_id || previous.version !== version || previous.client_turn_id !== client)) return no;
  const candidate = {...no, recovery_candidate: true, answer_review: true, read_only_review: true,
    client_turn_id: client, session_id: current.session_id, failed_turn_id: failed.turn_id,
    native_thread_ids: [current.upstream_thread_id]};
  if (previous != null && recovery.admitted_turn != null) {
    const admitted = requireJsonObject(recovery.admitted_turn, "admitted answer review");
    if (admitted.client_turn_id !== client || admitted.session_id !== current.session_id
        || admitted.turn_id === failed.turn_id || admitted.message !== row.message || admitted.origin !== row.origin
        || !Array.isArray(admitted.attachments ?? []) || (admitted.attachments as unknown[] | undefined)?.length) return no;
    return {...candidate, recovery_eligible: recovery.source_verified === true,
      answer_executor: "codex", admitted_turn_id: sessionIdentity(admitted.turn_id, "answer review"), native_thread_ids: []};
  }
  const now = Date.parse(String(recovery.observed_at)), ended = Date.parse(String(failed.completed_at));
  if (current.status !== "ready" || current.active_turn_id != null || recovery.queued_input_present !== false
      || !Number.isFinite(now) || !Number.isFinite(ended) || now < ended + 60_000) return no;
  if (recovery.native_observation == null) return candidate;
  const proof = requireJsonObject(recovery.native_observation, "interrupted original answer inventory");
  const created = Date.parse(String(proof.input_created_at));
  if (proof.thread_id !== current.upstream_thread_id || proof.request_ref !== row.request_ref
      || proof.start_step_index !== anchor.start_step_index || proof.input_sha256 !== anchor.input_sha256
      || typeof proof.execution_id !== "string" || !proof.execution_id || !Number.isFinite(created)
      || created < Date.parse(String(failed.started_at)) || created > ended
      || !Number.isSafeInteger(proof.end_step_index) || Number(proof.end_step_index) <= Number(proof.start_step_index) + 2
      || !Number.isSafeInteger(proof.total_steps) || Number(proof.end_step_index) > Number(proof.total_steps)
      || proof.read_only_review !== true || proof.no_final_answer !== true || proof.effects_observed !== true
      || !Array.isArray(proof.visible_results) || !proof.visible_results.length || proof.visible_results.length > 128
      || Buffer.byteLength(JSON.stringify(proof.visible_results), "utf8") > 256 * 1024
      || !proof.visible_results.every(interruptedVisibleResult)) return candidate;
  const capacity = nativeRecoveryCapacity(recovery, row, current, candidate);
  // This is a new restricted answer review, never native business replay.
  return {...capacity, recovery_eligible: capacity.recovery_eligible === true && capacity.answer_executor === "codex"};
}

/** Reuse the original queue for a restricted result review, never business replay. */
function planResultReview(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, previous: JsonObject | null, version: number): JsonObject {
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  const steward = current.steward_context != null;
  const review = steward ? {answer_review: true, answer_executor: "codex"} : {};
  if (failed.session_id !== current.session_id || failed.turn_id !== row.turn_id
      || failed.status !== "failed" || failed.response !== null
      || !Number.isSafeInteger(failed.delta_count) || Number(failed.delta_count) <= 0
      || recovery.steering_observed !== false || recovery.stop_observed !== false
      || recovery.attachments_present !== false
      || typeof failed.upstream_turn_id !== "string" || !failed.upstream_turn_id
      || steward && (failed.client_turn_id !== `external-${row.request_ref}`
        || failed.message !== row.message || failed.origin !== row.origin)) return no;
  const client = `external-${ref(row.request_ref, "original request")}-recovery-${version}-review-1`;
  if (previous?.read_only_review === true && (previous.failed_turn_id !== failed.turn_id
      || previous.version !== version || previous.client_turn_id !== client)) return no;
  if (recovery.admitted_turn != null && previous?.read_only_review === true) {
    const admitted = requireJsonObject(recovery.admitted_turn, "admitted result review");
    if (admitted.client_turn_id !== client || admitted.session_id !== current.session_id
        || admitted.turn_id === failed.turn_id || admitted.message !== row.message
        || admitted.origin !== row.origin || (admitted.attachments != null
          && (!Array.isArray(admitted.attachments) || admitted.attachments.length))) return no;
    return {...no, ...review, recovery_candidate: true, recovery_eligible: recovery.source_verified === true,
      read_only_review: true, client_turn_id: client, session_id: current.session_id,
      admitted_turn_id: sessionIdentity(admitted.turn_id, "admitted result review"), native_thread_ids: []};
  }
  if (current.active_turn_id != null || current.status !== "ready") return no;
  const now = Date.parse(String(recovery.observed_at)), completed = Date.parse(String(failed.completed_at));
  if (!Number.isFinite(now) || !Number.isFinite(completed) || now < completed + 60_000) return no;
  const native = sessionIdentity(current.upstream_thread_id, "original native thread");
  const candidate = {...no, ...review, recovery_candidate: true, read_only_review: true,
    client_turn_id: client, session_id: current.session_id, failed_turn_id: failed.turn_id,
    native_thread_ids: [native]};
  if (recovery.native_observation == null) return candidate;
  const observed = requireJsonObject(recovery.native_observation, "interrupted result inventory");
  const eligible = recovery.source_verified === true
    && observed.thread_id === native && observed.turn_id === failed.upstream_turn_id
    && observed.read_only_review === true && observed.no_final_answer === true
    && observed.effects_observed === true && (!steward
      || typeof observed.native_inventory_sha256 === "string"
        && BARE_SHA256_PATTERN.test(observed.native_inventory_sha256));
  if (!eligible || !steward) return {...candidate, recovery_eligible: eligible};
  const capacity = nativeRecoveryCapacity(recovery, row, current, candidate);
  return {...capacity, recovery_eligible: capacity.recovery_eligible === true && capacity.answer_executor === "codex"};
}

/** A local manager-context failure precedes native dispatch. Only the Core's
 * exact preparation event sequence can qualify it; absence alone cannot.
 */
function samePreparationImages(left: unknown, right: unknown): boolean {
  if (!left || typeof left !== "object" || Array.isArray(left)) return false;
  const image = left as JsonObject;
  return Number.isSafeInteger(image.count) && Number(image.count) > 0
    && typeof image.sha256 === "string" && BARE_SHA256_PATTERN.test(image.sha256)
    && isDeepStrictEqual(left, right);
}

function legacyImagePreparation(recovery: JsonObject, failed: JsonObject): boolean {
  const images = recovery.image_attachments as JsonObject | undefined;
  // A bounded migration for the old adapter's exact pre-dispatch rejection.
  // Neither generic runtime errors nor absent upstream IDs establish safety.
  return failed.error_code === "runtime_error"
    && failed.error === "attachments currently require the Codex Agent endpoint"
    && recovery.attachments_present === true && recovery.native_result_present === false
    && images != null && samePreparationImages(images.request, images.failed);
}

/** The queue's durable started_at checkpoint precedes every model dispatch.
 * Exact queued/failed records with that checkpoint still null qualify a local
 * adapter preparation failure, including the older misleading resume error.
 * Unknown or started Turns never enter this path. Continuations share the
 * existing three-attempt counter across quota/routing/preparation families.
 */
function planRuntimePreparationRecovery(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, version: number): JsonObject | null {
  if (!["model_auth_unavailable", "resume_failed"].includes(String(failed.error_code))) return null;
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  const expectedError = failed.error_code === "model_auth_unavailable"
    ? "Native account API transport is unavailable." : "The previous Agent conversation could not be restored.";
  if (current.agent_id !== "codex" || failed.session_id !== current.session_id || failed.turn_id !== row.turn_id
      || failed.status !== "failed" || failed.error !== expectedError || failed.started_at !== null
      || failed.first_event_at !== null || failed.response !== null || failed.upstream_turn_id !== null
      || failed.delta_count !== 0 || failed.message !== row.message || failed.origin !== row.origin
      || recovery.attachments_present !== false || recovery.steering_observed !== false
      || recovery.native_observation != null || recovery.native_result_present !== false) return no;
  const observed = recovery.preparation_observation == null ? null
    : requireJsonObject(recovery.preparation_observation, "runtime preparation readback");
  if (observed?.kind !== "core_runtime_preparation_failure" || observed.session_id !== failed.session_id
      || observed.turn_id !== failed.turn_id || !Array.isArray(observed.events) || observed.events.length !== 2) return no;
  const events = observed.events.map(value => requireJsonObject(value, "runtime preparation event"));
  if (!events.every(item => typeof item.event_id === "string" && item.event_id.length > 0)
      || events[0].event_id === events[1].event_id || events[0].kind !== "turn.queued"
      || events[0].delivery_mode !== "session_queue" || events[1].kind !== "turn.failed"
      || events[1].error_code !== failed.error_code || events[1].message !== expectedError) return no;
  const original = `external-${ref(row.request_ref, "original request")}`;
  const previous = row.context_recovery == null ? null : requireJsonObject(row.context_recovery, "runtime preparation reservation");
  if (row.context_recovery_history != null && !Array.isArray(row.context_recovery_history)) return no;
  const history = ((row.context_recovery_history ?? []) as unknown[])
    .map(value => requireJsonObject(value, "runtime preparation lineage"));
  const lineage = [...history, ...(previous ? [previous] : [])];
  const counters = ["quota", "routing", "preparation"];
  const count = (item: JsonObject): number => {
    const kinds = counters.filter(kind => item[`${kind}_attempt`] != null);
    if (kinds.length !== 1 || item.version !== version
        || ["read_only_review", "metadata_review", "work_attempt"].some(key => item[key] != null)) return 0;
    const attempt = item[`${kinds[0]}_attempt`];
    return Number.isSafeInteger(attempt) && Number(attempt) >= 1 && Number(attempt) <= 3
      && item.client_turn_id === `${original}-recovery-${version}-${kinds[0]}-${attempt}` ? Number(attempt) : 0;
  };
  if (lineage.some(item => !count(item))) return no;
  const reserved = previous?.failed_turn_id === failed.turn_id;
  if (reserved && previous?.preparation_attempt == null) return no;
  const prior = reserved ? history : lineage;
  if (prior.some((item, i) => i > 0 && count(item) <= count(prior[i - 1]))) return no;
  const last = prior.at(-1);
  if (failed.client_turn_id !== (last?.client_turn_id ?? original)) return no;
  const attempt = last ? count(last) + 1 : 1;
  if (attempt > 3 || reserved && previous?.preparation_attempt !== attempt) return no;
  const client = `${original}-recovery-${version}-preparation-${attempt}`;
  if (reserved && previous?.client_turn_id !== client) return no;
  if (reserved && recovery.admitted_turn != null) {
    const admitted = requireJsonObject(recovery.admitted_turn, "admitted runtime preparation Turn");
    if (admitted.client_turn_id !== client || admitted.session_id !== current.session_id
        || admitted.turn_id === failed.turn_id || admitted.message !== row.message || admitted.origin !== row.origin
        || !Array.isArray(admitted.attachments ?? []) || (admitted.attachments as unknown[] | undefined)?.length) return no;
    return {...no, recovery_candidate: true, recovery_eligible: recovery.source_verified === true,
      client_turn_id: client, admitted_turn_id: sessionIdentity(admitted.turn_id, "admitted runtime preparation Turn"),
      preparation_attempt: attempt, session_id: current.session_id, native_thread_ids: []};
  }
  if (current.active_turn_id != null || current.status !== "ready" || recovery.stop_observed !== false
      || recovery.queued_input_present !== false || recovery.later_equivalent_input_present !== false) return no;
  const now = Date.parse(String(recovery.observed_at)), completed = Date.parse(String(failed.completed_at));
  const notBefore = completed + 60_000 * 2 ** (attempt - 1);
  if (!Number.isFinite(now) || !Number.isFinite(completed) || now < notBefore) return no;
  return {...no, recovery_candidate: true, recovery_eligible: recovery.source_verified === true
    && recovery.preparation_scope_available === true, preparation_attempt: attempt,
    not_before: new Date(notBefore).toISOString(), preparation_observation: observed,
    client_turn_id: client, failed_turn_id: failed.turn_id, session_id: current.session_id, native_thread_ids: []};
}

function planPreparationRecovery(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, version: number): JsonObject | null {
  const images = legacyImagePreparation(recovery, failed);
  if (failed.error_code !== "manager_authorization_unavailable" && !images) return null;
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  if (failed.session_id !== current.session_id || failed.turn_id !== row.turn_id
      || failed.status !== "failed" || failed.response !== null || failed.upstream_turn_id !== null
      || failed.delta_count !== 0 || failed.message !== row.message || (!images && recovery.attachments_present !== false)
      || recovery.steering_observed !== false || recovery.native_observation != null) return no;
  const previous = row.context_recovery == null ? null : requireJsonObject(row.context_recovery, "preparation recovery");
  // Only one migration of the original rejected image input. An admitted
  // continuation that later fails cannot become another attachment retry.
  if (images && previous !== null && (previous.failed_turn_id !== failed.turn_id
      || previous.preparation_attempt !== 1)) return no;
  const otherKinds = ["routing_attempt", "quota_attempt", "read_only_review", "metadata_review", "work_attempt"];
  if (previous !== null && (!Number.isSafeInteger(previous.preparation_attempt)
      || Number(previous.preparation_attempt) < 1 || Number(previous.preparation_attempt) > 3
      || previous.version !== version || otherKinds.some(key => previous[key] != null))) return no;
  if (row.context_recovery_history != null && (!Array.isArray(row.context_recovery_history)
      || row.context_recovery_history.some(value => {
        const item = requireJsonObject(value, "prior preparation recovery");
        return otherKinds.some(key => item[key] != null);
      }))) return no;
  const reserved = previous?.failed_turn_id === failed.turn_id;
  const prior = previous === null ? 0 : Number(previous.preparation_attempt) - (reserved ? 1 : 0);
  const originalClient = `external-${ref(row.request_ref, "original request")}`;
  const failedClient = prior === 0 ? originalClient : `${originalClient}-recovery-${version}-preparation-${prior}`;
  if (failed.client_turn_id !== failedClient) return no;
  if (reserved && recovery.admitted_turn != null) {
    const admitted = requireJsonObject(recovery.admitted_turn, "admitted preparation continuation");
    const client = `${originalClient}-recovery-${version}-preparation-${previous?.preparation_attempt}`;
    if (previous?.client_turn_id !== client || admitted.client_turn_id !== client
        || admitted.session_id !== current.session_id || admitted.turn_id === failed.turn_id
        || admitted.message !== row.message || admitted.origin !== row.origin
        || (images ? !samePreparationImages((recovery.image_attachments as JsonObject).request,
              (recovery.image_attachments as JsonObject).admitted)
          : !Array.isArray(admitted.attachments ?? []) || (admitted.attachments as unknown[] | undefined)?.length)) return no;
    return {...no, recovery_candidate: true, recovery_eligible: recovery.source_verified === true,
      client_turn_id: client, admitted_turn_id: sessionIdentity(admitted.turn_id, "admitted preparation Turn"),
      preparation_attempt: previous?.preparation_attempt,
      session_id: current.session_id, native_thread_ids: []};
  }
  // Newer human input has priority. An equivalent later submission can already
  // have executed; it is never authority to replay this older failure too.
  if (recovery.stop_observed !== false || recovery.queued_input_present !== false
      || recovery.later_equivalent_input_present !== false) return no;
  return planRoutingRecovery(recovery, row, current, failed, previous, version) ?? no;
}

/** Routing outages outlive one worker or context migration. Reserve at most
 * three separately correlated continuations, with a persisted failure clock.
 * Provider IO must prove a latest, terminal, pre-execution request every time.
 */
function planRoutingRecovery(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, previous: JsonObject | null, version: number): JsonObject | null {
  const quota = failed.error_code === "usage_limit_exceeded";
  const images = legacyImagePreparation(recovery, failed);
  const preparation = failed.error_code === "manager_authorization_unavailable" || images;
  const kind = preparation ? "preparation" : quota ? "quota" : "routing";
  const attemptKey = `${kind}_attempt`;
  if (!quota && !preparation && !["host_gate", "upstream_transport_failure"].includes(String(failed.error_code))) return null;
  // An unfinished older reservation must finish under its original identity.
  if (previous !== null && previous.failed_turn_id === failed.turn_id && previous[attemptKey] === undefined) return null;
  const raw = preparation ? recovery.preparation_observation : recovery.native_observation;
  const observed = raw == null ? null : requireJsonObject(raw, "pre-execution failure readback");
  const interrupted = observed?.interrupted_before_execution === true
    && ["host_gate", "upstream_transport_failure"].includes(String(failed.error_code));
  if (observed && !quota && !preparation && observed.pre_execution_routing_timeout !== true && !interrupted) return null;
  const no = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
  const reserved = previous?.failed_turn_id === failed.turn_id;
  const priorAttempt = previous?.[attemptKey] ?? 0;
  if (!Number.isSafeInteger(priorAttempt) || Number(priorAttempt) < 0) return no;
  const attempt = Number(priorAttempt) + (reserved ? 0 : 1);
  const disconnected = current.status === "stale" && current.last_error_code === "transport_disconnected"
    && ["host_gate", "upstream_transport_failure"].includes(String(failed.error_code));
  if (attempt < 1 || attempt > 3 || typeof current.upstream_thread_id !== "string"
      || !current.upstream_thread_id || current.active_turn_id != null
      || (current.status !== "ready" && !disconnected)) return no;
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
    native_thread_ids: preparation ? [] : [sessionIdentity(current.upstream_thread_id, "current native thread")]};
  if (!observed) return candidate;
  if (preparation) {
    const events = observed.events;
    if (observed.kind !== (images ? "core_attachment_preparation_failure" : "core_context_preparation_failure")
        || observed.session_id !== failed.session_id || observed.turn_id !== failed.turn_id
        || !Array.isArray(events) || (events.length !== 4 && !(images && events.length === 3))) return candidate;
    const rows = events.map(value => requireJsonObject(value, "Core preparation event"));
    const queued = rows[0], terminal = rows.at(-1)!, context = rows.at(-2)!;
    const phase = rows.length === 4 ? rows[1] : null;
    const ids = events.map(value => (value as JsonObject).event_id);
    const verified = new Set(ids).size === rows.length && ids.every(value => typeof value === "string" && value.length > 0)
      && queued.kind === "turn.queued" && (phase === null || phase.kind === "agent.phase" && phase.phase === "manager_context")
      && context.kind === "manager.context" && context.schema_version === "manager_turn_context_v1"
      && terminal.kind === "turn.failed" && terminal.error_code === failed.error_code;
    return {...candidate, recovery_eligible: verified && recovery.source_verified === true
      && recovery.preparation_scope_available === true,
      preparation_observation: observed};
  }
  return {...candidate, recovery_eligible: recovery.source_verified === true
    && observed.thread_id === current.upstream_thread_id && observed.turn_id === failed.upstream_turn_id
    && (quota ? observed.codex_error_info === "usageLimitExceeded" && observed.only_original_user_item === true
      && observed.quota_available === true && typeof observed.quota_observed_at === "number"
      && now / 1000 >= observed.quota_observed_at && now / 1000 - observed.quota_observed_at <= 120
      : interrupted ? recovery.stop_observed === false && observed.only_original_user_item === true
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
