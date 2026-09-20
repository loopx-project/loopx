/** Admission for Todo edits; terminal completion retains its own lease proof.
 * Grants may cross a claim owner;
 * exclusions, bindings and execution lineage remain independent restrictions. */
import type {JsonObject} from "../effect_program.ts";
import type {CoordinationTodoUpdateInput} from "./todo_update_intent.ts";
import {TODO_WORK_REQUIREMENT_FIELDS} from "../todos/work_requirements.ts";
import {TODO_OWNERSHIP_INTENT_FIELDS} from "../todos/authoring_scope.ts";
import {evaluateCoordinationTodoMutationDecision,
  COORDINATION_TODO_MUTATION_DECISION_REQUEST_SCHEMA} from "./todo_lifecycle_decision.ts";
import {evaluateCanonicalTaskLeaseProof} from "./task_lease_proof.ts";

interface TodoUpdateRejection {code: string; reason: string}
const reject = (code: string, reason: string): TodoUpdateRejection => ({code, reason});

export function todoUpdateAdmissionRejection(
  head: JsonObject, todo: JsonObject, leases: ReadonlyMap<string, JsonObject>,
  input: CoordinationTodoUpdateInput, kind: "planning" | "user_completion" = "planning",
): TodoUpdateRejection | null {
  if (input.expected_role !== null && todo.role !== input.expected_role) {
    return reject("todo_role_mismatch", "Todo does not have the requested role");
  }
  if (todo.archive_state !== "active") {
    return reject("todo_archived", "Todo update requires an active Todo");
  }
  const observation = input.monitor_observation;
  const reactivating = observation !== undefined && todo.status === "done" && input.planning_intent?.status === "open";
  if (observation !== undefined && (todo.role !== "agent" || todo.task_class !== "continuous_monitor" ||
      (todo.status !== "open" && !reactivating))) {
    return reject("invalid_monitor_observation_target", "Observation requires an open Agent Monitor or explicit reactivation of a completed Monitor");
  }
  if (todo.status === "done" && kind === "planning" && !reactivating) {
    return reject("unsupported_todo_update_target",
      "native metadata update cannot complete a Todo; use the terminal lifecycle command");
  }
  const lease = leases.get(input.todo_id);
  if (reactivating && lease !== undefined) {
    return reject("monitor_reactivation_lease_transition_required", "A retained execution lease requires lifecycle resolution before Monitor reactivation");
  }
  const mode = head.handoff_mode === undefined ? "legacy" : head.handoff_mode;
  if (typeof mode !== "string" || !["legacy", "soft_claim", "hard_lease"].includes(mode)) {
    return reject("invalid_handoff_mode", "canonical handoff mode is invalid");
  }
  const intent = input.planning_intent ?? {};
  const ownershipMutation = ["claimed_by", "clear_claim", "excluded_agents", "bound_agent",
    "goal_bound", "blocks_agent", "clear_blocks_agent", "global_gate", "clear_global_gate"]
    .some(field => Object.hasOwn(intent, field));
  if (kind === "user_completion" && (lease !== undefined || mode === "hard_lease") && ownershipMutation) {
    return reject("update_lease_ownership_transition_unsupported",
      "Ownership/exclusion edits require a lease lifecycle transaction; complete before changing the execution grant");
  }
  const authorityDecision = evaluateCoordinationTodoMutationDecision({
    schema_version: COORDINATION_TODO_MUTATION_DECISION_REQUEST_SCHEMA,
    command: "update", handoff_mode: mode, registered_agents: input.registered_agents,
    lifecycle_grants: observation === undefined ? input.lifecycle_grants ?? [] : [],
    // A reassign-only grant cannot authorize a bundled copy/planning edit.
    authority_action: Object.keys(input.patch).length === 0 && input.clear_fields.length === 0 &&
      Object.keys(intent).every(field => field === "claimed_by" || field === "clear_claim") &&
      (intent.claimed_by !== undefined || intent.clear_claim === true) ? "reassign" : "update",
    authority_reason: input.authority_reason ?? null,
    actor_agent_id: input.actor_agent_id,
    requested_claimed_by: intent.claimed_by ?? null,
    clear_claim: intent.clear_claim === true,
    ownership_mutation: ownershipMutation,
    todo: {
      ...todo, role: todo.role, status: todo.status,
      excluded_agents: todo.excluded_agents ?? [],
      required_decision_scopes: todo.required_decision_scopes ?? [],
    },
  });
  if (authorityDecision.outcome !== "apply") {
    const decisionCode = String(authorityDecision.code ?? "mutation_rejected");
    const code = decisionCode === "claim_owner_mismatch"
      ? "update_owner_mismatch" : decisionCode;
    // Keep the public owner-mismatch diagnostic stable while other shared
    // admission failures use a provider-neutral explanation.
    const reason = code === "update_owner_mismatch"
      ? "Todo update cannot edit another claim owner's work"
      : "Todo update is outside the actor's registered owner/binding scope";
    return reject(code, reason);
  }
  // Preserve the single-agent compatibility path only for genuinely
  // unowned work. An empty registry is not evidence that an arbitrary actor
  // may rewrite an already-owned Todo.
  if (input.registered_agents.length === 0 && input.actor_agent_id !== null) {
    return reject("actor_not_registered", "Todo update requires a registered actor");
  }
  if (input.actor_agent_id === null && (todo.claimed_by !== undefined ||
      todo.bound_agent !== undefined || todo.blocks_agent !== undefined)) {
    return reject("actor_required", "owned or bound Todo updates require an actor");
  }
  if (observation !== undefined && input.actor_agent_id === null) {
    return reject("actor_required", "Monitor observations require a registered actor");
  }
  // A retained lease, even expired/released, has execution lineage. Ownership
  // and exclusions must not change beneath it through a metadata operation.
  if ((lease !== undefined || mode === "hard_lease") && TODO_OWNERSHIP_INTENT_FIELDS.some(field =>
    Object.hasOwn(input.planning_intent ?? {}, field))) {
    return reject("update_lease_ownership_transition_unsupported",
      "Ownership/exclusion edits require a lease lifecycle transaction; metadata update cannot rewrite an execution grant");
  }
  // Completion has its own terminal lease proof and atomic release. It still
  // cannot rewrite an execution grant's ownership or work requirements.
  if (kind === "user_completion") {
    if (lease !== undefined && TODO_WORK_REQUIREMENT_FIELDS.some(field => Object.hasOwn(intent, field))) {
      return reject("update_lease_requirements_transition_unsupported", "Complete leased work before changing its requirements");
    }
    return null;
  }
  if (lease !== undefined || mode === "hard_lease" ||
      input.lease_idempotency_key != null || input.lease_expected_version != null) {
    try {
      const fence = evaluateCanonicalTaskLeaseProof({todo, lease, handoff_mode: mode,
        registered_agents: input.registered_agents, actor_agent_id: input.actor_agent_id,
        lease_idempotency_key: input.lease_idempotency_key ?? null,
        lease_expected_version: input.lease_expected_version ?? null, now: input.now});
      if (fence.outcome !== "apply") {
        return reject(String(fence.code), "Todo update requires the current active lease execution proof");
      }
      if (lease !== undefined && todo.claimed_by !== input.actor_agent_id) {
        return reject("update_owner_mismatch", "Leased Todo update requires the current claim owner");
      }
      const status = input.planning_intent?.status;
      if (lease !== undefined && TODO_WORK_REQUIREMENT_FIELDS.some(field =>
        Object.hasOwn(input.planning_intent ?? {}, field))) {
        return reject("update_lease_requirements_transition_unsupported",
          "Changing leased work requirements requires a new execution grant; metadata update leaves the lease unchanged");
      }
      if (lease !== undefined && typeof status === "string" && status.toLowerCase() !== todo.status) {
        return reject("update_lease_status_transition_unsupported",
          "Changing a leased Todo status requires an atomic lifecycle operation; planning update leaves the lease unchanged");
      }
    } catch (error) {
      return reject("invalid_coordination_projection",
        error instanceof Error ? error.message : "invalid lease facts");
    }
  }
  return null;
}
