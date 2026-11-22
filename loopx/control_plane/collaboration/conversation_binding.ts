import type {JsonObject} from "../effect_program.ts";
import {isDeepStrictEqual} from "node:util";
import {createHash} from "node:crypto";
import {BARE_SHA256_PATTERN, ENVELOPED_SHA256_PATTERN} from "../content_digest.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {jsonObject, requireInteger, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {requireAutomationCadenceEligibility} from "../quota/automation_cadence.ts";
import {boundConversationExecutorMatches, normalizeProjectContext, normalizeStewardContext, normalizeStewardGoalScope} from "./conversation_scope.ts";
import {bindStewardManagedActor, normalizeStewardManagedActor} from "./steward_managed_mode.ts";

/** Core owns the context and audience grant. A provider supplies verified,
 * opaque identity observations.
 * Ordinary project bindings carry no Goal. A verified personal steward can
 * read the registered owner portfolio; legacy selected scopes stay bounded.
 */
const BINDING_SCHEMA = "loopx_chat_conversation_binding_v0";
const SET_SCHEMA = "loopx_chat_conversation_bindings_v0";
const SESSION_ID_PATTERN = /^[A-Za-z0-9._-]{1,160}$/;
const NO_RECOVERY: JsonObject = {operation: "admit_turn", recovery_candidate: false, recovery_eligible: false};
const RESPONSE_METADATA_KEYS = ["gate", "context_handoff", "protected_action", "goal_draft", "unfinished_work"] as const;
const RECOVERY_FAMILY_KEYS = ["quota_attempt", "routing_attempt", "preparation_attempt", "read_only_review", "metadata_review", "work_attempt"] as const;
const RECIPIENT_COMMANDS: readonly unknown[] = ["agents", "select_agent", "select_project"];
const STEWARD_COMMANDS: readonly unknown[] = ["commission", "confirm_commission", "cancel_commission", "stop_commission", "resume_commission"];
const VALID_REQUEST_COMMANDS: readonly unknown[] = [null, ...RECIPIENT_COMMANDS, ...STEWARD_COMMANDS, "status", "help", "new", "stop", "unsupported"];
const PENDING_ERROR_CODES: readonly unknown[] = [
  "usage_limit_exceeded", "response_timeout", "upstream_transport_failure", "context_window_exceeded",
  "antigravity_rpc_error", "antigravity_transport_error", "antigravity_step_error",
  "manager_authorization_unavailable", "runtime_error", "execution_worker_exited",
  "execution_worker_exited_before_dispatch", "server_restarted",
];

const isNonEmptyStr = (v: unknown): v is string => typeof v === "string" && v.length > 0;
const isNonBlankStr = (v: unknown): v is string => typeof v === "string" && v.trim().length > 0;
const isBareSha256 = (v: unknown): v is string => typeof v === "string" && BARE_SHA256_PATTERN.test(v);
const isSafeInt = (v: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): v is number =>
  Number.isSafeInteger(v) && Number(v) >= min && Number(v) <= max;
const allUnique = (items: readonly unknown[]): boolean => new Set(items).size === items.length;
const hasNoFields = (obj: JsonObject, keys: readonly string[]): boolean => keys.every(key => obj[key] == null);
const hasAnyField = (obj: JsonObject, keys: readonly string[]): boolean => keys.some(key => obj[key] != null);
const hasConflictingRecoveryKey = (obj: JsonObject, allowed: string): boolean =>
  RECOVERY_FAMILY_KEYS.some(key => key !== allowed && obj[key] != null);
const fieldsMatch = (left: JsonObject, right: JsonObject, keys: readonly string[]): boolean =>
  keys.every(key => left[key] === right[key]);
const isStewardEndpoint = (v: unknown): boolean => v === "antigravity" || v === "codex";
const boundChannelId = (steward: boolean, bindingId: unknown, sourceRef: unknown): string =>
  `${steward ? "manager.external.native" : "project.external"}.${bindingId}.${sourceRef}`;
const recoveryClientBase = (row: JsonObject, version: number): string =>
  `external-${ref(row.request_ref, "original request")}-recovery-${version}`;
const reviewClientId = (row: JsonObject, version: number): string => `${recoveryClientBase(row, version)}-review-1`;
const matchesReservation = (prev: JsonObject, version: number, turnId: unknown, client: string): boolean =>
  prev.version === version && prev.failed_turn_id === turnId && prev.client_turn_id === client;
const matchesOriginalTurn = (turn: JsonObject, row: JsonObject, requestRef = row.request_ref): boolean =>
  turn.client_turn_id === `external-${requestRef}` && turn.message === row.message && turn.origin === row.origin;
const isFreshEpochSeconds = (observedAt: unknown, nowSec: number): boolean =>
  typeof observedAt === "number" && Number.isFinite(observedAt) && nowSec >= observedAt && nowSec - observedAt <= 120;
const withinTurnWindow = (createdAt: unknown, turn: JsonObject): boolean => {
  const created = Date.parse(String(createdAt));
  return Number.isFinite(created) && created >= Date.parse(String(turn.started_at))
    && created <= Date.parse(String(turn.completed_at));
};
const validNativeAnchor = (anchor: unknown, threadId: unknown): anchor is JsonObject => {
  const obj = jsonObject(anchor);
  return obj !== null && obj.thread_id === threadId && isSafeInt(obj.start_step_index) && isBareSha256(obj.input_sha256);
};
const isClosedNativeSegment = (proof: JsonObject): boolean =>
  proof.latest_input_verified === false && isSafeInt(proof.end_step_index)
  && Number(proof.end_step_index) > Number(proof.start_step_index) + 1
  && Number(proof.end_step_index) < Number(proof.total_steps);
const matchesAttachedSession = (current: JsonObject | null, target: JsonObject): boolean =>
  current !== null && current.session_id === target.session_id && current.goal_id === target.goal_id
  && current.agent_id === target.agent_id && current.session_mode === "attached_host";
const matchesPrivateOwnerSource = (selected: JsonObject, row: JsonObject, source: JsonObject, context: JsonObject): boolean =>
  row.binding_id === selected.binding_id && source.private_human_message === true
  && source.sender_ref === selected.operator_ref && context.source_ref === source.source_ref
  && fieldsMatch(context, selected, ["binding_id", "provider_ref", "operator_ref", "project_ref"]);
const matchesNativeProofBase = (proof: JsonObject, current: JsonObject, row: JsonObject, failed: JsonObject): boolean =>
  proof.thread_id === current.upstream_thread_id && proof.request_ref === row.request_ref
  && isNonEmptyStr(proof.execution_id) && withinTurnWindow(proof.input_created_at, failed);
const codexErrorDetail = (info: unknown, allowedKeys: readonly string[]): unknown => {
  const obj = jsonObject(info);
  const entries = obj ? Object.entries(obj) : [];
  return entries.length === 1 && allowedKeys.includes(entries[0][0]) ? entries[0][1] : undefined;
};
const provenanceThreads = (history: unknown[], pred: (item: JsonObject) => boolean): string[] =>
  history.map(v => requireJsonObject(v, "native context provenance")).filter(pred).map(i => sessionIdentity(i.thread_id, "original native thread"));

function ensure(condition: unknown, message: string): asserts condition {
  if (!condition) throw new EffectRuntimeRequestError(message);
}

function matchId(value: unknown, label: string, pattern: RegExp): string {
  const result = requireNonEmptyString(value, label);
  ensure(pattern.test(result), `${label} is invalid`);
  return result;
}

const ref = (value: unknown, label: string): string => matchId(value, label, /^[a-f0-9]{24}$/);
const token = (value: unknown, label: string): string => matchId(value, label, /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/);
/** Mirror the canonical Chat store opaque-id boundary, including legacy Goals. */
const sessionIdentity = (value: unknown, label: string): string => matchId(value, label, SESSION_ID_PATTERN);

