import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireBoolean, requireJsonObject, requireNonEmptyString, requireStringArray } from "../runtime_decode.ts";

type Recipient = { goal_id: string; agent_id?: string };

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
    blocked: recipients(Object.hasOwn(source, "blocked_targets") ? source.blocked_targets : [], "blocked context recipients", true),
  };
}

function matches(grant: Recipient, target: Recipient): boolean {
  return grant.goal_id === target.goal_id && (grant.agent_id === undefined || grant.agent_id === target.agent_id);
}

function granted(target: Recipient, targets: Recipient[], blocked: Recipient[]): boolean {
  return targets.some(row => matches(row, target)) && !blocked.some(row => matches(row, target));
}

/** Source provenance is verified by the provider adapter; registration is observed
 * afresh. A Goal target covers its current/future Agents, never other Goals,
 * evidence reads, protected operations or executor readiness.
 */
export function resolveSourceRecipients(params: JsonObject): JsonObject {
  const source = requireJsonObject(params.source, "source policy");
  const sender = requireNonEmptyString(params.sender_id, "verified source sender");
  if (!requireStringArray(source.sender_ids, "source senders").includes(sender)) {
    throw new EffectRuntimeRequestError("source sender is not authorized");
  }
  const { targets, blocked } = grants(source);
  const available = recipients(params.available, "registered recipients", true);
  const selected = available.filter(target => granted(target, targets, blocked));
  const unique = new Map(selected.map(row => [JSON.stringify([row.goal_id, row.agent_id]), row]));
  return { targets: [...unique.values()].sort((a, b) =>
    a.goal_id.localeCompare(b.goal_id) || a.agent_id!.localeCompare(b.agent_id!)) };
}

/** Plan one trusted-local configuration change; the adapter owns locking and IO.
 * Missing agent_id chooses the managed Goal, explicit Agent chooses an exception.
 */
export function configureSourceRecipient(params: JsonObject): JsonObject {
  const source = requireJsonObject(params.source, "source policy");
  const { targets, blocked } = grants(source);
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
    if (Object.hasOwn(source, "evidence_goal_ids")) {
      const readGoals = requireStringArray(source.evidence_goal_ids, "source read Goals");
      if (readGoals.some(id => !/^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/.test(id)) || !readGoals.includes(goal_id)) {
        throw new EffectRuntimeRequestError("target Goal is outside the channel read scope");
      }
    }
  }
  const before = agent_id === undefined
    ? targets.some(row => row.goal_id === goal_id && row.agent_id === undefined)
    : granted(target, targets, blocked);
  let updatedTargets = targets;
  let updatedBlocked = blocked;
  if (grant) {
    if (!targets.some(row => matches(row, target))) updatedTargets = [...targets, target];
    if (agent_id !== undefined) updatedBlocked = blocked.filter(row => !matches(target, row));
  } else if (agent_id === undefined) {
    // Revoking a Goal also revokes individually enrolled members of that Goal.
    updatedTargets = targets.filter(row => row.goal_id !== goal_id);
    updatedBlocked = blocked.filter(row => row.goal_id !== goal_id);
  } else {
    updatedTargets = targets.filter(row => !matches(target, row));
    if (updatedTargets.some(row => matches(row, target)) && !blocked.some(row => matches(row, target))) {
      updatedBlocked = [...blocked, target];
    }
  }
  const changed = JSON.stringify(updatedTargets) !== JSON.stringify(targets) ||
    JSON.stringify(updatedBlocked) !== JSON.stringify(blocked);
  // Preserve provider metadata when the semantic recipient set is unchanged.
  const updatedSource: JsonObject = { ...source };
  if (changed) {
    updatedSource.targets = updatedTargets;
    if (updatedBlocked.length) updatedSource.blocked_targets = updatedBlocked;
    else delete updatedSource.blocked_targets;
  }
  return { source: updatedSource, target, would_change: changed,
    granted_before: before, granted_after: grant,
    existing_target_count: targets.length, resulting_target_count: updatedTargets.length,
    includes_future_agents: agent_id === undefined && grant };
}
