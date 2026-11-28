/** Read lenses over one full Todo snapshot; never an execution grant. */
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject, optionalNonEmptyString, requireStringArray } from "../runtime_decode.ts";
import {
  TASK_LEASE_SCHEMA_VERSION,
  leaseIsActive,
  leaseEpoch,
  leaseVersion,
  TaskLeaseAcquireError,
  type LeaseRecord,
} from "../work_items/task_lease_acquire.ts";
import { parseIsoTimestamp } from "../runtime_timestamp.ts";
import { normalizeTodoAgent } from "../coordination/todo_agents.ts";
import { AuthorityStoreProtocolError } from "../coordination/authority_store_codec.ts";
import { normalizeWriteScopes } from "../work_items/write_scope_codec.ts";
import { normalizeTodoRepository } from "../todos/work_requirements.ts";

function laneFields(item: JsonObject, leaseActive: boolean | null): JsonObject {
  const priority = optionalNonEmptyString(item.priority, "priority");
  const normalizedPriority = priority && /^p[0-4]$/iu.test(priority.trim()) ? priority.trim().toUpperCase() : null;
  const actionKind = optionalNonEmptyString(item.action_kind, "action_kind");
  const taskDomain = optionalNonEmptyString(item.task_domain, "task_domain");
  return {
    todo_id: String(item.todo_id),
    priority: normalizedPriority,
    action_kind: actionKind ? actionKind.trim().toLowerCase() : null,
    task_domain: taskDomain ? taskDomain.trim().toLowerCase() : null,
    task_repository: normalizeTodoRepository(item.task_repository, "task_repository"),
    target_key: optionalNonEmptyString(item.target_key, "target_key"),
    required_write_scopes: normalizeWriteScopes(item.required_write_scopes ?? []),
    completion_validation_required: item.completion_validation_required === true,
    lease_active: leaseActive,
  };
}

/** Validate active lease credentials symmetrically for callers and peers. */
function activeLeaseDetails(
  lease: JsonObject | null,
  now: Date,
): { owner: string; epoch: number } | null {
  if (!leaseIsActive(lease, now)) return null;
  return {
    owner: normalizeTodoAgent(lease?.owner, "active task lease owner"),
    epoch: leaseEpoch(lease),
  };
}

/**
 * Validate active or released `task_lease_v0` credentials using canonical
 * task-lease rules so unknown schemas or corrupt status/fields degrade that
 * peer lane to `null` instead of masquerading as unleased (`false`).
 */
function parseKnownTaskLease(lease: JsonObject): LeaseRecord {
  if (lease.schema_version !== TASK_LEASE_SCHEMA_VERSION) {
    throw new TaskLeaseAcquireError("unknown task lease schema_version", "corrupt_lease");
  }
  if (lease.status !== "active" && lease.status !== "released") {
    throw new TaskLeaseAcquireError("unknown task lease status", "corrupt_lease");
  }
  if (
    lease.expires_at !== undefined && lease.expires_at !== null
    && (typeof lease.expires_at !== "string" || parseIsoTimestamp(lease.expires_at) === null)
  ) {
    throw new TaskLeaseAcquireError("lease expires_at must be a valid timestamp", "corrupt_lease");
  }
  normalizeTodoAgent(lease.owner, "active task lease owner");
  leaseEpoch(lease);
  leaseVersion(lease);
  return lease as LeaseRecord;
}

function peerLeaseActive(item: JsonObject, now: Date): boolean | null {
  if (item.lease_read_error != null) return null;
  if (item.lease == null) return false;
  try {
    const lease = parseKnownTaskLease(requireJsonObject(item.lease, "peer claim lease"));
    return activeLeaseDetails(lease, now) !== null;
  } catch {
    return null;
  }
}