function automationCadenceStatus(value: unknown, recipient: JsonObject): JsonObject {
  const row = requireJsonObject(value, "automation cadence observation");
  ensure(row.goal_id === recipient.goal_id && row.agent_id === recipient.agent_id && row.automation_id === null,
    "automation cadence observation belongs to another Agent scope");
  const eligibility = requireAutomationCadenceEligibility(row.eligibility);
  const unavailable = eligibility.state === "unavailable";
  if (unavailable) {
    ensure(row.configuration_revision === null && row.min_interval_minutes === null,
      "unavailable automation cadence observation cannot claim policy values");
  } else {
    for (const [field, label] of [
      ["configuration_revision", "automation cadence configuration revision"],
      ["min_interval_minutes", "automation cadence minimum interval"],
    ] as const) {
      const number = requireInteger(row[field], label);
      ensure(Number.isSafeInteger(number) && number >= 0, `${label} must be a non-negative safe integer`);
    }
    ensure(Number(row.min_interval_minutes) <= 525600, "automation cadence minimum interval exceeds one year");
  }
  return {
    goal_id: recipient.goal_id, agent_id: recipient.agent_id, automation_id: null,
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
  ensure(granted && JSON.stringify(granted) === JSON.stringify(target), "the exact Agent target grant is unavailable");
  const context = normalizeProjectContext(params.context);
  ensure(selected.context_kind === "project" && context.audience === "bound_owner"
    && fieldsMatch(context, selected, ["binding_id", "project_ref", "provider_ref", "operator_ref"]),
    "Agent target belongs to another audience");
  validateAgentTargetObservation(target, params.target_observation, context.workspace_path, selected.binding_id);
  return {target, audience: {binding_id: selected.binding_id, source_ref: ref(context.source_ref, "Agent source"),
    provider_ref: selected.provider_ref, operator_ref: selected.operator_ref, project_ref: selected.project_ref}};
}

function validateAgentTargetObservation(target: JsonObject, value: unknown, workspace: unknown, bindingId: unknown): void {
  const observation = requireJsonObject(value, "attached Agent observation");
  const session = requireJsonObject(observation.session, "attached Session observation");
  const goal = requireJsonObject(observation.goal, "registered Agent Goal observation");
  ensure(session.external_conversation_binding_id == null || session.external_conversation_binding_id === bindingId,
    "the attached Session previously belonged to another App audience");
  ensure(observation.host_binding_verified === true && session.session_mode === "attached_host"
    && ["ready", "busy"].includes(String(session.status)) && observation.host_ref === target.host_ref
    && Array.isArray(observation.host_audience_binding_ids)
    && !observation.host_audience_binding_ids.some(owner => owner !== bindingId)
    && goal.workspace_path === workspace && goal.goal_id === target.goal_id
    && Array.isArray(goal.registered_agents) && goal.registered_agents.includes(target.agent_id),
    "Agent target is not an available registered host in this workspace");
  ensure(fieldsMatch(session, target, ["session_id", "goal_id", "agent_id", "executor_endpoint_id"]),
    "Agent Session identity changed");
  ensure((session.goal_instance_id ?? null) === target.goal_instance_id, "Agent Goal lifetime changed");
}

function binding(value: unknown): JsonObject {
  const row = requireJsonObject(value, "conversation binding");
  const group = row.audience === "group";
  ensure(row.audience === undefined || row.audience === "group", "unsupported conversation audience");
  ensure(!group || (row.context_kind === "project" && row.executor_endpoint_id === "codex"),
    "group Chat requires a native project executor");
  const groups = row.group_refs;
  ensure(group ? Array.isArray(groups) && groups.length > 0 && groups.length <= 16 : groups === undefined,
    "invalid group audience grant");
  const groupRefs = group ? [...new Set((groups as unknown[]).map(value => ref(value, "group reference")))].sort() : [];
  ensure(!group || groupRefs.length === (groups as unknown[]).length, "duplicate group audience grant");
  ensure(row.goal_scope === undefined || (row.context_kind === "steward"
    && ["all_registered", "selected"].includes(String(row.goal_scope))), "unsupported steward Goal scope");
  ensure(row.schema_version === BINDING_SCHEMA && ["project", "steward"].includes(String(row.context_kind))
    && (row.context_kind === "project" ? ["workspace_read", "workspace_write"].includes(String(row.grant)) : row.grant === "portfolio_read")
    && (row.grant !== "workspace_write" || row.executor_endpoint_id === "codex") && row.enabled === true,
    "unsupported conversation binding");
  const targets = row.agent_targets === undefined ? [] : row.agent_targets;
  ensure(Array.isArray(targets) && targets.length <= 16 && ((row.context_kind === "project" && !group) || targets.length === 0),
    "invalid Agent target grant set");
  const agents = targets.map(agentTarget);
  for (const field of ["target_ref", "session_id"]) ensure(allUnique(agents.map(row => row[field])), "ambiguous Agent target grant");
  // The prepared identity survives executor changes. Native execution still
  // requires the Codex binding at stewardManagedBasis, not at storage read.
  ensure(row.managed_actor === undefined || (row.context_kind === "steward" && !group), "managed identity requires a private steward");
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
  ensure(row.schema_version === SET_SCHEMA && isSafeInt(row.revision) && Array.isArray(row.bindings), "invalid conversation bindings state");
  const bindings = row.bindings.map(binding);
  for (const field of ["binding_id", "transport_ref", "provider_ref"]) {
    ensure(allUnique(bindings.map(row => row[field])), "conversation listener ownership is ambiguous");
  }
  const sessions = bindings.flatMap(row => (row.agent_targets as JsonObject[] | undefined ?? []).map(target => target.session_id));
  const hosts = new Map<unknown, unknown>();
  for (const row of bindings) for (const target of row.agent_targets as JsonObject[] | undefined ?? []) {
    ensure(!hosts.has(target.host_ref) || hosts.get(target.host_ref) === row.binding_id, "an attached host cannot share two App audiences");
    hosts.set(target.host_ref, row.binding_id);
  }
  ensure(allUnique(sessions), "an attached Session cannot share two App audiences");
  // This is an explicit local entry preference, not an audience grant. Retain a
  // removed identity so disconnect/replacement cannot silently select a peer.
  const preferred = row.app_steward_binding_id;
  return {schema_version: SET_SCHEMA, revision: Number(row.revision), bindings,
    ...(preferred !== undefined ? {app_steward_binding_id: preferred === null ? null : ref(preferred, "App steward binding")} : {})};
}

function observed(row: JsonObject, value: unknown): void {
  const observation = requireJsonObject(value, "provider identity observation");
  ensure(observation.verified === true && fieldsMatch(observation, row, ["transport_ref", "provider_ref", "operator_ref"]),
    "provider or operator identity is not independently verified");
}

export function planConversationBinding(params: JsonObject): JsonObject {
  const current = state(params.current);
  ensure(params.expected_revision === current.revision, "conversation binding revision changed");
  const unchanged = {changed: false, state: current};
  const findBinding = (id: string) => current.bindings.find(row => row.binding_id === id);
  const replaceBinding = (id: string, patch: JsonObject): JsonObject[] =>
    current.bindings.map(row => row.binding_id === id ? {...row, ...patch} : row);
  const commit = (bindings: JsonObject[]): JsonObject => ({changed: true, state: state({...current, revision: current.revision + 1, bindings})});
  if (params.operation === "select_app_steward") {
    const id = params.binding_id === null ? null : ref(params.binding_id, "App steward binding");
    if (id !== null) {
      const selected = findBinding(id);
      ensure(selected && selected.context_kind === "steward" && selected.audience !== "group", "select an existing private owner steward");
      observed(selected, params.observation);
      boundProject(params, selected);
    }
    return (current.app_steward_binding_id ?? null) === id ? unchanged
      : {changed: true, state: state({...current, revision: current.revision + 1, app_steward_binding_id: id})};
  }
  if (params.operation === "reconcile_steward_executor") {
    const id = ref(params.binding_id, "steward binding");
    const previous = findBinding(id);
    const session = requireJsonObject(params.session, "original steward Session");
    const allocation = requireJsonObject(session.manager_executor_allocation, "steward executor allocation");
    const defaults = requireJsonObject(params.machine_defaults, "steward machine defaults");
    const endpoint = defaults.executor_endpoint;
    ensure(previous && previous.context_kind === "steward" && previous.audience !== "group"
      && isStewardEndpoint(previous.executor_endpoint_id) && isStewardEndpoint(endpoint)
      && defaults.status === "ready" && defaults.source === "machine_configuration"
      && ["preferred", "pinned"].includes(String(defaults.selection_policy))
      && typeof defaults.configuration_revision === "string" && ENVELOPED_SHA256_PATTERN.test(defaults.configuration_revision)
      && allocation.schema_version === "manager_executor_allocation_v0" && allocation.configuration_revision === defaults.configuration_revision
      && allocation.selection_policy === defaults.selection_policy && allocation.available === true
      && allocation.configured_endpoint === endpoint && allocation.executor_endpoint === endpoint
      && allocation.model === session.agent_model_id && allocation.reasoning_effort === session.agent_reasoning_effort
      && (defaults.executor_model == null || defaults.executor_model === allocation.model)
      && (defaults.executor_reasoning_effort == null || defaults.executor_reasoning_effort === allocation.reasoning_effort)
      && session.agent_id === endpoint && session.executor_endpoint_id === endpoint
      && session.status === "ready" && session.active_turn_id === null && params.queue_empty === true,
      "reconciliation requires the idle original steward and its adopted machine executor");
    const saved = requireJsonObject(session.steward_context, "original steward context");
    const rows = replaceBinding(id, {executor_endpoint_id: endpoint});
    resolveBoundConversation({current: {...current, bindings: rows}, binding_id: id, observation: params.observation,
      available_projects: params.available_projects, available_goal_ids: params.available_goal_ids, source_ref: saved.source_ref,
      sender_ref: previous.operator_ref, private_human_message: false, origin: "web", session_context: saved, session});
    return previous.executor_endpoint_id === endpoint ? unchanged : commit(rows);
  }
  if (params.operation === "configure") {
    const candidate = binding(params.binding);
    observed(candidate, params.observation);
    ensure(candidate.audience !== "group" || (Array.isArray(params.available_group_refs)
      && (candidate.group_refs as string[]).every(group => (params.available_group_refs as unknown[]).includes(group))),
      "selected groups are not independently verified under this App");
    const project = Array.isArray(params.available_projects)
      ? params.available_projects.map(normalizeProjectContext).find(row => row.project_ref === candidate.project_ref) : undefined;
    ensure(project && (candidate.grant !== "workspace_write" || project.grant === "workspace_write"), "workspace grant is unavailable");
    const previous = current.bindings.find(row => row.transport_ref === candidate.transport_ref);
    // An exact repeat is idempotent. A changed context receives a new binding
    // identity, so an existing Session can never silently move workspaces.
    if (previous && JSON.stringify({...previous, binding_id: null}) === JSON.stringify({...candidate, binding_id: null})) return unchanged;
    const scopeOnly = previous?.context_kind === "steward" && candidate.context_kind === "steward"
      && JSON.stringify({...previous, goal_scope: null}) === JSON.stringify({...candidate, goal_scope: null});
    ensure(previous?.binding_id !== candidate.binding_id || scopeOnly, "changed context requires a new binding identity");
    return commit([...current.bindings.filter(row => row.transport_ref !== candidate.transport_ref), candidate]);
  }
  if (params.operation === "grant_agent_target" || params.operation === "revoke_agent_target") {
    const id = ref(params.binding_id, "binding identity");
    const previous = findBinding(id);
    ensure(previous && previous.context_kind === "project" && previous.audience !== "group", "Agent selection requires a private project binding");
    observed(previous, params.observation);
    const existing = previous.agent_targets as JsonObject[] | undefined ?? [];
    if (params.operation === "revoke_agent_target") {
      return commit(replaceBinding(id, {agent_targets: existing.filter(row => row.target_ref !== ref(params.target_ref, "Agent target reference"))}));
    }
    const target = agentTarget(params.target);
    ensure(Array.isArray(params.available_projects), "workspace grants unavailable");
    const project = params.available_projects.map(normalizeProjectContext).find(row => row.project_ref === previous.project_ref);
    ensure(project, "workspace grant unavailable");
    validateAgentTargetObservation(target, params.target_observation, project.workspace_path, previous.binding_id);
    ensure(!existing.some(row => row.session_id === target.session_id), "this Agent Session is already granted");
    return commit(replaceBinding(id, {agent_targets: [...existing, target]}));
  }
  if (params.operation === "adopt_created_goal") {
    const id = ref(params.binding_id, "binding identity");
    const previous = findBinding(id);
    const context = normalizeStewardContext(params.context);
    const proposal = requireJsonObject(params.proposal, "creation proposal");
    const principal = requireJsonObject(proposal.context, "creation audience");
    const receipt = requireJsonObject(proposal.receipt, "creation receipt");
    const resources = requireJsonObject(receipt.resource_ids, "creation resources");
    const goal = requireJsonObject(params.goal, "created Goal observation");
    ensure(previous && previous.context_kind === "steward" && previous.project_ref === context.project_ref
      && proposal.action_kind === "goal.create" && proposal.status === "applied"
      && receipt.outcome === "goal_created" && receipt.projection_verified === true
      && goal.goal_id === resources.goal_id && goal.creation_operation_id === proposal.proposal_id
      && goal.workspace_path === context.workspace_path, "steward adoption requires its exact creation receipt");
    ensure(fieldsMatch(principal, context, ["binding_id", "source_ref", "provider_ref", "operator_ref"])
      && fieldsMatch(previous, context, ["binding_id", "provider_ref", "operator_ref"]), "creation receipt belongs to another audience");
    const goals = normalizeStewardGoalScope([...(previous.goal_ids as string[]), goal.goal_id]);
    return JSON.stringify(goals) === JSON.stringify(previous.goal_ids) ? unchanged : commit(replaceBinding(id, {goal_ids: goals}));
  }
  if (params.operation === "bind_managed_actor") {
    const id = ref(params.binding_id, "binding identity");
    const previous = findBinding(id);
    ensure(previous, "the original steward binding is unavailable");
    const actor = bindStewardManagedActor({...params, binding: previous});
    if (JSON.stringify(actor) === JSON.stringify(previous.managed_actor)) return unchanged;
    ensure(previous.managed_actor === undefined, "retain the original managed steward identity");
    return commit(replaceBinding(id, {managed_actor: actor}));
  }
  if (params.operation === "disconnect") {
    const id = ref(params.binding_id, "binding identity");
    const rows = current.bindings.filter(row => row.binding_id !== id);
    return rows.length === current.bindings.length ? unchanged : commit(rows);
  }
  throw new EffectRuntimeRequestError("unsupported conversation binding operation");
}

function boundProject(params: JsonObject, row: JsonObject): Record<string, string> {
  ensure(Array.isArray(params.available_projects), "workspace grants unavailable");
  const projects = params.available_projects.map(normalizeProjectContext).filter(project => project.project_ref === row.project_ref);
  ensure(projects.length === 1, "workspace grant is unavailable or ambiguous");
  ensure(row.grant !== "workspace_write" || projects[0].grant === "workspace_write",
    "workspace write grant is no longer available");
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
  ensure(row, "conversation binding is no longer authorized");
  observed(row, params.observation);
  const steward = row.context_kind === "steward";
  const source = ref(params.source_ref, "source conversation identity");
  const group = row.audience === "group";
  // The local operator may continue an existing private Session. This is a
  // distinct host-observed origin, never a fabricated provider human event.
  const local = params.origin === "web";
  ensure(params.origin === undefined || local, "unsupported bound input origin");
  ensure(!local || (!group && params.session_context !== undefined && params.private_human_message === false),
    "local input requires an existing private bound Session");
  let topic: JsonObject = {};
  if (group) {
    const groupRef = ref(params.group_ref, "group reference"), topicRef = ref(params.topic_ref, "topic reference");
    ref(params.sender_ref, "group sender identity");
    ensure(params.group_human_message === true && params.private_human_message === false && topicRef === source
      && (row.group_refs as string[]).includes(groupRef), "message audience is outside the binding grant");
    topic = {group_ref: groupRef, topic_ref: topicRef, filesystem_scope: "workspace_only"};
  } else {
    ensure(params.sender_ref === row.operator_ref && params.private_human_message === !local
      && params.group_ref === undefined && params.topic_ref === undefined && params.group_human_message !== true,
      "message audience is outside the binding grant");
  }
  const project = boundProject(params, row);
  const goals = steward && row.goal_scope === "all_registered"
    ? normalizeStewardGoalScope(params.available_goal_ids) : row.goal_ids;
  const rawContext = {...project, ...topic, audience: group ? "bound_group" : "bound_owner", binding_id: id, source_ref: source,
    provider_ref: row.provider_ref, operator_ref: row.operator_ref, grant: row.grant,
    ...(steward ? {kind: "bound_steward", grant: "portfolio_read", goal_ids: goals} : {})};
  const context = group ? normalizeProjectContext(rawContext) : rawContext;
  if (params.session_context !== undefined) {
    const saved = requireJsonObject(params.session_context, "bound Session context");
    // New commissions may extend the same owner's scope. Workspace, role and
    // audience remain frozen; all evidence reads use the fresh binding scope.
    // JSON object member order is not identity. Preserve every value and field
    // check while accepting equivalent contexts from another JSON writer.
    const matches = steward ? matchesStewardContext(saved, context, row) : isDeepStrictEqual(saved, context);
    ensure(matches, "bound Session context changed");
  }
  const channel = boundChannelId(steward, id, source);
  if (params.session !== undefined) {
    const session = requireJsonObject(params.session, "canonical bound Session");
    sessionIdentity(session.session_id, "bound Session identity");
    ensure(session.status !== "closed" && session.channel_id === channel && session.goal_id === (steward ? "loopx-manager" : null)
      && boundConversationExecutorMatches(row, session) && session.session_mode === "managed_runtime"
      && (steward ? session.project_context : session.steward_context) == null && params.session_context !== undefined
      && isDeepStrictEqual(steward ? session.steward_context : session.project_context, params.session_context),
      "canonical bound Session identity changed");
  }
  // This is an audience proof, not a host-tool grant. Only the independently
  // verified owner in this private source may inherit an explicit machine
  // owner grant. Portfolio scope and project write grants do not imply it.
  return {binding: row, context, owner_manager_audience: steward && project.filesystem_scope !== "workspace_only",
    channel_id: channel};
}

/** Discover existing private targets from canonical metadata. This is a read
 * projection: the next input still needs fresh source and execution admission.
 */
export function listLocalBoundSessions(params: JsonObject): JsonObject {
  const current = state(params.current);
  const id = ref(params.binding_id, "binding identity");
  const row = current.bindings.find(item => item.binding_id === id);
  ensure(row, "conversation binding is no longer authorized");
  const steward = row.context_kind === "steward";
  ensure(params.app_steward_entry !== true
    || (current.app_steward_binding_id === id && steward && row.audience !== "group"),
    "the selected original steward is unavailable");
  observed(row, params.observation);
  ensure(row.audience !== "group", "local discovery requires a private binding");
  boundProject(params, row);
  ensure(Array.isArray(params.sessions), "canonical Session observations unavailable");
  const ids: string[] = [];
  for (const value of params.sessions) {
    const session = requireJsonObject(value, "canonical Session");
    if (![session.project_context, session.steward_context].some(v => jsonObject(v)?.binding_id === id)
        || session.status === "closed") continue;
    const saved = requireJsonObject(steward ? session.steward_context : session.project_context, "bound Session context");
    resolveBoundConversation({...params, source_ref: saved.source_ref, session,
      sender_ref: row.operator_ref, private_human_message: false, origin: "web", session_context: saved});
    ids.push(sessionIdentity(session.session_id, "bound Session identity"));
  }
  ensure(allUnique(ids), "canonical bound Session identity is ambiguous");
  return {session_ids: ids};
}

/** Shared native command projection. Provider grammar carries the explicit
 * command; Core binds its exact target before IO. Replays retain that target.
 */
export function planBoundConversationRequest(params: JsonObject): JsonObject {
  if (params.pending_observations !== undefined) {
    const observations = params.pending_observations;
    ensure(Object.keys(params).length === 1 && Array.isArray(observations) && observations.length <= 64,
      "select at most 64 read-only pending observations");
    return {pending_flags: observations.map(value => {
      const item = requireJsonObject(value, "pending observation input");
      ensure(!Object.keys(item).some(key => !["request", "current_session", "pending_observation"].includes(key))
        && item.pending_observation !== undefined, "pending batches cannot admit or recover work");
      return planBoundConversationRequest(item).pending;
    })};
  }
  const row = requireJsonObject(params.request, "external request");
  const request = ref(row.request_ref, "external request identity");
  const command = row.command;
  const imageCount = params.attachment_count ?? 0;
  ensure(isSafeInt(imageCount, 0, 4), "invalid external image attachment count");
  if (params.history_answer_review !== undefined) {
    const observed = requireJsonObject(params.history_answer_review, "historical answer observation");
    const binding = requireJsonObject(params.binding, "conversation binding");
    const current = params.current_session == null ? null : requireJsonObject(params.current_session, "current Session");
    const source = requireJsonObject(row.source, "original owner source");
    const context = jsonObject(current?.steward_context);
    const identityAvailable = binding.context_kind === "steward" && binding.grant === "portfolio_read"
      && binding.enabled === true && (binding.audience ?? "owner") === "owner"
      && row.binding_id === binding.binding_id && source.sender_ref === binding.operator_ref
      && source.private_human_message === true && context !== null
      && fieldsMatch(context, binding, ["binding_id", "provider_ref", "operator_ref"])
      && context.source_ref === source.source_ref && current?.agent_id === "codex";
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
      && isSafeInt(originalReview.version, 1) && Number.isSafeInteger(correctionVersion)
      && originalReview.failed_turn_id == null && originalReview.native_observation == null
      && row.session_id === current?.session_id
      && previous?.status === "completed" && previous.session_id === current?.session_id
      && isNonEmptyStr(previous.upstream_turn_id) && previous.turn_id === row.turn_id && previous.message === row.message
      && previous.client_turn_id === originalReview.client_turn_id
      && originalReview.client_turn_id === reviewClientId(row, Number(originalReview.version))
      && response?.gate != null && Array.isArray(response.proposals) && response.proposals.length === 0
      && !RESPONSE_METADATA_KEYS.slice(1).some(key => response[key])
      && (reserved === originalReview || reserved?.context_version === 1
        && reserved.context_correction === true && reserved.previous_answer_turn_id === previous.turn_id
        && reserved.version === correctionVersion && reserved.client_turn_id === reviewClientId(row, correctionVersion));
    if (observed.discovery_only === true) return {pending: correction && identityAvailable && row.command === null
      && (reserved?.context_correction === true || current?.status === "ready" && current.active_turn_id == null)};
    const created = Date.parse(String(observed.source_created_at)), now = Date.parse(String(observed.observed_at));
    const sessionCreated = Date.parse(String(current?.created_at));
    const holdReason = observed.stop_observed === true ? "source_stopped"
      : observed.adapter_replaceable !== true ? "owned_background_unavailable"
      : observed.queue_empty !== true || current?.active_turn_id != null ? "session_busy"
      : current?.agent_id !== "codex" ? "current_executor_unavailable"
      : created < sessionCreated ? "source_predates_session" : null;
    if (holdReason || row.command !== null || (row.status !== "prepared" && !correction) || Number(imageCount) !== 0
        || !identityAvailable || !current || current.status !== "ready" || current.steward_context == null
        || observed.source_verified !== true || observed.stop_observed !== false || !isSafeInt(observed.version, 1)
        || !Number.isFinite(created) || !Number.isFinite(now) || !Number.isFinite(sessionCreated)
        || created > now - 5_000 || !isNonBlankStr(row.message) || row.message.trim().startsWith("/")) {
      return {operation: "hold_history_answer", reason: holdReason ?? "source_scope_unavailable"};
    }
    // This creates a new, tools-restricted answer, never an execution of the
    // historical request or a fabricated failed native Turn.
    const nextVersion = correction ? correctionVersion : Number(observed.version);
    return {operation: "admit_history_answer", session_id: current.session_id,
      context_correction: correction, context_version: 1, version: nextVersion,
      ...(correction ? {previous_answer_turn_id: previous!.turn_id} : {}),
      // Preserve the existing read-only suffix for older answer-capable
      // readers. Advancing this request's reserved version is not a global
      // manager-context upgrade and cannot turn the review into business.
      client_turn_id: reviewClientId(row, nextVersion)};
  }
  if (!VALID_REQUEST_COMMANDS.includes(command)) throw new EffectRuntimeRequestError("unsupported external conversation command");
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
    const response = jsonObject(turn?.response);
    const reservation = jsonObject(row.context_recovery);
    const gate = jsonObject(response?.gate);
    const validGate = gate !== null && typeof gate.kind === "string" && typeof gate.summary === "string";
    const sameClient = reservation !== null && turn?.client_turn_id === reservation.client_turn_id;
    const continuationGate = sameClient && isSafeInt(reservation.quota_attempt ?? reservation.preparation_attempt, 1, 3)
      && typeof reservation.failed_turn_id === "string" && reservation.failed_turn_id !== row.turn_id
      && isNonEmptyStr(reservation.client_turn_id) && validGate;
    const invalidMetadata = validGate && gate.kind === "response_metadata_invalid";
    const unfinished = turn?.status === "completed" && response !== null
      && (unfinishedProjectWork(response) !== null || continuationGate || invalidMetadata);
    const metadataReceipts = response?.context_handoff_receipts;
    const metadataOwed = sameClient && reservation?.metadata_review === true && (turn?.status !== "completed"
      || !Array.isArray(metadataReceipts) || !Array.isArray(reservation.metadata_handoffs)
      || metadataReceipts.length !== reservation.metadata_handoffs.length
      || !Array.isArray(response?.context_handoff_failures) || response.context_handoff_failures.length > 0);
    const answerReviewOwed = sameClient && reservation?.answer_review === true
      && (turn?.status !== "completed" || response?.gate != null);
    // A restricted result review can supply an answer without settling the
    // original business. Retain the exact declaration independently of whatever
    // the reviewer says; neither a delivered review nor its failure is replay authority.
    const originalResult = jsonObject(reservation?.original_turn);
    const originalResponse = jsonObject(originalResult?.response);
    const retainedWork = sameClient && reservation.answer_review !== true
      && (reservation.read_only_review === true || reservation.work_attempt === 1
        && typeof reservation.completed_review_turn_id === "string" && !pureResultText(response))
      && originalResult?.status === "completed" && originalResult.session_id === row.session_id
      && originalResult.turn_id === reservation.failed_turn_id && originalResult.turn_id !== row.turn_id
      && originalResponse !== null && unfinishedProjectWork(originalResponse) !== null
      && isDeepStrictEqual(originalResponse.unfinished_work, reservation.unfinished_work);
    const recovered = row.native_result_recovery == null ? null
      : requireJsonObject(row.native_result_recovery, "recovered native result");
    const result = (recovered?.native_observation as JsonObject | undefined)?.response as JsonObject | undefined;
    const recoveredMatch = recovered !== null && recovered.turn_id === row.turn_id
      && recovered.message_id === `native.${row.turn_id}.recovered`
      && isNonEmptyStr(recovered.message_text) && result != null;
    const returned = recoveredMatch && recovered.delivery_verified === true
      && !RESPONSE_METADATA_KEYS.some(key => result[key]) && Array.isArray(result.proposals) && result.proposals.length === 0;
    const expandedResult = recoveredMatch && turn?.status === "completed" && response !== null && extendsOriginalText(response, result);
    return {pending: !returned && row.command === null && row.status === "accepted"
      && turn?.session_id === row.session_id && turn?.turn_id === row.turn_id
      && (expandedResult || metadataOwed || answerReviewOwed || retainedWork || unfinished
        || turn?.status === "completed" && response?.message === "" && pureResultText(response, true)
        || turn?.status === "failed" && PENDING_ERROR_CODES.includes(turn.error_code))
      && !(current?.session_id === row.session_id && current?.status === "closed")
      && observation.stop_observed !== true};
  }
  if (params.context_recovery !== undefined) return planContextRecovery(params, row, current);
  const target = row.target_recorded === true ? row : current;
  const session = target?.session_id ?? null;
  const turn = row.target_recorded === true ? row.turn_id ?? null : current?.active_turn_id ?? null;
  const reply = (response_code: string, sessionId = session, extra: JsonObject = {}): JsonObject =>
    ({operation: "reply", session_id: sessionId, turn_id: null, response_code, ...extra});
  if (Number(imageCount) > 0 && (command !== null || params.agent_target != null)) {
    // Preserve the existing attached-host capability boundary and never drop
    // images while executing a control command or handing off to that host.
    return reply("unsupported_attachment");
  }
  if (RECIPIENT_COMMANDS.includes(command)) {
    return requireJsonObject(params.binding, "conversation binding").audience === "group"
      ? reply("group_recipient_unavailable") : {operation: "select_recipient", session_id: null, turn_id: null};
  }
  if (params.agent_target != null) {
    ensure(matchesAttachedSession(current, agentTarget(params.agent_target)), "the selected Agent Session is unavailable");
    if (command === "new" || command === "stop") return reply("attached_control_unavailable", current!.session_id);
  }
  if (STEWARD_COMMANDS.includes(command)) {
    ensure(requireJsonObject(params.binding, "steward binding").context_kind === "steward",
      "explicit commissions require a selected steward");
    return {operation: "steward_action", session_id: session, turn_id: null};
  }
  if (command === "status" || command === "help") {
    return reply(command === "help" ? "conversation_help" : !current ? "no_session"
      : current.active_turn_id ? "active_session" : "ready_session", session,
      {status_snapshot: boundConversationStatus(params, current)});
  }
  if (command === "unsupported") return reply("unsupported_attachment");
  if (command === "stop" || command === "new") {
    // Older prepared journals may predate freezing before inventory. Their
    // original scope is unknown; current work cannot reconstruct that intent.
    ensure(!(command === "stop" && params.recovering_request === true && row.target_recorded !== true),
      "the original stop scope was not recorded");
    const rawBackground = command === "stop"
      ? (row.target_recorded === true ? row.background_turn_ids : params.background_turn_ids) ?? [] : [];
    ensure(Array.isArray(rawBackground), "invalid background Turn observation");
    const background = rawBackground.map(value => sessionIdentity(value, "completed background Turn"));
    ensure(allUnique(background) && (!background.length || (session && !params.agent_target)),
      "background Turns require the original ordinary Session");
    return {operation: command, session_id: session, turn_id: turn,
      ...(background.length ? {background_turn_ids: background} : {}),
      response_code: command === "new" ? "new_session" : turn || background.length ? "stop_requested" : "no_active_turn"};
  }
  return planStewardBindingAdoptionHold(params, row)
    ?? {operation: "admit_turn", client_turn_id: `external-${request}`, session_id: session, turn_id: null};
}

