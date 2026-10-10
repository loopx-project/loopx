import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";
import {evaluateCoordinationTodoClaimDecision} from "../coordination/todo_claim.ts";

/** Content-minimal room orientation. Claim admission remains in the Todo owner. */
export function projectRoomWork(value: unknown): JsonObject {
  const input = requireJsonObject(value, "room work projection");
  const goal = requireNonEmptyString(input.goal_id, "goal_id");
  const actor = requireNonEmptyString(input.actor_id, "actor_id");
  const registered = requireStringArray(input.registered_agents, "registered_agents");
  if (!registered.includes(actor)) throw new Error("room actor is not registered");
  const snapshot = requireJsonObject(input.snapshot, "canonical snapshot");
  if (snapshot.status !== "loaded" || snapshot.decision_read_from_provider !== true ||
      snapshot.legacy_fallback_used !== false || !Array.isArray(snapshot.todos)) {
    throw new Error("canonical room snapshot unavailable");
  }
  const revision = requireNonEmptyString(snapshot.provider_revision, "provider_revision");
  const observed = requireNonEmptyString(input.observed_at, "observed_at");
  const now = new Date(observed);
  if (Number.isNaN(now.valueOf())) throw new Error("invalid observation time");
  const guards = snapshot.goal_acceptance_work_guards == null ? {} :
    requireJsonObject(snapshot.goal_acceptance_work_guards, "acceptance guards");
  const available: JsonObject[] = [];
  let gates = 0;
  for (const item of snapshot.todos) {
    const todo = requireJsonObject(item, "canonical Todo");
    if (todo.role === "user" && todo.status === "open" && todo.task_class === "user_gate" &&
        (todo.global_gate === true || todo.blocks_agent === actor || todo.bound_agent === actor)) gates++;
    if (todo.role === "agent" && todo.task_class != null && todo.task_class !== "advancement_task") continue;
    if (todo.claimed_by != null && todo.claimed_by !== "") continue;
    const id = requireNonEmptyString(todo.todo_id, "todo_id");
    const guard = guards[id];
    if (guard != null && requireJsonObject(guard, "acceptance guard").allowed === false) continue;
    const decision = evaluateCoordinationTodoClaimDecision(todo, {
      goal_id: goal, todo_id: id, claimed_by: actor, actor_agent_id: actor,
      expected_role: "agent", registered_agents: registered, operation_id: "room-orientation",
      expected_provider_revision: revision, dry_run: true, now,
    });
    if (decision.status === "accepted") available.push({todo_id: id, claimed_by: null,
      actionability: "unclaimed"});
  }
  return {schema_version: "loopx_room_work_projection_v0", goal_id: goal, actor_id: actor,
    authority_state: "available", mode: "read_only", source_revision: revision,
    generated_at: observed, selected_todo: available[0] ?? null,
    counts: {unclaimed: available.length, user_gates: gates},
    truth_contract: {projection_grants_authority: false, memory_grants_authority: false,
      private_task_content_included: false}};
}