export function sharedGoalWorkFacts(value: unknown, agent: string, observedAt: unknown): JsonObject {
  if (typeof observedAt !== "string" || !Number.isFinite(Date.parse(observedAt))) {
    throw new EffectRuntimeRequestError("shared goal work requires a valid observed_at");
  }
  const now = new Date(observedAt);
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError("shared goal work_items must be an array");
  const seen = new Set<string>();
  const current: JsonObject[] = [], unclaimed: JsonObject[] = [], peers: JsonObject[] = [];
  const inventory: JsonObject[] = [];
  for (const entry of value) {
    const item = requireJsonObject(entry, "shared goal work item");
    const id = optionalNonEmptyString(item.todo_id, "todo_id");
    if (!id) continue; // Historical Markdown can contain unaddressable rows.
    if (!/^todo_[a-z0-9_-]{3,64}$/.test(id)) throw new EffectRuntimeRequestError("invalid shared goal Todo id");
    if (seen.has(id)) throw new EffectRuntimeRequestError("duplicate Todo in shared goal snapshot");
    seen.add(id);
    const status = optionalNonEmptyString(item.status, "status") ?? "open";
    if (!["open", "done", "blocked", "deferred"].includes(status)) {
      throw new EffectRuntimeRequestError("invalid shared goal Todo status");
    }
    for (const field of ["done", "resume_ready"]) {
      if (item[field] != null && typeof item[field] !== "boolean") throw new EffectRuntimeRequestError(`${field} must be boolean`);
    }
    if (item.archive_state === "archive" || item.done === true || status !== "open" ||
      (item.resume_when && item.resume_ready !== true)) continue;
    const claim = optionalNonEmptyString(item.claimed_by, "claimed_by");
    const bound = optionalNonEmptyString(item.bound_agent, "bound_agent");
    const taskClass = optionalNonEmptyString(item.task_class, "task_class");
    inventory.push({todo_id: id, status: "open", task_class: taskClass, claimed_by: claim, bound_agent: bound});
    if (taskClass !== "advancement_task") continue;
    const excluded = requireStringArray(item.excluded_agents ?? [], "excluded_agents").includes(agent);
    if (claim && claim !== agent) peers.push(item);
    else if (!excluded) (claim ? current : unclaimed).push(item);
  }
  const activeCallerLease = new Map<string, boolean>();
  const claims = current.map((item) => {
    if (item.lease_read_error != null) {
      throw new EffectRuntimeRequestError(`cannot read selected claim lease: ${item.todo_id}`);
    }
    const lease = item.lease == null ? null : requireJsonObject(item.lease, "claim lease");
    let details: { owner: string; epoch: number } | null = null;
    try {
      details = activeLeaseDetails(lease, now);
    } catch (error) {
      if (error instanceof AuthorityStoreProtocolError) {
        throw new EffectRuntimeRequestError("active task lease has no valid owner");
      }
      if (error instanceof TaskLeaseAcquireError) {
        throw new EffectRuntimeRequestError(error.message);
      }
      throw error;
    }
    const epoch = details?.epoch ?? null;
    const owner = details?.owner ?? null;
    activeCallerLease.set(String(item.todo_id), details !== null);
    return {todo_id: item.todo_id, claimed_by: item.claimed_by, lease_epoch: epoch, lease_owner: owner};
  });
  return {
    frontier_counts: {current_agent_claimed_advancement_count: current.length,
      unclaimed_advancement_count: unclaimed.length, other_agent_claimed_advancement_count: peers.length},
    claims,
    caller_lanes: current.map((item) => laneFields(item, activeCallerLease.get(String(item.todo_id)) ?? false)),
    peer_frontiers: peers.map((item) => ({agent_id: item.claimed_by, ...laneFields(item, peerLeaseActive(item, now))})),
    unclaimed_eligible: unclaimed.map((item) => ({todo_id: item.todo_id, task_class: "advancement_task"})),
    peer_claimed_bound_todo_ids: peers.filter((item) => item.bound_agent === agent).map((item) => item.todo_id),
    goal_todo_inventory: inventory,
  };
}
