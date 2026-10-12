/** Reopening work must not make its retained execution effective again.
 * Admission and observation validation run first; all effects join their CAS. */
import {evaluateCoordinationTodoMutationDecision, COORDINATION_TODO_MUTATION_DECISION_REQUEST_SCHEMA} from "./todo_lifecycle_decision.ts";
import {evaluateCanonicalTaskLeaseProof, type TaskLeaseProof} from "./task_lease_proof.ts";
import {HANDOFF_MODES} from "./handoff_mode_policy.ts";
import {requireStringLiteral} from "../runtime_decode.ts";
import type {JsonObject} from "../effect_program.ts";
import type {CoordinationProjectionMutation} from "./coordination_projection.ts";
import {canonicalTaskLease} from "./task_lease_state.ts";
import {leaseEpoch, leaseVersion} from "../work_items/task_lease_acquire.ts";
import {releasedTaskLeaseRecord} from "../work_items/task_lease_lifecycle_decision.ts";
import {todoExecutionDependencyRejection} from "./todo_execution_dependency.ts";

export function planMonitorCycleTransition(input: {
  goal_id: string; before: JsonObject; after: JsonObject; lease: JsonObject | undefined;
  handoff_mode: unknown; now: Date;
}): {mutations: CoordinationProjectionMutation[]; transition: JsonObject | null} {
  if (input.before.status !== "done" || input.after.status !== "open" ||
      input.before.task_class !== "continuous_monitor") return {mutations: [], transition: null};
  const lease = input.lease === undefined ? null :
    canonicalTaskLease(input.lease, input.goal_id, String(input.before.todo_id));
  const retiring = lease !== null && lease.status !== "released";
  return {
    mutations: retiring ? [{kind: "lease_upsert", lease: releasedTaskLeaseRecord(lease, input.now)}] : [],
    transition: {kind: "reactivated", execution_authority_granted: false,
      lease_retirement: lease === null ? "absent" : retiring ? "released" : "already_released",
      next_execution: input.handoff_mode === "hard_lease" || (input.handoff_mode !== "soft_claim" && lease !== null) ? "acquire_fresh_lease" : "ordinary_admission",
      ...(lease === null ? {} : {retired_lease_version: leaseVersion(lease), retired_lease_epoch: leaseEpoch(lease)})},
  };
}

/** Observation and reactivation share actor admission; only an open cycle may
 * consume current execution proof. No delegated grant turns polling into work. */
export function monitorMutationRejection(input: {
  goal_id: string; todo: JsonObject; todos: ReadonlyMap<string, JsonObject>;
  lease: JsonObject | undefined; handoff_mode: unknown;
  actor_agent_id: string | null; registered_agents: readonly string[];
  operation: "observe" | "reactivate"; proof: TaskLeaseProof | null; now: Date;
}): {code: string; reason: string} | null {
  const reject = (code: string, reason: string) => ({code, reason});
  const {todo, lease, actor_agent_id: actor, registered_agents: registered} = input;
  const executionWait = () => {
    const dependency = todoExecutionDependencyRejection(input.todos, String(todo.todo_id), input.now);
    return dependency === null ? null : reject(dependency.code, dependency.reason);
  };
  if (todo.role !== "agent" || todo.task_class !== "continuous_monitor" || todo.archive_state !== "active" ||
      todo.status !== (input.operation === "reactivate" ? "done" : "open")) {
    return reject("invalid_monitor_observation_target", "Observation requires an active Agent Monitor in the requested lifecycle state");
  }
  if (actor === null || !registered.includes(actor)) {
    return reject("actor_not_registered", "Monitor observation requires a registered actor");
  }
  try {
    if (lease !== undefined) canonicalTaskLease(lease, input.goal_id, String(todo.todo_id));
    const mode = requireStringLiteral(input.handoff_mode ?? "legacy", HANDOFF_MODES, "canonical handoff_mode");
    const decision = evaluateCoordinationTodoMutationDecision({
      schema_version: COORDINATION_TODO_MUTATION_DECISION_REQUEST_SCHEMA,
      command: "update", handoff_mode: mode, registered_agents: registered, lifecycle_grants: [],
      authority_action: "update", authority_reason: null, actor_agent_id: actor,
      requested_claimed_by: null, clear_claim: false, ownership_mutation: false,
      todo: {...todo, excluded_agents: todo.excluded_agents ?? [], required_decision_scopes: todo.required_decision_scopes ?? []},
    });
    if (decision.outcome !== "apply") {
      return reject(decision.code === "claim_owner_mismatch" ? "update_owner_mismatch" : String(decision.code),
        "Monitor observation is outside the actor's registered owner/binding scope");
    }
    if (input.operation === "reactivate") {
      return input.proof === null ? null : reject("monitor_reactivation_execution_proof_not_allowed",
        "Reactivation records a new observation cycle; acquire a fresh lease after it commits");
    }
    if (mode === "soft_claim") {
      // A released record is history, not a second authority in claim-only mode.
      // An active leftover still requires explicit cleanup; never use its proof.
      if (input.proof !== null || (lease !== undefined && lease.status !== "released")) {
        return reject("handoff_mode_forbids_lease", "soft_claim forbids lease-backed Monitor observation; release retained active execution first");
      }
      return executionWait();
    }
    if (lease === undefined && mode !== "hard_lease" && input.proof === null) return executionWait();
    const fence = evaluateCanonicalTaskLeaseProof({todo, lease, handoff_mode: mode,
      actor_agent_id: actor, registered_agents: registered, now: input.now,
      lease_idempotency_key: input.proof?.idempotency_key ?? null,
      lease_expected_version: input.proof?.expected_version ?? null});
    if (fence.outcome !== "apply") return reject(String(fence.code), `Monitor requires current lease proof: ${fence.code}`);
    if (lease !== undefined && todo.claimed_by !== actor) return reject("update_owner_mismatch",
      "Leased Monitor observation requires the current claim owner");
    return executionWait();
  } catch (error) {
    return reject("invalid_coordination_projection", error instanceof Error ? error.message : "invalid Monitor authority");
  }
}
