/** Exact rollback of an owner-confirmed declaration grants no execution lease.
 * A stale binding can prevent lease acquisition; restoration must consequently
 * prove the old declaration without requiring a lease over the changed work. */
import type {JsonObject} from "../effect_program.ts";
import {acceptanceTask, goalAcceptanceTodoDigest, readGoalAcceptance} from "../goals/acceptance_contract.ts";
import {canonicalTaskLease} from "./task_lease_state.ts";
import {leaseIsActive} from "../work_items/task_lease_acquire.ts";
import {prepareUpdatedTodo, type CoordinationTodoUpdateInput} from "./todo_update_intent.ts";

export type AcceptanceRestoration =
  | {kind: "exact_restoration"}
  | {kind: "unavailable"; reason: string};

/** Runs only after actor/claim/exclusion admission and failed execution proof.
 * The caller's existing reviewed-update revision is checked by the transaction;
 * its operation receipt handles retries before current-state admission. */
export function acceptanceRestoration(
  head: JsonObject, todo: JsonObject, lease: JsonObject | undefined,
  input: CoordinationTodoUpdateInput,
): AcceptanceRestoration | null {
  const state = readGoalAcceptance(head, input.goal_id);
  if (!state?.enabled || acceptanceTask(input.todo_id, todo, state).state !== "stale") return null;
  const unavailable = (reason: string): AcceptanceRestoration => ({kind: "unavailable", reason});
  if (todo.role !== "agent" || !input.actor_agent_id || todo.claimed_by !== input.actor_agent_id) {
    return unavailable("Only the same claimed Agent may restore this declaration; ask the owner to review and rebind the Todo");
  }
  if (input.lease_idempotency_key != null || input.lease_expected_version != null) {
    return unavailable("Restoration cannot consume stale execution proof; release any active lease and retry without lease proof");
  }
  if (lease !== undefined && leaseIsActive(canonicalTaskLease(lease, input.goal_id, input.todo_id), input.now)) {
    return unavailable("Release the active execution lease before restoring the owner-confirmed declaration");
  }
  if (input.expected_provider_revision === undefined) {
    return unavailable("Inspect the current provider revision, then restore the exact prior text/wait with --update-operation-id and --update-expected-provider-revision; if the prior declaration is unknown, ask the owner to review and rebind this Todo");
  }
  // Restoration is deliberately narrower than ordinary planning. It cannot
  // change lifecycle, ownership, validator, effects, or acceptance criteria.
  if (input.clear_fields.length || Object.keys(input.patch).some(key => key !== "text") ||
      Object.keys(input.planning_intent ?? {}).some(key => !["resume_when", "clear_resume_when"].includes(key)) ||
      input.completion !== undefined || input.monitor_observation !== undefined ||
      input.completion_validation_revision !== undefined) {
    return unavailable("Exact restoration supports only the prior text/wait; ask the owner to review and rebind other work changes");
  }
  const {monitor_observation: _absentObservation, ...restorationInput} = input;
  const candidate = prepareUpdatedTodo(todo, restorationInput, head).next;
  const binding = state.bindings.find(item => item.todo_id === input.todo_id)!;
  if (goalAcceptanceTodoDigest(candidate) !== binding.todo_semantic_digest) {
    return unavailable("The supplied text/wait does not reconstruct the exact owner-confirmed declaration; ask the owner to review and rebind this Todo");
  }
  return {kind: "exact_restoration"};
}