function planStewardBindingAdoptionHold(params: JsonObject, request: JsonObject): JsonObject | null {
  if (params.steward_adoption_session == null || request.status !== "prepared"
      || request.command !== null || params.agent_target != null) return null;
  const original = requireJsonObject(params.steward_adoption_session, "original steward Session");
  const row = binding(params.binding);
  const allocation = original.manager_executor_allocation as JsonObject | undefined;
  const endpoint = original.executor_endpoint_id;
  if (row.context_kind !== "steward" || row.audience === "group"
      || !isStewardEndpoint(row.executor_endpoint_id) || !isStewardEndpoint(endpoint)
      || original.agent_id !== endpoint || endpoint === row.executor_endpoint_id
      || !["preferred", "pinned"].includes(String(allocation?.selection_policy))
      || !["ready", "busy", "stale", "resuming"].includes(String(original.status))) return null;
  const source = requireJsonObject(request.source, "original owner source");
  const context = requireJsonObject(params.context, "verified steward context");
  const saved = requireJsonObject(original.steward_context, "saved steward context");
  ensure(matchesPrivateOwnerSource(row, request, source, context)
    && context.audience === "bound_owner" && context.grant === "portfolio_read"
    && original.goal_id === "loopx-manager" && original.session_mode === "managed_runtime"
    && original.project_context == null && original.channel_id === boundChannelId(true, row.binding_id, source.source_ref)
    && matchesStewardContext(saved, context, row), "the original steward Session belongs to another source");
  return {operation: "hold_steward_binding", session_id: sessionIdentity(original.session_id, "original steward Session")};
}

