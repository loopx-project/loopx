/** Admission for Todo edits; terminal completion retains its own lease proof.
 * Grants may cross a claim owner;
 * exclusions, bindings and execution lineage remain independent restrictions. */
import {acceptanceRestoration} from "./todo_acceptance_restoration.ts";
import {monitorMutationRejection} from "./todo_monitor_cycle.ts";
import type {JsonObject} from "../effect_program.ts";
import type {CoordinationTodoUpdateInput} from "./todo_update_intent.ts";
import {TODO_WORK_REQUIREMENT_FIELDS} from "../todos/work_requirements.ts";
import {TODO_OWNERSHIP_INTENT_FIELDS} from "../todos/authoring_scope.ts";
import {evaluateCoordinationTodoMutationDecision,
  COORDINATION_TODO_MUTATION_DECISION_REQUEST_SCHEMA} from "./todo_lifecycle_decision.ts";
import {decodeTaskLeaseProof, evaluateCanonicalTaskLeaseProof, todoUpdateLeaseRecovery, leasedTodoEditRejection} from "./task_lease_proof.ts";
import {deferredReopenRejection, isDeferredReopen, isOwnerDeferral} from "./todo_deferred_lifecycle.ts";
import {blockedLifecycleRejection, isBlockedLifecycleTransition} from "./todo_blocked_lifecycle.ts";

interface TodoUpdateRejection {code: string; reason: string; handoff_mode?: string; recovery?: JsonObject}
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
  if (input.monitor_observation !== undefined) {
    return monitorMutationRejection({goal_id: input.goal_id, todo, lease: leases.get(input.todo_id), handoff_mode: head.handoff_mode,
      actor_agent_id: input.actor_agent_id, registered_agents: input.registered_agents,
      operation: todo.status === "done" && input.planning_intent?.status === "open" ? "reactivate" : "observe",
      proof: decodeTaskLeaseProof(input.lease_idempotency_key == null && input.lease_expected_version == null ? null :
        {idempotency_key: input.lease_idempotency_key, expected_version: input.lease_expected_version}), now: input.now});
  }
  if (todo.status === "done" && kind === "planning") {
    return reject("unsupported_todo_update_target",
      "native metadata update cannot complete a Todo; use the terminal lifecycle command");
  }
  const lease = leases.get(input.todo_id);
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
    lifecycle_grants: input.lifecycle_grants ?? [],
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
  // Promotion deliberately preserves legacy claims without inventing leases.
  // An explicitly granted controller must be able to repair planning state on
  // that unleased claim; otherwise a stale blocked/deferred status can never
  // become eligible enough for the real owner to acquire its first lease.
  // Once any lease lineage exists, the ordinary holder/CAS fence still wins.
  const delegatedUnleasedOverride =
    authorityDecision.authority_mode === "delegated_orchestration_override" &&
    mode === "hard_lease" && lease === undefined &&
    input.lease_idempotency_key == null && input.lease_expected_version == null;
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
  if (mode === "hard_lease" && isBlockedLifecycleTransition(input, todo)) {
    try {
      return blockedLifecycleRejection({goal_id: input.goal_id, todo_id: input.todo_id,
        actor_agent_id: input.actor_agent_id, registered_agents: input.registered_agents,
        lease, lease_idempotency_key: input.lease_idempotency_key ?? null,
        lease_expected_version: input.lease_expected_version ?? null, now: input.now});
    } catch (error) {
      return reject("invalid_coordination_projection",
        error instanceof Error ? error.message : "invalid retained lease facts");
    }
  }
  if ((mode === "hard_lease" || lease !== undefined) && isDeferredReopen(input, todo)) {
    try {
      return deferredReopenRejection({goal_id: input.goal_id, todo_id: input.todo_id,
        actor_agent_id: input.actor_agent_id, registered_agents: input.registered_agents,
        lease, lease_idempotency_key: input.lease_idempotency_key ?? null,
        lease_expected_version: input.lease_expected_version ?? null, now: input.now});
    } catch (error) {
      return reject("invalid_coordination_projection",
        error instanceof Error ? error.message : "invalid retained lease facts");
    }
  }
  if (!delegatedUnleasedOverride && (lease !== undefined || mode === "hard_lease" ||
      input.lease_idempotency_key != null || input.lease_expected_version != null)) {
    try {
      const fence = evaluateCanonicalTaskLeaseProof({todo, lease, handoff_mode: mode,
        registered_agents: input.registered_agents, actor_agent_id: input.actor_agent_id,
        lease_idempotency_key: input.lease_idempotency_key ?? null,
        lease_expected_version: input.lease_expected_version ?? null, now: input.now});
      if (fence.outcome !== "apply") {
        const restoration = acceptanceRestoration(head, todo, lease, input);
        if (restoration?.kind === "exact_restoration") return null;
        if (restoration?.kind === "unavailable") {
          return reject("goal_acceptance_restoration_unavailable", restoration.reason);
        }
        return {...reject(String(fence.code), "Todo update requires the current active lease execution proof"),
          handoff_mode: mode, recovery: todoUpdateLeaseRecovery(head, input, mode)};
      }
      if (lease !== undefined && todo.claimed_by !== input.actor_agent_id) {
        return reject("update_owner_mismatch", "Leased Todo update requires the current claim owner");
      }
      if (lease !== undefined) {
        return isOwnerDeferral(input, todo) ? null : leasedTodoEditRejection(todo, input.planning_intent ?? {});
      }
    } catch (error) {
      return reject("invalid_coordination_projection",
        error instanceof Error ? error.message : "invalid lease facts");
    }
  }
  return null;
}
