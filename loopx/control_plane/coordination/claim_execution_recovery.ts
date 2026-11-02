/** Reuse ordinary claim admission for actionable, effect-free expiry guidance. */
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore} from "./authority_store.ts";
import type {AuthoritySourceCheck} from "./authority_source.ts";
import {canonicalAuthoritySha256} from "./authority_store_codec.ts";
import {ClaimExecutionUnavailable} from "./claim_execution.ts";
import {executeCoordinationTodoClaim, type CoordinationTodoClaimInput} from "./todo_claim.ts";

type RecoveryInput = Pick<CoordinationTodoClaimInput, "goal_id" | "todo_id" | "actor_agent_id" |
  "registered_agents" | "expected_role" | "operation_id" | "now"> & {
    claim_operation_id: string; expected_provider_revision: string;
  };

export async function claimExecutionRecovery(store: AuthorityStore, input: RecoveryInput,
  command: "update" | "complete", error: unknown,
  authoritySourcesCurrent: AuthoritySourceCheck): Promise<JsonObject | undefined> {
  if (!(error instanceof ClaimExecutionUnavailable)) return undefined;
  if (!error.sameExecutionExpired || input.actor_agent_id === null) {
    return {action: "reconcile_lease_owner", execution_authority_granted: false,
      reason: "Inspect this Todo and reconcile its original claim and current ownership before preparing a new claim. Do not replace the original execution proof with another grant's key or version.",
      inspect: {command: "loopx todo list", cli_args: ["todo", "list", "--goal-id", input.goal_id,
        "--todo-id", input.todo_id]}};
  }
  // Pin the failed observation. A newer head must not make another execution
  // look like recovery of the expired one. Real claim still rechecks its CAS.
  const preview = await executeCoordinationTodoClaim(store, {
    goal_id: input.goal_id, todo_id: input.todo_id, actor_agent_id: input.actor_agent_id,
    claimed_by: input.actor_agent_id, expected_role: input.expected_role,
    registered_agents: input.registered_agents, manage_execution: true, dry_run: true,
    operation_id: `claim-recovery-preview-${canonicalAuthoritySha256({operation_id: input.operation_id,
      claim_operation_id: input.claim_operation_id, command}).slice(0, 32)}`,
    expected_provider_revision: input.expected_provider_revision, now: input.now,
  }, authoritySourcesCurrent).catch(() => null);
  // An optional observation failure must not hide the original proof refusal.
  if (preview === null) return undefined;
  if (preview.dry_run === true && ["planned", "no_change"].includes(String(preview.status))) {
    return {action: "claim_task", lease_state: "expired", execution_authority_granted: false,
      reason: "The original execution expired. Explicitly run a new ordinary Todo claim, then retry the unchanged operation with its returned claim_operation_id. Do not reuse the expired claim id. Claim rechecks current ownership and eligibility; this preview grants no execution authority.",
      claim: {command: "loopx todo claim", cli_args: ["todo", "claim", "--goal-id", input.goal_id,
        "--todo-id", input.todo_id, "--agent-id", input.actor_agent_id, "--claimed-by", input.actor_agent_id]},
      retry: {command: `loopx todo ${command}`, requires_flags: ["--claim-operation-id"],
        reference_source: "claim_operation_id_from_claim"}};
  }
  if (typeof preview.reason_code === "string") {
    return {action: "resolve_acquire_rejection", execution_authority_granted: false,
      reason_code: preview.reason_code, reason: typeof preview.reason === "string"
        ? preview.reason : "Task preparation is not currently available."};
  }
  return undefined;
}