function pureResultText(response: JsonObject | null, allowEmpty = false): boolean {
  return response?.schema_version === "loopx_chat_agent_response_v0"
    && typeof response.message === "string" && (allowEmpty || response.message.length > 0)
    && Array.isArray(response.proposals) && response.proposals.length === 0
    && hasNoFields(response, RESPONSE_METADATA_KEYS);
}

function unsubmittedMetadata(response: JsonObject | null): boolean {
  const gate = response?.gate as JsonObject | undefined;
  return response?.schema_version === "loopx_chat_agent_response_v0"
    && isNonEmptyStr(response.message)
    && gate?.kind === "response_metadata_invalid" && typeof gate.summary === "string"
    && Array.isArray(response.proposals) && response.proposals.length === 0
    && hasNoFields(response, RESPONSE_METADATA_KEYS.slice(1));
}

function extendsOriginalText(original: JsonObject, result: JsonObject): boolean {
  const metadata = (value: JsonObject) => Object.fromEntries(Object.entries(value)
    .filter(([key, value]) => key !== "message" && value != null));
  return pureResultText(original, true) && pureResultText(result)
    && (result.message as string).length > (original.message as string).length
    && (result.message as string).startsWith(original.message as string)
    && isDeepStrictEqual(metadata(original), metadata(result));
}

function admittedRecoveryResult(
  recovery: JsonObject, row: JsonObject, current: JsonObject, failedTurnId: unknown,
  client: string, base: JsonObject, label: string,
  options: {turnIdLabel?: string; origin?: unknown; allowImages?: boolean; extraValid?: boolean; includeNativeThreads?: boolean} = {},
): JsonObject {
  const admitted = requireJsonObject(recovery.admitted_turn, label);
  const expectedOrigin = options.origin !== undefined ? options.origin : row.origin;
  if (options.extraValid === false || admitted.client_turn_id !== client
      || admitted.session_id !== current.session_id || admitted.turn_id === failedTurnId
      || admitted.message !== row.message || admitted.origin !== expectedOrigin) return NO_RECOVERY;
  const validAttachments = options.allowImages
    ? samePreparationImages((recovery.image_attachments as JsonObject).request,
        (recovery.image_attachments as JsonObject).admitted)
    : Array.isArray(admitted.attachments ?? []) && !(admitted.attachments as unknown[] | undefined)?.length;
  if (!validAttachments) return NO_RECOVERY;
  return {...base, recovery_candidate: true, recovery_eligible: recovery.source_verified === true,
    client_turn_id: client, session_id: current.session_id,
    admitted_turn_id: sessionIdentity(admitted.turn_id, options.turnIdLabel ?? label),
    ...(options.includeNativeThreads !== false ? {native_thread_ids: []} : {})};
}

function parseCorePreparationEvents(rawEvents: unknown[], failed: JsonObject, label: string): JsonObject[] | null {
  const events = rawEvents.map(value => requireJsonObject(value, label));
  const ids = events.map(item => item.event_id);
  const terminal = events.at(-1)!;
  if (!ids.every(isNonEmptyStr) || !allUnique(ids) || events[0].kind !== "turn.queued"
      || terminal.kind !== "turn.failed" || terminal.error_code !== failed.error_code
      || events.slice(1, -1).some(item => item.kind !== "manager.context"
        && !(item.kind === "agent.phase" && item.phase === "manager_context"))) return null;
  return events;
}

function cooldownNotBefore(
  recovery: JsonObject, current: JsonObject, endedAt: unknown,
  opts: {backoffMs?: number; requireQueueEmpty?: boolean; requireNoStop?: boolean; laterInput?: "none" | "not_true"} = {},
): string | null {
  if (current.status !== "ready" || current.active_turn_id != null) return null;
  if (opts.requireQueueEmpty !== false && recovery.queued_input_present !== false) return null;
  if (opts.requireNoStop && recovery.stop_observed !== false) return null;
  if (opts.laterInput === "none" && recovery.later_equivalent_input_present !== false) return null;
  if (opts.laterInput === "not_true" && recovery.later_equivalent_input_present === true) return null;
  const now = Date.parse(String(recovery.observed_at));
  const notBefore = Date.parse(String(endedAt)) + (opts.backoffMs ?? 60_000);
  return Number.isFinite(now) && Number.isFinite(notBefore) && now >= notBefore
    ? new Date(notBefore).toISOString() : null;
}

function matchesFailedTurnBase(
  recovery: JsonObject, row: JsonObject, current: JsonObject, failed: JsonObject,
  opts: {sameInput?: boolean; sameOrigin?: boolean; zeroDelta?: boolean; requireUpstream?: boolean | "null"; noAttachments?: boolean; noStop?: boolean} = {},
): boolean {
  if (failed.session_id !== current.session_id || failed.turn_id !== row.turn_id
      || failed.status !== "failed" || failed.response !== null || recovery.steering_observed !== false) return false;
  if (opts.sameInput && failed.message !== row.message) return false;
  if (opts.sameOrigin && failed.origin !== row.origin) return false;
  if (opts.zeroDelta && failed.delta_count !== 0) return false;
  if (opts.noAttachments && recovery.attachments_present !== false) return false;
  if (opts.noStop && recovery.stop_observed !== false) return false;
  if (opts.requireUpstream === true && !isNonEmptyStr(failed.upstream_turn_id)) return false;
  if (opts.requireUpstream === "null" && failed.upstream_turn_id !== null) return false;
  return true;
}

function stewardAnswerWithCapacity(recovery: JsonObject, row: JsonObject, current: JsonObject, candidate: JsonObject): JsonObject {
  const capacity = nativeRecoveryCapacity(recovery, row, current, candidate);
  return {...capacity, recovery_eligible: capacity.recovery_eligible === true && capacity.answer_executor === "codex"};
}

function reviewCandidate(current: JsonObject, failed: JsonObject, client: string, nativeThreads: unknown[], extra: JsonObject = {}): JsonObject {
  return {...NO_RECOVERY, recovery_candidate: true, read_only_review: true, ...extra,
    client_turn_id: client, session_id: current.session_id, failed_turn_id: failed.turn_id, native_thread_ids: nativeThreads};
}

function matchesRetainedWorkReview(
  reservation: JsonObject, original: JsonObject, reviewed: JsonObject, current: JsonObject,
  row: JsonObject, selected: JsonObject, work: JsonObject, originalRequestRef: unknown,
): boolean {
  return reservation.read_only_review === true && reservation.answer_review !== true
    && reservation.history_review !== true && reservation.work_attempt == null
    && isDeepStrictEqual(reservation.binding_snapshot, selected)
    && isDeepStrictEqual(reservation.unfinished_work, work)
    && original.status === "completed" && original.session_id === current.session_id
    && original.turn_id === reservation.failed_turn_id
    && matchesOriginalTurn(original, row, originalRequestRef)
    && reviewed.status === "completed" && reviewed.session_id === current.session_id
    && reviewed.client_turn_id === reservation.client_turn_id
    && reviewed.message === row.message && reviewed.origin === row.origin
    && pureResultText(jsonObject(reviewed.response));
}

/** A new, correlated continuation after an observed pre-execution failure.
 * A delivered failure notice is not a completed business request. This bounded
 * migration path never reopens the failed Turn or retries unknown effects.
 */
