/** Plan the Todo half of an explicit lease/claim handover on the same head.
 * The lifecycle owner must still verify the lease tuple and commit both halves.
 *  */
import {carryContinuationAcrossClaimTransfer} from "./continuation_note.ts";
import type {JsonObject} from "../effect_program.ts";
import {registeredTodoMutationRejection} from "./todo_lifecycle_decision.ts";

export type LeaseClaimTransferPlan =
  | {status: "unchanged"; todo: JsonObject | undefined}
  | {status: "transfer"; todo: JsonObject}
  | {status: "rejected"; code: string};

export function planLeaseClaimTransfer(input: {
  requested: boolean; handoff_mode: string; todo: JsonObject | undefined;
  owner: string; new_owner: string | null; registered_agents: readonly string[];
  updated_at: string;
}): LeaseClaimTransferPlan {
  const {todo, owner, new_owner: target, registered_agents: registered} = input;
  if (!input.requested) return {status: "unchanged", todo};
  if (input.handoff_mode !== "hard_lease") return {status: "rejected", code: "claim_transfer_requires_hard_lease"};
  if (!todo || todo.archive_state !== "active" || todo.status !== "open" || todo.role !== "agent") {
    return {status: "rejected", code: "claim_transfer_requires_open_agent_todo"};
  }
  if (todo.claimed_by !== owner) return {status: "rejected", code: "claim_transfer_owner_mismatch"};
  if (typeof todo.removed_continuation_policy === "string" && todo.removed_continuation_policy) {
    return {status: "rejected", code: "removed_continuation_policy"};
  }
  const sourceRejection = registeredTodoMutationRejection(todo, owner, registered);
  if (sourceRejection) return {status: "rejected", code: sourceRejection};
  // Eligibility is evaluated against the proposed claim, with every other
  // binding/exclusion intact. Source lease proof is checked separately.
  const next = {...todo, claimed_by: target};
  const targetRejection = registeredTodoMutationRejection(next, target, registered);
  if (targetRejection) return {status: "rejected", code: targetRejection};
  if (target === owner) return {status: "unchanged", todo};
  return {status: "transfer", todo: carryContinuationAcrossClaimTransfer(todo,
    {...next, updated_at: input.updated_at, last_actor_agent_id: owner})};
}
