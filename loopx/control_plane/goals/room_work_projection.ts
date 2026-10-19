import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";
import {evaluateCoordinationTodoClaimDecision} from "../coordination/todo_claim.ts";
import {normalizeTodoAgent} from "../coordination/todo_agents.ts";
import {sealProjectionEnvelope, PROJECTION_ENVELOPE_SEAL_REQUEST} from "../projection_envelope.ts";

const ROOM_WORK_SOURCE_IDS = ["registry", "connection", "canonical_todos"] as const;

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
  const sources = input.read_sources;
  if (!Array.isArray(sources) || sources.length !== ROOM_WORK_SOURCE_IDS.length ||
      ROOM_WORK_SOURCE_IDS.some(id => sources.filter(raw =>
        requireJsonObject(raw, "room source read").source_id === id).length !== 1)) {
    throw new Error("room projection requires every source read fact exactly once");
  }
  const envelope = sealProjectionEnvelope({schema_version: PROJECTION_ENVELOPE_SEAL_REQUEST,
    projection: "room_work", observed_at: observed, sources: input.read_sources,
    coverage: {scope: "goal_actor", expected_count: 1, included_count: 1, omitted: []}});
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
  const inspectedId = input.inspected_todo_id == null ? null : requireNonEmptyString(input.inspected_todo_id, "inspected_todo_id");
  const inspected = inspectedId == null ? null : snapshot.todos.map(item => requireJsonObject(item, "canonical Todo"))
    .find(todo => todo.todo_id === inspectedId && todo.role === "agent" &&
      (todo.bound_agent == null || todo.bound_agent === "" || normalizeTodoAgent(todo.bound_agent, "todo.bound_agent") === actor));
  return {schema_version: "loopx_room_work_projection_v0", goal_id: goal, actor_id: actor,
    authority_state: "available", mode: "read_only", source_revision: revision,
    generated_at: observed, projection_envelope: envelope, selected_todo: available[0] ?? null,
    counts: {unclaimed: available.length, user_gates: gates},
    ...(inspectedId == null ? {} : {inspected_todo: inspected == null ? null : {
      todo_id: inspectedId, current_owner: inspected.claimed_by === "" ? null : inspected.claimed_by ?? null,
      current_claim_matches_actor: inspected.claimed_by === actor,
    }}),
    truth_contract: {projection_grants_authority: false, memory_grants_authority: false,
      private_task_content_included: false}};
}
