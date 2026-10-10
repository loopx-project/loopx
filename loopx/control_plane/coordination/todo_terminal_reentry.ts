import type {JsonObject} from "../effect_program.ts";
import {AuthorityStoreProtocolError, canonicalAuthoritySha256} from "./authority_store_codec.ts";
import type {CoordinationTodoTerminalLifecycleInput} from "./todo_terminal_lifecycle.ts";
import {registeredTodoMutationRejection} from "./todo_lifecycle_decision.ts";
import {evaluateTodoCompletionFence, TODO_COMPLETION_FENCE_REQUEST_SCHEMA} from "../todos/completion_fence.ts";
import {selectTodoCompletionState, TODO_COMPLETION_STATE_REQUEST_SCHEMA} from "../todos/completion_state.ts";
import {resolveTodoCompletionPolicy} from "../todos/completion_policy.ts";

export function isLifecycleCloseout(input: CoordinationTodoTerminalLifecycleInput): boolean {
  return input.command === "complete" && input.requested_no_followup &&
    input.requested_completion_identity_source === "lifecycle_reentry";
}

/** A migrated completion is already authoritative. Close only its continuation;
 * never manufacture a missing validation receipt or repeat the original work. */
export function prepareTerminalReentry(todo: JsonObject, input: CoordinationTodoTerminalLifecycleInput,
  hasActiveLease: boolean): JsonObject | null {
  const reject = (reason: string): never => {throw new AuthorityStoreProtocolError(reason);};
  const authority = registeredTodoMutationRejection(todo, input.actor_agent_id, input.registered_agents);
  if (authority !== null) reject(authority);
  if (hasActiveLease) reject("Lifecycle closeout cannot change a Todo with an active execution lease");
  if (input.user_update !== undefined || input.clear_claim || input.decision_outcome !== null ||
      input.successor_intents.length > 0 || input.linked_successor_todo_ids.length > 0 ||
      input.validation_receipt !== null || input.validation_declaration !== null ||
      input.completion_result != null || input.goal_acceptance_source_binding != null ||
      input.goal_acceptance_validation_receipts != null) {
    reject("Lifecycle closeout cannot alter completion evidence, ownership, results or successors");
  }
  for (const field of ["note", "evidence", "reason"] as const) {
    if (input[field] !== null && input[field] !== todo[field]) {
      reject(`Lifecycle closeout must preserve the original ${field}`);
    }
  }
  if (input.completion_policy_request !== null) {
    const policy = resolveTodoCompletionPolicy(input.completion_policy_request);
    if ((policy.effective_claimed_by !== null && policy.effective_claimed_by !== todo.claimed_by) ||
        policy.effective_next_claimed_by !== null || policy.effective_next_excluded_agents.length > 0 ||
        policy.linked_successor_id !== null || policy.self_merged ||
        input.completion_policy_request.next_agent_todo != null ||
        input.completion_policy_request.next_continuation_policy != null ||
        canonicalAuthoritySha256(policy.registered_agents) !== canonicalAuthoritySha256(input.registered_agents)) {
      reject("Lifecycle closeout cannot change completion policy");
    }
  }
  const fence = evaluateTodoCompletionFence({schema_version: TODO_COMPLETION_FENCE_REQUEST_SCHEMA,
    projection_source: "materialized", goal_id: input.goal_id, todo_id: input.todo_id, todo,
    requested_no_followup: true, requested_completion_turn_key: input.requested_completion_turn_key,
    requested_completion_identity_source: input.requested_completion_identity_source});
  if (fence.outcome === "replay") return null;
  const state = selectTodoCompletionState({schema_version: TODO_COMPLETION_STATE_REQUEST_SCHEMA, todo,
    requested_no_followup: true, has_successor: false, completion_identity_source: "lifecycle_reentry"});
  return {...todo, no_followup: true, completion_continuation: state.continuation,
    completion_recovery: state.recovery, completion_turn_key: input.requested_completion_turn_key};
}