function planContextRecovery(params: JsonObject, row: JsonObject, current: JsonObject | null): JsonObject {
  const recovery = requireJsonObject(params.context_recovery, "context recovery observation");
  const version = requireInteger(recovery.version, "context representation version");
  if (version < 1) throw new EffectRuntimeRequestError("invalid context representation version");
  const pendingTurn = recovery.failed_turn as JsonObject | undefined;
  const pendingResponse = pendingTurn?.response as JsonObject | undefined;
  // A committed empty answer or a pre-dispatch preparation failure whose
  // notice receipt is ambiguous has no delivered failure to acknowledge first.
  const pendingEmpty = current?.agent_id === "antigravity" && pendingTurn?.status === "completed"
    && pendingResponse?.message === "" && pureResultText(pendingResponse, true);
  const pendingPreparationNotice = recovery.failure_notice_receipt_unknown === true
    && pendingTurn?.status === "failed" && pendingTurn?.upstream_turn_id === null
    && pendingTurn?.delta_count === 0 && pendingTurn?.response === null;
  if (!current || row.command !== null || row.status !== "accepted"
      || (row.delivery_verified !== true && !pendingEmpty && !pendingPreparationNotice)
      || row.agent_target != null || current.session_id !== row.session_id
      || !isStewardEndpoint(current.agent_id)
      || current.upstream_mode !== (current.agent_id === "antigravity" ? "default" : "chat") || current.status === "closed"
      || current.session_mode === "attached_host") return NO_RECOVERY;
  const selected = binding(params.binding);
  if (selected.audience === "group") return NO_RECOVERY;
  const steward = selected.context_kind === "steward";
  if (!boundConversationExecutorMatches(selected, {...current, executor_endpoint_id: current.executor_endpoint_id ?? current.agent_id}) || current.goal_id !== (steward ? "loopx-manager" : null)) return NO_RECOVERY;
  const saved = steward ? normalizeStewardContext(current.steward_context)
    : normalizeProjectContext(current.project_context);
  const source = requireJsonObject(row.source, "original conversation source");
  if (!matchesPrivateOwnerSource(selected, row, source, saved)
      || (!steward && saved.grant !== selected.grant)
      || current.channel_id !== boundChannelId(steward, selected.binding_id, saved.source_ref)) return NO_RECOVERY;
  const failed = requireJsonObject(recovery.failed_turn, "original failed Turn");
  if (recovery.retained_result_review != null) {
    return (row.context_recovery as JsonObject | undefined)?.legacy_result_review === true
      ? planRelatedResultReview(recovery, row, current, failed, row.context_recovery as JsonObject, selected, version)
      : planWorkAfterResultReview(recovery, row, current, failed, selected, version);
  }
  const runtimePreparation = planRuntimePreparationRecovery(recovery, row, current, failed, version);
  if (runtimePreparation !== null) return runtimePreparation;
  const preparation = steward ? planPreparationRecovery(recovery, row, current, failed, version) : null;
  if (preparation !== null) return preparation;
  // Post-dispatch and read-only recovery families always require a verified prior delivery.
  if (row.delivery_verified !== true && !pendingEmpty) return NO_RECOVERY;
  const hasPriorHistory = (label: string, pred: (item: JsonObject) => boolean): boolean =>
    Array.isArray(row.context_recovery_history) && row.context_recovery_history.some(v => pred(requireJsonObject(v, label)));
  // Once preparation has dispatched, a different terminal category must not
  // switch recovery families and start a fresh business retry budget.
  if ((row.context_recovery != null && requireJsonObject(row.context_recovery, "preparation reservation").preparation_attempt != null)
      || hasPriorHistory("prior preparation recovery", item => item.preparation_attempt != null)) return NO_RECOVERY;
  if (current.agent_id === "antigravity") {
    // Parsing/presentation can fail after native work. This route admits
    // only a restricted review of independently read original evidence;
    // it never retries the failed business Turn or adopts an unverified final.
    if (failed.error_code === "runtime_error") return planInterruptedStewardAnswer(recovery, row, current, failed, version);
    if (failed.error_code === "antigravity_step_error") {
      const quota = planNativeQuotaRecovery(recovery, row, current, failed, version);
      return quota.recovery_candidate === true && (recovery.native_observation as JsonObject | null | undefined)?.read_only_review !== true
        ? quota : planInterruptedStewardAnswer(recovery, row, current, failed, version);
    }
    const originalResponse = jsonObject(failed.response);
    const anchorValid = validNativeAnchor(recovery.native_input, current.upstream_thread_id);
    const anchor = anchorValid ? recovery.native_input as JsonObject : null;
    // The lexical hint only bounds read-only probes. It confers no result,
    // source or execution authority; an anchored native final is still needed.
    const completedText = failed.status === "completed" && pureResultText(originalResponse, true)
      && (recovery.response_literal_open === true || originalResponse!.message === "")
      && recovery.queued_input_present === false && anchorValid;
    const completedMetadata = failed.status === "completed" && unsubmittedMetadata(originalResponse) && anchorValid;
    // Result adoption has no admission/dispatch authority. Keep the failed
    // Turn and original failure delivery, even when native tools ran.
    if (!steward || failed.session_id !== current.session_id || failed.turn_id !== row.turn_id
        || failed.client_turn_id !== `external-${row.request_ref}` || failed.message !== row.message
        || (!completedText && !completedMetadata && (failed.status !== "failed" || failed.response != null
          || !["response_timeout", "antigravity_rpc_error", "antigravity_transport_error",
            "upstream_transport_failure", "server_restarted"].includes(String(failed.error_code))))
        || recovery.attachments_present !== false || recovery.steering_observed !== false
        || recovery.stop_observed !== false) return NO_RECOVERY;
    const adopted = row.native_result_recovery == null ? null : requireJsonObject(row.native_result_recovery, "adopted native result");
    // The original prose was already delivered. Review only its unsubmitted
    // metadata: no duplicate prose adoption, Turn rewrite or business replay.
    if (completedMetadata) return planNativeMetadataReview(recovery, row, current, failed, null, version);
    if (adopted?.delivery_verified === true) {
      // A repaired answer never starts a format review or business continuation.
      return completedText ? NO_RECOVERY : planNativeMetadataReview(recovery, row, current, failed, adopted, version);
    }
    // A fresh native result requires an idle, stable trajectory. Do not spend
    // shared reply workers probing older results while this Session is busy.
    if (current.active_turn_id != null || current.status !== "ready" || row.context_recovery != null) return NO_RECOVERY;
    const candidate = {...NO_RECOVERY, operation: "adopt_native_result", recovery_candidate: true,
      recover_native_result: true, failed_turn_id: failed.turn_id, session_id: current.session_id};
    const proof = recovery.native_observation == null ? null : requireJsonObject(recovery.native_observation, "native terminal readback");
    if (proof === null) return candidate;
    const response = proof.response == null ? null : requireJsonObject(proof.response, "native result response");
    // Returning an already generated historical answer is not replay. Bind its
    // exact original input and closed native segment; later ordinary questions
    // must not strand that answer. Legacy unanchored results stay latest-only.
    const anchored = anchor !== null && proof.start_step_index === anchor.start_step_index && proof.input_sha256 === anchor.input_sha256;
    const closedOrLatest = proof.latest_input_verified === true || isClosedNativeSegment(proof);
    const segmentMatch = completedText
      ? anchored && closedOrLatest && response !== null && extendsOriginalText(originalResponse!, response)
      : proof.latest_input_verified === true || (anchored && isClosedNativeSegment(proof));
    return {...candidate, recovery_eligible: recovery.source_verified === true
      && matchesNativeProofBase(proof, current, row, failed) && segmentMatch && proof.normal_terminal_verified === true
      && isSafeInt(proof.start_step_index) && isSafeInt(proof.total_steps, Number(proof.start_step_index) + 2)
      && isBareSha256(proof.input_sha256) && isBareSha256(proof.answer_sha256)
      && response?.schema_version === "loopx_chat_agent_response_v0" && isNonBlankStr(response.message)
      && (adopted === null || adopted.turn_id === failed.turn_id && adopted.message_id === `native.${failed.turn_id}.recovered`
        && isNonEmptyStr(adopted.message_text) && isDeepStrictEqual(adopted.native_observation, proof))};
  }
  const previous = row.context_recovery == null ? null : requireJsonObject(row.context_recovery, "reserved context recovery");
  // One completed-work continuation is terminal for this request, including
  // when it later fails. It cannot fall through to routing/quota retries.
  if ((previous?.work_attempt != null && previous.failed_turn_id !== row.turn_id)
      || hasPriorHistory("prior recovery", item => item.work_attempt != null)) return NO_RECOVERY;
  // A failed review cannot fall through to routing/quota business retries.
  if ((previous?.read_only_review === true && previous.failed_turn_id !== row.turn_id)
      || hasPriorHistory("prior recovery", item => item.read_only_review === true)) return NO_RECOVERY;
  if (failed.error_code === "server_restarted") return planResultReview(recovery, row, current, failed, previous, version);
  const exhaustedAnswer = steward && current.agent_id === "codex"
    ? planExhaustedStewardAnswer(recovery, row, current, failed, previous, version) : null;
  if (exhaustedAnswer !== null) return exhaustedAnswer;
  if (failed.status === "completed" && !steward) {
    return recovery.related_result_review != null
      ? planRelatedResultReview(recovery, row, current, failed, previous, selected, version)
      : planUnfinishedWork(recovery, row, current, failed, previous, version);
  }
  if (!matchesFailedTurnBase(recovery, row, current, failed, {zeroDelta: true, requireUpstream: true})
      || !["context_window_exceeded", "host_gate", "upstream_transport_failure", "server_overloaded", "usage_limit_exceeded"].includes(String(failed.error_code))) {
    return NO_RECOVERY;
  }
  const retryKind = failed.error_code === "usage_limit_exceeded" ? "quota" : "routing";
  const attemptKey = `${retryKind}_attempt`;
  const client = recoveryClientBase(row, version);
  const attempt = previous?.[attemptKey];
  const reservedClient = isSafeInt(attempt, 1, 3) ? `${client}-${retryKind}-${attempt}`
    : attempt === undefined && steward && previous?.client_turn_id === client ? client : null;
  if (recovery.admitted_turn != null && previous !== null && previous.failed_turn_id === failed.turn_id
      && previous.version === version && reservedClient !== null) {
    // A crash after queue admission must adopt that exact canonical Turn,
    // even if it has already executed. This branch never creates model work.
    const origin = reservedClient === client && row.origin === undefined ? "lark" : row.origin;
    return admittedRecoveryResult(recovery, row, current, failed.turn_id, reservedClient,
      NO_RECOVERY, "admitted recovery Turn", {
        origin,
        extraValid: previous.client_turn_id === reservedClient
          && typeof row.message === "string" && typeof origin === "string",
      });
  }
  // A source stop or a newer admitted equivalent vetoes new execution.
  // Correlation repair above remains read-only and keeps its exact identity.
  if (recovery.stop_observed === true || recovery.later_equivalent_input_present === true) return NO_RECOVERY;
  const routing = planRoutingRecovery(recovery, row, current, failed, previous, version);
  if (routing !== null) return routing;
  if (!steward || (previous && !matchesReservation(previous, version, row.turn_id, client))) return NO_RECOVERY;
  const history = current.upstream_context_history ?? [];
  if (!Array.isArray(history)) throw new EffectRuntimeRequestError("invalid native context provenance");
  const oldThreads = provenanceThreads(history,
    item => isSafeInt(item.manager_context_version) && Number(item.manager_context_version) < version);
  if (isSafeInt(current.manager_context_version) && Number(current.manager_context_version) < version) {
    oldThreads.push(sessionIdentity(current.upstream_thread_id, "original native thread"));
  }
  const currentHostFailure = current.manager_context_version === version && failed.error_code === "host_gate";
  const nativeThreads = currentHostFailure
    ? [sessionIdentity(current.upstream_thread_id, "current native thread"), ...oldThreads] : oldThreads;
  if (!nativeThreads.length) return NO_RECOVERY;
  const candidate = {...NO_RECOVERY, recovery_candidate: true, client_turn_id: client,
    session_id: current.session_id, failed_turn_id: failed.turn_id, native_thread_ids: [...new Set(nativeThreads)]};
  if (recovery.native_observation == null) return candidate;
  const observed = requireJsonObject(recovery.native_observation, "native failure readback");
  if (!nativeThreads.includes(String(observed.thread_id)) || observed.turn_id !== failed.upstream_turn_id
      || observed.only_original_user_item !== true || recovery.source_verified !== true) return candidate;
  const info = observed.codex_error_info;
  let transient = (oldThreads.includes(String(observed.thread_id))
    && ["contextWindowExceeded", "serverOverloaded", "internalServerError"].includes(String(info)))
    || (currentHostFailure && observed.thread_id === current.upstream_thread_id && info === "other");
  const rawDetail = codexErrorDetail(info, ["httpConnectionFailed", "responseStreamConnectionFailed", "responseStreamDisconnected", "responseTooManyFailedAttempts"]);
  if (rawDetail !== undefined) {
    const detail = requireJsonObject(rawDetail, "native connection readback");
    transient = Object.hasOwn(detail, "httpStatusCode")
      && (detail.httpStatusCode === null || isSafeInt(detail.httpStatusCode, 500, 599));
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
  const client = `${recoveryClientBase(row, version)}-metadata-1`;
  const previous = row.context_recovery == null ? null : requireJsonObject(row.context_recovery, "metadata review reservation");
  if (previous !== null && (previous.metadata_review !== true || !matchesReservation(previous, version, failed.turn_id, client))) return NO_RECOVERY;
  const candidate = {...NO_RECOVERY, recovery_candidate: true, metadata_review: true, client_turn_id: client,
    session_id: current.session_id, failed_turn_id: failed.turn_id};
  const reviewWindowOpen = current.status === "ready" && current.active_turn_id == null
    && recovery.queued_input_present === false;
  if (recovery.admitted_turn == null && !reviewWindowOpen) return NO_RECOVERY;
  const supplied = adopted?.native_observation ?? previous?.native_observation ?? recovery.native_observation;
  if (supplied == null) return adopted === null ? candidate : NO_RECOVERY;
  const {raw_response: _raw, ...frozen} = requireJsonObject(supplied, "original native observation");
  const response = requireJsonObject(frozen.response, "original native response");
  if (!unsubmittedMetadata(response) || frozen.thread_id !== current.upstream_thread_id
      || frozen.request_ref !== row.request_ref || frozen.normal_terminal_verified !== true) return NO_RECOVERY;
  if (adopted !== null) {
    if (adopted.turn_id !== failed.turn_id || adopted.message_id !== `native.${failed.turn_id}.recovered`
        || !isNonEmptyStr(adopted.message_text) || frozen.latest_input_verified !== true) return NO_RECOVERY;
  } else {
    const anchor = requireJsonObject(recovery.native_input, "original completed input");
    if (!isDeepStrictEqual(failed.response, response) || frozen.start_step_index !== anchor.start_step_index
        || frozen.input_sha256 !== anchor.input_sha256
        || !matchesNativeProofBase(frozen, current, row, failed)
        || !isSafeInt(frozen.total_steps, Number(frozen.start_step_index) + 2)
        || !isBareSha256(frozen.input_sha256) || !isBareSha256(frozen.answer_sha256)
        || (frozen.latest_input_verified !== true && !isClosedNativeSegment(frozen))) return NO_RECOVERY;
  }
  if (previous !== null && recovery.admitted_turn != null) {
    const res = admittedRecoveryResult(recovery, row, current, failed.turn_id, client, candidate,
      "admitted metadata review", {includeNativeThreads: false});
    return res.recovery_candidate === true ? {...res,
      ...(previous.metadata_executor === "codex" ? {metadata_executor: "codex"} : {}),
      metadata_handoffs: previous.metadata_handoffs} : res;
  }
  if (!reviewWindowOpen) return NO_RECOVERY;
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
  if (Object.keys(payload).some(key => !["schema_version", "message", "proposals", ...RESPONSE_METADATA_KEYS].includes(key))
      || hasAnyField(payload, RESPONSE_METADATA_KEYS.filter(key => key !== "context_handoff"))
      || !Array.isArray(payload.proposals ?? []) || (payload.proposals as unknown[] | undefined)?.length) return candidate;
  const handoffs = Array.isArray(payload.context_handoff) ? payload.context_handoff : [payload.context_handoff];
  if (!handoffs.length || handoffs.length > 32) return candidate;
  const targets = new Set<string>();
  const transcribed: JsonObject[] = [];
  for (const value of handoffs) {
    const item = jsonObject(value);
    if (!item) return candidate;
    const keys = Object.keys(item).filter(key => key !== "schema_version").sort().join(",");
    if (keys !== "agent_id,brief,goal_id" || item.schema_version != null && item.schema_version !== "context_handoff_v0"
        || [item.goal_id, item.agent_id].some(v => typeof v !== "string" || !SESSION_ID_PATTERN.test(v))
        || !(typeof item.brief === "string" || jsonObject(item.brief) !== null)
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
  const originalClient = `external-${ref(row.request_ref, "original request")}`;
  if (!matchesFailedTurnBase(recovery, row, current, failed,
      {sameInput: true, sameOrigin: true, zeroDelta: true, requireUpstream: true, noAttachments: true})
      || failed.client_turn_id !== originalClient) return NO_RECOVERY;
  const client = `${originalClient}-recovery-${version}-quota-1`;
  const previous = row.context_recovery == null ? null : requireJsonObject(row.context_recovery, "quota reservation");
  if (row.context_recovery_history != null
      && (!Array.isArray(row.context_recovery_history) || row.context_recovery_history.length)
      || previous !== null && (previous.quota_attempt !== 1 || !matchesReservation(previous, version, failed.turn_id, client)
        || hasConflictingRecoveryKey(previous, "quota_attempt"))) {
    return NO_RECOVERY;
  }
  if (recovery.admitted_turn != null && previous !== null) {
    return admittedRecoveryResult(recovery, row, current, failed.turn_id, client, NO_RECOVERY, "admitted quota Turn");
  }
  const notBefore = cooldownNotBefore(recovery, current, failed.completed_at, {requireNoStop: true});
  if (recovery.later_source_input_present !== false || !notBefore
      || !validNativeAnchor(recovery.native_input, current.upstream_thread_id)) return NO_RECOVERY;
  const anchor = recovery.native_input as JsonObject;
  const candidate = {...NO_RECOVERY, recovery_candidate: true, quota_attempt: 1, client_turn_id: client,
    not_before: notBefore, failed_turn_id: failed.turn_id, session_id: current.session_id,
    native_thread_ids: [current.upstream_thread_id]};
  const proof = recovery.native_observation as JsonObject | null | undefined;
  if (!proof) return candidate;
  const nowSec = Date.parse(String(recovery.observed_at)) / 1000;
  if (!matchesNativeProofBase(proof, current, row, failed)
      || proof.start_step_index !== anchor.start_step_index || proof.input_sha256 !== anchor.input_sha256
      || proof.total_steps !== Number(anchor.start_step_index) + 2 || proof.latest_input_verified !== true
      || proof.pre_execution_quota_failure !== true || proof.only_original_user_item !== true
      || proof.model_id !== (current.agent_model_id ?? "gemini-4-argon-high")
      || !isFreshEpochSeconds(proof.observed_at, nowSec)) return candidate;
  return nativeRecoveryCapacity(recovery, row, current, candidate);
}

const isEligibleEndpointPair = (obj: JsonObject | undefined): boolean =>
  Array.isArray(obj?.eligible_endpoints) && obj.eligible_endpoints.length === 2
  && obj.eligible_endpoints.includes("antigravity") && obj.eligible_endpoints.includes("codex");

const isValidQuotaRecord = (obj: JsonObject): boolean =>
  isBareSha256(obj.account_ref) && typeof obj.remaining_fraction === "number"
  && Number.isFinite(obj.remaining_fraction) && obj.remaining_fraction >= 0 && obj.remaining_fraction <= 1
  && typeof obj.reset_time === "string" && Number.isFinite(Date.parse(obj.reset_time));

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
  if (alternate && isFreshEpochSeconds(alternate.observed_at, now)
      && allocation?.selection_policy === "flexible" && allocation.configured_endpoint === "antigravity"
      && allocation.executor_endpoint === "codex" && allocation.available === true
      && isEligibleEndpointPair(allocation)
      && allocation.schema_version === "manager_executor_allocation_v0"
      && allocation.allocation_reason === "flexible_availability_fallback"
      && configuration?.status === "ready" && configuration.selection_policy === "flexible"
      && configuration.executor_endpoint === "antigravity" && isEligibleEndpointPair(configuration)
      && isNonEmptyStr(allocation.configuration_revision)
      && allocation.configuration_revision === configuration.configuration_revision) {
    return {...candidate, recovery_eligible: true,
      ...(candidate.metadata_review === true ? {metadata_executor: "codex"}
        : candidate.answer_review === true ? {answer_executor: "codex"} : {quota_executor: "codex"})};
  }
  if (!capacity || capacity.model_id !== (current.agent_model_id ?? "gemini-4-argon-high")
      || !isValidQuotaRecord(capacity) || !isFreshEpochSeconds(capacity.observed_at, now)
      || typeof capacity.quota_exhausted !== "boolean" || typeof capacity.normal_completion !== "boolean") return candidate;
  const waiting = row.metadata_quota_wait as JsonObject | null | undefined;
  if (waiting != null && (typeof waiting.model_id !== "string" || !isValidQuotaRecord(waiting))) return candidate;
  const sameEpoch = waiting?.model_id === capacity.model_id && waiting.account_ref === capacity.account_ref
    && Date.parse(String(waiting.reset_time)) === Date.parse(String(capacity.reset_time));
  // Fractions can fluctuate across refreshes. Only a quota/account epoch
  // change or the known reset boundary can clear this recorded hard failure.
  const restored = waiting != null && (!sameEpoch || now >= Date.parse(String(waiting.reset_time)) / 1000);
  if (Number(capacity.remaining_fraction) <= 0 || waiting != null && !restored
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
  const work = jsonObject(result.unfinished_work);
  if (!work) return null;
  if (Object.keys(work).sort().join(",") !== "dependency,effects_settled,remaining"
      || work.effects_settled !== true
      || [work.remaining, work.dependency].some(value => !isNonBlankStr(value) || value.length > 1000)
      || hasAnyField(result, ["gate", "protected_action", "context_handoff", "goal_draft"])
      || !Array.isArray(result.proposals) || result.proposals.length) return null;
  return work;
}

function planWorkAfterResultReview(recovery: JsonObject, row: JsonObject, current: JsonObject,
  original: JsonObject, selected: JsonObject, version: number): JsonObject {
  const retained = requireJsonObject(recovery.retained_result_review, "retained result review");
  const reservation = requireJsonObject(retained.reservation, "original review reservation");
  const reviewed = requireJsonObject(retained.turn, "completed result review");
  const previous = requireJsonObject(row.context_recovery, "current recovery reservation");
  const history = Array.isArray(row.context_recovery_history) ? row.context_recovery_history as JsonObject[] : [];
  const continuing = previous.work_attempt === 1;
  const expected = continuing ? history.at(-1) : previous;
  const originalResp = jsonObject(original.response);
  const work = originalResp ? unfinishedProjectWork(originalResp) : null;
  // A delivered read-only report retains intent. Only a fresh observation by
  // the original writable owner can admit the already bounded remaining work.
  if (selected.context_kind !== "project" || current.agent_id !== "codex" || work === null
      || !isDeepStrictEqual(expected, reservation)
      || !matchesRetainedWorkReview(reservation, original, reviewed, current, row, selected, work, row.request_ref)
      || reservation.version !== version || reservation.client_turn_id !== reviewClientId(row, version)
      || !isDeepStrictEqual(reservation.original_turn, original)
      || reviewed.turn_id !== row.turn_id || reviewed.turn_id === original.turn_id
      || !isNonEmptyStr(reviewed.upstream_turn_id) || history.some(item => item.work_attempt != null)
      || continuing && (previous.failed_turn_id !== original.turn_id
        || previous.completed_review_turn_id !== reviewed.turn_id)) return NO_RECOVERY;
  const admitted = continuing ? recovery.admitted_turn : null;
  if (admitted == null && (recovery.live_work_owner !== true || recovery.later_equivalent_input_present !== false)) return NO_RECOVERY;
  const proof = recovery.native_observation as JsonObject | null | undefined;
  if (proof != null && (proof.completed_work !== true || proof.all_effects_settled !== true)) return NO_RECOVERY;
  const plan = planUnfinishedWork({...recovery, admitted_turn: admitted}, {...row, turn_id: original.turn_id},
    current, original, continuing ? previous : null, version);
  return plan.work_attempt === 1 ? {...plan, completed_review_turn_id: reviewed.turn_id} : NO_RECOVERY;
}

function planUnfinishedWork(recovery: JsonObject, row: JsonObject, current: JsonObject,
  completed: JsonObject, previous: JsonObject | null, version: number): JsonObject {
  const response = jsonObject(completed.response);
  const work = response ? unfinishedProjectWork(response) : null;
  if (work === null) return NO_RECOVERY;
  if (completed.session_id !== current.session_id || completed.turn_id !== row.turn_id
      || recovery.steering_observed !== false || recovery.stop_observed !== false
      || recovery.attachments_present !== false || !isNonEmptyStr(completed.upstream_turn_id)) return NO_RECOVERY;
  const observedResult = recovery.native_observation as JsonObject | null | undefined;
  // An admitted continuation may already have appended the original input.
  // Recover its existing receipt before considering a fresh result review.
  const existingWorkTurn = previous?.work_attempt === 1 && recovery.admitted_turn != null;
  if (previous?.read_only_review === true || (!existingWorkTurn
      && (recovery.later_equivalent_input_present === true || observedResult?.completed_result_review === true))) {
    return planCompletedResultReview(recovery, row, current, completed, previous, version, work);
  }
  const client = `${recoveryClientBase(row, version)}-work-1`;
  if (previous?.work_attempt != null && (previous.work_attempt !== 1
      || !isDeepStrictEqual(previous.unfinished_work, work)
      || !matchesReservation(previous, version, completed.turn_id, client))) return NO_RECOVERY;
  const candidate = {...NO_RECOVERY, recovery_candidate: true, work_attempt: 1, unfinished_work: work,
    client_turn_id: client, session_id: current.session_id, failed_turn_id: completed.turn_id};
  if (existingWorkTurn) {
    const res = admittedRecoveryResult(recovery, row, current, completed.turn_id, client, candidate, "admitted unfinished work");
    return res.recovery_candidate === true ? {...res, not_before: previous.not_before} : res;
  }
  const notBefore = cooldownNotBefore(recovery, current, completed.completed_at, {laterInput: "none"});
  if (!notBefore) return NO_RECOVERY;
  const native = sessionIdentity(current.upstream_thread_id, "original native thread");
  const planned = {...candidate, native_thread_ids: [native], not_before: notBefore};
  if (recovery.native_observation == null) return planned;
  const observed = requireJsonObject(recovery.native_observation, "completed unfinished-work inventory");
  return {...planned, recovery_eligible: recovery.source_verified === true
    && observed.thread_id === native && observed.turn_id === completed.upstream_turn_id
    && observed.completed_work === true && observed.all_effects_settled === true
    && isBareSha256(observed.native_inventory_sha256) && isDeepStrictEqual(observed.unfinished_work, work)};
}

/** A separately delivered review can justify inspecting an older same-question
 * source, never treating two message IDs as one completed business request. */
function planRelatedResultReview(recovery: JsonObject, row: JsonObject, current: JsonObject,
  completed: JsonObject, previous: JsonObject | null, selected: JsonObject, version: number): JsonObject {
  const baseClient = reviewClientId(row, version);
  const preparationReserved = previous?.review_preparation_attempt === 1;
  const history = (row.context_recovery_history ?? []) as JsonObject[];
  if (selected.context_kind !== "project" || current.agent_id !== "codex"
      || !pureResultText(completed.response as JsonObject | null)
      || completed.session_id !== current.session_id || completed.status !== "completed"
      || recovery.attachments_present !== false || recovery.steering_observed !== false
      || recovery.stop_observed !== false || !isNonEmptyStr(completed.upstream_turn_id)
      || previous !== null && previous.legacy_result_review !== true
      || previous?.review_preparation_attempt != null && !preparationReserved
      || !Array.isArray(history) || history.length && !(preparationReserved && history.length === 1
        && history[0].legacy_result_review === true && history[0].read_only_review === true
        && history[0].review_preparation_attempt == null && history[0].client_turn_id === baseClient
        && history[0].failed_turn_id === completed.turn_id)) return NO_RECOVERY;
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
      || reserved.legacy_result_review === true
      || !matchesRetainedWorkReview(reserved, original, reviewed, current, row, selected, work, sibling.request_ref)
      || ![original.completed_at, completed.created_at, reviewed.completed_at, completed.completed_at]
        .every(ts => Number.isFinite(Date.parse(String(ts))))
      || Date.parse(String(original.completed_at)) > Date.parse(String(completed.created_at))
      || Date.parse(String(reviewed.completed_at)) <= Date.parse(String(completed.completed_at))) return NO_RECOVERY;
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
  if (previous !== null && row.turn_id !== completed.turn_id && row.delivery_verified === true && !preparationOnly) return NO_RECOVERY;
  const observation = preparationOnly && !preparationReserved ? {...recovery, admitted_turn: null} : recovery;
  const plan = planCompletedResultReview(observation, row, current, completed, previous, version, null);
  return preparationOnly && plan.recovery_candidate === true ? {...plan,
    client_turn_id: `${recoveryClientBase(row, version)}-preparation-1`, review_preparation_attempt: 1,
    preparation_failed_turn_id: failed!.turn_id, original_result_turn_id: completed.turn_id} : plan;
}

/** A result-only fallback never consumes or restores the writable retry budget. */
function planCompletedResultReview(recovery: JsonObject, row: JsonObject, current: JsonObject,
  completed: JsonObject, previous: JsonObject | null, version: number, work: JsonObject | null): JsonObject {
  // An inconclusive effect observation must not replace the original writable
  // owner with a result reader. Keep that owner for the next fresh work proof.
  // Already reserved reviews retain their own adoption and execution checks.
  if (previous === null && recovery.live_work_owner !== false) return NO_RECOVERY;
  // Old readers recognize preparation-1 and reject its read-only policy, so a
  // rollback holds this receipt instead of treating an unknown key as ordinary input.
  const client = `${recoveryClientBase(row, version)}-${previous?.review_preparation_attempt === 1 ? "preparation-1" : "review-1"}`;
  if (work !== null && previous?.review_preparation_attempt != null) return NO_RECOVERY;
  if (current.steward_context != null || !matchesOriginalTurn(completed, row)
      || previous !== null && (previous.read_only_review !== true || !matchesReservation(previous, version, completed.turn_id, client)
        || previous.work_attempt != null || previous.answer_review === true || !isDeepStrictEqual(previous.original_turn, completed)
        || (work === null ? previous.legacy_result_review !== true || previous.unfinished_work != null
          || !isDeepStrictEqual(previous.related_result_review, recovery.related_result_review)
          : !isDeepStrictEqual(previous.unfinished_work, work)))) return NO_RECOVERY;
  const intent = work === null
    ? {legacy_result_review: true, related_result_review: recovery.related_result_review}
    : {unfinished_work: work};
  const candidate = {...NO_RECOVERY, recovery_candidate: true, ...intent,
    read_only_review: true, failed_turn_id: completed.turn_id, session_id: current.session_id, client_turn_id: client};
  if (previous !== null && recovery.admitted_turn != null) {
    return admittedRecoveryResult(recovery, row, current, completed.turn_id, client, candidate, "admitted result review");
  }
  if (!cooldownNotBefore(recovery, current, completed.completed_at)) return NO_RECOVERY;
  const native = sessionIdentity(current.upstream_thread_id, "original native thread");
  const planned = {...candidate, native_thread_ids: [native]};
  if (recovery.native_observation == null) return planned;
  const proof = requireJsonObject(recovery.native_observation, "completed result review inventory");
  return {...planned, recovery_eligible: recovery.source_verified === true
    && proof.thread_id === native && proof.turn_id === completed.upstream_turn_id
    && proof.read_only_review === true && proof.completed_result_review === true
    && proof.completed_work !== true && proof.all_effects_settled !== true
    && isNonBlankStr(proof.visible_response) && isBareSha256(proof.native_inventory_sha256)
    && isDeepStrictEqual(proof.unfinished_work, work)
    && (work !== null || proof.untyped_result_review === true && isDeepStrictEqual(proof.response, completed.response))};
}

function interruptedVisibleResult(value: unknown): boolean {
  const result = jsonObject(value);
  if (!result) return false;
  if (Object.keys(result).join(",") === "visible_response") return isNonBlankStr(result.visible_response);
  if (Object.keys(result).sort().join(",") === "content,file_uri") {
    if (typeof result.file_uri !== "string" || typeof result.content !== "string") return false;
    try {
      const uri = new URL(result.file_uri);
      return uri.protocol === "file:" && uri.host === "" && uri.pathname.startsWith("/")
        && uri.search === "" && uri.hash === "";
    } catch { return false; }
  }
  return isNonEmptyStr(result.commandLine) && Number.isSafeInteger(result.exitCode);
}

/** Exhausting business retries must not erase the original question. One
 * separately sandboxed answer review can inspect existing results, never
 * reopen the business budget. Its failed attempt is terminal too.
 */
function planExhaustedStewardAnswer(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, previous: JsonObject | null, version: number): JsonObject | null {
  if (previous === null) return null;
  const client = reviewClientId(row, version);
  const reserved = previous?.answer_review === true && previous.read_only_review === true
    && previous.failed_turn_id === failed.turn_id && previous.client_turn_id === client;
  if (!reserved && previous?.quota_attempt !== 3) return null;
  const budget = reserved && Array.isArray(row.context_recovery_history)
    ? row.context_recovery_history.at(-1) as JsonObject | undefined : previous;
  if (current.steward_context == null
      || !matchesFailedTurnBase(recovery, row, current, failed,
        {sameInput: true, sameOrigin: true, zeroDelta: true, requireUpstream: true, noAttachments: true, noStop: true})
      || failed.error_code !== "usage_limit_exceeded" || previous?.version !== version
      || !isNonEmptyStr(current.upstream_thread_id)
      || budget?.quota_attempt !== 3 || budget.version !== version || budget.failed_turn_id === failed.turn_id
      || budget.client_turn_id !== failed.client_turn_id
      || failed.client_turn_id !== `${recoveryClientBase(row, version)}-quota-3`
      || hasAnyField(previous, ["metadata_review", "preparation_attempt", "routing_attempt", "work_attempt"])) {
    return NO_RECOVERY;
  }
  const history = current.upstream_context_history ?? [];
  if (!Array.isArray(history)) return NO_RECOVERY;
  const threads = [current.upstream_thread_id, ...provenanceThreads(history,
    item => (item.agent_id == null || item.agent_id === "codex") && isSafeInt(item.manager_context_version, 1, version))];
  const priorThreadId = (budget.native_observation as JsonObject | undefined)?.thread_id;
  const nativeThreads = [...new Set([...(typeof priorThreadId === "string" && threads.includes(priorThreadId) ? [priorThreadId] : []), ...threads])];
  const candidate = reviewCandidate(current, failed, client, nativeThreads, {answer_review: true});
  if (reserved && recovery.admitted_turn != null) {
    return admittedRecoveryResult(recovery, row, current, failed.turn_id, client,
      {...candidate, answer_executor: "codex"}, "admitted answer review", {turnIdLabel: "answer review"});
  }
  // Let a newer admitted resend answer the question. The branch above still
  // repairs the receipt of a review already accepted under its own identity.
  if (!cooldownNotBefore(recovery, current, failed.completed_at, {laterInput: "not_true"})) return NO_RECOVERY;
  if (recovery.native_observation == null) return candidate;
  const proof = requireJsonObject(recovery.native_observation, "exhausted original request inventory");
  if (!nativeThreads.some(thread => thread === proof.thread_id) || proof.turn_id !== failed.upstream_turn_id
      || proof.only_original_user_item !== true || proof.codex_error_info !== "usageLimitExceeded") return candidate;
  return stewardAnswerWithCapacity(recovery, row, current, candidate);
}

/** Interrupted work may have effects. Never retry it. The original worker may
 * reserve one read-only review of existing results, with its own presentation.
 * The adapter retains only the original runtime's authorized reads, with
 * filesystem writes, network effects and new delegated actions disabled.
 */
function planInterruptedStewardAnswer(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, version: number): JsonObject {
  const previous = row.context_recovery as JsonObject | null | undefined;
  const client = reviewClientId(row, version);
  if (current.steward_context == null
      || !matchesFailedTurnBase(recovery, row, current, failed,
        {sameInput: true, sameOrigin: true, noAttachments: true, noStop: true})
      || failed.client_turn_id !== `external-${row.request_ref}`
      || typeof failed.upstream_turn_id !== "string"
      || !validNativeAnchor(recovery.native_input, current.upstream_thread_id)
      || (Array.isArray(row.context_recovery_history) && row.context_recovery_history.length)
      || previous != null && (previous.answer_review !== true || previous.read_only_review !== true
        || hasConflictingRecoveryKey(previous, "read_only_review")
        || !matchesReservation(previous, version, failed.turn_id, client))) return NO_RECOVERY;
  const anchor = recovery.native_input as JsonObject;
  const candidate = reviewCandidate(current, failed, client, [current.upstream_thread_id], {answer_review: true});
  if (previous != null && recovery.admitted_turn != null) {
    return admittedRecoveryResult(recovery, row, current, failed.turn_id, client,
      {...candidate, answer_executor: "codex"}, "admitted answer review", {turnIdLabel: "answer review"});
  }
  if (!cooldownNotBefore(recovery, current, failed.completed_at)) return NO_RECOVERY;
  if (recovery.native_observation == null) return candidate;
  const proof = requireJsonObject(recovery.native_observation, "interrupted original answer inventory");
  if (!matchesNativeProofBase(proof, current, row, failed)
      || proof.start_step_index !== anchor.start_step_index || proof.input_sha256 !== anchor.input_sha256
      || !isSafeInt(proof.total_steps)
      || !isSafeInt(proof.end_step_index, Number(proof.start_step_index) + 3, Number(proof.total_steps))
      || proof.read_only_review !== true || proof.no_final_answer !== true || proof.effects_observed !== true
      || !Array.isArray(proof.visible_results) || !proof.visible_results.length || proof.visible_results.length > 128
      || Buffer.byteLength(JSON.stringify(proof.visible_results), "utf8") > 256 * 1024
      || !proof.visible_results.every(interruptedVisibleResult)) return candidate;
  // This is a new restricted answer review, never native business replay.
  return stewardAnswerWithCapacity(recovery, row, current, candidate);
}

/** Reuse the original queue for a restricted result review, never business replay. */
function planResultReview(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, previous: JsonObject | null, version: number): JsonObject {
  const steward = current.steward_context != null;
  const review = steward ? {answer_review: true, answer_executor: "codex"} : {};
  if (!matchesFailedTurnBase(recovery, row, current, failed, {requireUpstream: true, noAttachments: true, noStop: true})
      || !isSafeInt(failed.delta_count, 1) || (steward && !matchesOriginalTurn(failed, row))) return NO_RECOVERY;
  const client = reviewClientId(row, version);
  if (previous?.read_only_review === true && !matchesReservation(previous, version, failed.turn_id, client)) return NO_RECOVERY;
  if (recovery.admitted_turn != null && previous?.read_only_review === true) {
    return admittedRecoveryResult(recovery, row, current, failed.turn_id, client,
      {...NO_RECOVERY, ...review, read_only_review: true}, "admitted result review");
  }
  if (!cooldownNotBefore(recovery, current, failed.completed_at, {requireQueueEmpty: false})) return NO_RECOVERY;
  const native = sessionIdentity(current.upstream_thread_id, "original native thread");
  const candidate = reviewCandidate(current, failed, client, [native], review);
  if (recovery.native_observation == null) return candidate;
  const observed = requireJsonObject(recovery.native_observation, "interrupted result inventory");
  const eligible = recovery.source_verified === true
    && observed.thread_id === native && observed.turn_id === failed.upstream_turn_id
    && observed.read_only_review === true && observed.no_final_answer === true
    && observed.effects_observed === true && (!steward || isBareSha256(observed.native_inventory_sha256));
  if (!eligible || !steward) return {...candidate, recovery_eligible: eligible};
  return stewardAnswerWithCapacity(recovery, row, current, candidate);
}

/** A local manager-context failure precedes native dispatch. Only the Core's
 * exact preparation event sequence can qualify it; absence alone cannot.
 */
function samePreparationImages(left: unknown, right: unknown): boolean {
  const image = jsonObject(left);
  return image !== null && isSafeInt(image.count, 1)
    && isBareSha256(image.sha256) && isDeepStrictEqual(left, right);
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
  const expectedErrors = new Map<string, string[]>([
    ["model_auth_unavailable", ["Native account API transport is unavailable."]],
    ["resume_failed", [
      "The previous Agent conversation could not be restored.",
      ...(current.agent_id === "antigravity"
        ? ["The original Antigravity conversation is still active. Stop it in Antigravity before retrying."]
        : []),
    ]],
    ["execution_worker_exited_before_dispatch", ["The LoopX Chat worker exited before the original request was dispatched."]],
  ]).get(String(failed.error_code));
  if (expectedErrors === undefined) return null;
  const lostWorker = failed.error_code === "execution_worker_exited_before_dispatch";
  const stewardPreDispatchBase = row.origin === "lark" && current.steward_context != null
    && failed.loopx_request == null && !failed.loopx_execution
    && (failed.attachments === undefined || Array.isArray(failed.attachments) && failed.attachments.length === 0)
    && typeof failed.started_at === "string" && Number.isFinite(Date.parse(failed.started_at));
  const stewardManagerContext = !lostWorker && stewardPreDispatchBase && failed.execution_dispatch == null
    && typeof failed.first_event_at === "string" && Number.isFinite(Date.parse(failed.first_event_at));
  const beforeDispatch = lostWorker ? stewardPreDispatchBase && failed.execution_dispatch === "pending"
    : (failed.started_at === null && failed.first_event_at === null) || stewardManagerContext;
  if (!isStewardEndpoint(current.agent_id)
      || !matchesFailedTurnBase(recovery, row, current, failed,
        {sameInput: true, sameOrigin: true, zeroDelta: true, requireUpstream: "null", noAttachments: true})
      || !expectedErrors.includes(String(failed.error)) || !beforeDispatch
      || recovery.native_observation != null || recovery.native_result_present !== false) return NO_RECOVERY;
  const observed = recovery.preparation_observation == null ? null
    : requireJsonObject(recovery.preparation_observation, "runtime preparation readback");
  if (observed?.kind !== "core_runtime_preparation_failure" || observed.session_id !== failed.session_id
      || observed.turn_id !== failed.turn_id || !Array.isArray(observed.events)
      || observed.events.length < 2
      || (!lostWorker && !stewardManagerContext && observed.events.length !== 2)
      || (stewardManagerContext && observed.events.length <= 2)) return NO_RECOVERY;
  const events = parseCorePreparationEvents(observed.events, failed, "runtime preparation event");
  if (!events || events[0].delivery_mode !== "session_queue" || events.at(-1)!.message !== failed.error
      || (stewardManagerContext && !events.slice(1, -1).some(item => item.kind === "manager.context"))) return NO_RECOVERY;
  const original = `external-${ref(row.request_ref, "original request")}`;
  const previous = row.context_recovery == null ? null : requireJsonObject(row.context_recovery, "runtime preparation reservation");
  if (row.context_recovery_history != null && !Array.isArray(row.context_recovery_history)) return NO_RECOVERY;
  const history = ((row.context_recovery_history ?? []) as unknown[])
    .map(value => requireJsonObject(value, "runtime preparation lineage"));
  const lineage = [...history, ...(previous ? [previous] : [])];
  const counters = ["quota", "routing", "preparation"];
  const count = (item: JsonObject): number => {
    const kinds = counters.filter(kind => item[`${kind}_attempt`] != null);
    if (kinds.length !== 1 || item.version !== version
        || hasAnyField(item, ["read_only_review", "metadata_review", "work_attempt"])) return 0;
    const attempt = item[`${kinds[0]}_attempt`];
    return isSafeInt(attempt, 1, 3) && item.client_turn_id === `${original}-recovery-${version}-${kinds[0]}-${attempt}`
      ? Number(attempt) : 0;
  };
  if (lineage.some(item => !count(item))) return NO_RECOVERY;
  const reserved = previous?.failed_turn_id === failed.turn_id;
  if (reserved && previous?.preparation_attempt == null) return NO_RECOVERY;
  const prior = reserved ? history : lineage;
  if (prior.some((item, i) => i > 0 && count(item) <= count(prior[i - 1]))) return NO_RECOVERY;
  const last = prior.at(-1);
  if (failed.client_turn_id !== (last?.client_turn_id ?? original)) return NO_RECOVERY;
  const attempt = last ? count(last) + 1 : 1;
  if (attempt > 3 || reserved && previous?.preparation_attempt !== attempt) return NO_RECOVERY;
  const client = `${original}-recovery-${version}-preparation-${attempt}`;
  if (reserved && previous?.client_turn_id !== client) return NO_RECOVERY;
  if (reserved && recovery.admitted_turn != null) {
    return admittedRecoveryResult(recovery, row, current, failed.turn_id, client,
      {...NO_RECOVERY, preparation_attempt: attempt}, "admitted runtime preparation Turn");
  }
  const notBefore = cooldownNotBefore(recovery, current, failed.completed_at,
    {backoffMs: 60_000 * 2 ** (attempt - 1), requireNoStop: true, laterInput: "none"});
  if (!notBefore) return NO_RECOVERY;
  return {...NO_RECOVERY, recovery_candidate: true, recovery_eligible: recovery.source_verified === true
    && recovery.preparation_scope_available === true, preparation_attempt: attempt,
    not_before: notBefore, preparation_observation: observed,
    client_turn_id: client, failed_turn_id: failed.turn_id, session_id: current.session_id, native_thread_ids: []};
}

function planPreparationRecovery(recovery: JsonObject, row: JsonObject, current: JsonObject,
  failed: JsonObject, version: number): JsonObject | null {
  const images = legacyImagePreparation(recovery, failed);
  if (failed.error_code !== "manager_authorization_unavailable" && !images) return null;
  if (!matchesFailedTurnBase(recovery, row, current, failed, {sameInput: true, zeroDelta: true, requireUpstream: "null"})
      || (!images && recovery.attachments_present !== false) || recovery.native_observation != null) return NO_RECOVERY;
  const previous = row.context_recovery == null ? null : requireJsonObject(row.context_recovery, "preparation recovery");
  // Only one migration of the original rejected image input. An admitted
  // continuation that later fails cannot become another attachment retry.
  if (images && previous !== null && (previous.failed_turn_id !== failed.turn_id
      || previous.preparation_attempt !== 1)) return NO_RECOVERY;
  if (previous !== null && (!isSafeInt(previous.preparation_attempt, 1, 3)
      || previous.version !== version || hasConflictingRecoveryKey(previous, "preparation_attempt"))) return NO_RECOVERY;
  if (row.context_recovery_history != null && (!Array.isArray(row.context_recovery_history)
      || row.context_recovery_history.some(value =>
        hasConflictingRecoveryKey(requireJsonObject(value, "prior preparation recovery"), "preparation_attempt")))) return NO_RECOVERY;
  const reserved = previous?.failed_turn_id === failed.turn_id;
  const prior = previous === null ? 0 : Number(previous.preparation_attempt) - (reserved ? 1 : 0);
  const originalClient = `external-${ref(row.request_ref, "original request")}`;
  const failedClient = prior === 0 ? originalClient : `${originalClient}-recovery-${version}-preparation-${prior}`;
  if (failed.client_turn_id !== failedClient) return NO_RECOVERY;
  if (reserved && recovery.admitted_turn != null) {
    const client = `${originalClient}-recovery-${version}-preparation-${previous?.preparation_attempt}`;
    return admittedRecoveryResult(recovery, row, current, failed.turn_id, client,
      {...NO_RECOVERY, preparation_attempt: previous?.preparation_attempt},
      "admitted preparation continuation",
      {turnIdLabel: "admitted preparation Turn", allowImages: images, extraValid: previous?.client_turn_id === client});
  }
  // Newer human input has priority. An equivalent later submission can already
  // have executed; it is never authority to replay this older failure too.
  if (recovery.stop_observed !== false || recovery.queued_input_present !== false
      || recovery.later_equivalent_input_present !== false) return NO_RECOVERY;
  return planRoutingRecovery(recovery, row, current, failed, previous, version) ?? NO_RECOVERY;
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
  const routingFailure = failed.error_code === "host_gate" || failed.error_code === "upstream_transport_failure";
  const kind = preparation ? "preparation" : quota ? "quota" : "routing";
  const attemptKey = `${kind}_attempt`;
  if (!quota && !preparation && !routingFailure) return null;
  // An unfinished older reservation must finish under its original identity.
  if (previous !== null && previous.failed_turn_id === failed.turn_id && previous[attemptKey] === undefined) return null;
  const raw = preparation ? recovery.preparation_observation : recovery.native_observation;
  const observed = raw == null ? null : requireJsonObject(raw, "pre-execution failure readback");
  const interrupted = observed?.interrupted_before_execution === true && routingFailure;
  const infoDetail = jsonObject(codexErrorDetail(observed?.codex_error_info,
    ["httpConnectionFailed", "responseStreamConnectionFailed"]));
  const loopbackRelayAuth = observed?.loopback_relay_auth_failure === true
    && failed.error_code === "upstream_transport_failure" && infoDetail?.httpStatusCode === 401;
  if (observed && !quota && !preparation && observed.pre_execution_routing_timeout !== true && !interrupted && !loopbackRelayAuth) return null;
  const reserved = previous?.failed_turn_id === failed.turn_id;
  const priorAttempt = previous?.[attemptKey] ?? 0;
  if (!isSafeInt(priorAttempt)) return NO_RECOVERY;
  const attempt = Number(priorAttempt) + (reserved ? 0 : 1);
  const disconnected = current.status === "stale" && current.last_error_code === "transport_disconnected" && routingFailure;
  if (attempt < 1 || attempt > 3 || !isNonEmptyStr(current.upstream_thread_id)
      || current.active_turn_id != null || (current.status !== "ready" && !disconnected)) return NO_RECOVERY;
  const now = typeof recovery.observed_at === "string" ? Date.parse(recovery.observed_at) : NaN;
  const completed = typeof failed.completed_at === "string" ? Date.parse(failed.completed_at) : NaN;
  const notBefore = completed + 60_000 * 2 ** (attempt - 1);
  if (!Number.isFinite(now) || !Number.isFinite(completed) || now < notBefore) {
    // Preserve the existing context-migration path when no routing diagnostic
    // has been established. A proven routing timeout always obeys backoff.
    return observed ? NO_RECOVERY : null;
  }
  const client = `${recoveryClientBase(row, version)}-${kind}-${attempt}`;
  if (reserved && (previous?.client_turn_id !== client || previous.version !== version)) return NO_RECOVERY;
  const candidate = {...NO_RECOVERY, recovery_candidate: true, client_turn_id: client,
    [attemptKey]: attempt, not_before: new Date(notBefore).toISOString(),
    session_id: current.session_id, failed_turn_id: failed.turn_id,
    native_thread_ids: preparation ? [] : [sessionIdentity(current.upstream_thread_id, "current native thread")]};
  if (!observed) return candidate;
  if (preparation) {
    const events = observed.events;
    if (observed.kind !== (images ? "core_attachment_preparation_failure" : "core_context_preparation_failure")
        || observed.session_id !== failed.session_id || observed.turn_id !== failed.turn_id
        || !Array.isArray(events) || (events.length !== 4 && !(images && events.length === 3))) return candidate;
    const rows = parseCorePreparationEvents(events, failed, "Core preparation event");
    const verified = rows !== null && (rows.length !== 4 || rows[1].kind === "agent.phase")
      && rows.at(-2)!.kind === "manager.context" && rows.at(-2)!.schema_version === "manager_turn_context_v1";
    return {...candidate, recovery_eligible: verified && recovery.source_verified === true
      && recovery.preparation_scope_available === true, preparation_observation: observed};
  }
  const nowSec = now / 1000;
  return {...candidate, recovery_eligible: recovery.source_verified === true
    && observed.thread_id === current.upstream_thread_id && observed.turn_id === failed.upstream_turn_id
    && (quota ? observed.codex_error_info === "usageLimitExceeded" && observed.only_original_user_item === true
      && observed.quota_available === true && isFreshEpochSeconds(observed.quota_observed_at, nowSec)
      : interrupted ? recovery.stop_observed === false && observed.only_original_user_item === true
      : loopbackRelayAuth ? observed.only_pre_execution_items === true
        && observed.relay_transport_ready === true && isFreshEpochSeconds(observed.relay_observed_at, nowSec)
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
  ensure(fieldsMatch(context, selected, ["binding_id", "project_ref", "provider_ref", "operator_ref"]),
    "status context belongs to another binding");
  const source = ref(context.source_ref, "status source identity");
  const channel = boundChannelId(steward, selected.binding_id, source);
  const recipient = params.agent_target ? agentTarget(params.agent_target) : null;
  if (recipient) {
    ensure(matchesAttachedSession(current, recipient), "status Agent Session changed");
  } else if (current) {
    const saved = steward ? normalizeStewardContext(current.steward_context) : normalizeProjectContext(current.project_context);
    ensure(current.channel_id === channel && current.goal_id === (steward ? "loopx-manager" : null)
      && JSON.stringify({...saved, goal_ids: []}) === JSON.stringify({...context, goal_ids: []}),
      "status Session context changed");
  }
  const cadenceExpected = requireJsonObject(params.request, "external request").command === "status" && recipient !== null;
  ensure((params.automation_cadence !== undefined) === cadenceExpected,
    "automation cadence observation requires an exact selected Agent status");
  const cadence = cadenceExpected ? automationCadenceStatus(params.automation_cadence, recipient as JsonObject) : null;
  ensure(isSafeInt(params.queued_count) && (current || params.queued_count === 0), "invalid canonical queue observation");
  const instant = requireNonEmptyString(params.observed_at, "status observation time");
  ensure(instant.length <= 64 && Number.isFinite(Date.parse(instant)), "invalid status observation time");
  const turn = params.active_turn === null ? null : requireJsonObject(params.active_turn, "observed active Turn");
  ensure(!turn || (current && turn.session_id === current.session_id && turn.turn_id === current.active_turn_id),
    "status Turn belongs to another Session");
  for (const item of [current, turn]) {
    ensure(!item || (typeof item.status === "string" && /^[a-z][a-z0-9_]{0,63}$/.test(item.status)), "invalid canonical status observation");
  }
  return {schema_version: "loopx_chat_bound_status_v0", observed_at: instant, context_kind: selected.context_kind,
    workspace_path: selected.audience === "group" ? String(context.workspace_path).split(/[\\/]/).filter(Boolean).at(-1) : context.workspace_path,
    ...(selected.audience === "group" ? {audience: "group"} : {}),
    executor_endpoint_id: recipient?.executor_endpoint_id ?? selected.executor_endpoint_id, grant: selected.grant,
    ...(recipient ? {recipient_agent_id: recipient.agent_id, recipient_goal_id: recipient.goal_id, recipient_mode: "attached_host"} : {}),
    ...(cadence ? {automation_cadence: cadence} : {}),
    authorized_commission_count: steward ? (selected.goal_ids as string[]).length : 0,
    session_status: current?.status ?? null, active_turn_status: turn?.status ?? null,
    active_turn_observation_available: !current?.active_turn_id || turn !== null, queued_count: params.queued_count};
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
  ensure(match && isNonBlankStr(argument) && Array.from(argument).length <= 1000, "invalid explicit steward command");
  const budgetGroup = command === "commission" ? 1 : command === "resume_commission" ? 2 : 0;
  return {argument: argument.trim(), ...(budgetGroup ? {native_token_budget: Number(match[budgetGroup])} : {})};
}

export function authorizeStewardCreation(params: JsonObject): JsonObject {
  const selected = normalizeStewardContext(params.context);
  const proposal = requireJsonObject(params.proposal, "creation proposal");
  const audience = requireJsonObject(proposal.context, "creation audience");
  const todoCreation = proposal.action_kind === "todo.create";
  const prMerge = proposal.action_kind === "pr.merge";
  const isExecutionControl = params.operation === "stop" || params.operation === "resume";
  ensure((todoCreation || prMerge || proposal.action_kind === "goal.create") && audience.kind === "manager"
    && audience.goal_id === "loopx-manager", "not a steward creation preview");
  ensure(fieldsMatch(audience, selected, ["binding_id", "source_ref", "provider_ref", "operator_ref", "project_ref"]),
    "confirmation audience changed");
  if (todoCreation) {
    const parameters = requireJsonObject(proposal.normalized_parameters, "Todo creation parameters");
    ensure((selected.goal_ids as string[]).includes(String(parameters.goal_id))
      && parameters.start_execution !== true && parameters.agent_id == null && !isExecutionControl,
      "Todo confirmation requires its currently granted Goal and cannot start execution");
  }
  if (prMerge) {
    const parameters = requireJsonObject(proposal.normalized_parameters, "PR merge parameters");
    ensure(!isExecutionControl && params.repository_authorized === true
      && isNonBlankStr(parameters.repository) && isSafeInt(parameters.pr_number, 1),
      "PR merge confirmation requires an authorized repository and exact PR target");
  }
  if (isExecutionControl) {
    const receipt = requireJsonObject(proposal.receipt, "commission receipt");
    const resources = requireJsonObject(receipt.resource_ids, "commission resources");
    ensure(proposal.status === "applied" && (selected.goal_ids as string[]).includes(String(resources.goal_id)),
      "execution control requires the exact adopted commission");
    return {authorized: true};
  }
  const expiry = Date.parse(String(audience.expires_at));
  const now = Date.parse(String(params.now));
  ensure(Number.isFinite(expiry) && Number.isFinite(now) && now <= expiry
    && now >= Date.parse(String(proposal.created_at)) - 300_000,
    "steward confirmation expired or timestamp invalid");
  return {authorized: true};
}
