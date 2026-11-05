import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString,
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
  requireStringLiteral,
} from "../runtime_decode.ts";

export const EXECUTION_PREPARATION_FACTS_SCHEMA_VERSION =
  "execution_preparation_facts_v0";
export const EXECUTION_PREPARATION_SCHEMA_VERSION =
  "execution_preparation_v0";

const HANDOFF_MODES = ["soft_claim", "hard_lease"] as const;
const LEASE_STATUSES = ["active", "released", "unknown"] as const;

function leaseFact(value: unknown, label: string): JsonObject {
  const lease = requireJsonObject(value, label);
  const status = requireStringLiteral(lease.status, LEASE_STATUSES, `${label}.status`);
  const active = requireBoolean(lease.active, `${label}.active`);
  const owner = optionalNonEmptyString(lease.owner, `${label}.owner`);
  if (active && status !== "active") {
    throw new EffectRuntimeRequestError(`${label}.active requires status=active`);
  }
  return {status, active, ...(owner === null ? {} : {owner})};
}

function claimAction(goalId: string, todoId: string, agentId: string): JsonObject {
  return {
    command: "loopx todo claim",
    cli_args: [
      "todo", "claim", "--goal-id", goalId, "--todo-id", todoId,
      "--agent-id", agentId, "--claimed-by", agentId,
    ],
    host_execution_allowed: true,
    postcondition: "rerun quota should-run and require execution_preparation.state=ready",
    safety: "ordinary managed claim atomically rechecks current ownership, write scopes, workspace isolation, and lease generation",
  };
}

/** Decide whether the selected Todo can execute under its current hard lease.
 *
 * This is a read-model decision.  It never acquires a lease.  A capable Host
 * may execute the returned ordinary claim before starting the model; every
 * authority and workspace check remains owned by that atomic claim.
 */
export function projectExecutionPreparation(value: unknown): JsonObject {
  const request = requireJsonObject(value, "execution preparation facts");
  if (request.schema_version !== EXECUTION_PREPARATION_FACTS_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `execution_preparation_facts.schema_version must be ${EXECUTION_PREPARATION_FACTS_SCHEMA_VERSION}`,
    );
  }
  const goalId = requireNonEmptyString(request.goal_id, "goal_id");
  const agentId = requireNonEmptyString(request.agent_id, "agent_id");
  const handoffMode = requireStringLiteral(request.handoff_mode, HANDOFF_MODES, "handoff_mode");
  const authorityAvailable = requireBoolean(request.authority_available, "authority_available");
  const selected = requireJsonObject(request.selected_todo, "selected_todo");
  const todoId = requireNonEmptyString(selected.todo_id, "selected_todo.todo_id");
  const claimedBy = optionalNonEmptyString(selected.claimed_by, "selected_todo.claimed_by");
  const status = optionalNonEmptyString(selected.status, "selected_todo.status");
  const rawLeases = request.leases;
  if (!Array.isArray(rawLeases)) {
    throw new EffectRuntimeRequestError("leases must be an array");
  }
  const leases = rawLeases.map((lease, index) => leaseFact(lease, `leases[${index}]`));
  const base = {
    schema_version: EXECUTION_PREPARATION_SCHEMA_VERSION,
    todo_id: todoId,
    agent_id: agentId,
    handoff_mode: handoffMode,
  };

  if (!authorityAvailable) {
    return {...base, state: "authority_unavailable", delivery_allowed: false,
      reason_code: "canonical_authority_unavailable",
      reason: "Canonical Todo and lease authority is unavailable; do not start delivery work."};
  }
  if (status !== "open") {
    return {...base, state: "blocked_by_conflict", delivery_allowed: false,
      reason_code: "selected_todo_not_open",
      reason: "The selected Todo is no longer open; select current work before execution."};
  }
  if (claimedBy !== null && claimedBy !== agentId) {
    return {...base, state: "blocked_by_conflict", delivery_allowed: false,
      reason_code: "todo_claimed_by_other_agent", owner: claimedBy,
      reason: "The selected Todo is claimed by another agent; do not take over its execution."};
  }
  if (handoffMode === "soft_claim") {
    return {...base, state: "not_required", delivery_allowed: true,
      reason_code: "soft_claim_has_no_execution_lease"};
  }

  const active = leases.filter(lease => lease.active === true);
  if (active.length > 1) {
    return {...base, state: "blocked_by_conflict", delivery_allowed: false,
      reason_code: "multiple_active_task_leases",
      reason: "Multiple active leases were observed for one Todo; repair canonical authority before execution."};
  }
  if (active.length === 1) {
    const owner = optionalNonEmptyString(active[0].owner, "active lease owner");
    if (owner !== agentId) {
      return {...base, state: "blocked_by_conflict", delivery_allowed: false,
        reason_code: "active_lease_owned_by_other_agent", ...(owner ? {owner} : {}),
        reason: "The selected Todo has an active lease owned by another agent; do not take over its execution."};
    }
    return {...base, state: "ready", delivery_allowed: true,
      reason_code: "matching_active_task_lease"};
  }

  const prior = leases[0];
  const reasonCode = prior?.status === "released"
    ? "task_lease_released"
    : prior?.status === "active"
    ? "task_lease_expired"
    : "task_lease_missing";
  return {...base, state: "claim_required", delivery_allowed: false,
    reason_code: reasonCode,
    reason: "Prepare the selected Todo with one ordinary managed claim before delivery work.",
    claim: claimAction(goalId, todoId, agentId)};
}
