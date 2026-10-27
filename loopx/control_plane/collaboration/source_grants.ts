import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireBoolean, requireJsonObject, requireNonEmptyString, requireStringArray } from "../runtime_decode.ts";
import { authorityUnicodeCompare } from "../coordination/authority_store_codec.ts";
import { normalizeStewardGoalScope, resolveConversationScope } from "./conversation_scope.ts";
import {normalizeOriginalConversationRecipient} from "./semantic_request.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {parseExactGoalRef} from "../goals/goal_instance_identity.ts";

type Recipient = { goal_id: string; agent_id?: string };
type LocalDeliveryScope = "all_registered" | "selected";

function originalConversationGrant(params: JsonObject): JsonObject {
  const requester = requireJsonObject(params.requester, "original requester");
  const parsed = parseExactGoalRef(requester.goal_ref);
  const agent = requireNonEmptyString(requester.agent_id, "requesting Agent");
  if (parsed.kind !== "parsed" || Object.keys(requester).sort().join(",") !== "agent_id,goal_ref"
      || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/.test(agent)) {
    throw new EffectRuntimeRequestError("original conversation requires an exact registered requester");
  }
  return {requester, source_id: requireNonEmptyString(params.original_source_id, "original source identity"),
    parent_request_id: requireNonEmptyString(params.parent_request_id, "original parent request"),
    recipient: normalizeOriginalConversationRecipient(params.recipient)};
}
function originalConversationTargets(policy: JsonObject): JsonObject[] {
  if (policy.schema_version !== "loopx_manager_context_policy_v1") throw new EffectRuntimeRequestError("invalid source policy");
  requireJsonObject(policy.sources, "context sources");
  const rows = policy.original_conversation_targets ?? [];
  if (!Array.isArray(rows) || rows.length > 128) throw new EffectRuntimeRequestError("invalid original conversation grants");
  return rows.map(value => {
    const row = requireJsonObject(value, "original conversation grant");
    return originalConversationGrant({...row, original_source_id: row.source_id});
  });
}

export function inspectOriginalConversationGrants(params: JsonObject): JsonObject {
  const policy = requireJsonObject(params.policy, "source policy");
  const session = params.session_id;
  return {policy_sha256: canonicalAuthoritySha256(policy), grants: originalConversationTargets(policy)
    .filter(row => session === undefined || (row.recipient as JsonObject).session_id === session)};
}

/** Registered-recipient defaults never enroll this explicit recipient kind. */
export function requireOriginalConversationGrant(params: JsonObject): JsonObject {
  const grant = originalConversationGrant(params);
  const policy = requireJsonObject(params.policy, "source policy");
  if (params.parent_access_verified !== true || !originalConversationTargets(policy).some(row =>
    canonicalAuthoritySha256(row) === canonicalAuthoritySha256(grant))) {
    throw new EffectRuntimeRequestError("original source has no explicit grant to this conversation recipient");
  }
  return grant;
}

export function configureOriginalConversationGrant(params: JsonObject): JsonObject {
  const policy = requireJsonObject(params.policy, "source policy"), rows = originalConversationTargets(policy);
  const grant = originalConversationGrant(params), enable = requireBoolean(params.grant, "original conversation grant");
  if (params.expected_policy_sha256 !== canonicalAuthoritySha256(policy)) {
    throw new EffectRuntimeRequestError("source policy changed; refresh before changing the conversation grant");
  }
  if (enable && (params.parent_access_verified !== true
      || canonicalAuthoritySha256(grant.recipient) !== canonicalAuthoritySha256(params.current_recipient))) {
    throw new EffectRuntimeRequestError("source or original conversation recipient is unavailable");
  }
  const key = canonicalAuthoritySha256(grant), present = rows.some(row => canonicalAuthoritySha256(row) === key);
  const next = enable ? present ? rows : [...rows, grant] : rows.filter(row => canonicalAuthoritySha256(row) !== key);
  if (next.length > 128) throw new EffectRuntimeRequestError("original conversation grant capacity exceeded");
  const updated = present === enable ? policy : {...policy, original_conversation_targets: next};
  return {policy: updated, would_change: present !== enable,
    policy_sha256: canonicalAuthoritySha256(updated), grant};
}

