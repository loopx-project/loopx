/** A receipt for receiving context under an existing lease. This does not
 * acquire, renew or transfer either a claim or an execution lease. */
import type {JsonObject} from "../effect_program.ts";
import {acceptanceWorkGuard} from "../goals/acceptance_contract.ts";
import type {AuthorityStore} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthoritySha256} from "./authority_store_codec.ts";
import {CoordinationCommandReceipt, commandReceiptResult} from "./command_receipt.ts";
import {indexCoordinationProjection, validateCoordinationTodoReadModel} from "./coordination_projection.ts";
import {computeContinuationTodoFacts, validateContinuationNote} from "./continuation_note.ts";
import {evaluateCanonicalTaskLeaseProof, type TaskLeaseProof} from "./task_lease_proof.ts";
import {todoExecutionDependencyRejection} from "./todo_execution_dependency.ts";

interface ExecutionInput {
  goal_id: string; todo_id: string; agent_id: string;
  registered_agents: readonly string[]; proof: TaskLeaseProof | null;
}

export function continuationExecutionAuthority(head: JsonObject, input: ExecutionInput) {
  validateCoordinationTodoReadModel(head, input.goal_id);
  const index = indexCoordinationProjection(head, input.goal_id);
  const todo = index.todos.get(input.todo_id);
  if (!todo) return {allowed: false, reason_code: "todo_not_found"};
  const guard = acceptanceWorkGuard(head, input.goal_id, input.todo_id);
  if (guard !== null && !guard.allowed) return {allowed: false, reason_code: String(guard.reason_code)};
  const now = new Date();
  const decision = evaluateCanonicalTaskLeaseProof({todo, lease: index.leases.get(input.todo_id),
    handoff_mode: String(head.handoff_mode ?? "legacy"), actor_agent_id: input.agent_id,
    registered_agents: input.registered_agents, lease_idempotency_key: input.proof?.idempotency_key ?? null,
    lease_expected_version: input.proof?.expected_version ?? null, now});
  if (decision.outcome !== "apply") return {allowed: false, reason_code: String(decision.code)};
  const dependency = todoExecutionDependencyRejection(index.todos, input.todo_id, now);
  return dependency === null ? {allowed: true, reason_code: String(decision.code)}
    : {allowed: false, reason_code: dependency.code};
}

export async function sealLeasedContinuationAdoption(store: AuthorityStore, input: ExecutionInput & {
  operation_id: string; session_id: string; expected_provider_revision: string; note_facts: string;
}): Promise<JsonObject> {
  const resultSchema = "loopx_continuation_adoption_result_v0";
  const failure = (reason_code: string, reason: string) =>
    ({schema_version: resultSchema, status: "rejected", changed: false, reason_code, reason});
  const {registered_agents: _registered, ...intent} = input;
  const identity = {schema_version: "loopx_continuation_adoption_receipt_v0",
    operation_id: input.operation_id, goal_id: input.goal_id, request_sha256: canonicalAuthoritySha256(intent)};
  const receipt = new CoordinationCommandReceipt({result_schema: resultSchema, identity, failure,
    decode(original) {
      const payload = commandReceiptResult(original);
      if (payload.changed || payload.fields.adopted !== true || payload.fields.todo_id !== input.todo_id ||
          payload.fields.agent_id !== input.agent_id || payload.fields.note_facts !== input.note_facts) {
        throw new AuthorityStoreProtocolError("adoption receipt does not match the accepted context");
      }
      return payload;
    }});
  const replay = await receipt.read(store);
  if (replay !== null) return replay;
  const observation = await receipt.observe(store);
  if (observation.kind === "receipt") return observation.result;
  const head = observation.authority;
  if (head.status !== "loaded") return {schema_version: resultSchema, ...head, changed: false};
  if (head.provider_revision !== input.expected_provider_revision) {
    return failure("provider_revision_mismatch", "Inspect the current context and lease before adopting");
  }
  const execution = continuationExecutionAuthority(head.head, input);
  if (!execution.allowed) return failure(execution.reason_code, "Current execution authority does not admit adoption");
  const todo = indexCoordinationProjection(head.head, input.goal_id).todos.get(input.todo_id)!;
  const note = validateContinuationNote(todo.note, computeContinuationTodoFacts(todo));
  if (todo.claimed_by !== input.agent_id || !note.valid || note.noteFacts !== input.note_facts ||
      note.note?.source_session === input.session_id) {
    return failure("continuation_not_ready", "Current owner, context or source session changed");
  }
  // Receipt-only CAS: seal the context decision without inventing a Todo edit.
  // The caller separately rereads current authority after both commit and replay.
  return receipt.commit(store, {operation_id: input.operation_id,
    expected_provider_revision: head.provider_revision, next_projection: head.head, events: [],
    receipts: [{...identity, result: {changed: false, adopted: true, todo_id: input.todo_id,
      agent_id: input.agent_id, note_facts: input.note_facts}}]});
}
