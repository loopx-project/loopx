import assert from "node:assert/strict";
import test from "node:test";
import {projectRoomWork} from "../../loopx/control_plane/goals/room_work_projection.ts";
import {requireJsonObject} from "../../loopx/control_plane/runtime_decode.ts";

const todo = (todo_id: string, patch = {}) => ({todo_id, role: "agent", status: "open",
  archive_state: "active", task_class: "advancement_task", text: "PRIVATE_TASK_TEXT", ...patch});
const packet = (todos: unknown[], patch = {}) => ({goal_id: "room-goal", actor_id: "agent-a",
  registered_agents: ["agent-a", "agent-b"], observed_at: "2026-01-01T00:00:00Z",
  read_sources: ["registry", "connection", "canonical_todos"].map(source_id => ({source_id,
    read_status: "read", last_read_at: "2026-01-01T00:00:00Z"})),
  snapshot: {status: "loaded", provider_revision: "fixture:1", decision_read_from_provider: true,
    legacy_fallback_used: false, todos, ...patch}});

test("room orientation reuses claim admission and exposes no task content", () => {
  const result = projectRoomWork(packet([
    todo("todo_done", {status: "done"}), todo("todo_bound", {bound_agent: "agent-b"}),
    todo("todo_excluded", {excluded_agents: ["agent-a"]}), todo("todo_claimed", {claimed_by: "agent-b"}),
    todo("todo_monitor", {task_class: "continuous_monitor"}), todo("todo_guarded"), todo("todo_available"),
    todo("todo_gate", {role: "user", task_class: "user_gate", blocks_agent: "agent-a"}),
    todo("todo_foreign_gate", {role: "user", task_class: "user_gate", blocks_agent: "agent-b"}),
  ], {goal_acceptance_work_guards: {todo_guarded: {allowed: false}}}));
  assert.deepEqual(result.counts, {unclaimed: 1, user_gates: 1});
  assert.deepEqual(result.selected_todo, {todo_id: "todo_available", claimed_by: null, actionability: "unclaimed"});
  assert.equal(JSON.stringify(result).includes("PRIVATE_"), false);
  assert.deepEqual(result.truth_contract, {projection_grants_authority: false,
    memory_grants_authority: false, private_task_content_included: false});
});

test("room orientation refuses revoked identity, partial snapshot and invalid freshness", () => {
  for (const bad of [{...packet([]), registered_agents: []}, packet([], {status: "failed"}),
    packet([], {legacy_fallback_used: true}), {...packet([]), observed_at: "not-a-date"},
    {...packet([]), read_sources: []}]) {
    assert.throws(() => projectRoomWork(bad));
  }
});

test("room envelope inherits source read time and discloses unreadable source facts", () => {
  const input = packet([todo("todo_available")]);
  const stale = projectRoomWork({...input, observed_at: "2026-01-01T00:10:00Z"});
  assert.equal(requireJsonObject(stale.projection_envelope, "envelope").fresh, false);
  const unreadable = projectRoomWork({...input, read_sources: input.read_sources.map(row =>
    row.source_id === "canonical_todos" ? {...row, read_status: "unreadable"} : row)});
  assert.equal(requireJsonObject(unreadable.projection_envelope, "envelope").alert, true);
});

test("optional ownership readback is fresh, minimal and actor scoped", () => {
  const input = packet([todo("todo_owned", {claimed_by: "agent-b"}), todo("todo_hidden", {bound_agent: "agent-b"})]);
  const plain = projectRoomWork(input);
  assert.equal(Object.hasOwn(plain, "inspected_todo"), false, "existing callers retain their result shape");
  const observed = projectRoomWork({...input, inspected_todo_id: "todo_owned"});
  assert.deepEqual(observed.inspected_todo, {todo_id: "todo_owned", current_owner: "agent-b", current_claim_matches_actor: false});
  assert.equal(JSON.stringify(observed).includes("PRIVATE_"), false);
  assert.equal(projectRoomWork({...input, inspected_todo_id: "todo_hidden"}).inspected_todo, null);
  const legacy = projectRoomWork({...packet([todo("todo_empty", {bound_agent: "", claimed_by: ""})]), inspected_todo_id: "todo_empty"});
  assert.deepEqual(legacy.inspected_todo, {todo_id: "todo_empty", current_owner: null, current_claim_matches_actor: false});
});