/** Retire only this exact native conversation's grants. Historical requests and
 * receipts remain readable; neither another App nor a legacy source is revoked.
 */
export function revokeConversationSources(params: JsonObject): JsonObject {
  const id = requireNonEmptyString(params.binding_id, "conversation binding identity");
  if (!/^[a-f0-9]{24}$/.test(id)) throw new EffectRuntimeRequestError("invalid conversation binding identity");
  const policy = requireJsonObject(params.policy, "source policy");
  if (policy.schema_version !== "loopx_manager_context_policy_v1") throw new EffectRuntimeRequestError("invalid source policy");
  const sources = requireJsonObject(policy.sources, "context sources");
  let changed = false;
  const retired = Object.fromEntries(Object.entries(sources).map(([key, value]) => {
    const sourceBinding = /^manager\.external\.native\.([a-f0-9]{24})\.[a-f0-9]{24}$/.exec(key)?.[1];
    if (sourceBinding !== id) return [key, value];
    const source = requireJsonObject(value, "conversation source");
    // Keep an explicit empty read scope: removing the row would restore the
    // historical evidence-read fallback before the binding write completes.
    const next = {...source, sender_ids: [], evidence_goal_scope: "selected", evidence_goal_ids: []};
    changed ||= JSON.stringify(source) !== JSON.stringify(next);
    return [key, next];
  }));
  return {policy: {...policy, sources: retired}, would_change: changed};
}

/** A live read grant is independent of delivery and of the bounded Turn index.
 * Missing mode preserves historical exact lists and the connection's fallback.
 * Only a trusted local configuration operation may select all registered Goals.
 */
export function resolveSourceEvidenceGoals(params: JsonObject): JsonObject {
  const source = requireJsonObject(params.source, "source policy");
  const mode = Object.hasOwn(source, "evidence_goal_scope") ? source.evidence_goal_scope : "selected";
  if (mode !== "all_registered" && mode !== "selected") throw new EffectRuntimeRequestError("invalid evidence Goal scope");
  const selected = Object.hasOwn(source, "evidence_goal_ids")
    ? normalizeStewardGoalScope(source.evidence_goal_ids) : null;
  return {goal_scope: mode, goal_ids: mode === "all_registered"
    ? normalizeStewardGoalScope(params.available_goal_ids) : selected};
}

export function configureSourceEvidenceScope(params: JsonObject): JsonObject {
  const source = requireJsonObject(params.source, "source policy");
  const mode = params.goal_scope;
  if (mode !== "all_registered" && mode !== "selected") throw new EffectRuntimeRequestError("invalid evidence Goal scope");
  const ids = normalizeStewardGoalScope(params.goal_ids);
  const available = normalizeStewardGoalScope(params.available_goal_ids);
  if (ids.some(id => !available.includes(id))) throw new EffectRuntimeRequestError("every read Goal must be registered");
  const next = {...source, evidence_goal_scope: mode, evidence_goal_ids: ids};
  return {source: next, goal_scope: mode, goal_ids: mode === "all_registered" ? available : ids,
    would_change: JSON.stringify(next) !== JSON.stringify(source)};
}

function recipients(value: unknown, label: string, exact: boolean): Recipient[] {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an array`);
  return value.map(row => {
    const item = requireJsonObject(row, label);
    const goal_id = requireNonEmptyString(item.goal_id, `${label} Goal`);
    // Only absence means the entire Goal. A malformed Agent never widens scope.
    if (!exact && !Object.hasOwn(item, "agent_id")) return { goal_id };
    return { goal_id, agent_id: requireNonEmptyString(item.agent_id, `${label} Agent`) };
  });
}

function grants(source: JsonObject) {
  return {
    targets: recipients(Object.hasOwn(source, "targets") ? source.targets : [], "context delivery targets", false),
    blocked: recipients(Object.hasOwn(source, "blocked_targets") ? source.blocked_targets : [], "blocked context recipients", false),
    scope: localScope(source),
  };
}

function localScope(source: JsonObject): LocalDeliveryScope {
  const scope = Object.hasOwn(source, "local_delivery_scope") ? source.local_delivery_scope : "all_registered";
  if (scope !== "all_registered" && scope !== "selected") {
    throw new EffectRuntimeRequestError("local delivery scope must be all_registered or selected");
  }
  return scope;
}

/** Trusted-local scope configuration preserves explicit recipient revocations. */
export function configureSourceScope(params: JsonObject): JsonObject {
  const source = requireJsonObject(params.source, "source policy");
  grants(source);
  const scope = localScope({local_delivery_scope: params.local_delivery_scope});
  const senders = requireStringArray(source.sender_ids, "source senders");
  if (!senders.length || senders.some(sender => !sender.trim())) {
    throw new EffectRuntimeRequestError("external channel has no valid sender grant");
  }
  const next = {...source, local_delivery_scope: scope};
  return {source: next, local_delivery_scope: scope,
    would_change: JSON.stringify(next) !== JSON.stringify(source)};
}

function matches(grant: Recipient, target: Recipient): boolean {
  return grant.goal_id === target.goal_id && (grant.agent_id === undefined || grant.agent_id === target.agent_id);
}

function granted(target: Recipient, targets: Recipient[], blocked: Recipient[], scope: LocalDeliveryScope): boolean {
  return (scope === "all_registered" || targets.some(row => matches(row, target))) &&
    !blocked.some(row => matches(row, target));
}

/** Source provenance is verified by the provider adapter; registration is observed
 * afresh on this host. Authorized sources default to all registered local
 * recipients; selected scope opts into enrollment. Neither scope grants remote
 * delivery, evidence reads, protected operations or executor readiness.
 */
export function resolveSourceRecipients(params: JsonObject): JsonObject {
  const source = requireJsonObject(params.source, "source policy");
  const sender = requireNonEmptyString(params.sender_id, "verified source sender");
  if (!requireStringArray(source.sender_ids, "source senders").includes(sender)) {
    throw new EffectRuntimeRequestError("source sender is not authorized");
  }
  const { targets, blocked, scope } = grants(source);
  const available = recipients(params.available, "registered recipients", true);
  const selected = available.filter(target => granted(target, targets, blocked, scope));
  const unique = new Map(selected.map(row => [JSON.stringify([row.goal_id, row.agent_id]), row]));
  return { targets: [...unique.values()].sort((a, b) =>
    a.goal_id.localeCompare(b.goal_id) || a.agent_id!.localeCompare(b.agent_id!)) };
}

/** Combine the canonical conversation scope with observed recipient grants.
 * Python supplies registry/policy IO and the original source identity. Neither a
 * project workspace grant nor an unverified source becomes delivery authority.
 */
export function resolveSourceContextAuthority(params: JsonObject): JsonObject {
  const conversation = requireJsonObject(params.conversation, "source conversation");
  const scope = resolveConversationScope(conversation);
  if (!Array.isArray(params.available)) throw new EffectRuntimeRequestError("registered recipients unavailable");
  let selected: Required<Recipient>[];
  let sourceId = params.source_id;
  if (scope.private_conversation && conversation.origin === "web") {
    // Match the registry observation's historical treatment of unreadable rows.
    const available = params.available.filter((raw): raw is JsonObject & Required<Recipient> =>
      raw !== null && typeof raw === "object" && !Array.isArray(raw)
      && typeof raw.goal_id === "string" && typeof raw.agent_id === "string");
    selected = available.filter(row => scope.goal_ids === null
      || scope.goal_ids.some(goal => goal === row.goal_id));
  } else {
    if (scope.kind !== "external_audience" || params.source === null) {
      return {mode: "unavailable", targets: []};
    }
    try {
      validateSourceIngress({session: conversation, turn: params.turn, ingress: params.ingress});
      const ingress = requireJsonObject(params.ingress, "source ingress");
      selected = resolveSourceRecipients({source: params.source,
        sender_id: ingress.sender_id, available: params.available}).targets as Required<Recipient>[];
      sourceId = ingress.source_id;
    } catch (error) {
      if (!(error instanceof EffectRuntimeRequestError)) throw error;
      return {mode: "unavailable", targets: []};
    }
  }
  const targets = new Map(selected.map(row => [JSON.stringify([row.goal_id, row.agent_id]),
    {goal_id: row.goal_id, agent_id: row.agent_id}]));
  // The combined authority catalog has always used code-point ordering, even
  // though the underlying source-policy view uses locale ordering.
  return {mode: "context_only", source_id: requireNonEmptyString(sourceId, "original source identity"),
    targets: [...targets.values()].sort((a, b) => authorityUnicodeCompare(a.goal_id, b.goal_id)
      || authorityUnicodeCompare(a.agent_id, b.agent_id))};
}

/** Verify the host's immutable ingress against its canonical Session/Turn.
 * IO supplies digests, not a second provenance rule or model-authored identity.
 * Local continuation retains the binding's narrow audience and source policy.
 */
export function validateSourceIngress(params: JsonObject): JsonObject {
  const session = requireJsonObject(params.session, "source Session");
  const turn = requireJsonObject(params.turn, "source Turn");
  const ingress = requireJsonObject(params.ingress, "source ingress");
  const identity: [string, unknown][] = [["session_id", session.session_id], ["client_turn_id", turn.client_turn_id],
    ["channel", session.channel_id], ["message_digest", turn.message_digest]];
  for (const [field, expected] of identity) {
    if (requireNonEmptyString(ingress[field], `ingress ${field}`) !==
        requireNonEmptyString(expected, `source ${field}`)) throw new EffectRuntimeRequestError("source mismatch");
  }
  const origin = turn.origin;
  const scope = resolveConversationScope({...session, origin});
  if (origin !== "lark" && origin !== "external" && origin !== "web"
      || ingress.origin !== undefined && ingress.origin !== origin
      || origin === "external" && !scope.bound_steward
      || origin === "web" && (!scope.bound_steward || ingress.origin !== "web")) {
    throw new EffectRuntimeRequestError("source mismatch");
  }
  const sender = requireNonEmptyString(ingress.sender_id, "verified source sender");
  requireNonEmptyString(ingress.source_id, "source identity");
  if (origin === "web" && sender !== requireJsonObject(session.steward_context, "bound steward").operator_ref) {
    throw new EffectRuntimeRequestError("source mismatch");
  }
  return {verified: true};
}

function executionBinding(raw: unknown): JsonObject {
  const row = requireJsonObject(raw, "source execution binding");
  const keys = ["goal_id", "agent_id", "requester_agent_id", "binding_id"];
  if (Object.keys(row).some(key => !keys.includes(key)) || keys.some(key => !Object.hasOwn(row, key))) {
    throw new EffectRuntimeRequestError("exact source execution binding fields required");
  }
  const binding: JsonObject = {};
  for (const key of keys) {
    const id = requireNonEmptyString(row[key], key);
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/.test(id)) {
      throw new EffectRuntimeRequestError("invalid source execution binding identity");
    }
    binding[key] = id;
  }
  if (binding.requester_agent_id === binding.agent_id) {
    throw new EffectRuntimeRequestError("execution requester must be an independent registered Agent");
  }
  return binding;
}

/** A separate, exact operator grant for existing governed work. Context delivery
 * alone never authorizes launch; neither models nor sources choose host profiles.
 * required_binding lets an executing worker recheck its original exact grant.
 */
export function sourceExecutionBindings(params: JsonObject): JsonObject {
  const source = requireJsonObject(params.source, "source policy");
  const authorized = resolveSourceRecipients(params).targets as Recipient[];
  const available = recipients(params.available, "registered recipients", true);
  const rows = Object.hasOwn(source, "execution_bindings") ? source.execution_bindings : [];
  if (!Array.isArray(rows) || rows.length > 100) {
    throw new EffectRuntimeRequestError("bounded source execution bindings required");
  }
  const bindings = rows.map(executionBinding);
  const identities = bindings.map(row => JSON.stringify([row.goal_id, row.agent_id, row.binding_id]));
  if (new Set(identities).size !== identities.length) {
    throw new EffectRuntimeRequestError("ambiguous source execution binding");
  }
  const permitted = bindings.filter(row =>
    authorized.some(target => target.goal_id === row.goal_id && target.agent_id === row.agent_id)
    && available.some(target => target.goal_id === row.goal_id && target.agent_id === row.requester_agent_id));
  if (Object.hasOwn(params, "required_binding")) {
    const required = executionBinding(params.required_binding);
    if (!permitted.some(row => Object.keys(required).every(key => row[key] === required[key]))) {
      throw new EffectRuntimeRequestError("original source execution binding is no longer authorized");
    }
  }
  return {bindings: permitted};
}

/** Plan one trusted-local configuration change; the adapter owns locking and IO.
 * Missing agent_id chooses the managed Goal, explicit Agent chooses an exception.
 */
export function configureSourceRecipient(params: JsonObject): JsonObject {
  const source = requireJsonObject(params.source, "source policy");
  const { targets, blocked, scope } = grants(source);
  const goal_id = requireNonEmptyString(params.goal_id, "delivery target Goal");
  const agent_id = params.agent_id === null || params.agent_id === undefined
    ? undefined : requireNonEmptyString(params.agent_id, "delivery target Agent");
  const target: Recipient = agent_id === undefined ? { goal_id } : { goal_id, agent_id };
  const grant = requireBoolean(params.grant, "delivery grant");
  const available = recipients(params.available, "registered recipients", true);
  const activeGoals = requireStringArray(params.active_goal_ids, "active Goals");
  if (grant) {
    const senders = requireStringArray(source.sender_ids, "source senders");
    if (!senders.length || senders.some(sender => !sender.trim())) {
      throw new EffectRuntimeRequestError("external channel has no valid sender grant");
    }
    if (!activeGoals.includes(goal_id) || (agent_id !== undefined &&
        !available.some(row => matches(target, row)))) {
      throw new EffectRuntimeRequestError("delivery target must be a registered Agent or all Agents in an active Goal");
    }
    if (scope === "selected" && Object.hasOwn(source, "evidence_goal_ids")) {
      const readGoals = requireStringArray(source.evidence_goal_ids, "source read Goals");
      if (readGoals.some(id => !/^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/.test(id)) || !readGoals.includes(goal_id)) {
        throw new EffectRuntimeRequestError("target Goal is outside the channel read scope");
      }
    }
  }
  const before = agent_id === undefined
    ? (scope === "all_registered" || targets.some(row => row.goal_id === goal_id && row.agent_id === undefined)) &&
      !blocked.some(row => row.goal_id === goal_id && row.agent_id === undefined)
    : granted(target, targets, blocked, scope);
  let updatedTargets = targets;
  let updatedBlocked = blocked;
  if (grant) {
    if (agent_id !== undefined && blocked.some(row => row.goal_id === goal_id && row.agent_id === undefined)) {
      throw new EffectRuntimeRequestError("restore the Goal delivery grant before restoring an individual Agent");
    }
    if (scope === "selected" && !targets.some(row => matches(row, target))) updatedTargets = [...targets, target];
    // Regrant only this scope; a Goal regrant preserves individual revocations.
    updatedBlocked = blocked.filter(row => !(row.goal_id === goal_id && row.agent_id === agent_id));
  } else if (agent_id === undefined) {
    updatedTargets = targets.filter(row => row.goal_id !== goal_id);
    if (scope === "all_registered" && !blocked.some(row => row.goal_id === goal_id && row.agent_id === undefined)) {
      updatedBlocked = [...blocked, target];
    }
    // Preserve individual exceptions when the Goal is restored later.
  } else {
    updatedTargets = targets.filter(row => !matches(target, row));
    // Record the exact revocation even if a Goal block or missing enrollment
    // already disables delivery. Restoring that broader scope must not erase it.
    if (!blocked.some(row => row.goal_id === goal_id && row.agent_id === agent_id)) {
      updatedBlocked = [...blocked, target];
    }
  }
  const changed = JSON.stringify(updatedTargets) !== JSON.stringify(targets) ||
    JSON.stringify(updatedBlocked) !== JSON.stringify(blocked);
  // Preserve provider metadata when the declared grants and exceptions are unchanged.
  const updatedSource: JsonObject = { ...source };
  if (changed) {
    updatedSource.targets = updatedTargets;
    if (updatedBlocked.length) updatedSource.blocked_targets = updatedBlocked;
    else delete updatedSource.blocked_targets;
  }
  return { source: updatedSource, target, local_delivery_scope: scope, would_change: changed,
    granted_before: before, granted_after: grant,
    existing_target_count: targets.length, resulting_target_count: updatedTargets.length,
    includes_future_agents: agent_id === undefined && grant };
}
